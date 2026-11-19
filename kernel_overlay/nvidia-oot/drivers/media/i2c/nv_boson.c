// SPDX-License-Identifier: GPL-2.0-only
/*
 * FLIR Boson+ tegracam driver for the ARK Boson Adapter.
 *
 * A MIPI Boson+ sends 640x512 at 60 Hz on two CSI-2 lanes, with a continuous
 * 246 MHz clock (492 Mbps per lane). It takes commands on I2C at 0x6a, in
 * FLIR's command protocol wrapped in an I2C frame: 8E A1, a big-endian u16
 * length, then <sequence><function id><status><arguments>, every field a
 * big-endian u32, with no CRC and no byte stuffing. Replies queue in a FIFO
 * that the host reads back, and an empty FIFO reads 0xFF. Function ids and
 * enum values are those of FLIR's Boson SDK (FunctionCodes.h, EnumTypes.h).
 * Videology's MIT-licensed flir_boson_kernel_module showed the I2C framing
 * and the MIPI start sequence.
 *
 * Everything this driver sets on the Boson is volatile: nothing is written to
 * its flash, so a power cycle brings back the camera's saved settings.
 *
 * Copyright (c) 2026, ARK Electronics.
 */

#include <nvidia/conftest.h>

#include <linux/delay.h>
#include <linux/i2c.h>
#include <linux/jiffies.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of_device.h>
#include <linux/slab.h>
#include <linux/string.h>
#include <asm/unaligned.h>
#include <media/tegra_v4l2_camera.h>
#include <media/tegracam_core.h>

#define BOSON_I2C_ADDR			0x6a

/* FLIR SDK function ids */
#define BOSON_GETCAMERASN		0x00050002
#define BOSON_GETCAMERAPN		0x00050004
#define BOSON_GETSOFTWAREREV		0x00050022
#define TELEMETRY_SETSTATE		0x00040001
#define TELEMETRY_SETMIPIEMBEDDEDDATATAG	0x0004000b
#define DVO_SETOUTPUTFORMAT		0x00060006
#define DVO_SETTYPE			0x0006000f
#define DVO_GETTYPE			0x00060010
#define DVO_SETMIPISTATE		0x00060024
#define DVO_GETMIPISTATE		0x00060025
#define DVO_SETMIPICLOCKLANEMODE	0x00060026
#define DVO_SETOUTPUTINTERFACE		0x00060028
#define DVO_GETOUTPUTINTERFACE		0x00060029
#define DVOMUX_SETTYPE			0x00330000

/* FLIR SDK enum values */
#define FLR_DISABLE			0
#define FLR_DVO_YCBCR			1
#define FLR_DVO_IR16			3
#define FLR_DVO_TYPE_COLOR		2
#define FLR_DVO_TYPE_MONO14		5
#define FLR_DVO_MIPI			1
#define FLR_DVO_MIPI_STATE_OFF		0
#define FLR_DVO_MIPI_STATE_ACTIVE	2
#define FLR_DVO_MIPI_CLOCK_NON_CONTINUOUS	0
#define FLR_DVO_MIPI_CLOCK_CONTINUOUS	1
#define FLR_DVOMUX_OUTPUT_IF_MIPITX	3
#define FLR_DVOMUX_SRC_IR		0
#define FLR_DVOMUX_TYPE_MONO16		0
#define FLR_DVOMUX_TYPE_COLOR		2

#define BOSON_HDR0			0x8e
#define BOSON_HDR1			0xa1
#define BOSON_MAX_ARGS			3
#define BOSON_MAX_REPLY			64
#define BOSON_FLUSH_READS		32
#define BOSON_BUSY_TIMEOUT_MS		2000
#define BOSON_POLL_MS			5
/*
 * Starting MIPI keeps the Boson busy for about 300 ms. While busy it holds
 * SCL low until the Tegra controller gives up (-ETIMEDOUT) or NAKs its
 * address. A read that times out still clocks out a byte, which can be the
 * reply's first header byte: the reply poll accepts a bare second header byte
 * for that reason, as FLIR's own I2C transport does.
 */
#define BOSON_SLOW_CMD_MS		500

struct boson_mode_cfg {
	u32 dvo_type;
	u32 out_format;
	u32 mux_type;
};

/* Indexed by the DT mode number. */
static const struct boson_mode_cfg boson_mode_cfg[] = {
	/* mode0: post-colorize UYVY */
	{ FLR_DVO_TYPE_COLOR, FLR_DVO_YCBCR, FLR_DVOMUX_TYPE_COLOR },
	/*
	 * mode1: pre-AGC 14-bit. The mux takes MONO16 here: MONO14 fails with
	 * FLR_BAD_OUT_TYPE, and DVO type MONO14 packs the 16-bit IR to RAW14.
	 */
	{ FLR_DVO_TYPE_MONO14, FLR_DVO_IR16, FLR_DVOMUX_TYPE_MONO16 },
};

static const int boson_60fps[] = { 60 };

static const struct camera_common_frmfmt boson_frmfmt[] = {
	{ { 640, 512 }, boson_60fps, ARRAY_SIZE(boson_60fps), 0, 0 },
	{ { 640, 512 }, boson_60fps, ARRAY_SIZE(boson_60fps), 0, 1 },
};

static const struct of_device_id boson_of_match[] = {
	{ .compatible = "flir,boson-plus", },
	{ },
};
MODULE_DEVICE_TABLE(of, boson_of_match);

static const u32 ctrl_cid_list[] = {
	TEGRA_CAMERA_CID_SENSOR_MODE_ID,
};

struct boson {
	struct i2c_client *i2c_client;
	struct tegracam_device *tc_dev;
	struct camera_common_data *s_data;
	/* Serialises commands: replies come back in order through one FIFO. */
	struct mutex lock;
	u32 seq;
};

/*
 * tegracam needs a regmap, but the Boson has no registers. Nothing in this
 * driver uses it, and the register ops below refuse.
 */
static const struct regmap_config boson_regmap_config = {
	.reg_bits = 8,
	.val_bits = 8,
};

static int boson_read_reg(struct camera_common_data *s_data, u16 addr, u8 *val)
{
	return -EOPNOTSUPP;
}

static int boson_write_reg(struct camera_common_data *s_data, u16 addr, u8 val)
{
	return -EOPNOTSUPP;
}

static int boson_recv(struct boson *priv, u8 *buf, int len)
{
	int ret = i2c_master_recv(priv->i2c_client, buf, len);

	if (ret == len)
		return 0;
	return ret < 0 ? ret : -EIO;
}

/*
 * Drain stale replies, such as one to a command that timed out, and wait out
 * a busy Boson.
 */
static int boson_flush(struct boson *priv)
{
	unsigned long deadline = jiffies + msecs_to_jiffies(BOSON_BUSY_TIMEOUT_MS);
	u8 buf[4];
	int reads = 0, err;

	for (;;) {
		err = boson_recv(priv, buf, sizeof(buf));
		if (!err && get_unaligned_be32(buf) == 0xffffffff)
			return 0;
		if (!err && ++reads >= BOSON_FLUSH_READS)
			return -EIO;
		if (time_after(jiffies, deadline))
			return err ? err : -ETIMEDOUT;
		if (err)
			msleep(BOSON_POLL_MS);
	}
}

static int boson_send(struct boson *priv, u32 fn, const u32 *args,
		      unsigned int nargs)
{
	unsigned long deadline = jiffies + msecs_to_jiffies(BOSON_BUSY_TIMEOUT_MS);
	u8 tx[4 + 12 + 4 * BOSON_MAX_ARGS];
	unsigned int len = 12 + 4 * nargs, i;
	int ret;

	if (nargs > BOSON_MAX_ARGS)
		return -EINVAL;

	tx[0] = BOSON_HDR0;
	tx[1] = BOSON_HDR1;
	put_unaligned_be16(len, &tx[2]);
	put_unaligned_be32(++priv->seq, &tx[4]);
	put_unaligned_be32(fn, &tx[8]);
	put_unaligned_be32(0xffffffff, &tx[12]);
	for (i = 0; i < nargs; i++)
		put_unaligned_be32(args[i], &tx[16 + 4 * i]);

	for (;;) {
		ret = i2c_master_send(priv->i2c_client, tx, 4 + len);
		if (ret == 4 + len)
			return 0;
		if (ret >= 0)
			return -EIO;
		/* A NAKed address means the Boson is busy. */
		if (ret != -EREMOTEIO || time_after(jiffies, deadline))
			return ret;
		msleep(BOSON_POLL_MS);
	}
}

/*
 * Wait for the reply to the last command and copy up to data_len bytes of its
 * data. Returns the number of bytes copied, or a negative error.
 */
static int boson_reply(struct boson *priv, u32 fn, void *data,
		       unsigned int data_len)
{
	struct device *dev = &priv->i2c_client->dev;
	unsigned long deadline = jiffies + msecs_to_jiffies(BOSON_BUSY_TIMEOUT_MS);
	u8 rx[12 + BOSON_MAX_REPLY];
	u8 b;
	u16 len;
	u32 status;
	int err;

	/* An idle FIFO reads 0xff; a busy Boson NAKs or times the read out. */
	for (;;) {
		err = boson_recv(priv, &b, 1);
		if (!err && (b == BOSON_HDR0 || b == BOSON_HDR1))
			break;
		if (!err && b != 0xff) {
			dev_err(dev, "0x%08x: stray byte 0x%02x\n", fn, b);
			return -EPROTO;
		}
		if (time_after(jiffies, deadline)) {
			dev_err(dev, "0x%08x: no reply (%d)\n", fn, err);
			return -ETIMEDOUT;
		}
		msleep(BOSON_POLL_MS);
	}

	/* A bare 0xa1: a read that timed out took the 0x8e. */
	if (b == BOSON_HDR0) {
		err = boson_recv(priv, &b, 1);
		if (!err && b != BOSON_HDR1)
			err = -EPROTO;
	}
	if (!err)
		err = boson_recv(priv, rx, 2);
	if (err) {
		dev_err(dev, "0x%08x: bad reply header (%d)\n", fn, err);
		return err;
	}

	len = get_unaligned_be16(rx);
	if (len < 12 || len > sizeof(rx)) {
		dev_err(dev, "0x%08x: reply length %u\n", fn, len);
		return -EPROTO;
	}
	err = boson_recv(priv, rx, len);
	if (err)
		return err;

	if (get_unaligned_be32(&rx[0]) != priv->seq ||
	    get_unaligned_be32(&rx[4]) != fn) {
		dev_err(dev, "0x%08x: reply is for 0x%08x, sequence %u\n", fn,
			get_unaligned_be32(&rx[4]), get_unaligned_be32(&rx[0]));
		return -EPROTO;
	}
	status = get_unaligned_be32(&rx[8]);
	if (status) {
		dev_err(dev, "0x%08x: status 0x%x\n", fn, status);
		return -EIO;
	}

	if (!data)
		return 0;
	data_len = min_t(unsigned int, data_len, len - 12);
	memcpy(data, &rx[12], data_len);
	return data_len;
}

/* Returns the number of reply data bytes copied, or a negative error. */
static int boson_cmd(struct boson *priv, u32 fn, const u32 *args,
		     unsigned int nargs, void *data, unsigned int data_len,
		     unsigned int settle_ms)
{
	int err;

	err = boson_flush(priv);
	if (!err)
		err = boson_send(priv, fn, args, nargs);
	if (err) {
		dev_err(&priv->i2c_client->dev, "0x%08x: I2C error %d\n", fn, err);
		return err;
	}
	if (settle_ms)
		msleep(settle_ms);
	return boson_reply(priv, fn, data, data_len);
}

static int boson_get_u32(struct boson *priv, u32 fn, u32 *val)
{
	u8 buf[4];
	int ret = boson_cmd(priv, fn, NULL, 0, buf, sizeof(buf), 0);

	if (ret < 0)
		return ret;
	if (ret < sizeof(buf))
		return -EPROTO;
	*val = get_unaligned_be32(buf);
	return 0;
}

static int boson_set_u32(struct boson *priv, u32 fn, u32 val,
			 unsigned int settle_ms)
{
	int ret = boson_cmd(priv, fn, &val, 1, NULL, 0, settle_ms);

	return ret < 0 ? ret : 0;
}

/* Set a value the Boson can be slow to change, only when it differs. */
static int boson_update_u32(struct boson *priv, u32 get_fn, u32 set_fn, u32 val)
{
	u32 cur;
	int err = boson_get_u32(priv, get_fn, &cur);

	if (err || cur == val)
		return err;
	return boson_set_u32(priv, set_fn, val, BOSON_SLOW_CMD_MS);
}

/*
 * DVO_SETMIPISTATE ACTIVE takes about 300 ms, and its I2C reply did not always
 * arrive on the bench. Send it, wait it out, and read the state back; the
 * flush in front of that read drains a late reply.
 */
static int boson_mipi_start(struct boson *priv)
{
	u32 state = FLR_DVO_MIPI_STATE_ACTIVE;
	int err;

	err = boson_flush(priv);
	if (!err)
		err = boson_send(priv, DVO_SETMIPISTATE, &state, 1);
	if (err)
		return err;
	msleep(BOSON_SLOW_CMD_MS);

	err = boson_get_u32(priv, DVO_GETMIPISTATE, &state);
	if (err)
		return err;
	if (state != FLR_DVO_MIPI_STATE_ACTIVE) {
		dev_err(&priv->i2c_client->dev, "MIPI state %u after start\n", state);
		return -EIO;
	}
	return 0;
}

static int boson_set_group_hold(struct tegracam_device *tc_dev, bool val)
{
	return 0;
}

static struct tegracam_ctrl_ops boson_ctrl_ops = {
	.numctrls = ARRAY_SIZE(ctrl_cid_list),
	.ctrl_cid_list = ctrl_cid_list,
	.set_group_hold = boson_set_group_hold,
};

/* The Boson runs from the camera connector's always-on 3.3 V. */
static int boson_power_on(struct camera_common_data *s_data)
{
	s_data->power->state = SWITCH_ON;
	return 0;
}

static int boson_power_off(struct camera_common_data *s_data)
{
	s_data->power->state = SWITCH_OFF;
	return 0;
}

static int boson_power_get(struct tegracam_device *tc_dev)
{
	struct camera_common_power_rail *pw = tc_dev->s_data->power;

	if (!pw)
		return -EFAULT;
	pw->mclk = NULL;
	pw->state = SWITCH_OFF;
	return 0;
}

static int boson_power_put(struct tegracam_device *tc_dev)
{
	return tc_dev->s_data->power ? 0 : -EFAULT;
}

static struct camera_common_pdata *boson_parse_dt(struct tegracam_device *tc_dev)
{
	struct device *dev = tc_dev->dev;

	/*
	 * tegracam_device_register() handles only NULL and ERR_PTR(-EPROBE_DEFER);
	 * any other error pointer would be used as pdata.
	 */
	if (!dev->of_node || !of_match_device(boson_of_match, dev))
		return NULL;

	/* No clocks, regulators or GPIOs: the adapter wires none of them. */
	return devm_kzalloc(dev, sizeof(struct camera_common_pdata), GFP_KERNEL);
}

static int boson_set_mode(struct tegracam_device *tc_dev)
{
	struct boson *priv = tegracam_get_privdata(tc_dev);
	struct camera_common_data *s_data = tc_dev->s_data;
	const struct boson_mode_cfg *cfg;
	int mode = s_data->mode_prop_idx;
	u32 mux[3], clock;
	int err;

	if (mode < 0 || mode >= ARRAY_SIZE(boson_mode_cfg) ||
	    mode >= s_data->sensor_props.num_modes)
		return -EINVAL;
	cfg = &boson_mode_cfg[mode];
	/* The mode's discontinuous_clk picks the Boson's clock-lane mode. */
	clock =s_data->sensor_props.sensor_modes[mode].signal_properties.discontinuous_clk ?
		FLR_DVO_MIPI_CLOCK_NON_CONTINUOUS : FLR_DVO_MIPI_CLOCK_CONTINUOUS;
	mux[0] = FLR_DVOMUX_OUTPUT_IF_MIPITX;
	mux[1] = FLR_DVOMUX_SRC_IR;
	mux[2] = cfg->mux_type;

	mutex_lock(&priv->lock);
	err = boson_set_u32(priv, DVO_SETMIPISTATE, FLR_DVO_MIPI_STATE_OFF, 0);
	/*
	 * The modes describe 512 image lines and no embedded data. Telemetry
	 * would add a line, and the MIPI embedded-data tag (on from the factory,
	 * even with telemetry off) sends an embedded-data packet that the VI
	 * rejects as EMBED_INFRINGE, discarding every frame.
	 */
	if (!err)
		err = boson_set_u32(priv, TELEMETRY_SETSTATE, FLR_DISABLE, 0);
	if (!err)
		err = boson_set_u32(priv, TELEMETRY_SETMIPIEMBEDDEDDATATAG,
				    FLR_DISABLE, 0);
	if (!err)
		err = boson_update_u32(priv, DVO_GETTYPE, DVO_SETTYPE, cfg->dvo_type);
	if (!err)
		err = boson_set_u32(priv, DVO_SETOUTPUTFORMAT, cfg->out_format, 0);
	if (!err)
		err = boson_update_u32(priv, DVO_GETOUTPUTINTERFACE,
				       DVO_SETOUTPUTINTERFACE, FLR_DVO_MIPI);
	if (!err)
		err = boson_set_u32(priv, DVO_SETMIPISTATE, FLR_DVO_MIPI_STATE_OFF, 0);
	/* With no reply data, boson_cmd() returns 0 or an error. */
	if (!err)
		err = boson_cmd(priv, DVOMUX_SETTYPE, mux, ARRAY_SIZE(mux), NULL, 0, 0);
	if (!err)
		err = boson_set_u32(priv, DVO_SETMIPICLOCKLANEMODE, clock, 0);
	mutex_unlock(&priv->lock);

	if (err)
		dev_err(tc_dev->dev, "mode %d setup failed (%d)\n", mode, err);
	return err;
}

static int boson_start_streaming(struct tegracam_device *tc_dev)
{
	struct boson *priv = tegracam_get_privdata(tc_dev);
	int err;

	mutex_lock(&priv->lock);
	err = boson_mipi_start(priv);
	mutex_unlock(&priv->lock);

	return err;
}

static int boson_stop_streaming(struct tegracam_device *tc_dev)
{
	struct boson *priv = tegracam_get_privdata(tc_dev);
	int err;

	mutex_lock(&priv->lock);
	err = boson_set_u32(priv, DVO_SETMIPISTATE, FLR_DVO_MIPI_STATE_OFF, 0);
	mutex_unlock(&priv->lock);

	return err;
}

static struct camera_common_sensor_ops boson_common_ops = {
	.numfrmfmts = ARRAY_SIZE(boson_frmfmt),
	.frmfmt_table = boson_frmfmt,
	.power_on = boson_power_on,
	.power_off = boson_power_off,
	.write_reg = boson_write_reg,
	.read_reg = boson_read_reg,
	.parse_dt = boson_parse_dt,
	.power_get = boson_power_get,
	.power_put = boson_power_put,
	.set_mode = boson_set_mode,
	.start_streaming = boson_start_streaming,
	.stop_streaming = boson_stop_streaming,
};

/* Check the Boson answers, and log what it is. */
static int boson_identify(struct boson *priv)
{
	struct device *dev = &priv->i2c_client->dev;
	char pn[33] = { 0 };
	u8 rev[12];
	u32 sn;
	int ret;

	mutex_lock(&priv->lock);
	ret = boson_get_u32(priv, BOSON_GETCAMERASN, &sn);
	if (!ret)
		ret = boson_cmd(priv, BOSON_GETCAMERAPN, NULL, 0, pn,
				sizeof(pn) - 1, 0);
	if (ret >= 0)
		ret = boson_cmd(priv, BOSON_GETSOFTWAREREV, NULL, 0, rev,
				sizeof(rev), 0);
	mutex_unlock(&priv->lock);
	if (ret < 0)
		return ret;
	if (ret < sizeof(rev))
		return -EPROTO;

	dev_info(dev, "Boson SN %u, PN %s, software %u.%u.%u\n", sn, strim(pn),
		 get_unaligned_be32(&rev[0]), get_unaligned_be32(&rev[4]),
		 get_unaligned_be32(&rev[8]));
	return 0;
}

static int boson_open(struct v4l2_subdev *sd, struct v4l2_subdev_fh *fh)
{
	return 0;
}

static const struct v4l2_subdev_internal_ops boson_subdev_internal_ops = {
	.open = boson_open,
};

#if defined(NV_I2C_DRIVER_STRUCT_PROBE_WITHOUT_I2C_DEVICE_ID_ARG) /* Linux 6.3 */
static int boson_probe(struct i2c_client *client)
#else
static int boson_probe(struct i2c_client *client,
		       const struct i2c_device_id *id)
#endif
{
	struct device *dev = &client->dev;
	struct tegracam_device *tc_dev;
	struct boson *priv;
	int err;

	if (!IS_ENABLED(CONFIG_OF) || !dev->of_node)
		return -EINVAL;
	if (client->addr != BOSON_I2C_ADDR)
		return -ENODEV;

	priv = devm_kzalloc(dev, sizeof(*priv), GFP_KERNEL);
	if (!priv)
		return -ENOMEM;
	mutex_init(&priv->lock);
	tc_dev = devm_kzalloc(dev, sizeof(*tc_dev), GFP_KERNEL);
	if (!tc_dev)
		return -ENOMEM;

	priv->i2c_client = tc_dev->client = client;
	tc_dev->dev = dev;
	strscpy(tc_dev->name, "boson", sizeof(tc_dev->name));
	tc_dev->dev_regmap_config = &boson_regmap_config;
	tc_dev->sensor_ops = &boson_common_ops;
	tc_dev->v4l2sd_internal_ops = &boson_subdev_internal_ops;
	tc_dev->tcctrl_ops = &boson_ctrl_ops;

	err = boson_identify(priv);
	if (err) {
		dev_err(dev, "no Boson answering at 0x%02x (%d)\n", client->addr, err);
		return -ENODEV;
	}

	err = tegracam_device_register(tc_dev);
	if (err) {
		dev_err(dev, "tegra camera registration failed (%d)\n", err);
		return err;
	}
	priv->tc_dev = tc_dev;
	priv->s_data = tc_dev->s_data;
	tegracam_set_privdata(tc_dev, priv);

	err = tegracam_v4l2subdev_register(tc_dev, true);
	if (err) {
		dev_err(dev, "tegra camera subdev registration failed (%d)\n", err);
		tegracam_device_unregister(tc_dev);
		return err;
	}

	return 0;
}

#if defined(NV_I2C_DRIVER_STRUCT_REMOVE_RETURN_TYPE_INT) /* Linux 6.1 */
static int boson_remove(struct i2c_client *client)
#else
static void boson_remove(struct i2c_client *client)
#endif
{
	struct camera_common_data *s_data = to_camera_common_data(&client->dev);
	struct boson *priv = s_data ? s_data->priv : NULL;

	if (priv) {
		tegracam_v4l2subdev_unregister(priv->tc_dev);
		tegracam_device_unregister(priv->tc_dev);
	}
#if defined(NV_I2C_DRIVER_STRUCT_REMOVE_RETURN_TYPE_INT) /* Linux 6.1 */
	return 0;
#endif
}

static const struct i2c_device_id boson_id[] = {
	{ "boson", 0 },
	{ }
};
MODULE_DEVICE_TABLE(i2c, boson_id);

static struct i2c_driver boson_i2c_driver = {
	.driver = {
		.name = "boson",
		.of_match_table = boson_of_match,
	},
	.probe = boson_probe,
	.remove = boson_remove,
	.id_table = boson_id,
};
module_i2c_driver(boson_i2c_driver);

MODULE_DESCRIPTION("Tegracam driver for the FLIR Boson+ MIPI thermal camera");
MODULE_AUTHOR("ARK Electronics");
MODULE_LICENSE("GPL v2");
