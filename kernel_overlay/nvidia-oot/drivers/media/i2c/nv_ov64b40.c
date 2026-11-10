// SPDX-License-Identifier: GPL-2.0-only
/*
 * OV64B40 tegracam driver for the FLIR Hadron 640R visible camera.
 * Structured after RidgeRun's nv_imx708.c. The register tables come from the
 * GPL-2.0 OV64B imgsensor driver in realme's Narzo 50 / 8i kernel release.
 *
 * Copyright (c) 2021-2023, NVIDIA CORPORATION. All rights reserved.
 * Copyright (c) 2023, RidgeRun <support@ridgerun.com>. All rights reserved.
 * Copyright (c) 2026, ARK Electronics.
 */

#include <linux/gpio.h>
#include <linux/mutex.h>
#include <linux/of_device.h>
#include <linux/of_gpio.h>
#include <linux/slab.h>
#include <linux/string.h>
#include <media/tegra_v4l2_camera.h>
#include <media/tegracam_core.h>

#include <media/ov64b40.h>

#include "ov64b40_mode_tbls.h"

static unsigned int test_pattern;
module_param(test_pattern, uint, 0444);
MODULE_PARM_DESC(test_pattern,
	"OV64B40 test-pattern byte written to 0x50c1 (0 = off, 1 = colour bars)");

static const struct of_device_id ov64b40_of_match[] = {
	{.compatible = "ovti,ov64b40",},
	{},
};

MODULE_DEVICE_TABLE(of, ov64b40_of_match);

static const u32 ctrl_cid_list[] = {
	TEGRA_CAMERA_CID_GAIN,
	TEGRA_CAMERA_CID_EXPOSURE,
	TEGRA_CAMERA_CID_FRAME_RATE,
	TEGRA_CAMERA_CID_SENSOR_MODE_ID,
};

struct ov64b40 {
	struct i2c_client *i2c_client;
	struct v4l2_subdev *subdev;
	u32 frame_length;
	u32 coarse_time;
	bool exposure_valid;
	bool rotate_180;
	/*
	 * Last requested gain, exposure and frame rate; 0 means "use the mode's DT
	 * default". set_mode's reset and tables overwrite the sensor, and tegracam
	 * does not replay controls afterwards, so set_mode re-applies these.
	 */
	s64 req_gain;
	s64 req_exposure;
	s64 req_frame_rate;
	/* The exposure and rate the sensor runs at, which their controls have to read. */
	s64 exposure;
	s64 frame_rate;
	struct v4l2_ctrl *exposure_ctrl;
	struct v4l2_ctrl *frame_rate_ctrl;
	bool streaming;
	/* Userspace holds the register group through the group_hold control. */
	bool group_hold;
	/*
	 * Set between set_mode and start_streaming, where tegracam clamps the
	 * controls to the new mode's ranges. Those writes reach the sensor but
	 * are not userspace requests, so they must not outlive the mode.
	 */
	bool updating_ranges;
	/* Serialises whole control, mode and stream transactions. */
	struct mutex lock;
	struct camera_common_data *s_data;
	struct tegracam_device *tc_dev;
};

static const struct regmap_config sensor_regmap_config = {
	.reg_bits = 16,
	.val_bits = 8,
	/* Software reset invalidates sensor state; always access hardware. */
	.cache_type = REGCACHE_NONE,
	.use_single_read = true,
	.use_single_write = true,
};

static int ov64b40_read_reg(struct camera_common_data *s_data,
			  u16 addr, u8 *val)
{
	u32 reg_val;
	int err;

	err = regmap_read(s_data->regmap, addr, &reg_val);
	if (!err)
		*val = reg_val & OV64B40_BYTE_MASK;

	return err;
}

static int ov64b40_write_reg(struct camera_common_data *s_data,
			   u16 addr, u8 val)
{
	int err;

	err = regmap_write(s_data->regmap, addr, val);
	if (err)
		dev_err(s_data->dev, "i2c write failed: 0x%04x = 0x%02x (%d)\n",
			addr, val, err);

	return err;
}

static int ov64b40_write_table(struct ov64b40 *priv,
			       const ov64b40_reg table[])
{
	return regmap_util_write_table_8(priv->s_data->regmap, table, NULL, 0,
					OV64B40_TABLE_WAIT_MS, OV64B40_TABLE_END);
}

static int ov64b40_write_regs(struct ov64b40 *priv,
			      const ov64b40_reg *regs, unsigned int count)
{
	unsigned int i;
	int err;

	for (i = 0; i < count; i++) {
		err = ov64b40_write_reg(priv->s_data, regs[i].addr, regs[i].val);
		if (err)
			return err;
	}

	return 0;
}

static const struct sensor_mode_properties *
ov64b40_get_mode(struct camera_common_data *s_data)
{
	if (!s_data->sensor_props.sensor_modes || s_data->mode_prop_idx < 0 ||
	    s_data->mode_prop_idx >= s_data->sensor_props.num_modes)
		return NULL;

	return &s_data->sensor_props.sensor_modes[s_data->mode_prop_idx];
}

static int ov64b40_write_group_hold(struct camera_common_data *s_data, bool val)
{
	int err;

	if (val)
		return ov64b40_write_reg(s_data, OV64B40_GROUP_HOLD_ADDR,
					 OV64B40_GROUP_HOLD_START);

	err = ov64b40_write_reg(s_data, OV64B40_GROUP_HOLD_ADDR,
				OV64B40_GROUP_HOLD_END);
	if (!err)
		err = ov64b40_write_reg(s_data, OV64B40_GROUP_HOLD_ADDR,
					OV64B40_GROUP_HOLD_LAUNCH);

	return err;
}

static int ov64b40_set_group_hold(struct tegracam_device *tc_dev, bool val)
{
	struct ov64b40 *priv = tegracam_get_privdata(tc_dev);
	int err;

	mutex_lock(&priv->lock);
	err = ov64b40_write_group_hold(tc_dev->s_data, val);
	if (!err)
		priv->group_hold = val;
	mutex_unlock(&priv->lock);

	return err;
}

static int ov64b40_apply_gain(struct tegracam_device *tc_dev, s64 val)
{
	struct ov64b40 *priv = tegracam_get_privdata(tc_dev);
	const struct sensor_mode_properties *mode = ov64b40_get_mode(tc_dev->s_data);
	ov64b40_reg regs[2];
	u16 gain;
	int err;

	if (!mode)
		return -EINVAL;

	err = ov64b40_gain_to_reg(val, mode->control_properties.gain_factor,
				 &gain);
	if (err)
		return err;

	ov64b40_get_gain_regs(regs, gain);
	return ov64b40_write_regs(priv, regs, ARRAY_SIZE(regs));
}

static int ov64b40_set_gain(struct tegracam_device *tc_dev, s64 val)
{
	struct ov64b40 *priv = tegracam_get_privdata(tc_dev);
	int err = 0;

	mutex_lock(&priv->lock);
	/* Before set_mode programs the sensor, only record the request. */
	if (priv->frame_length)
		err = ov64b40_apply_gain(tc_dev, val);
	if (!err && !priv->updating_ranges)
		priv->req_gain = val;
	mutex_unlock(&priv->lock);

	return err;
}

static int ov64b40_write_exposure(struct ov64b40 *priv, u32 lines)
{
	ov64b40_reg regs[3];
	int err;

	ov64b40_get_exposure_regs(regs, lines);
	err = ov64b40_write_regs(priv, regs, ARRAY_SIZE(regs));
	if (err)
		return err;

	priv->coarse_time = lines;
	priv->exposure_valid = true;
	return 0;
}

static int ov64b40_read_exposure(struct ov64b40 *priv)
{
	u8 msb, mid, lsb;
	int err;

	err = ov64b40_read_reg(priv->s_data, OV64B40_EXPOSURE_ADDR_MSB, &msb);
	if (err)
		return err;
	err = ov64b40_read_reg(priv->s_data, OV64B40_EXPOSURE_ADDR_MID, &mid);
	if (err)
		return err;
	err = ov64b40_read_reg(priv->s_data, OV64B40_EXPOSURE_ADDR_LSB, &lsb);
	if (err)
		return err;

	priv->coarse_time = ov64b40_exposure_from_regs(msb, mid, lsb);
	priv->exposure_valid = true;
	return 0;
}

static int ov64b40_apply_exposure(struct tegracam_device *tc_dev, s64 val)
{
	struct ov64b40 *priv = tegracam_get_privdata(tc_dev);
	const struct sensor_mode_properties *mode = ov64b40_get_mode(tc_dev->s_data);
	u32 lines;
	int err;

	if (!mode)
		return -EINVAL;

	/* A request can come from another mode's range. */
	val = clamp_t(s64, val, mode->control_properties.min_exp_time.val,
		      mode->control_properties.max_exp_time.val);

	err = ov64b40_exposure_to_lines(val,
			mode->signal_properties.pixel_clock.val,
			mode->image_properties.line_length,
			mode->control_properties.exposure_factor,
			priv->frame_length, &lines);
	if (err)
		return err;

	err = ov64b40_write_exposure(priv, lines);
	if (!err)
		priv->exposure = val;

	return err;
}

/*
 * tegracam narrows a control to a mode's range at S_FMT and at stream start,
 * and passes a clamp down like a write. The control's current value is then
 * still the old one, outside the new range. A write from userspace always
 * starts from a value inside it. Called with the control handler locked.
 */
static bool ov64b40_range_clamp(const struct v4l2_ctrl *ctrl)
{
	s64 cur;

	if (!ctrl)
		return false;

	cur = *ctrl->p_cur.p_s64;
	return cur < ctrl->minimum || cur > ctrl->maximum;
}

static int ov64b40_set_exposure(struct tegracam_device *tc_dev, s64 val)
{
	struct ov64b40 *priv = tegracam_get_privdata(tc_dev);
	int err = 0;

	mutex_lock(&priv->lock);
	/* Exposure is clamped to VTS, which set_mode programs first. */
	if (priv->frame_length)
		err = ov64b40_apply_exposure(tc_dev, val);
	if (!err && !priv->updating_ranges &&
	    !ov64b40_range_clamp(priv->exposure_ctrl))
		priv->req_exposure = val;
	mutex_unlock(&priv->lock);

	return err;
}

static int ov64b40_write_frame_length(struct tegracam_device *tc_dev,
				      u32 frame_length)
{
	struct ov64b40 *priv = tegracam_get_privdata(tc_dev);
	ov64b40_reg regs[3];
	u32 lines;
	int err;

	/* Preserve the table's exposure unless the new VTS requires a clamp. */
	lines = ov64b40_clamp_exposure(priv->coarse_time, frame_length);
	if (lines != priv->coarse_time) {
		/* Shorten exposure before shortening the frame. */
		err = ov64b40_write_exposure(priv, lines);
		if (err)
			return err;
	}

	ov64b40_get_frame_length_regs(regs, frame_length);
	err = ov64b40_write_regs(priv, regs, ARRAY_SIZE(regs));
	if (err) {
		/* VTS may be half-written: distrust it until the next set_mode. */
		priv->frame_length = 0;
		return err;
	}

	priv->frame_length = frame_length;

	/* Re-fit the requested exposure, which may have been clamped to the old VTS. */
	if (priv->req_exposure > 0)
		return ov64b40_apply_exposure(tc_dev, priv->req_exposure);

	return 0;
}

static int ov64b40_apply_frame_rate(struct tegracam_device *tc_dev, s64 val)
{
	struct ov64b40 *priv = tegracam_get_privdata(tc_dev);
	struct camera_common_data *s_data = tc_dev->s_data;
	const struct sensor_mode_properties *mode = ov64b40_get_mode(s_data);
	u32 frame_length;
	bool hold;
	int hold_err;
	int err;

	if (!mode || s_data->mode < 0 || s_data->mode >= s_data->numfmts)
		return -EINVAL;

	/* Never run faster than the mode's table timing (its VTS) allows. */
	val = clamp_t(s64, val, mode->control_properties.min_framerate,
		      mode->control_properties.max_framerate);

	err = ov64b40_framerate_to_vts(val,
			mode->signal_properties.pixel_clock.val,
			mode->image_properties.line_length,
			mode->control_properties.framerate_factor,
			s_data->frmfmt[s_data->mode].size.height, &frame_length);
	if (err)
		return err;

	if (!priv->exposure_valid) {
		err = ov64b40_read_exposure(priv);
		if (err)
			return err;
	}

	/*
	 * VTS and exposure are three bytes each. While streaming, hold them in
	 * the register group so one frame boundary takes them whole, unless
	 * userspace already holds it.
	 */
	hold = priv->streaming && !priv->group_hold;
	if (hold) {
		err = ov64b40_write_group_hold(s_data, true);
		if (err)
			return err;
	}
	err = ov64b40_write_frame_length(tc_dev, frame_length);
	if (hold) {
		hold_err = ov64b40_write_group_hold(s_data, false);
		if (!err)
			err = hold_err;
	}
	if (!err)
		priv->frame_rate = val;

	return err;
}

static int ov64b40_set_frame_rate(struct tegracam_device *tc_dev, s64 val)
{
	struct ov64b40 *priv = tegracam_get_privdata(tc_dev);
	int err = 0;

	mutex_lock(&priv->lock);
	/* Before set_mode programs the sensor, only record the request. */
	if (priv->frame_length)
		err = ov64b40_apply_frame_rate(tc_dev, val);
	if (!err && !priv->updating_ranges &&
	    !ov64b40_range_clamp(priv->frame_rate_ctrl))
		priv->req_frame_rate = val;
	mutex_unlock(&priv->lock);

	return err;
}

/*
 * tegracam's range update leaves an in-range control value alone, so after a
 * mode change a control can read a value the sensor isn't running. Make the
 * frame-rate and exposure controls read what set_mode applied. Called during
 * the range update, so the writes are not kept as requests.
 */
static int ov64b40_sync_ctrls(struct ov64b40 *priv)
{
	s64 exposure;
	s64 rate;
	int err = 0;

	mutex_lock(&priv->lock);
	rate = priv->frame_rate;
	exposure = priv->exposure;
	mutex_unlock(&priv->lock);

	if (priv->frame_rate_ctrl)
		err = v4l2_ctrl_s_ctrl_int64(priv->frame_rate_ctrl, rate);
	if (!err && priv->exposure_ctrl)
		err = v4l2_ctrl_s_ctrl_int64(priv->exposure_ctrl, exposure);

	return err;
}

static void ov64b40_open_range(struct v4l2_ctrl *ctrl, s64 lo, s64 hi)
{
	if (ctrl && lo <= hi)
		v4l2_ctrl_modify_range(ctrl, lo, hi, ctrl->step,
				       ctrl->default_value);
}

/*
 * tegracam leaves the frame-rate and exposure controls with the range of the
 * last mode they were set to. Between streams, open them to every mode's
 * range, so a request for the next mode is not clamped to the last one's.
 * set_mode clamps each to its mode. Gain's range is the same in every mode.
 */
static void ov64b40_open_ranges(struct ov64b40 *priv)
{
	const struct sensor_properties *props = &priv->s_data->sensor_props;
	s64 rate_lo = S64_MAX;
	s64 rate_hi = 0;
	s64 exp_lo = S64_MAX;
	s64 exp_hi = 0;
	u32 i;

	for (i = 0; i < props->num_modes; i++) {
		const struct sensor_control_properties *cp =
			&props->sensor_modes[i].control_properties;

		rate_lo = min_t(s64, rate_lo, cp->min_framerate);
		rate_hi = max_t(s64, rate_hi, cp->max_framerate);
		exp_lo = min_t(s64, exp_lo, cp->min_exp_time.val);
		exp_hi = max_t(s64, exp_hi, cp->max_exp_time.val);
	}
	ov64b40_open_range(priv->frame_rate_ctrl, rate_lo, rate_hi);
	/* tegracam's own exposure ranges end one above the maximum. */
	ov64b40_open_range(priv->exposure_ctrl, exp_lo, exp_hi + 1);
}

/*
 * The tables come from a phone module that reads out with mirror and flip
 * enabled. Undo both for a 180-degree mount, shifting the window offsets by
 * one so the output stays BGGR (the OnePlus OV64B driver's ROTATION_180 block).
 */
static int ov64b40_apply_rotation(struct ov64b40 *priv)
{
	static const u16 offsets[] = {
		OV64B40_ISP_X_OFFSET_ADDR, OV64B40_ISP_Y_OFFSET_ADDR,
		OV64B40_PD_X_OFFSET_ADDR,
	};
	struct camera_common_data *s_data = priv->s_data;
	unsigned int i;
	u8 val;
	int err;

	err = ov64b40_read_reg(s_data, OV64B40_TIMING_FORMAT1_ADDR, &val);
	if (!err)
		err = ov64b40_write_reg(s_data, OV64B40_TIMING_FORMAT1_ADDR,
					val & ~OV64B40_VFLIP_BIT);
	if (!err)
		err = ov64b40_read_reg(s_data, OV64B40_TIMING_FORMAT2_ADDR, &val);
	if (!err)
		err = ov64b40_write_reg(s_data, OV64B40_TIMING_FORMAT2_ADDR,
					val | OV64B40_MIRROR_OFF_BIT);
	for (i = 0; !err && i < ARRAY_SIZE(offsets); i++) {
		err = ov64b40_read_reg(s_data, offsets[i], &val);
		if (!err)
			err = ov64b40_write_reg(s_data, offsets[i], val + 1);
	}

	return err;
}

/*
 * The tables are 4-lane. Two lanes at the same lane rate carry half the data,
 * so each line needs twice the time: double HTS, which halves the frame rate
 * and doubles the line time (the DT's x2 modes halve pix_clk_hz to match).
 */
static int ov64b40_apply_two_lanes(struct ov64b40 *priv)
{
	struct camera_common_data *s_data = priv->s_data;
	u8 msb, lsb;
	u32 hts;
	int err;

	err = ov64b40_read_reg(s_data, OV64B40_LINE_LENGTH_ADDR_MSB, &msb);
	if (!err)
		err = ov64b40_read_reg(s_data, OV64B40_LINE_LENGTH_ADDR_LSB, &lsb);
	if (err)
		return err;

	hts = 2 * (((u32)msb << OV64B40_BYTE_SHIFT) | lsb);
	err = ov64b40_write_reg(s_data, OV64B40_LINE_LENGTH_ADDR_MSB,
				hts >> OV64B40_BYTE_SHIFT);
	if (!err)
		err = ov64b40_write_reg(s_data, OV64B40_LINE_LENGTH_ADDR_LSB,
					hts & OV64B40_BYTE_MASK);
	if (!err)
		err = ov64b40_write_reg(s_data, OV64B40_MIPI_LANES_ADDR,
					OV64B40_MIPI_2_LANES);

	return err;
}

static struct tegracam_ctrl_ops ov64b40_ctrl_ops = {
	.numctrls = ARRAY_SIZE(ctrl_cid_list),
	.ctrl_cid_list = ctrl_cid_list,
	.set_gain = ov64b40_set_gain,
	.set_exposure = ov64b40_set_exposure,
	.set_frame_rate = ov64b40_set_frame_rate,
	.set_group_hold = ov64b40_set_group_hold,
};

static int ov64b40_power_on(struct camera_common_data *s_data)
{
	struct camera_common_power_rail *pw = s_data->power;
	int err;

	if (!pw)
		return -EFAULT;

	/* /HADRON_RST is shared with Boson. Never pulse it low. */
	err = gpio_direction_output(pw->reset_gpio, 1);
	if (err) {
		dev_err(s_data->dev, "failed to release shared reset (%d)\n", err);
		return err;
	}
	usleep_range(OV64B40_RESET_SETTLE_US, OV64B40_RESET_SETTLE_MAX_US);
	pw->state = SWITCH_ON;

	return 0;
}

static int ov64b40_power_off(struct camera_common_data *s_data)
{
	struct camera_common_power_rail *pw = s_data->power;
	struct ov64b40 *priv = s_data->priv;
	int err;

	if (!pw || !priv)
		return -EFAULT;

	/* The module remains powered; leave shared reset released. */
	mutex_lock(&priv->lock);
	priv->updating_ranges = false;
	err = ov64b40_write_reg(s_data, OV64B40_STREAM_ADDR, OV64B40_STREAM_OFF);
	if (!err)
		priv->streaming = false;
	mutex_unlock(&priv->lock);
	if (err)
		return err;

	/* A format set without streaming left the ranges at that mode's. */
	ov64b40_open_ranges(priv);

	/*
	 * Software standby only: the sensor stays powered and addressable. Keep
	 * reporting it on, or tegracam drops gain/exposure/frame-rate writes made
	 * before streaming (g_input_status) and set_mode never sees them.
	 */
	pw->state = SWITCH_ON;
	return 0;
}

static int ov64b40_power_get(struct tegracam_device *tc_dev)
{
	struct camera_common_data *s_data = tc_dev->s_data;
	struct camera_common_power_rail *pw = s_data->power;
	struct camera_common_pdata *pdata = s_data->pdata;
	int err;

	if (!pw || !pdata)
		return -EFAULT;

	/* Hadron is independently powered and has an internal oscillator. */
	pw->mclk = NULL;
	pw->reset_gpio = pdata->reset_gpio;
	pw->state = SWITCH_OFF;

	/* Request without an initial level; board_setup releases it once. */
	err = devm_gpio_request(tc_dev->dev, pw->reset_gpio, "ov64b40_reset");
	if (err)
		dev_err(tc_dev->dev, "failed to request reset GPIO (%d)\n", err);

	return err;
}

static int ov64b40_power_put(struct tegracam_device *tc_dev)
{
	if (!tc_dev->s_data->power)
		return -EFAULT;

	/* devm owns the GPIO. Do not change its level or direction on release. */
	return 0;
}

static struct camera_common_pdata *
ov64b40_parse_dt(struct tegracam_device *tc_dev)
{
	struct device *dev = tc_dev->dev;
	struct device_node *np = dev->of_node;
	struct camera_common_pdata *pdata;
	int gpio;

	/*
	 * tegracam_device_register() handles only NULL and ERR_PTR(-EPROBE_DEFER);
	 * any other error pointer would be used as pdata.
	 */
	if (!np || !of_match_device(ov64b40_of_match, dev))
		return NULL;

	pdata = devm_kzalloc(dev, sizeof(*pdata), GFP_KERNEL);
	if (!pdata)
		return NULL;

	gpio = of_get_named_gpio(np, "reset-gpios", 0);
	if (gpio == -EPROBE_DEFER)
		return ERR_PTR(-EPROBE_DEFER);
	if (gpio < 0 || !gpio_is_valid(gpio)) {
		dev_err(dev, "reset-gpios lookup failed (%d)\n", gpio);
		return NULL;
	}

	pdata->reset_gpio = gpio;
	/* No clocks or regulators in this DT; no EEPROM/other bus clients. */
	return pdata;
}

static int __ov64b40_set_mode(struct tegracam_device *tc_dev)
{
	struct ov64b40 *priv = tegracam_get_privdata(tc_dev);
	struct camera_common_data *s_data = tc_dev->s_data;
	const struct sensor_mode_properties *mode = ov64b40_get_mode(s_data);
	s64 rate;
	int err;

	/* A range update that failed never reached start_streaming to clear this. */
	priv->updating_ranges = false;

	if (!mode || s_data->mode < 0 || s_data->mode >= s_data->numfmts)
		return -EINVAL;

	if (mode->signal_properties.num_lanes != 4 &&
	    mode->signal_properties.num_lanes != 2) {
		dev_err(tc_dev->dev, "%u lanes are not supported (2 or 4)\n",
			mode->signal_properties.num_lanes);
		return -EINVAL;
	}

	/* Reset only the EO sensor over I2C, never the shared reset GPIO. */
	priv->frame_length = 0;
	priv->exposure_valid = false;
	priv->group_hold = false;
	err = ov64b40_write_reg(s_data, OV64B40_SW_RESET_ADDR, OV64B40_SW_RESET);
	if (err)
		return err;
	usleep_range(OV64B40_SW_RESET_WAIT_US, OV64B40_SW_RESET_WAIT_MAX_US);

	err = ov64b40_write_table(priv, mode_table[OV64B40_MODE_COMMON]);
	if (err) {
		dev_err(tc_dev->dev, "failed writing global table (%d)\n", err);
		return err;
	}

	/* Full-array remosaic needs the crosstalk defaults first, as in MIAMI. */
	if (s_data->mode == OV64B40_MODE_9248x6936) {
		err = ov64b40_write_table(priv, ov64b40_crosstalk);
		if (err) {
			dev_err(tc_dev->dev, "failed writing crosstalk table (%d)\n",
				err);
			return err;
		}
	}

	err = ov64b40_write_table(priv, mode_table[s_data->mode]);
	if (err) {
		dev_err(tc_dev->dev, "failed writing mode %d (%d)\n",
			s_data->mode, err);
		return err;
	}

	if (mode->signal_properties.num_lanes == 2) {
		err = ov64b40_apply_two_lanes(priv);
		if (err) {
			dev_err(tc_dev->dev, "failed switching to 2 lanes (%d)\n", err);
			return err;
		}
	}

	if (priv->rotate_180) {
		err = ov64b40_apply_rotation(priv);
		if (err) {
			dev_err(tc_dev->dev, "failed applying rotation (%d)\n", err);
			return err;
		}
	}

	if (test_pattern) {
		err = ov64b40_write_reg(s_data, OV64B40_TEST_PATTERN_ADDR,
					test_pattern);
		if (err)
			return err;
	}

	/*
	 * The tables reset exposure, gain (to 8x) and VTS. Program the frame rate
	 * first, then exposure and gain: the requested values, or the mode's DT
	 * defaults.
	 */
	rate = priv->req_frame_rate;
	if (rate <= 0)
		rate = mode->control_properties.default_framerate;
	if (rate <= 0)
		rate = ov64b40_scaled_framerate(
				s_data->frmfmt[s_data->mode].framerates[0],
				mode->control_properties.framerate_factor);
	err = ov64b40_apply_frame_rate(tc_dev, rate);
	if (err)
		return err;

	err = ov64b40_apply_exposure(tc_dev, priv->req_exposure > 0 ?
			priv->req_exposure :
			(s64)mode->control_properties.default_exp_time.val);
	if (err)
		return err;

	err = ov64b40_apply_gain(tc_dev, priv->req_gain > 0 ?
			priv->req_gain :
			(s64)mode->control_properties.default_gain);
	if (err)
		return err;

	priv->updating_ranges = true;
	return 0;
}

static int ov64b40_set_mode(struct tegracam_device *tc_dev)
{
	struct ov64b40 *priv = tegracam_get_privdata(tc_dev);
	int err;

	mutex_lock(&priv->lock);
	err = __ov64b40_set_mode(tc_dev);
	mutex_unlock(&priv->lock);

	return err;
}

static int ov64b40_start_streaming(struct tegracam_device *tc_dev)
{
	struct ov64b40 *priv = tegracam_get_privdata(tc_dev);
	int err;

	err = ov64b40_sync_ctrls(priv);
	if (err)
		return err;

	mutex_lock(&priv->lock);
	priv->updating_ranges = false;
	err = ov64b40_write_table(priv, mode_table[OV64B40_START_STREAM]);
	if (!err)
		priv->streaming = true;
	mutex_unlock(&priv->lock);

	return err;
}

static int ov64b40_stop_streaming(struct tegracam_device *tc_dev)
{
	struct ov64b40 *priv = tegracam_get_privdata(tc_dev);
	int err;

	mutex_lock(&priv->lock);
	priv->updating_ranges = false;
	err = ov64b40_write_table(priv, mode_table[OV64B40_STOP_STREAM]);
	if (!err)
		priv->streaming = false;
	mutex_unlock(&priv->lock);

	ov64b40_open_ranges(priv);

	return err;
}

static struct camera_common_sensor_ops ov64b40_common_ops = {
	.numfrmfmts = ARRAY_SIZE(ov64b40_frmfmt),
	.frmfmt_table = ov64b40_frmfmt,
	.power_on = ov64b40_power_on,
	.power_off = ov64b40_power_off,
	.write_reg = ov64b40_write_reg,
	.read_reg = ov64b40_read_reg,
	.parse_dt = ov64b40_parse_dt,
	.power_get = ov64b40_power_get,
	.power_put = ov64b40_power_put,
	.set_mode = ov64b40_set_mode,
	.start_streaming = ov64b40_start_streaming,
	.stop_streaming = ov64b40_stop_streaming,
};

static int ov64b40_board_setup(struct ov64b40 *priv)
{
	struct camera_common_data *s_data = priv->s_data;
	u8 msb, mid, lsb;
	u32 chip_id;
	int err;
	int off_err;

	/* Skip MCLK entirely; releasing shared reset is the only GPIO action. */
	err = ov64b40_power_on(s_data);
	if (err)
		return err;

	err = ov64b40_read_reg(s_data, OV64B40_CHIP_ID_ADDR_MSB, &msb);
	if (err)
		goto read_error;
	err = ov64b40_read_reg(s_data, OV64B40_CHIP_ID_ADDR_MID, &mid);
	if (err)
		goto read_error;
	err = ov64b40_read_reg(s_data, OV64B40_CHIP_ID_ADDR_LSB, &lsb);
	if (err)
		goto read_error;

	chip_id = ov64b40_chip_id_from_regs(msb, mid, lsb);
	dev_info(s_data->dev, "chip ID read: 0x%06x\n", chip_id);
	if (chip_id != OV64B40_CHIP_ID) {
		dev_err(s_data->dev, "unexpected chip ID (expected 0x%06x)\n",
			OV64B40_CHIP_ID);
		err = -ENODEV;
	}
	goto power_off;

read_error:
	dev_err(s_data->dev, "chip ID read failed (%d)\n", err);
power_off:
	/* Preserve an ID mismatch/read failure if the standby write also fails. */
	off_err = ov64b40_power_off(s_data);
	return err ? err : off_err;
}

/*
 * Control registration clamps the uninitialised controls to their minimums
 * and passes them down. Give the controls the first mode's DT defaults
 * instead, then forget them as requests so each mode starts from its own
 * defaults until userspace asks for something.
 */
static void ov64b40_init_ctrls(struct ov64b40 *priv)
{
	struct camera_common_data *s_data = priv->s_data;
	const struct sensor_control_properties *cp;
	struct v4l2_ctrl_handler *hdl;
	struct v4l2_ctrl *ctrl;

	if (!s_data->tegracam_ctrl_hdl || !s_data->sensor_props.num_modes)
		return;

	hdl = &s_data->tegracam_ctrl_hdl->ctrl_handler;
	cp = &s_data->sensor_props.sensor_modes[0].control_properties;

	/*
	 * The frame-rate and exposure controls read the value in use, which may
	 * be a mode default or a clamp nobody asked for. A write of that same
	 * value is still a request.
	 */
	ctrl = v4l2_ctrl_find(hdl, TEGRA_CAMERA_CID_FRAME_RATE);
	if (ctrl) {
		v4l2_ctrl_s_ctrl_int64(ctrl, cp->default_framerate);
		ctrl->flags |= V4L2_CTRL_FLAG_EXECUTE_ON_WRITE;
		priv->frame_rate_ctrl = ctrl;
	}
	ctrl = v4l2_ctrl_find(hdl, TEGRA_CAMERA_CID_EXPOSURE);
	if (ctrl) {
		v4l2_ctrl_s_ctrl_int64(ctrl, cp->default_exp_time.val);
		ctrl->flags |= V4L2_CTRL_FLAG_EXECUTE_ON_WRITE;
		priv->exposure_ctrl = ctrl;
	}
	ctrl = v4l2_ctrl_find(hdl, TEGRA_CAMERA_CID_GAIN);
	if (ctrl)
		v4l2_ctrl_s_ctrl_int64(ctrl, cp->default_gain);

	priv->req_exposure = 0;
	priv->req_gain = 0;
	priv->req_frame_rate = 0;

	ov64b40_open_ranges(priv);
}

static int ov64b40_open(struct v4l2_subdev *sd, struct v4l2_subdev_fh *fh)
{
	struct i2c_client *client = v4l2_get_subdevdata(sd);

	dev_dbg(&client->dev, "%s:\n", __func__);
	return 0;
}

static const struct v4l2_subdev_internal_ops ov64b40_subdev_internal_ops = {
	.open = ov64b40_open,
};

static int ov64b40_probe(struct i2c_client *client,
			 const struct i2c_device_id *id)
{
	struct device *dev = &client->dev;
	struct tegracam_device *tc_dev;
	struct ov64b40 *priv;
	u32 rotation;
	int err;

	if (!IS_ENABLED(CONFIG_OF) || !dev->of_node)
		return -EINVAL;
	if (client->addr != OV64B40_I2C_ADDR)
		return -ENODEV;
	if (test_pattern > OV64B40_BYTE_MASK) {
		dev_err(dev, "test_pattern must fit in one register byte\n");
		return -EINVAL;
	}

	priv = devm_kzalloc(dev, sizeof(*priv), GFP_KERNEL);
	if (!priv)
		return -ENOMEM;
	mutex_init(&priv->lock);
	tc_dev = devm_kzalloc(dev, sizeof(*tc_dev), GFP_KERNEL);
	if (!tc_dev)
		return -ENOMEM;

	priv->i2c_client = tc_dev->client = client;
	tc_dev->dev = dev;
	strscpy(tc_dev->name, "ov64b40", sizeof(tc_dev->name));
	tc_dev->dev_regmap_config = &sensor_regmap_config;
	tc_dev->sensor_ops = &ov64b40_common_ops;
	tc_dev->v4l2sd_internal_ops = &ov64b40_subdev_internal_ops;
	tc_dev->tcctrl_ops = &ov64b40_ctrl_ops;

	err = tegracam_device_register(tc_dev);
	if (err) {
		dev_err(dev, "tegra camera registration failed (%d)\n", err);
		return err;
	}

	priv->tc_dev = tc_dev;
	priv->s_data = tc_dev->s_data;
	priv->subdev = &tc_dev->s_data->subdev;
	tegracam_set_privdata(tc_dev, priv);

	/* Frame intervals the VI reports: two lanes run every mode at half rate. */
	BUILD_BUG_ON(ARRAY_SIZE(ov64b40_frmfmt_2lane) != ARRAY_SIZE(ov64b40_frmfmt));
	if (priv->s_data->sensor_props.num_modes &&
	    priv->s_data->sensor_props.sensor_modes[0].signal_properties.num_lanes == 2)
		priv->s_data->frmfmt = ov64b40_frmfmt_2lane;

	/* Standard video-interfaces rotation of the module as mounted. */
	if (!of_property_read_u32(dev->of_node, "rotation", &rotation))
		priv->rotate_180 = rotation == 180;

	err = ov64b40_board_setup(priv);
	if (err) {
		dev_err(dev, "board setup failed (%d)\n", err);
		goto tegracam_error;
	}

	err = tegracam_v4l2subdev_register(tc_dev, true);
	if (err) {
		dev_err(dev, "tegra camera subdev registration failed (%d)\n", err);
		goto v4l2_error;
	}

	ov64b40_init_ctrls(priv);

	dev_info(dev, "OV64B40 registered%s\n",
		 priv->rotate_180 ? ", rotated 180 degrees" : "");
	return 0;

v4l2_error:
	tegracam_v4l2subdev_unregister(tc_dev);
tegracam_error:
	tegracam_device_unregister(tc_dev);
	return err;
}

/* Linux 5.15 signature, matching the supplied nv_imx708.c. */
static int ov64b40_remove(struct i2c_client *client)
{
	struct camera_common_data *s_data = to_camera_common_data(&client->dev);
	struct ov64b40 *priv;
	int err;

	if (!s_data)
		return 0;
	priv = s_data->priv;

	err = ov64b40_power_off(s_data);
	if (err)
		dev_err(&client->dev, "failed to enter standby on remove (%d)\n", err);
	tegracam_v4l2subdev_unregister(priv->tc_dev);
	tegracam_device_unregister(priv->tc_dev);

	return 0;
}

static const struct i2c_device_id ov64b40_id[] = {
	{"ov64b40", 0},
	{},
};

MODULE_DEVICE_TABLE(i2c, ov64b40_id);

static struct i2c_driver ov64b40_i2c_driver = {
	.driver = {
		.name = "ov64b40",
		.owner = THIS_MODULE,
		.of_match_table = of_match_ptr(ov64b40_of_match),
	},
	.probe = ov64b40_probe,
	.remove = ov64b40_remove,
	.id_table = ov64b40_id,
};

module_i2c_driver(ov64b40_i2c_driver);

MODULE_DESCRIPTION("Tegracam driver for OmniVision OV64B40 / FLIR Hadron EO");
MODULE_LICENSE("GPL v2");
