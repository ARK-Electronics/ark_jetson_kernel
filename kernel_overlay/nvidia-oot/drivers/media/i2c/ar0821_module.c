/*
 * ar0821.c - AR0821 sensor driver
 * Copyright (c) 2021-2022, e-con Systems.  All rights reserved.
 *
 * This program is free software; you can redistribute it and/or modify it
 * under the terms and conditions of the GNU General Public License,
 * version 2, as published by the Free Software Foundation.
 *
 * This program is distributed in the hope it will be useful, but WITHOUT
 * ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
 * FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License for
 * more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <http://www.gnu.org/licenses/>.
 */

/*
 * ARK: from e-con release e-CAM81_CUONX_..._L4T35.4.1_19-DEC-2023_R01_RC2,
 * ported to L4T r36 / kernel 5.15: pad ops take v4l2_subdev_state; subdev
 * g_parm/s_parm became g/s_frame_interval (VIDIOC_[GS]_PARM reaches them via
 * v4l2_[gs]_parm_cap in the r36 VI channel); dead MCU stream-on path dropped.
 */

#include <linux/slab.h>
#include <linux/uaccess.h>
#include <linux/gpio.h>
#include <linux/module.h>

#include <linux/seq_file.h>
#include <linux/of.h>
#include <linux/of_device.h>
#include <linux/of_gpio.h>
#include <asm/byteorder.h>

#include <media/camera_common.h>
#include "../platform/tegra/camera/camera_gpio.h"

#include "ar0821.h"

#include "mcu_firmware.h"
//#include "pca9685.h"

#define DEBUG_PRINTK
#ifndef DEBUG_PRINTK
#define debug_printk(s , ... )
#else
#define debug_printk printk
#endif

static const struct v4l2_ctrl_ops ar0821_ctrl_ops = {
	.g_volatile_ctrl = ar0821_g_volatile_ctrl,
	.s_ctrl = ar0821_s_ctrl,
};

static int ar0821_power_on(struct camera_common_data *s_data)
{
	int err = 0;
	struct ar0821 *priv = (struct ar0821 *)s_data->priv;
	struct camera_common_power_rail *pw = &priv->power;

	if (!priv || !priv->pdata)
		return -EINVAL;
	dev_dbg(&priv->i2c_client->dev, "%s: power on\n", __func__);

	if (priv->pdata && priv->pdata->power_on) {
		err = priv->pdata->power_on(pw);
		if (err)
			dev_err(&priv->i2c_client->dev,"%s failed.\n", __func__);
		else
			pw->state = SWITCH_ON;
		return err;
	}

	if (unlikely(!(pw->avdd || pw->iovdd )))
		goto skip_power_seqn;

	if (pw->avdd)
		err = regulator_enable(pw->avdd);
	if (err)
		goto ar0821_avdd_fail;

        if (pw->iovdd)
                err = regulator_enable(pw->iovdd);
        if (err)
               goto ar0821_iovdd_fail;
 
skip_power_seqn:
	usleep_range(1350, 1360);

	pw->state = SWITCH_ON;
	return 0;

 ar0821_iovdd_fail:
	regulator_disable(pw->avdd);

 ar0821_avdd_fail:
	dev_err(&priv->i2c_client->dev,"%s failed.\n", __func__);
	return -ENODEV;
}

static int ar0821_power_put(struct ar0821 *priv)
{
	struct camera_common_power_rail *pw = &priv->power;
	if (!priv || !priv->pdata)
		return -EINVAL;

	if (unlikely(!pw))
		return -EFAULT;

//	if (likely(pw->avdd))
//		regulator_put(pw->avdd);
//
//	if (likely(pw->iovdd))
//		regulator_put(pw->iovdd);

	pw->avdd = NULL;
	pw->iovdd = NULL;

	if (priv->pdata->use_cam_gpio)
		cam_gpio_deregister(&priv->i2c_client->dev, pw->pwdn_gpio);
	else {
		gpio_free(pw->pwdn_gpio);
		gpio_free(pw->reset_gpio);
	}

	return 0;
}

static int ar0821_power_get(struct ar0821 *priv)
{
	struct camera_common_power_rail *pw = &priv->power;
	struct camera_common_pdata *pdata = priv->pdata;
	const char *mclk_name;
	const char *parentclk_name;
	struct clk *parent;
	int err = 0;

	if (!priv || !priv->pdata)
		return -EINVAL;

	mclk_name = priv->pdata->mclk_name ? priv->pdata->mclk_name : "cam_mclk1";
        if(priv->pdata->mclk_name) {
	        pw->mclk = devm_clk_get(&priv->i2c_client->dev, mclk_name);
	    	if (IS_ERR(pw->mclk)) {
			dev_err(&priv->i2c_client->dev, "unable to get clock %s\n",
				mclk_name);
			return PTR_ERR(pw->mclk);
	        }

		parentclk_name = priv->pdata->parentclk_name;
	        if (parentclk_name) {
		        parent = devm_clk_get(&priv->i2c_client->dev, parentclk_name);
	                if (IS_ERR(parent))
				dev_err(&priv->i2c_client->dev,
					"unable to get parent clcok %s",
					parentclk_name);
	                else
			        clk_set_parent(pw->mclk, parent);
		}
	}

	if(pdata->regulators.avdd)
        err |=
            camera_common_regulator_get(&priv->i2c_client->dev, &pw->avdd,
                                        pdata->regulators.avdd);

	if(pdata->regulators.iovdd)
        err |=
            camera_common_regulator_get(&priv->i2c_client->dev, &pw->iovdd,
                                        pdata->regulators.iovdd);

	if (err) {
		dev_err(&priv->i2c_client->dev, "%s: unable to get regulator(s)\n", __func__);
		goto done;
	}

done :
	pw->state = SWITCH_OFF;
	return err;
}

static int ar0821_s_stream(struct v4l2_subdev *sd, int enable)
{
	struct i2c_client *client = v4l2_get_subdevdata(sd);
	struct camera_common_data *s_data = to_camera_common_data(&client->dev);
	struct ar0821 *priv = (struct ar0821 *)s_data->priv;

	if (!priv || !priv->pdata)
		return -EINVAL;

	// Increment the refs count when streaming and decrement when streaming is disabled
	if (enable) {
	  	if (!try_module_get(s_data->owner))
		  return -ENODEV;
	} else {
	  	module_put(s_data->owner);
	}

	/*
	 * No MCU stream on/off here: e-con avoids them for a green-frame
	 * issue; the MCU starts streaming from mcu_stream_config().
	 */
	return 0;
}

static int ar0821_g_input_status(struct v4l2_subdev *sd, u32 * status)
{
	struct i2c_client *client = v4l2_get_subdevdata(sd);
	struct camera_common_data *s_data = to_camera_common_data(&client->dev);
	struct ar0821 *priv = (struct ar0821 *)s_data->priv;
	struct camera_common_power_rail *pw = &priv->power;

	if (!priv || !priv->pdata)
		return -EINVAL;

	*status = pw->state == SWITCH_ON;
	return 0;
}

static int ar0821_g_frame_interval(struct v4l2_subdev *sd,
				   struct v4l2_subdev_frame_interval *ival)
{
	struct i2c_client *client = v4l2_get_subdevdata(sd);
	struct camera_common_data *s_data = to_camera_common_data(&client->dev);
	struct ar0821 *priv = (struct ar0821 *)s_data->priv;

	if (!priv || !priv->pdata) {
		return -ENOTTY;
	}

	ival->interval.numerator = 1;
	ival->interval.denominator =
	    priv->mcu_cam_frmfmt[priv->frmfmt_mode].framerates[priv->frate_index];

	return 0;
}

static int ar0821_s_frame_interval(struct v4l2_subdev *sd,
				   struct v4l2_subdev_frame_interval *ival)
{
	struct i2c_client *client = v4l2_get_subdevdata(sd);
	struct camera_common_data *s_data = to_camera_common_data(&client->dev);
	struct ar0821 *priv = (struct ar0821 *)s_data->priv;
	unsigned int fps;
	int ret = 0, err = 0;
	int retry = 5;

	if (!priv || !priv->pdata) {
		return -EINVAL;
	}

	if (ival->interval.numerator == 0 || ival->interval.denominator == 0)
		goto unmatched;

	/* timeperframe = 1/fps; an unmatched rate keeps the current one */
	fps = ival->interval.denominator / ival->interval.numerator;

	for (ret = 0; ret < priv->mcu_cam_frmfmt[priv->frmfmt_mode].num_framerates;
	     ret++) {
		if ((priv->mcu_cam_frmfmt[priv->frmfmt_mode].framerates[ret] ==
		     fps)) {
			priv->frate_index = ret;

			/* call stream config with width, height, frame rate */
			while (--retry >= 0) {
				err = mcu_stream_config(client, priv->format_fourcc, priv->frmfmt_mode,
							priv->frate_index);
				if (err < 0) {
				  	dev_err(&client->dev, "(%s) mcu_stream_config failure. retrying \n", __func__);
				  	continue;
				} else {
				  	break;
				}
			}
			if (retry < 0) {
				dev_err(&client->dev, "%s: Failed stream_config \n", __func__);
				return err;
			}
			mdelay(10);

			ival->interval.numerator = 1;
			ival->interval.denominator =
			priv->mcu_cam_frmfmt[priv->frmfmt_mode].framerates[priv->frate_index];

			return 0;
		}
	}

unmatched:
	ival->interval.numerator = 1;
	ival->interval.denominator =
	priv->mcu_cam_frmfmt[priv->frmfmt_mode].framerates[priv->frate_index];

	return 0;
}

static struct v4l2_subdev_video_ops ar0821_subdev_video_ops = {
	.s_stream = ar0821_s_stream,
//	.g_mbus_config = camera_common_g_mbus_config,
	.g_input_status = ar0821_g_input_status,
	.g_frame_interval = ar0821_g_frame_interval,
	.s_frame_interval = ar0821_s_frame_interval,
};

static struct v4l2_subdev_core_ops ar0821_subdev_core_ops = {
	.s_power = camera_common_s_power,
};

static int ar0821_get_fmt(struct v4l2_subdev *sd, struct v4l2_subdev_state *sd_state,
			  struct v4l2_subdev_format *format)
{
	return camera_common_g_fmt(sd, &format->format);
}

static int ar0821_set_fmt(struct v4l2_subdev *sd, struct v4l2_subdev_state *sd_state,
			  struct v4l2_subdev_format *format)
{
	int ret;
	struct i2c_client *client = v4l2_get_subdevdata(sd);
	struct camera_common_data *s_data = to_camera_common_data(&client->dev);
	struct ar0821 *priv = (struct ar0821 *)s_data->priv;
	int flag = 0, err = 0;
	int retry = 5;

#ifdef FRAMESYNC_ENABLE
	int mode = 0;
	mode = s_data->mode;
#endif
	
	if (!priv || !priv->pdata)
		return -EINVAL;

	switch (format->format.code) {
	case MEDIA_BUS_FMT_UYVY8_1X16:
		priv->format_fourcc = V4L2_PIX_FMT_UYVY;
		break;

	default:
		/* Not Implemented */
		if (format->which != V4L2_SUBDEV_FORMAT_TRY) {
			return -EINVAL;
		}
	}

	/* Delay added for green frame issue */
	msleep(50);
	//mode = s_data->mode;
	if (format->which == V4L2_SUBDEV_FORMAT_TRY) {
		ret = camera_common_try_fmt(sd, &format->format);
	} else {

		for (ret = 0; ret < s_data->numfmts ; ret++) {
			if ((priv->mcu_cam_frmfmt[ret].size.width == format->format.width)
					&& (priv->mcu_cam_frmfmt[ret].size.height ==
						format->format.height)) {
				priv->frmfmt_mode = priv->mcu_cam_frmfmt[ret].mode;
				flag = 1;
				break;
			}
		}

		if(flag == 0) {
			return -EINVAL;
		}

		/* call stream config with width, height, frame rate */
		while (--retry >= 0) {
		  	err = mcu_stream_config(client, priv->format_fourcc, priv->frmfmt_mode,
					priv->frate_index);
			if (err < 0) {
			  	dev_err(&client->dev, "%s: Stream_config failure. retrying \n", __func__);
				continue;
			} else {
			  	break;
			}
		}
		if (retry < 0) {
			dev_err(&client->dev, "%s: Failed stream_config \n", __func__);
			return err;
		}

		mdelay(10);

		ret = camera_common_s_fmt(sd, &format->format);
	}

#ifdef FRAMESYNC_ENABLE	
	/* 
	 * Recalibrate in case of Resolution switch from other resolution to MODE_2304x1296 or MODE_2304x1536
	 * when frame sync is calibrated at 60Hz Frequency then ReCalibrate to 
	 * 30Hz frequency for MODE_2304x1296 or MODE_2304x1536 resolution
	 */

	if((mode == MODE_2304x1536 && priv->last_sync_mode == 2) ||
			(mode == MODE_2304x1296 && priv->last_sync_mode == 2 && 
			 priv->mipi_lane_config == NUM_LANES_2)) {
		calibration_init(0);
		priv->last_sync_mode = 1;
	}
#endif
	return ret;
}

static struct v4l2_subdev_pad_ops ar0821_subdev_pad_ops = {
	.enum_mbus_code = camera_common_enum_mbus_code,
	.set_fmt = ar0821_set_fmt,
	.get_fmt = ar0821_get_fmt,
	.enum_frame_size = camera_common_enum_framesizes,
	.enum_frame_interval = camera_common_enum_frameintervals,
};

static struct v4l2_subdev_ops ar0821_subdev_ops = {
	.core = &ar0821_subdev_core_ops,
	.video = &ar0821_subdev_video_ops,
	.pad = &ar0821_subdev_pad_ops,
};

static struct of_device_id ar0821_of_match[] = {
	{.compatible = "nvidia,ar0821",},
	{},
};

static int ar0821_g_volatile_ctrl(struct v4l2_ctrl *ctrl)
{
	struct ar0821 *priv =
	    container_of(ctrl->handler, struct ar0821, ctrl_handler);
	struct i2c_client *client = priv->i2c_client;
	int err = 0;

	uint8_t ctrl_type = 0;
	int ctrl_val = 0;
	if (!priv || !priv->pdata)
		return -EINVAL;

	if (priv->power.state == SWITCH_OFF)
		return 0;

	if ((err = mcu_get_ctrl(client, ctrl->id, &ctrl_type, &ctrl_val)) < 0) {
		return err;
	}

	if (ctrl_type == CTRL_STANDARD) {
		ctrl->val = ctrl_val;
	} else {
		/* Not Implemented */
		return -EINVAL;
	}

	return err;
}

static int ar0821_s_ctrl(struct v4l2_ctrl *ctrl)
{
	struct ar0821 *priv =
	    container_of(ctrl->handler, struct ar0821, ctrl_handler);
	struct i2c_client *client = priv->i2c_client;
	int err = 0;
#ifdef FRAMESYNC_ENABLE	
	struct camera_common_data *s_data = to_camera_common_data(&client->dev);
        int mode = 0;

        mode = s_data->mode;
#endif

	if (!priv || !priv->pdata)
		return -EINVAL;

	if (priv->power.state == SWITCH_OFF)
		return 0;
	
	if ((err = mcu_set_ctrl(client, ctrl->id, CTRL_STANDARD, ctrl->val)) < 0) {
		dev_err(&client->dev," %s (%d ) \n", __func__, __LINE__);
		return -EINVAL;
	}
	
#ifdef FRAMESYNC_ENABLE
	if(ctrl->id == V4L2_CID_FRAME_SYNC) {
		if(ctrl->val == 1){ 
			calibration_init(0);
			priv->last_sync_mode = 1;
		}else if(ctrl->val == 2 &&(  mode < MODE_3840x2160  ||
				( mode == MODE_3840x2160 &&  priv->mipi_lane_config == NUM_LANES_4 ))) {
			calibration_init(1);
			priv->last_sync_mode = 2;
		}
	}
#endif
	return err;
}

static int ar0821_try_add_ctrls(struct ar0821 *priv, int index,
				ISP_CTRL_INFO * mcu_ctrl)
{
	struct i2c_client *client = priv->i2c_client;
	struct v4l2_ctrl_config custom_ctrl_config;

#ifdef FRAMESYNC_ENABLE	
	enum tegra_chipid chip_id;
#endif	

	if (!priv || !priv->pdata)
		return -EINVAL;

	priv->ctrl_handler.error = 0;

	/* zoom, pan and tilt is not stable in latest bootdata */
	switch (mcu_ctrl->ctrl_id) {
	//	case V4L2_CID_FRAME_SYNC:
		case V4L2_CID_ZOOM_ABSOLUTE:
		case V4L2_CID_PAN_ABSOLUTE:
		case V4L2_CID_TILT_ABSOLUTE:
		  return 0;
	}

#ifdef FRAMESYNC_ENABLE
	chip_id= tegra_get_chipid();
	if(chip_id == JETSON_TX2 || chip_id == JETSON_TX1){
		if((mcu_ctrl->ctrl_id == V4L2_CID_FRAME_SYNC) && 
				priv->mipi_lane_config == NUM_LANES_2){
			mcu_ctrl->ctrl_data.std.ctrl_max=1;
		}
	}
#endif	

	/* Try Enumerating in standard controls */
	priv->ctrls[index] =
	    v4l2_ctrl_new_std(&priv->ctrl_handler,
			      &ar0821_ctrl_ops,
			      mcu_ctrl->ctrl_id,
			      mcu_ctrl->ctrl_data.std.ctrl_min,
			      mcu_ctrl->ctrl_data.std.ctrl_max,
			      mcu_ctrl->ctrl_data.std.ctrl_step,
			      mcu_ctrl->ctrl_data.std.ctrl_def);
	if (priv->ctrls[index] != NULL) {
		debug_printk("%d. Initialized Control 0x%08x - %s \n",
			     index, mcu_ctrl->ctrl_id,
			     priv->ctrls[index]->name);
		return 0;
	}

	if(mcu_ctrl->ctrl_id == V4L2_CID_EXPOSURE_AUTO)
		goto custom;


	/* Try Enumerating in standard menu */
	priv->ctrl_handler.error = 0;
	priv->ctrls[index] =
	    v4l2_ctrl_new_std_menu(&priv->ctrl_handler,
				   &ar0821_ctrl_ops,
				   mcu_ctrl->ctrl_id,
				   mcu_ctrl->ctrl_data.std.ctrl_max,
				   0, mcu_ctrl->ctrl_data.std.ctrl_def);
	if (priv->ctrls[index] != NULL) {
		debug_printk("%d. Initialized Control Menu 0x%08x - %s \n",
			     index, mcu_ctrl->ctrl_id,
			     priv->ctrls[index]->name);
		return 0;
	}


custom:
	priv->ctrl_handler.error = 0;
	memset(&custom_ctrl_config, 0x0, sizeof(struct v4l2_ctrl_config));

	if (mcu_get_ctrl_ui(client, mcu_ctrl, index)!= ERRCODE_SUCCESS) {
		dev_err(&client->dev, "Error Enumerating Control 0x%08x !! \n",
			mcu_ctrl->ctrl_id);
		return -EIO;
	}

	/* Fill in Values for Custom Ctrls */
	custom_ctrl_config.ops = &ar0821_ctrl_ops;
	custom_ctrl_config.id = mcu_ctrl->ctrl_id;
	/* Do not change the name field for the control */
	custom_ctrl_config.name = mcu_ctrl->ctrl_ui_data.ctrl_ui_info.ctrl_name;

	/* Sample Control Type and Flags */
	custom_ctrl_config.type = mcu_ctrl->ctrl_ui_data.ctrl_ui_info.ctrl_ui_type;
	custom_ctrl_config.flags = mcu_ctrl->ctrl_ui_data.ctrl_ui_info.ctrl_ui_flags;

	custom_ctrl_config.min = mcu_ctrl->ctrl_data.std.ctrl_min;
	custom_ctrl_config.max = mcu_ctrl->ctrl_data.std.ctrl_max;
	custom_ctrl_config.step = mcu_ctrl->ctrl_data.std.ctrl_step;
	custom_ctrl_config.def = mcu_ctrl->ctrl_data.std.ctrl_def;

	if (custom_ctrl_config.type == V4L2_CTRL_TYPE_MENU) {
		custom_ctrl_config.step = 0;
		custom_ctrl_config.type_ops = NULL;

		custom_ctrl_config.qmenu =
			(const char *const *)(mcu_ctrl->ctrl_ui_data.ctrl_menu_info.menu);
	}

	priv->ctrls[index] =
	    v4l2_ctrl_new_custom(&priv->ctrl_handler,
				 &custom_ctrl_config, NULL);
	if (priv->ctrls[index] != NULL) {
		debug_printk("%d. Initialized Custom Ctrl 0x%08x - %s \n",
			     index, mcu_ctrl->ctrl_id,
			     priv->ctrls[index]->name);
		return 0;
	}

	dev_err(&client->dev,
		"%d.  default: Failed to init 0x%08x ctrl Error - %d \n",
		index, mcu_ctrl->ctrl_id, priv->ctrl_handler.error);
	return -EINVAL;
}

static int ar0821_ctrls_init(struct ar0821 *priv, ISP_CTRL_INFO *mcu_cam_ctrls)
{
	struct i2c_client *client = priv->i2c_client;
	int err = 0, i = 0;
	int retry = 5;

	/* Array of Ctrls */

	/* Custom Ctrl */
	if (!priv || !priv->pdata)
		return -EINVAL;

	while (--retry >= 0) {
	  	err = mcu_list_ctrls(client, mcu_cam_ctrls, priv);
		if (err < 0) {
			dev_err(&client->dev, "Init ctrls failure. retrying\n");
			continue;
		} else {
		  	break;
		}
	}

	if (retry < 0) {
	  	dev_err(&client->dev, "(%s): Unable to init ctrls \n",__func__);
	  	goto error;
	}

	v4l2_ctrl_handler_init(&priv->ctrl_handler, priv->num_ctrls+1);
	priv->subdev->ctrl_handler = &priv->ctrl_handler;
	for (i = 0; i < priv->num_ctrls; i++) {

		if (mcu_cam_ctrls[i].ctrl_type == CTRL_STANDARD) {
				ar0821_try_add_ctrls(priv, i,
						     &mcu_cam_ctrls[i]);
		} else {
			/* Not Implemented */
		}
	}

	return 0;

 error:
	v4l2_ctrl_handler_free(&priv->ctrl_handler);
	return err;
}

MODULE_DEVICE_TABLE(of, ar0821_of_match);

static struct camera_common_pdata *ar0821_parse_dt(struct i2c_client *client)
{
	struct device_node *node = client->dev.of_node;
	struct camera_common_pdata *board_priv_pdata;
	const struct of_device_id *match;
	int gpio;
	int err;

	if (!node)
		return NULL;

	match = of_match_device(ar0821_of_match, &client->dev);
	if (!match) {
		dev_err(&client->dev, "Failed to find matching dt id\n");
		return NULL;
	}

	board_priv_pdata =
	    devm_kzalloc(&client->dev, sizeof(*board_priv_pdata), GFP_KERNEL);
	if (!board_priv_pdata)
		return NULL;

#if 0
	err = camera_common_parse_clocks(&client->dev, board_priv_pdata);
	if (err) {
		dev_err(&client->dev, "Failed to find clocks\n");
		goto error;
	}
#endif
	gpio = of_get_named_gpio(node, "pwdn-gpios", 0);
	if (gpio < 0) {
		dev_err(&client->dev, "pwdn gpios not in DT\n");
		goto error;
	}
	board_priv_pdata->pwdn_gpio = (unsigned int)gpio;

	gpio = of_get_named_gpio(node, "reset-gpios", 0);
	if (gpio < 0) {
		/* reset-gpio is not absoluctly needed */
		dev_dbg(&client->dev, "reset gpios not in DT\n");
		gpio = 0;
	}
	board_priv_pdata->reset_gpio = (unsigned int)gpio;

	board_priv_pdata->use_cam_gpio =
	    of_property_read_bool(node, "cam,use-cam-gpio");

	err =
	    of_property_read_string(node, "avdd-reg",
				    &board_priv_pdata->regulators.avdd);
	err |=
	    of_property_read_string(node, "iovdd-reg",
				    &board_priv_pdata->regulators.iovdd);
	if (err) {
		dev_dbg(&client->dev, "avdd, iovdd-reg not in DT, assume sensor powered independently\n");
	}

	board_priv_pdata->has_eeprom =
	    of_property_read_bool(node, "has-eeprom");

	return board_priv_pdata;

error:
	devm_kfree(&client->dev, board_priv_pdata);
	return NULL;
}

static int ar0821_open(struct v4l2_subdev *sd, struct v4l2_subdev_fh *fh)
{
	return 0;
}

static const struct v4l2_subdev_internal_ops ar0821_subdev_internal_ops = {
	.open = ar0821_open,
};

static const struct media_entity_operations ar0821_media_ops = {
	.link_validate = v4l2_subdev_link_validate,
};

static int ar0821_read(struct i2c_client *client, u8 * val, u32 count)
{
	int ret;
	struct i2c_msg msg = {
		.addr = client->addr,
		.flags = 0,
		.buf = val,
	};

	msg.flags = I2C_M_RD;
	msg.len = count;
	ret = i2c_transfer(client->adapter, &msg, 1);
	if (ret < 0)
		goto err;

	return 0;

 err:
	dev_err(&client->dev, "Failed reading register ret = %d!\n", ret);
	return ret;
}

static int ar0821_write(struct i2c_client *client, u8 * val, u32 count)
{
	int ret;
	struct i2c_msg msg = {
		.addr = client->addr,
		.flags = 0,
		.len = count,
		.buf = val,
	};

	ret = i2c_transfer(client->adapter, &msg, 1);
	if (ret < 0) {
		dev_err(&client->dev, "Failed writing register ret = %d!\n",
			ret);
		return ret;
	}

	return 0;
}

static int mcu_bload_ascii2hex(unsigned char ascii)
{
	if (ascii <= '9') {
		return (ascii - '0');
	} else if ((ascii >= 'a') && (ascii <= 'f')) {
		return (0xA + (ascii - 'a'));
	} else if ((ascii >= 'A') && (ascii <= 'F')) {
		return (0xA + (ascii - 'A'));
	}
	return -1;
}

static void toggle_gpio(unsigned int gpio, int val)
{
	if (gpio_cansleep(gpio)){
		gpio_direction_output(gpio,val);
		gpio_set_value_cansleep(gpio, val);
	} else{
		gpio_direction_output(gpio,val);
		gpio_set_value(gpio, val);
	}
}

unsigned char errorcheck(char *data, unsigned int len)
{
	unsigned int i = 0;
	unsigned char crc = 0x00;

	for (i = 0; i < len; i++) {
		crc ^= data[i];
	}

	return crc;
}

static int mcu_jump_bload(struct i2c_client *client)
{
	uint32_t payload_len = 0;
	int err = 0;

	/*lock semaphore */
	mutex_lock(&mcu_i2c_mutex);
	/* First Txn Payload length = 0 */
	payload_len = 0;

	mc_data[0] = CMD_SIGNATURE;
	mc_data[1] = CMD_ID_FW_UPDT;
	mc_data[2] = payload_len >> 8;
	mc_data[3] = payload_len & 0xFF;
	mc_data[4] = errorcheck(&mc_data[2], 2);

	err = ar0821_write(client, mc_data, TX_LEN_PKT);
	if (err !=0 ) {
		dev_err(&client->dev, " %s(%d) Error - %d \n",
				__func__, __LINE__, err);
		goto exit;
	}

	mc_data[0] = CMD_SIGNATURE;
	mc_data[1] = CMD_ID_FW_UPDT;
	err = ar0821_write(client, mc_data, 2);
	if (err != 0) {
		dev_err(&client->dev, " %s(%d) Error - %d \n",
			__func__, __LINE__, err);
		goto exit;
	} 

 exit:
	/* unlock semaphore */
	mutex_unlock(&mcu_i2c_mutex);
	return err;

}

static int mcu_stream_config(struct i2c_client *client, uint32_t format,
			     int mode, int frate_index)
{
	struct camera_common_data *s_data = to_camera_common_data(&client->dev);
	struct ar0821 *priv = (struct ar0821 *)s_data->priv;

	uint32_t payload_len = 0;

	uint16_t cmd_status = 0, index = 0xFFFF;
	uint8_t retcode = 0, cmd_id = 0;
	int loop = 0, ret = 0, err = 0, retry = 1000;

	/* lock semaphore */
	mutex_lock(&mcu_i2c_mutex);

	for (loop = 0;(&priv->streamdb[loop]) != NULL; loop++) {
		if (priv->streamdb[loop] == mode) {
			index = loop + frate_index;
			break;
		}
	}

	debug_printk(" Index = 0x%04x , format = 0x%08x, width = %hu,"
		     " height = %hu, frate num = %hu \n", index, format,
		     priv->mcu_cam_frmfmt[mode].size.width,
		     priv->mcu_cam_frmfmt[mode].size.height,
		     priv->mcu_cam_frmfmt[mode].framerates[frate_index]);

	if (index == 0xFFFF) {
		ret = -EINVAL;
		goto exit;
	}

	if(priv->prev_index == index) {
		debug_printk("Skipping Previous mode set ... \n");
		ret = 0;
		goto exit;
	}

issue_cmd:
	/* First Txn Payload length = 0 */
	payload_len = 14;

	mc_data[0] = CMD_SIGNATURE;
	mc_data[1] = CMD_ID_STREAM_CONFIG;
	mc_data[2] = payload_len >> 8;
	mc_data[3] = payload_len & 0xFF;
	mc_data[4] = errorcheck(&mc_data[2], 2);

	ar0821_write(client, mc_data, TX_LEN_PKT);

	mc_data[0] = CMD_SIGNATURE;
	mc_data[1] = CMD_ID_STREAM_CONFIG;
	mc_data[2] = index >> 8;
	mc_data[3] = index & 0xFF;

	/* Format Fourcc - currently only UYVY */
	mc_data[4] = format >> 24;
	mc_data[5] = format >> 16;
	mc_data[6] = format >> 8;
	mc_data[7] = format & 0xFF;

	/* width */
	mc_data[8] = priv->mcu_cam_frmfmt[mode].size.width >> 8;
	mc_data[9] = priv->mcu_cam_frmfmt[mode].size.width & 0xFF;

	/* height */
	mc_data[10] = priv->mcu_cam_frmfmt[mode].size.height >> 8;
	mc_data[11] = priv->mcu_cam_frmfmt[mode].size.height & 0xFF;

	/* frame rate num */
	mc_data[12] = priv->mcu_cam_frmfmt[mode].framerates[frate_index] >> 8;
	mc_data[13] = priv->mcu_cam_frmfmt[mode].framerates[frate_index] & 0xFF;

	/* frame rate denom */
	mc_data[14] = 0x00;
	mc_data[15] = 0x01;

	mc_data[16] = errorcheck(&mc_data[2], 14);

	printk("Debugging CRC data for resolution.\n");
	printk("width1, width2, frame-rate-num1, num2, crc:<%d, %d, %d, %d, %d>\n", mc_data[8], mc_data[9], mc_data[12], mc_data[13], mc_data[16]);
	
	err = ar0821_write(client, mc_data, 17);
	if (err != 0) {
		dev_err(&client->dev," %s(%d) Error - %d \n", __func__,
		       __LINE__, err);
		ret = -EIO;
		goto exit;
	}

	while (--retry > 0) {
		cmd_id = CMD_ID_STREAM_CONFIG;
		if (mcu_get_cmd_status
		    (client, &cmd_id, &cmd_status, &retcode) < 0) {
			dev_err(&client->dev,
				" %s(%d) MCU GET CMD Status Error : loop : %d \n",
				__func__, __LINE__, loop);
			ret = -EIO;
			goto exit;
		}

		if ((cmd_status == MCU_CMD_STATUS_SUCCESS) &&
		    (retcode == ERRCODE_SUCCESS)) {
			ret = 0;
			goto exit;
		}

		if(retcode == ERRCODE_AGAIN) {
			/* Issue Command Again if Set */
			retry = 1000;
			goto issue_cmd;
		}

		if ((retcode != ERRCODE_BUSY) &&
		    ((cmd_status != MCU_CMD_STATUS_PENDING))) {
			dev_err(&client->dev,
				"(%s) %d Error STATUS = 0x%04x RET = 0x%02x\n",
				__func__, __LINE__, cmd_status, retcode);
			ret = -EIO;
			goto exit;
		}

		/* Delay after retry */
		mdelay(10);
	}

	dev_err(&client->dev," %s(%d) Error - %d \n", __func__,
			__LINE__, err);
	ret = -ETIMEDOUT;

exit:
	if(!ret)
		priv->prev_index = index;

	/* unlock semaphore */
	mutex_unlock(&mcu_i2c_mutex);

	return ret;
}

static int mcu_get_ctrl(struct i2c_client *client, uint32_t arg_ctrl_id,
			uint8_t * ctrl_type, int32_t * curr_val)
{
	struct camera_common_data *s_data = to_camera_common_data(&client->dev);
	struct ar0821 *priv = (struct ar0821 *)s_data->priv;

	uint32_t payload_len = 0;
	uint8_t errcode = ERRCODE_SUCCESS, orig_crc = 0, calc_crc = 0;
	uint16_t index = 0xFFFF;
	int loop = 0, ret = 0, err = 0;

	uint32_t ctrl_id = 0;

	/* lock semaphore */
	mutex_lock(&mcu_i2c_mutex);

	ctrl_id = arg_ctrl_id;

	/* Read the Ctrl Value from Micro controller */

	for (loop = 0; loop < priv->num_ctrls; loop++) {
		if (priv->ctrldb[loop] == ctrl_id) {
			index = loop;
			break;
		}
	}

	if (index == 0xFFFF) {
		ret = -EINVAL;
		goto exit;
	}

	/* First Txn Payload length = 2 */
	payload_len = 2;

	mc_data[0] = CMD_SIGNATURE;
	mc_data[1] = CMD_ID_GET_CTRL;
	mc_data[2] = payload_len >> 8;
	mc_data[3] = payload_len & 0xFF;
	mc_data[4] = errorcheck(&mc_data[2], 2);

	ar0821_write(client, mc_data, TX_LEN_PKT);

	mc_data[0] = CMD_SIGNATURE;
	mc_data[1] = CMD_ID_GET_CTRL;
	mc_data[2] = index >> 8;
	mc_data[3] = index & 0xFF;
	mc_data[4] = errorcheck(&mc_data[2], 2);
	err = ar0821_write(client, mc_data, 5);
	if (err != 0) {
		dev_err(&client->dev," %s(%d) Error - %d \n", __func__,
		       __LINE__, err);
		ret = -EIO;
		goto exit;
	}

	err = ar0821_read(client, mc_ret_data, RX_LEN_PKT);
	if (err != 0) {
		dev_err(&client->dev," %s(%d) Error - %d \n", __func__,
		       __LINE__, err);
		ret = -EIO;
		goto exit;
	}

	/* Verify CRC */
	orig_crc = mc_ret_data[4];
	calc_crc = errorcheck(&mc_ret_data[2], 2);
	if (orig_crc != calc_crc) {
		dev_err(&client->dev," %s(%d) CRC 0x%02x != 0x%02x \n",
		       __func__, __LINE__, orig_crc, calc_crc);
		ret = -1;
		goto exit;
	}

	if (((mc_ret_data[2] << 8) | mc_ret_data[3]) == 0) {
		ret = -EIO;
		goto exit;
	}

	errcode = mc_ret_data[5];
	if (errcode != ERRCODE_SUCCESS) {
		dev_err(&client->dev," %s(%d) Errcode - 0x%02x \n",
		       __func__, __LINE__, errcode);
		ret = -EIO;
		goto exit;
	}

	payload_len =
	    ((mc_ret_data[2] << 8) | mc_ret_data[3]) + HEADER_FOOTER_SIZE;
	memset(mc_ret_data, 0x00, payload_len);
	err = ar0821_read(client, mc_ret_data, payload_len);
	if (err != 0) {
		dev_err(&client->dev," %s(%d) Error - %d \n", __func__,
		       __LINE__, err);
		ret = -EIO;
		goto exit;
	}

	/* Verify CRC */
	orig_crc = mc_ret_data[payload_len - 2];
	calc_crc =
	    errorcheck(&mc_ret_data[2], payload_len - HEADER_FOOTER_SIZE);
	if (orig_crc != calc_crc) {
		dev_err(&client->dev," %s(%d) CRC 0x%02x != 0x%02x \n",
		       __func__, __LINE__, orig_crc, calc_crc);
		ret = -EINVAL;
		goto exit;
	}

	/* Verify Errcode */
	errcode = mc_ret_data[payload_len - 1];
	if (errcode != ERRCODE_SUCCESS) {
		dev_err(&client->dev," %s(%d) Errcode - 0x%02x \n",
		       __func__, __LINE__, errcode);
		ret = -EINVAL;
		goto exit;
	}

	/* Ctrl type starts from index 6 */

	*ctrl_type = mc_ret_data[6];

	switch (*ctrl_type) {
	case CTRL_STANDARD:
		*curr_val =
		    mc_ret_data[7] << 24 | mc_ret_data[8] << 16 | mc_ret_data[9]
		    << 8 | mc_ret_data[10];
		break;

	case CTRL_EXTENDED:
		/* Not Implemented */
		break;
	}

 exit:
	/* unlock semaphore */
	mutex_unlock(&mcu_i2c_mutex);

	return ret;
}

static int mcu_set_ctrl(struct i2c_client *client, uint32_t arg_ctrl_id,
			uint8_t ctrl_type, int32_t curr_val)
{
	struct camera_common_data *s_data = to_camera_common_data(&client->dev);
	struct ar0821 *priv = (struct ar0821 *)s_data->priv;

	uint32_t payload_len = 0;

	uint16_t cmd_status = 0, index = 0xFFFF;
	uint8_t retcode = 0, cmd_id = 0;
	int loop = 0, ret = 0, err = 0;
	uint32_t ctrl_id = 0;

	/* lock semaphore */
	mutex_lock(&mcu_i2c_mutex);

	ctrl_id = arg_ctrl_id;

	/* call ISP Ctrl config command */

	for (loop = 0; loop < priv->num_ctrls; loop++) {
		if (priv->ctrldb[loop] == ctrl_id) {
			index = loop;
			break;
		}
	}

	if (index == 0xFFFF) {
		ret = -EINVAL;
		goto exit;
	}

	/* First Txn Payload length = 0 */
	payload_len = 11;

	mc_data[0] = CMD_SIGNATURE;
	mc_data[1] = CMD_ID_SET_CTRL;
	mc_data[2] = payload_len >> 8;
	mc_data[3] = payload_len & 0xFF;
	mc_data[4] = errorcheck(&mc_data[2], 2);

	ar0821_write(client, mc_data, TX_LEN_PKT);

	/* Second Txn */
	mc_data[0] = CMD_SIGNATURE;
	mc_data[1] = CMD_ID_SET_CTRL;

	/* Index */
	mc_data[2] = index >> 8;
	mc_data[3] = index & 0xFF;

	/* Control ID */
	mc_data[4] = ctrl_id >> 24;
	mc_data[5] = ctrl_id >> 16;
	mc_data[6] = ctrl_id >> 8;
	mc_data[7] = ctrl_id & 0xFF;

	/* Ctrl Type */
	mc_data[8] = ctrl_type;

	/* Ctrl Value */
	mc_data[9] = curr_val >> 24;
	mc_data[10] = curr_val >> 16;
	mc_data[11] = curr_val >> 8;
	mc_data[12] = curr_val & 0xFF;

	/* CRC */
	mc_data[13] = errorcheck(&mc_data[2], 11);

	err = ar0821_write(client, mc_data, 14);
	if (err != 0) {
		dev_err(&client->dev," %s(%d) Error - %d \n", __func__,
		       __LINE__, err);
		ret = -EIO;
		goto exit;
	}

	while (1) {
		cmd_id = CMD_ID_SET_CTRL;
		if (mcu_get_cmd_status
		    (client, &cmd_id, &cmd_status, &retcode) < 0) {
			dev_err(&client->dev," %s(%d) Error \n",
			       __func__, __LINE__);
			ret = -EINVAL;
			goto exit;
		}

		if ((cmd_status == MCU_CMD_STATUS_SUCCESS) &&
		    (retcode == ERRCODE_SUCCESS)) {
			ret = 0;
			goto exit;
		}

		if ((retcode != ERRCODE_BUSY) &&
		    ((cmd_status != MCU_CMD_STATUS_PENDING))) {
			pr_err
			    ("(%s) %d ISP Error STATUS = 0x%04x RET = 0x%02x\n",
			     __func__, __LINE__, cmd_status, retcode);
			ret = -EIO;
			goto exit;
		}
	}

 exit:
	/* unlock semaphore */
	mutex_unlock(&mcu_i2c_mutex);

	return ret;
}

static int mcu_list_fmts(struct i2c_client *client, ISP_STREAM_INFO *stream_info, int *frm_fmt_size,struct ar0821 *priv)
{
	uint32_t payload_len = 0, err = 0;
	uint8_t errcode = ERRCODE_SUCCESS, orig_crc = 0, calc_crc = 0, skip = 0;
	uint16_t index = 0, mode = 0;

	int loop = 0, num_frates = 0, ret = 0;

	/* Stream Info Variables */

	/* lock semaphore */
	mutex_lock(&mcu_i2c_mutex);

	/* List all formats from MCU and append to mcu_ar0821_frmfmt array */

	for (index = 0;; index++) {
		/* First Txn Payload length = 0 */
		payload_len = 2;

		mc_data[0] = CMD_SIGNATURE;
		mc_data[1] = CMD_ID_GET_STREAM_INFO;
		mc_data[2] = payload_len >> 8;
		mc_data[3] = payload_len & 0xFF;
		mc_data[4] = errorcheck(&mc_data[2], 2);

		ar0821_write(client, mc_data, TX_LEN_PKT);

		mc_data[0] = CMD_SIGNATURE;
		mc_data[1] = CMD_ID_GET_STREAM_INFO;
		mc_data[2] = index >> 8;
		mc_data[3] = index & 0xFF;
		mc_data[4] = errorcheck(&mc_data[2], 2);
		err = ar0821_write(client, mc_data, 5);
		if (err != 0) {
			dev_err(&client->dev," %s(%d) Error - %d \n",
			       __func__, __LINE__, err);
			ret = -EIO;
			goto exit;
		}

		err = ar0821_read(client, mc_ret_data, RX_LEN_PKT);
		if (err != 0) {
			dev_err(&client->dev," %s(%d) Error - %d \n",
			       __func__, __LINE__, err);
			ret = -EIO;
			goto exit;
		}

		/* Verify CRC */
		orig_crc = mc_ret_data[4];
		calc_crc = errorcheck(&mc_ret_data[2], 2);
		if (orig_crc != calc_crc) {
			pr_err
			    (" %s(%d) CRC 0x%02x != 0x%02x \n",
			     __func__, __LINE__, orig_crc, calc_crc);
			ret = -EINVAL;
			goto exit;
		}

		if (((mc_ret_data[2] << 8) | mc_ret_data[3]) == 0) {
			if(stream_info == NULL) {
				*frm_fmt_size = index;
			} else {
				*frm_fmt_size = mode;
			}
			break;
		}

		payload_len =
		    ((mc_ret_data[2] << 8) | mc_ret_data[3]) +
		    HEADER_FOOTER_SIZE;
		errcode = mc_ret_data[5];
		if (errcode != ERRCODE_SUCCESS) {
			pr_err
			    (" %s(%d) Errcode - 0x%02x \n",
			     __func__, __LINE__, errcode);
			ret = -EIO;
			goto exit;
		}

		memset(mc_ret_data, 0x00, payload_len);
		err = ar0821_read(client, mc_ret_data, payload_len);
		if (err != 0) {
			dev_err(&client->dev," %s(%d) Error - %d \n",
			       __func__, __LINE__, err);
			ret = -1;
			goto exit;
		}

		/* Verify CRC */
		orig_crc = mc_ret_data[payload_len - 2];
		calc_crc =
		    errorcheck(&mc_ret_data[2],
				 payload_len - HEADER_FOOTER_SIZE);
		if (orig_crc != calc_crc) {
			pr_err
			    (" %s(%d) CRC 0x%02x != 0x%02x \n",
			     __func__, __LINE__, orig_crc, calc_crc);
			ret = -EINVAL;
			goto exit;
		}

		/* Verify Errcode */
		errcode = mc_ret_data[payload_len - 1];
		if (errcode != ERRCODE_SUCCESS) {
			pr_err
			    (" %s(%d) Errcode - 0x%02x \n",
			     __func__, __LINE__, errcode);
			ret = -EIO;
			goto exit;
		}
		if(stream_info != NULL) {
		/* check if any other format than UYVY is queried - do not append in array */
		stream_info->fmt_fourcc =
		    mc_ret_data[2] << 24 | mc_ret_data[3] << 16 | mc_ret_data[4]
		    << 8 | mc_ret_data[5];
		stream_info->width = mc_ret_data[6] << 8 | mc_ret_data[7];
		stream_info->height = mc_ret_data[8] << 8 | mc_ret_data[9];
		stream_info->frame_rate_type = mc_ret_data[10];

		switch (stream_info->frame_rate_type) {
		case FRAME_RATE_DISCRETE:
			stream_info->frame_rate.disc.frame_rate_num =
			    mc_ret_data[11] << 8 | mc_ret_data[12];

			stream_info->frame_rate.disc.frame_rate_denom =
			    mc_ret_data[13] << 8 | mc_ret_data[14];

			break;

		case FRAME_RATE_CONTINOUS:
			debug_printk
			    (" The Stream format at index 0x%04x has FRAME_RATE_CONTINOUS,"
			     "which is unsupported !! \n", index);

#if 0
			stream_info.frame_rate.cont.frame_rate_min_num =
			    mc_ret_data[11] << 8 | mc_ret_data[12];
			stream_info.frame_rate.cont.frame_rate_min_denom =
			    mc_ret_data[13] << 8 | mc_ret_data[14];

			stream_info.frame_rate.cont.frame_rate_max_num =
			    mc_ret_data[15] << 8 | mc_ret_data[16];
			stream_info.frame_rate.cont.frame_rate_max_denom =
			    mc_ret_data[17] << 8 | mc_ret_data[18];

			stream_info.frame_rate.cont.frame_rate_step_num =
			    mc_ret_data[19] << 8 | mc_ret_data[20];
			stream_info.frame_rate.cont.frame_rate_step_denom =
			    mc_ret_data[21] << 8 | mc_ret_data[22];
			break;
#endif
			continue;
		}

		switch (stream_info->fmt_fourcc) {
		case V4L2_PIX_FMT_UYVY:
			/* ar0821_codes is already populated with V4L2_MBUS_FMT_UYVY8_1X16 */
			/* check if width and height are already in array - update frame rate only */
			for (loop = 0; loop < (mode); loop++) {
				if ((priv->mcu_cam_frmfmt[loop].size.width ==
				     stream_info->width)
				    && (priv->mcu_cam_frmfmt[loop].size.height ==
					stream_info->height)) {

					num_frates =
					    priv->mcu_cam_frmfmt
					    [loop].num_framerates;
					*((int *)(priv->mcu_cam_frmfmt[loop].framerates) + num_frates)
					    = (int)(stream_info->frame_rate.
						    disc.frame_rate_num /
						    stream_info->frame_rate.
						    disc.frame_rate_denom);

					priv->mcu_cam_frmfmt
					    [loop].num_framerates++;

					priv->streamdb[index] = loop;
					skip = 1;
					break;
				}
			}

			if (skip) {
				skip = 0;
				continue;
			}

			/* Add Width, Height, Frame Rate array, Mode into mcu_ar0821_frmfmt array */
			priv->mcu_cam_frmfmt[mode].size.width = stream_info->width;
			priv->mcu_cam_frmfmt[mode].size.height =
			    stream_info->height;
			num_frates = priv->mcu_cam_frmfmt[mode].num_framerates;

			*((int *)(priv->mcu_cam_frmfmt[mode].framerates) + num_frates) =
			    (int)(stream_info->frame_rate.disc.frame_rate_num /
				  stream_info->frame_rate.disc.frame_rate_denom);

			priv->mcu_cam_frmfmt[mode].num_framerates++;

			priv->mcu_cam_frmfmt[mode].mode = mode;
			priv->streamdb[index] = mode;
			mode++;
			break;

		default:
			debug_printk
			    (" The Stream format at index 0x%04x has format 0x%08x ,"
			     "which is unsupported !! \n", index,
			     stream_info->fmt_fourcc);
		}
		}
	}

 exit:
	/* unlock semaphore */
	mutex_unlock(&mcu_i2c_mutex);

	return ret;
}

static int mcu_get_ctrl_ui(struct i2c_client *client,
			   ISP_CTRL_INFO * mcu_ui_info, int index)
{
	uint32_t payload_len = 0;
	uint8_t errcode = ERRCODE_SUCCESS, orig_crc = 0, calc_crc = 0;
	int ret = 0, i = 0, err = 0;

	/* lock semaphore */
	mutex_lock(&mcu_i2c_mutex);

	/* First Txn Payload length = 0 */
	payload_len = 2;

	mc_data[0] = CMD_SIGNATURE;
	mc_data[1] = CMD_ID_GET_CTRL_UI_INFO;
	mc_data[2] = payload_len >> 8;
	mc_data[3] = payload_len & 0xFF;
	mc_data[4] = errorcheck(&mc_data[2], 2);

	ar0821_write(client, mc_data, TX_LEN_PKT);

	mc_data[0] = CMD_SIGNATURE;
	mc_data[1] = CMD_ID_GET_CTRL_UI_INFO;
	mc_data[2] = index >> 8;
	mc_data[3] = index & 0xFF;
	mc_data[4] = errorcheck(&mc_data[2], 2);
	err = ar0821_write(client, mc_data, 5);
	if (err != 0) {
		dev_err(&client->dev," %s(%d) Error - %d \n", __func__,
		       __LINE__, err);
		ret = -EIO;
		goto exit;
	}

	err = ar0821_read(client, mc_ret_data, RX_LEN_PKT);
	if (err != 0) {
		dev_err(&client->dev," %s(%d) Error - %d \n", __func__,
		       __LINE__, err);
		ret = -EIO;
		goto exit;
	}

	/* Verify CRC */
	orig_crc = mc_ret_data[4];
	calc_crc = errorcheck(&mc_ret_data[2], 2);
	if (orig_crc != calc_crc) {
		dev_err(&client->dev," %s(%d) CRC 0x%02x != 0x%02x \n",
		       __func__, __LINE__, orig_crc, calc_crc);
		ret = -EINVAL;
		goto exit;
	}

	payload_len =
	    ((mc_ret_data[2] << 8) | mc_ret_data[3]) + HEADER_FOOTER_SIZE;
	errcode = mc_ret_data[5];
	if (errcode != ERRCODE_SUCCESS) {
		dev_err(&client->dev," %s(%d) Errcode - 0x%02x \n",
		       __func__, __LINE__, errcode);
		ret = -EINVAL;
		goto exit;
	}

	memset(mc_ret_data, 0x00, payload_len);
	err = ar0821_read(client, mc_ret_data, payload_len);
	if (err != 0) {
		dev_err(&client->dev," %s(%d) Error - %d \n", __func__,
		       __LINE__, err);
		ret = -EIO;
		goto exit;
	}

	/* Verify CRC */
	orig_crc = mc_ret_data[payload_len - 2];
	calc_crc =
	    errorcheck(&mc_ret_data[2], payload_len - HEADER_FOOTER_SIZE);
	if (orig_crc != calc_crc) {
		dev_err(&client->dev," %s(%d) CRC 0x%02x != 0x%02x \n",
		       __func__, __LINE__, orig_crc, calc_crc);
		ret = -EINVAL;
		goto exit;
	}

	/* Verify Errcode */
	errcode = mc_ret_data[payload_len - 1];
	if (errcode != ERRCODE_SUCCESS) {
		dev_err(&client->dev," %s(%d) Errcode - 0x%02x \n",
		       __func__, __LINE__, errcode);
		ret = -EIO;
		goto exit;
	}

	strncpy((char *)mcu_ui_info->ctrl_ui_data.ctrl_ui_info.ctrl_name, &mc_ret_data[2],MAX_CTRL_UI_STRING_LEN);

	mcu_ui_info->ctrl_ui_data.ctrl_ui_info.ctrl_ui_type = mc_ret_data[34];
	mcu_ui_info->ctrl_ui_data.ctrl_ui_info.ctrl_ui_flags = mc_ret_data[35] << 8 |
	    mc_ret_data[36];

	if (mcu_ui_info->ctrl_ui_data.ctrl_ui_info.ctrl_ui_type == V4L2_CTRL_TYPE_MENU) {
		mcu_ui_info->ctrl_ui_data.ctrl_menu_info.num_menu_elem = mc_ret_data[37];

		mcu_ui_info->ctrl_ui_data.ctrl_menu_info.menu =
		    devm_kzalloc(&client->dev,((mcu_ui_info->ctrl_ui_data.ctrl_menu_info.num_menu_elem +1) * sizeof(char *)), GFP_KERNEL);
		for (i = 0; i < mcu_ui_info->ctrl_ui_data.ctrl_menu_info.num_menu_elem; i++) {
			mcu_ui_info->ctrl_ui_data.ctrl_menu_info.menu[i] =
			    devm_kzalloc(&client->dev,MAX_CTRL_UI_STRING_LEN, GFP_KERNEL);
			strncpy((char *)mcu_ui_info->ctrl_ui_data.ctrl_menu_info.menu[i],
				&mc_ret_data[38 +(i *MAX_CTRL_UI_STRING_LEN)], MAX_CTRL_UI_STRING_LEN);

			debug_printk(" Menu Element %d : %s \n",
				     i, mcu_ui_info->ctrl_ui_data.ctrl_menu_info.menu[i]);
		}

		mcu_ui_info->ctrl_ui_data.ctrl_menu_info.menu[i] = NULL;
	}

 exit:
	/* unlock semaphore */
	mutex_unlock(&mcu_i2c_mutex);

	return ret;

}

static int mcu_mipi_configuration(struct i2c_client *client, struct ar0821 *priv, u8 cmd_id)
{
	int ret = 0, err, retry = 1000;
	uint16_t payload_data;
        unsigned char mc_data[10];
        uint32_t payload_len = 0;
        uint16_t cmd_status = 0; 
        uint8_t retcode = 0;

        /* lock semaphore */
        mutex_lock(&mcu_i2c_mutex);

	payload_len = 2; 
		
	mc_data[0] = CMD_SIGNATURE;
        mc_data[1] = cmd_id;
        mc_data[2] = payload_len >> 8;
        mc_data[3] = payload_len & 0xFF;
        mc_data[4] = errorcheck(&mc_data[2], 2);

        ar0821_write(client, mc_data, TX_LEN_PKT);

        /* Second Txn */
        mc_data[0] = CMD_SIGNATURE;
        mc_data[1] = cmd_id;

		switch(cmd_id) {
			case CMD_ID_LANE_CONFIG:
				/*Lane configuration */
				payload_data = priv->mipi_lane_config == 4 ? NUM_LANES_4 : NUM_LANES_2; 
				mc_data[2] = payload_data >> 8;
				mc_data[3] = payload_data & 0xFF;
				break;
			case CMD_ID_MIPI_CLK_CONFIG:
				/* MIPI CLK Configuration */
				payload_data = priv->mipi_clk_config; 
				mc_data[2] = payload_data >> 8;
				mc_data[3] = payload_data & 0xFF;
				break;
			default:
				dev_err(&client->dev, "MCU MIPI CONF Error\n");
				err = -1;
				goto exit;
		}
		
       	/* CRC */
       	mc_data[4] = errorcheck(&mc_data[2], payload_len);
        err = ar0821_write(client, mc_data, payload_len+3);
	
        if (err != 0) {
                dev_err(&client->dev," %s(%d) MCU Set Ctrl Error - %d \n", __func__,
                       __LINE__, err);
                ret = -1;
                goto exit;
        }

	while (--retry > 0) {
		msleep(20);
                if (mcu_get_cmd_status(client, &cmd_id, &cmd_status, &retcode) <
                    0) {
                        dev_err(&client->dev," %s(%d) MCU Get CMD Status Error \n", __func__,
                               __LINE__);
                        ret = -1;
                        goto exit;
                }

                if ((cmd_status == MCU_CMD_STATUS_ISP_UNINIT) &&
                    (retcode == ERRCODE_SUCCESS)) {
                        ret = 0;
                        goto exit;
                }

                if ((retcode != ERRCODE_BUSY) &&
                    ((cmd_status != MCU_CMD_STATUS_ISP_UNINIT))) {
                       dev_err(&client->dev, 
                           "(%s) %d MCU Get CMD Error STATUS = 0x%04x RET = 0x%02x\n",
                             __func__, __LINE__, cmd_status, retcode);
                        ret = -1;
                        goto exit;
                }
        }
	err = -ETIMEDOUT;

 exit:
        /* unlock semaphore */
        mutex_unlock(&mcu_i2c_mutex);

        return ret;
}

static int mcu_list_ctrls(struct i2c_client *client,
			  ISP_CTRL_INFO * mcu_cam_ctrl, struct ar0821 *priv)
{
	uint32_t payload_len = 0;
	uint8_t errcode = ERRCODE_SUCCESS, orig_crc = 0, calc_crc = 0;
	uint16_t index = 0;
	int ret = 0, err = 0,retry=30;

	/* lock semaphore */
	mutex_lock(&mcu_i2c_mutex);

	/* Array of Ctrl Info */
	while (retry-- > 0) {
		/* First Txn Payload length = 0 */
		payload_len = 2;

		mc_data[0] = CMD_SIGNATURE;
		mc_data[1] = CMD_ID_GET_CTRL_INFO;
		mc_data[2] = payload_len >> 8;
		mc_data[3] = payload_len & 0xFF;
		mc_data[4] = errorcheck(&mc_data[2], 2);

		err = ar0821_write(client, mc_data, TX_LEN_PKT);
		if (err != 0) {
                        dev_err(&client->dev," %s(%d) Error - %d \n",
                               __func__, __LINE__, err);
                        continue;
                }
		mc_data[0] = CMD_SIGNATURE;
		mc_data[1] = CMD_ID_GET_CTRL_INFO;
		mc_data[2] = index >> 8;
		mc_data[3] = index & 0xFF;
		mc_data[4] = errorcheck(&mc_data[2], 2);
		err = ar0821_write(client, mc_data, 5);
		if (err != 0) {
			dev_err(&client->dev," %s(%d) Error - %d \n",
			       __func__, __LINE__, err);
			continue;
		}

		err = ar0821_read(client, mc_ret_data, RX_LEN_PKT);
		if (err != 0) {
			dev_err(&client->dev," %s(%d) Error - %d \n",
			       __func__, __LINE__, err);
			continue;
		}

		/* Verify CRC */
		orig_crc = mc_ret_data[4];
		calc_crc = errorcheck(&mc_ret_data[2], 2);
		if (orig_crc != calc_crc) {
			dev_err(&client->dev,
			    " %s(%d) CRC 0x%02x != 0x%02x \n",
			     __func__, __LINE__, orig_crc, calc_crc);
			continue;
		}

		if (((mc_ret_data[2] << 8) | mc_ret_data[3]) == 0) {
			priv->num_ctrls = index;
			break;
		}

		payload_len =
		    ((mc_ret_data[2] << 8) | mc_ret_data[3]) +
		    HEADER_FOOTER_SIZE;
		errcode = mc_ret_data[5];
		if (errcode != ERRCODE_SUCCESS) {
			dev_err(&client->dev,
			    " %s(%d) Errcode - 0x%02x \n",
			     __func__, __LINE__, errcode);
			continue;
		}

		memset(mc_ret_data, 0x00, payload_len);
		err = ar0821_read(client, mc_ret_data, payload_len);
		if (err != 0) {
			dev_err(&client->dev," %s(%d) Error - %d \n",
			       __func__, __LINE__, err);
			continue;
		}

		/* Verify CRC */
		orig_crc = mc_ret_data[payload_len - 2];
		calc_crc =
		    errorcheck(&mc_ret_data[2],
				 payload_len - HEADER_FOOTER_SIZE);
		if (orig_crc != calc_crc) {
			dev_err(&client->dev,
			    " %s(%d) CRC 0x%02x != 0x%02x \n",
			     __func__, __LINE__, orig_crc, calc_crc);
			continue;
		}

		/* Verify Errcode */
		errcode = mc_ret_data[payload_len - 1];
		if (errcode != ERRCODE_SUCCESS) {
			dev_err(&client->dev,
			    " %s(%d) Errcode - 0x%02x \n",
			     __func__, __LINE__, errcode);
			continue;
		}

		if(mcu_cam_ctrl != NULL) {

			/* append ctrl info in array */
			mcu_cam_ctrl[index].ctrl_id =
				mc_ret_data[2] << 24 | mc_ret_data[3] << 16 | mc_ret_data[4]
				<< 8 | mc_ret_data[5];
			mcu_cam_ctrl[index].ctrl_type = mc_ret_data[6];

			switch (mcu_cam_ctrl[index].ctrl_type) {
				case CTRL_STANDARD:
					mcu_cam_ctrl[index].ctrl_data.std.ctrl_min =
						mc_ret_data[7] << 24 | mc_ret_data[8] << 16
						| mc_ret_data[9] << 8 | mc_ret_data[10];

					mcu_cam_ctrl[index].ctrl_data.std.ctrl_max =
						mc_ret_data[11] << 24 | mc_ret_data[12] <<
						16 | mc_ret_data[13]
						<< 8 | mc_ret_data[14];

					mcu_cam_ctrl[index].ctrl_data.std.ctrl_def =
						mc_ret_data[15] << 24 | mc_ret_data[16] <<
						16 | mc_ret_data[17]
						<< 8 | mc_ret_data[18];

					mcu_cam_ctrl[index].ctrl_data.std.ctrl_step =
						mc_ret_data[19] << 24 | mc_ret_data[20] <<
						16 | mc_ret_data[21]
						<< 8 | mc_ret_data[22];
					break;

				case CTRL_EXTENDED:
					/* Not Implemented */
					break;
			}

			priv->ctrldb[index] = mcu_cam_ctrl[index].ctrl_id;
		}
		index++;
		if(retry == 0) {
			ret = -EIO;
                        goto exit;
		}
	}

 exit:
	/* unlock semaphore */
	mutex_unlock(&mcu_i2c_mutex);

	return ret;

}

static int mcu_get_fw_version(struct i2c_client *client, unsigned char *fw_version, unsigned char *txt_fw_version)
{
	uint32_t payload_len = 0;
	uint8_t errcode = ERRCODE_SUCCESS, orig_crc = 0, calc_crc = 0;
	int ret = 0, err = 0, loop, i=0, retry = 5;
	unsigned long txt_fw_pos = ARRAY_SIZE(g_mcu_fw_buf)-VERSION_FILE_OFFSET;

	/* lock semaphore */
	mutex_lock(&mcu_i2c_mutex);

	/* Get Text Firmware version*/
	for(loop = txt_fw_pos; loop < (txt_fw_pos+64); loop=loop+2) {
		*(txt_fw_version+i) = (mcu_bload_ascii2hex(g_mcu_fw_buf[loop]) << 4 |
				mcu_bload_ascii2hex(g_mcu_fw_buf[loop+1]));
		i++;
	}

	while (retry > 0) {
	  	retry -= 1;
		/* Query firmware version from MCU */
		payload_len = 0;

		mc_data[0] = CMD_SIGNATURE;
		mc_data[1] = CMD_ID_VERSION;
		mc_data[2] = payload_len >> 8;
		mc_data[3] = payload_len & 0xFF;
		mc_data[4] = errorcheck(&mc_data[2], 2);

		err = ar0821_write(client, mc_data, TX_LEN_PKT);

		mc_data[0] = CMD_SIGNATURE;
		mc_data[1] = CMD_ID_VERSION;
		err = ar0821_write(client, mc_data, 2);
		if (err != 0) {
			dev_err(&client->dev," %s(%d) MCU CMD ID Write PKT fw Version Error - %d \n", __func__,
					__LINE__, err);
			ret = -EIO;
			continue;
		}

		err = ar0821_read(client, mc_ret_data, RX_LEN_PKT);
		if (err != 0) {
			dev_err(&client->dev," %s(%d) MCU CMD ID Read PKT fw Version Error - %d \n", __func__,
					__LINE__, err);
			ret = -EIO;
			continue;
		}

		/* Verify CRC */
		orig_crc = mc_ret_data[4];
		calc_crc = errorcheck(&mc_ret_data[2], 2);
		if (orig_crc != calc_crc) {
			dev_err(&client->dev," %s(%d) MCU CMD ID fw Version Error CRC 0x%02x != 0x%02x \n",
					__func__, __LINE__, orig_crc, calc_crc);
			ret = -EINVAL;
			continue;
		}

		errcode = mc_ret_data[5];
		if (errcode != ERRCODE_SUCCESS) {
			dev_err(&client->dev," %s(%d) MCU CMD ID fw Errcode - 0x%02x \n", __func__,
					__LINE__, errcode);
			ret = -EIO;
			continue;
		}

		/* Read the actual version from MCU*/
		payload_len =
			((mc_ret_data[2] << 8) | mc_ret_data[3]) + HEADER_FOOTER_SIZE;
		memset(mc_ret_data, 0x00, payload_len);
		err = ar0821_read(client, mc_ret_data, payload_len);
		if (err != 0) {
			dev_err(&client->dev," %s(%d) MCU fw CMD ID Read Version Error - %d \n", __func__,
					__LINE__, ret);
			ret = -EIO;
			continue;
		}

		/* Verify CRC */
		orig_crc = mc_ret_data[payload_len - 2];
		calc_crc = errorcheck(&mc_ret_data[2], 32);
		if (orig_crc != calc_crc) {
			dev_err(&client->dev," %s(%d) MCU fw  CMD ID Version CRC ERROR 0x%02x != 0x%02x \n",
					__func__, __LINE__, orig_crc, calc_crc);
			ret = -EINVAL;
			continue;
		}

		/* Verify Errcode */
		errcode = mc_ret_data[payload_len - 1];
		if (errcode != ERRCODE_SUCCESS) {
			dev_err(&client->dev," %s(%d) MCU fw CMD ID Read Payload Error - 0x%02x \n", __func__,
					__LINE__, errcode);
			ret = -EIO;
			continue;
		}
		if(ret == ERRCODE_SUCCESS) 
			break;
	}
	printk("retry, ret now is <%d, %d>.\n", retry, ret);
	if (retry == 0 && ret != ERRCODE_SUCCESS) {
		goto exit;
	}

	for (loop = 0 ; loop < VERSION_SIZE ; loop++ )
		*(fw_version+loop) = mc_ret_data[2+loop];

	/* Check for forced/always update field in the text firmware version*/
	if(txt_fw_version[17] == '1') {
		dev_err(&client->dev, "Forced Update Enabled - Firmware Version - (%.32s) \n",
			fw_version);
		ret = 2;
		goto exit;
	}			

	for(i = 0; i < VERSION_SIZE; i++) {
		if(txt_fw_version[i] != fw_version[i]) {
			dev_dbg(&client->dev, "Previous Firmware Version - (%.32s)\n", fw_version);
			dev_dbg(&client->dev, "Current Firmware Version - (%.32s)\n", txt_fw_version);
			ret = 1;
			goto exit;
		}
	}

	ret = ERRCODE_SUCCESS;
exit:
	/* unlock semaphore */
	mutex_unlock(&mcu_i2c_mutex);

	return ret;
}

static int mcu_get_sensor_id(struct i2c_client *client, uint16_t * sensor_id)
{
	uint32_t payload_len = 0;
	uint8_t errcode = ERRCODE_SUCCESS, orig_crc = 0, calc_crc = 0;

	int ret = 0, err = 0;

	/* lock semaphore */
	mutex_lock(&mcu_i2c_mutex);

	/* Read the version info. from Micro controller */

	/* First Txn Payload length = 0 */
	payload_len = 0;

	mc_data[0] = CMD_SIGNATURE;
	mc_data[1] = CMD_ID_GET_SENSOR_ID;
	mc_data[2] = payload_len >> 8;
	mc_data[3] = payload_len & 0xFF;
	mc_data[4] = errorcheck(&mc_data[2], 2);

	ar0821_write(client, mc_data, TX_LEN_PKT);

	mc_data[0] = CMD_SIGNATURE;
	mc_data[1] = CMD_ID_GET_SENSOR_ID;
	err = ar0821_write(client, mc_data, 2);
	if (err != 0) {
		dev_err(&client->dev," %s(%d) Error - %d \n", __func__,
		       __LINE__, err);
		ret = -EIO;
		goto exit;
	}

	err = ar0821_read(client, mc_ret_data, RX_LEN_PKT);
	if (err != 0) {
		dev_err(&client->dev," %s(%d) Error - %d \n", __func__,
		       __LINE__, err);
		ret = -EIO;
		goto exit;
	}

	/* Verify CRC */
	orig_crc = mc_ret_data[4];
	calc_crc = errorcheck(&mc_ret_data[2], 2);
	if (orig_crc != calc_crc) {
		dev_err(&client->dev," %s(%d) CRC 0x%02x != 0x%02x \n",
		       __func__, __LINE__, orig_crc, calc_crc);
		ret = -EINVAL;
		goto exit;
	}

	errcode = mc_ret_data[5];
	if (errcode != ERRCODE_SUCCESS) {
		dev_err(&client->dev," %s(%d) Errcode - 0x%02x \n",
		       __func__, __LINE__, errcode);
		ret = -EIO;
		goto exit;
	}

	payload_len =
	    ((mc_ret_data[2] << 8) | mc_ret_data[3]) + HEADER_FOOTER_SIZE;

	memset(mc_ret_data, 0x00, payload_len);
	err = ar0821_read(client, mc_ret_data, payload_len);
	if (err != 0) {
		dev_err(&client->dev," %s(%d) Error - %d \n", __func__,
		       __LINE__, err);
		ret = -EIO;
		goto exit;
	}

	/* Verify CRC */
	orig_crc = mc_ret_data[payload_len - 2];
	calc_crc = errorcheck(&mc_ret_data[2], 2);
	if (orig_crc != calc_crc) {
		dev_err(&client->dev," %s(%d) CRC 0x%02x != 0x%02x \n",
		       __func__, __LINE__, orig_crc, calc_crc);
		ret = -EINVAL;
		goto exit;
	}

	/* Verify Errcode */
	errcode = mc_ret_data[payload_len - 1];
	if (errcode != ERRCODE_SUCCESS) {
		dev_err(&client->dev," %s(%d) Errcode - 0x%02x \n",
		       __func__, __LINE__, errcode);
		ret = -EIO;
		goto exit;
	}

	*sensor_id = mc_ret_data[2] << 8 | mc_ret_data[3];

 exit:
	/* unlock semaphore */
	mutex_unlock(&mcu_i2c_mutex);

	return ret;
}

static int mcu_get_cmd_status(struct i2c_client *client,
			      uint8_t * cmd_id, uint16_t * cmd_status,
			      uint8_t * ret_code)
{
	uint32_t payload_len = 0;
	uint8_t orig_crc = 0, calc_crc = 0;
	int err = 0;

	/* No Semaphore in Get command Status */

	/* First Txn Payload length = 0 */
	payload_len = 1;

	mc_data[0] = CMD_SIGNATURE;
	mc_data[1] = CMD_ID_GET_STATUS;
	mc_data[2] = payload_len >> 8;
	mc_data[3] = payload_len & 0xFF;
	mc_data[4] = errorcheck(&mc_data[2], 2);

	ar0821_write(client, mc_data, TX_LEN_PKT);

	mc_data[0] = CMD_SIGNATURE;
	mc_data[1] = CMD_ID_GET_STATUS;
	mc_data[2] = *cmd_id;
	err = ar0821_write(client, mc_data, 3);
	if (err != 0) {
		dev_err(&client->dev," %s(%d) Error - %d \n", __func__,
		       __LINE__, err);
		return -EIO;
	}

	payload_len = CMD_STATUS_MSG_LEN;
	memset(mc_ret_data, 0x00, payload_len);
	err = ar0821_read(client, mc_ret_data, payload_len);
	if (err != 0) {
		dev_err(&client->dev," %s(%d) Error - %d \n", __func__,
		       __LINE__, err);
		return -EIO;
	}

	/* Verify CRC */
	orig_crc = mc_ret_data[payload_len - 2];
	calc_crc = errorcheck(&mc_ret_data[2], 3);
	if (orig_crc != calc_crc) {
		dev_err(&client->dev," %s(%d) CRC 0x%02x != 0x%02x \n",
		       __func__, __LINE__, orig_crc, calc_crc);
		return -EINVAL;
	}

	*cmd_id = mc_ret_data[2];
	*cmd_status = mc_ret_data[3] << 8 | mc_ret_data[4];
	*ret_code = mc_ret_data[payload_len - 1];

	return 0;
}

static int mcu_cam_stream_off(struct i2c_client *client)
{
        unsigned char mc_data[100];
        uint32_t payload_len = 0;

        uint16_t cmd_status = 0;
        uint8_t retcode = 0, cmd_id = 0;
	int retry = 1000, err = 0;
        /* call ISP init command */

	/*lock semaphore*/
        mutex_lock(&mcu_i2c_mutex);

        /* First Txn Payload length = 0 */
        payload_len = 0;

        mc_data[0] = CMD_SIGNATURE;
        mc_data[1] = CMD_ID_STREAM_OFF;
        mc_data[2] = payload_len >> 8;
        mc_data[3] = payload_len & 0xFF;
        mc_data[4] = errorcheck(&mc_data[2], 2);

        ar0821_write(client, mc_data, TX_LEN_PKT);

        mc_data[0] = CMD_SIGNATURE;
        mc_data[1] = CMD_ID_STREAM_OFF;
        err = ar0821_write(client, mc_data, 2);
        if (err != 0) {
                dev_err(&client->dev," %s(%d) MCU Stream OFF Write Error - %d \n", __func__,
                       __LINE__, err);
                goto exit;
        }

        while (--retry > 0) {
                /* Some Sleep for init to process */
                yield();

                cmd_id = CMD_ID_STREAM_OFF;
                if (mcu_get_cmd_status(client, &cmd_id, &cmd_status, &retcode) <
                    0) {
                       dev_err(&client->dev," %s(%d) MCU Get CMD Stream Off Error \n", __func__,
                               __LINE__);
		       err = -1;
                        goto exit;
                }

                if ((cmd_status == MCU_CMD_STATUS_SUCCESS) &&
                    (retcode == ERRCODE_SUCCESS)) {
                        debug_printk(" %s %d MCU Get CMD Stream off Success !! \n", __func__, __LINE__ );
			err = 0;
                        goto exit;
                }

                if ((retcode != ERRCODE_BUSY) &&
                    ((cmd_status != MCU_CMD_STATUS_PENDING))) {
                       dev_err(&client->dev, 
                            "(%s) %d MCU Get CMD Stream off Error STATUS = 0x%04x RET = 0x%02x\n",
                             __func__, __LINE__, cmd_status, retcode);
		       err = -1;
                        goto exit;
                }
		mdelay(1);
        }
exit:
	/* unlock semaphore */
	mutex_unlock(&mcu_i2c_mutex);
	return err;
}

static int mcu_cam_init(struct i2c_client *client)
{
	uint32_t payload_len = 0;

	uint16_t cmd_status = 0;
	uint8_t retcode = 0, cmd_id = 0;
	int retry = 1000, err = 0;
	
	pr_info("mcu_cam_init\n");
	/* check current status - if initialized, no need for Init */
	cmd_id = CMD_ID_INIT_CAM;
	if (mcu_get_cmd_status(client, &cmd_id, &cmd_status, &retcode) < 0) {
		dev_err(&client->dev," %s(%d) Error \n", __func__, __LINE__);
		return -EIO;
	}

	if ((cmd_status == MCU_CMD_STATUS_SUCCESS) &&
	    (retcode == ERRCODE_SUCCESS)) {
		dev_err(&client->dev," Already Initialized !! \n");
		return 0;
	}

	/* call ISP init command */

	/* First Txn Payload length = 0 */
	payload_len = 0;

	mc_data[0] = CMD_SIGNATURE;
	mc_data[1] = CMD_ID_INIT_CAM;
	mc_data[2] = payload_len >> 8;
	mc_data[3] = payload_len & 0xFF;
	mc_data[4] = errorcheck(&mc_data[2], 2);

	ar0821_write(client, mc_data, TX_LEN_PKT);

	mc_data[0] = CMD_SIGNATURE;
	mc_data[1] = CMD_ID_INIT_CAM;
	err = ar0821_write(client, mc_data, 2);
	if (err != 0) {
		dev_err(&client->dev," %s(%d) Error - %d \n", __func__,
		       __LINE__, err);
		return -EIO;
	}

	while (--retry > 0) {
		/* Some Sleep for init to process */
		mdelay(500);

		cmd_id = CMD_ID_INIT_CAM;
		if (mcu_get_cmd_status
		    (client, &cmd_id, &cmd_status, &retcode) < 0) {
			dev_err(&client->dev," %s(%d) Error \n",
			       __func__, __LINE__);
			return -EIO;
		}

		if ((cmd_status == MCU_CMD_STATUS_SUCCESS) &&
		    ((retcode == ERRCODE_SUCCESS) || (retcode == ERRCODE_ALREADY))) {
			dev_err(&client->dev,"ISP Already Initialized !! \n");
			return 0;
		}

		if ((retcode != ERRCODE_BUSY) &&
		    ((cmd_status != MCU_CMD_STATUS_PENDING))) {
			dev_err(&client->dev,
			    "(%s) %d Init Error STATUS = 0x%04x RET = 0x%02x\n",
			     __func__, __LINE__, cmd_status, retcode);
			return -EIO;
		}
	}
	dev_err(&client->dev,"ETIMEDOUT Error\n");
	return -ETIMEDOUT;
}

static unsigned short int mcu_bload_calc_crc16(unsigned char *buf, int len)
{
	unsigned short int crc = 0;
	int i = 0;

	if (!buf || !(buf + len))
		return 0;

	for (i = 0; i < len; i++) {
		crc ^= buf[i];
	}

	return crc;
}

static unsigned char mcu_bload_inv_checksum(unsigned char *buf, int len)
{
	unsigned int checksum = 0x00;
	int i = 0;

	if (!buf || !(buf + len))
		return 0;

	for (i = 0; i < len; i++) {
		checksum = (checksum + buf[i]);
	}

	checksum &= (0xFF);
	return (~(checksum) + 1);
}

static int mcu_bload_get_version(struct i2c_client *client)
{
	int ret = 0;

	/*----------------------------- GET VERSION -------------------- */

	/*   Write Get Version CMD */
	g_bload_buf[0] = BL_GET_VERSION;
	g_bload_buf[1] = ~(BL_GET_VERSION);

	ret = ar0821_write(client, g_bload_buf, 2);
	if (ret < 0) {
		dev_err(&client->dev,"Write Failed \n");
		return -1;
	}

	/*   Wait for ACK or NACK */
	ret = ar0821_read(client, g_bload_buf, 1);
	if (ret < 0) {
		dev_err(&client->dev,"Read Failed \n");
		return -1;
	}

	if (g_bload_buf[0] != 'y') {
		/*   NACK Received */
		dev_err(&client->dev," NACK Received... exiting.. \n");
		return -1;
	}

	ret = ar0821_read(client, g_bload_buf, 1);
	if (ret < 0) {
		dev_err(&client->dev,"Read Failed \n");
		return -1;
	}

	ret = ar0821_read(client, g_bload_buf, 1);
	if (ret < 0) {
		dev_err(&client->dev,"Read Failed\n");
		return -1;
	}

	/* ---------------- GET VERSION END ------------------- */

	return 0;
}

static int mcu_bload_parse_send_cmd(struct i2c_client *client,
			     unsigned char *bytearray, int rec_len)
{
	IHEX_RECORD *ihex_rec = NULL;
	unsigned char checksum = 0, calc_checksum = 0;
	int i = 0, ret = 0;

	if (!bytearray)
		return -1;

	ihex_rec = (IHEX_RECORD *) bytearray;
	ihex_rec->addr = htons(ihex_rec->addr);

	checksum = bytearray[rec_len - 1];

	calc_checksum = mcu_bload_inv_checksum(bytearray, rec_len - 1);
	if (checksum != calc_checksum) {
		dev_err(&client->dev," Invalid Checksum 0x%02x != 0x%02x !! \n",
		       checksum, calc_checksum);
		return -1;
	}

	if ((ihex_rec->rectype == REC_TYPE_ELA)
	    && (ihex_rec->addr == 0x0000)
	    && (ihex_rec->datasize == 0x02)) {
		/*   Upper 32-bit configuration */
		g_bload_flashaddr = (ihex_rec->recdata[0] <<
				     24) | (ihex_rec->recdata[1]
					    << 16);

		debug_printk("Updated Flash Addr = 0x%08x \n",
			     g_bload_flashaddr);

	} else if (ihex_rec->rectype == REC_TYPE_DATA) {
		/*   Flash Data into Flashaddr */

		g_bload_flashaddr =
		    (g_bload_flashaddr & 0xFFFF0000) | (ihex_rec->addr);
		g_bload_crc16 ^=
		    mcu_bload_calc_crc16(ihex_rec->recdata, ihex_rec->datasize);

		/*   Write Erase Pages CMD */
		g_bload_buf[0] = BL_WRITE_MEM_NS;
		g_bload_buf[1] = ~(BL_WRITE_MEM_NS);

		ret = ar0821_write(client, g_bload_buf, 2);
		if (ret < 0) {
			dev_err(&client->dev,"Write Failed \n");
			return -1;
		}

		/*   Wait for ACK or NACK */
		ret = ar0821_read(client, g_bload_buf, 1);
		if (ret < 0) {
			dev_err(&client->dev,"Read Failed \n");
			return -1;
		}

		if (g_bload_buf[0] != RESP_ACK) {
			/*   NACK Received */
			dev_err(&client->dev," NACK Received... exiting.. \n");
			return -1;
		}

		g_bload_buf[0] = (g_bload_flashaddr & 0xFF000000) >> 24;
		g_bload_buf[1] = (g_bload_flashaddr & 0x00FF0000) >> 16;
		g_bload_buf[2] = (g_bload_flashaddr & 0x0000FF00) >> 8;
		g_bload_buf[3] = (g_bload_flashaddr & 0x000000FF);
		g_bload_buf[4] =
		    g_bload_buf[0] ^ g_bload_buf[1] ^ g_bload_buf[2] ^
		    g_bload_buf[3];

		ret = ar0821_write(client, g_bload_buf, 5);
		if (ret < 0) {
			dev_err(&client->dev,"Write Failed \n");
			return -1;
		}

		/*   Wait for ACK or NACK */
		ret = ar0821_read(client, g_bload_buf, 1);
		if (ret < 0) {
			dev_err(&client->dev,"Read Failed \n");
			return -1;
		}

		if (g_bload_buf[0] != RESP_ACK) {
			/*   NACK Received */
			dev_err(&client->dev," NACK Received... exiting.. \n");
			return -1;
		}

		g_bload_buf[0] = ihex_rec->datasize - 1;
		checksum = g_bload_buf[0];
		for (i = 0; i < ihex_rec->datasize; i++) {
			g_bload_buf[i + 1] = ihex_rec->recdata[i];
			checksum ^= g_bload_buf[i + 1];
		}

		g_bload_buf[i + 1] = checksum;

		ret = ar0821_write(client, g_bload_buf, i + 2);
		if (ret < 0) {
			dev_err(&client->dev,"Write Failed \n");
			return -1;
		}

 poll_busy:
		/*   Wait for ACK or NACK */
		ret = ar0821_read(client, g_bload_buf, 1);
		if (ret < 0) {
			dev_err(&client->dev,"Read Failed \n");
			return -1;
		}

		if (g_bload_buf[0] == RESP_BUSY)
			goto poll_busy;

		if (g_bload_buf[0] != RESP_ACK) {
			/*   NACK Received */
			dev_err(&client->dev," NACK Received... exiting.. \n");
			return -1;
		}

	} else if (ihex_rec->rectype == REC_TYPE_SLA) {
		/*   Update Instruction pointer to this address */

	} else if (ihex_rec->rectype == REC_TYPE_EOF) {
		/*   End of File - Issue I2C Go Command */
		return 0;
	} else {

		/*   Unhandled Type */
		dev_err(&client->dev,"Unhandled Command Type \n");
		return -1;
	}

	return 0;
}

static int mcu_bload_go(struct i2c_client *client)
{
	int ret = 0;

	g_bload_buf[0] = BL_GO;
	g_bload_buf[1] = ~(BL_GO);

	ret = ar0821_write(client, g_bload_buf, 2);
	if (ret < 0) {
		dev_err(&client->dev,"Write Failed \n");
		return -1;
	}

	ret = ar0821_read(client, g_bload_buf, 1);
	if (ret < 0) {
		dev_err(&client->dev,"Failed Read 1 \n");
		return -1;
	}

	/*   Start Address */
	g_bload_buf[0] = (FLASH_START_ADDRESS & 0xFF000000) >> 24;
	g_bload_buf[1] = (FLASH_START_ADDRESS & 0x00FF0000) >> 16;
	g_bload_buf[2] = (FLASH_START_ADDRESS & 0x0000FF00) >> 8;
	g_bload_buf[3] = (FLASH_START_ADDRESS & 0x000000FF);
	g_bload_buf[4] =
	    g_bload_buf[0] ^ g_bload_buf[1] ^ g_bload_buf[2] ^ g_bload_buf[3];

	ret = ar0821_write(client, g_bload_buf, 5);
	if (ret < 0) {
		dev_err(&client->dev,"Write Failed \n");
		return -1;
	}

	ret = ar0821_read(client, g_bload_buf, 1);
	if (ret < 0) {
		dev_err(&client->dev,"Failed Read 1 \n");
		return -1;
	}

	if (g_bload_buf[0] != RESP_ACK) {
		/*   NACK Received */
		dev_err(&client->dev," NACK Received... exiting.. \n");
		return -1;
	}

	return 0;
}

static int mcu_bload_update_fw(struct i2c_client *client)
{
	/* exclude NULL character at end of string */
	unsigned long hex_file_size = ARRAY_SIZE(g_mcu_fw_buf) - 1;
	/* static: one ihex record scratch buffer; too big for the kernel stack */
	static unsigned char wbuf[MAX_BUF_LEN];
	int i = 0, recindex = 0, ret = 0;

	for (i = 0; i < hex_file_size; i++) {
		if ((recindex == 0) && (g_mcu_fw_buf[i] == ':')) {
			/*  debug_printk("Start of a Record \n"); */
		} else if (g_mcu_fw_buf[i] == CR) {
			/*   No Implementation */
		} else if (g_mcu_fw_buf[i] == LF) {
			if (recindex == 0) {
				/*   Parsing Complete */
				break;
			}

			/*   Analyze Packet and Send Commands */
			ret = mcu_bload_parse_send_cmd(client, wbuf, recindex);
			if (ret < 0) {
				dev_err(&client->dev,"Error in Processing Commands \n");
				break;
			}

			recindex = 0;

		} else {
			/*   Parse Rec Data */
			if ((ret = mcu_bload_ascii2hex(g_mcu_fw_buf[i])) < 0) {
				dev_err(&client->dev,
					"Invalid Character - 0x%02x !! \n",
				     g_mcu_fw_buf[i]);
				break;
			}

			wbuf[recindex] = (0xF0 & (ret << 4));
			i++;

			if ((ret = mcu_bload_ascii2hex(g_mcu_fw_buf[i])) < 0) {
				dev_err(&client->dev,
				    "Invalid Character - 0x%02x !!!! \n",
				     g_mcu_fw_buf[i]);
				break;
			}

			wbuf[recindex] |= (0x0F & ret);
			recindex++;
		}
	}

	debug_printk("Program FLASH Success !! - CRC = 0x%04x \n",
		     g_bload_crc16);

	/* ------------ PROGRAM FLASH END ----------------------- */

	return ret;
}

static int mcu_bload_erase_flash(struct i2c_client *client)
{
	unsigned short int pagenum = 0x0000;
	int ret = 0, i = 0, checksum = 0;

	/* --------------- ERASE FLASH --------------------- */

	for (i = 0; i < NUM_ERASE_CYCLES; i++) {

		checksum = 0x00;
		/*   Write Erase Pages CMD */
		g_bload_buf[0] = BL_ERASE_MEM_NS;
		g_bload_buf[1] = ~(BL_ERASE_MEM_NS);

		ret = ar0821_write(client, g_bload_buf, 2);
		if (ret < 0) {
			dev_err(&client->dev,"Write Failed \n");
			return -1;
		}

		/*   Wait for ACK or NACK */
		ret = ar0821_read(client, g_bload_buf, 1);
		if (ret < 0) {
			dev_err(&client->dev,"Read Failed \n");
			return -1;
		}

		if (g_bload_buf[0] != RESP_ACK) {
			/*   NACK Received */
			dev_err(&client->dev," NACK Received... exiting.. \n");
			return -1;
		}

		g_bload_buf[0] = (MAX_PAGES - 1) >> 8;
		g_bload_buf[1] = (MAX_PAGES - 1) & 0xFF;
		g_bload_buf[2] = g_bload_buf[0] ^ g_bload_buf[1];

		ret = ar0821_write(client, g_bload_buf, 3);
		if (ret < 0) {
			dev_err(&client->dev,"Write Failed \n");
			return -1;
		}

		/*   Wait for ACK or NACK */
		ret = ar0821_read(client, g_bload_buf, 1);
		if (ret < 0) {
			dev_err(&client->dev,"Read Failed \n");
			return -1;
		}

		if (g_bload_buf[0] != RESP_ACK) {
			/*   NACK Received */
			dev_err(&client->dev," NACK Received... exiting.. \n");
			return -1;
		}

		for (pagenum = 0; pagenum < MAX_PAGES; pagenum++) {
			g_bload_buf[(2 * pagenum)] =
			    (pagenum + (i * MAX_PAGES)) >> 8;
			g_bload_buf[(2 * pagenum) + 1] =
			    (pagenum + (i * MAX_PAGES)) & 0xFF;
			checksum =
			    checksum ^ g_bload_buf[(2 * pagenum)] ^
			    g_bload_buf[(2 * pagenum) + 1];
		}
		g_bload_buf[2 * MAX_PAGES] = checksum;

		ret = ar0821_write(client, g_bload_buf, (2 * MAX_PAGES) + 1);
		if (ret < 0) {
			dev_err(&client->dev,"Write Failed \n");
			return -1;
		}

 poll_busy:
		/*   Wait for ACK or NACK */
		ret = ar0821_read(client, g_bload_buf, 1);
		if (ret < 0) {
			dev_err(&client->dev,"Read Failed \n");
			return -1;
		}

		if (g_bload_buf[0] == RESP_BUSY)
			goto poll_busy;

		if (g_bload_buf[0] != RESP_ACK) {
			/*   NACK Received */
			dev_err(&client->dev," NACK Received... exiting.. \n");
			return -1;
		}

		debug_printk(" ERASE Sector %d success !! \n", i + 1);
	}

	/* ------------ ERASE FLASH END ----------------------- */

	return 0;
}

static int mcu_bload_read(struct i2c_client *client,
		   unsigned int g_bload_flashaddr, char *bytearray,
		   unsigned int len)
{
	int ret = 0;

	g_bload_buf[0] = BL_READ_MEM;
	g_bload_buf[1] = ~(BL_READ_MEM);

	ret = ar0821_write(client, g_bload_buf, 2);
	if (ret < 0) {
		dev_err(&client->dev,"Write Failed \n");
		return -1;
	}

	/*   Wait for ACK or NACK */
	ret = ar0821_read(client, g_bload_buf, 1);
	if (ret < 0) {
		dev_err(&client->dev,"Read Failed \n");
		return -1;
	}

	if (g_bload_buf[0] != RESP_ACK) {
		/*   NACK Received */
		dev_err(&client->dev," NACK Received... exiting.. \n");
		return -1;
	}

	g_bload_buf[0] = (g_bload_flashaddr & 0xFF000000) >> 24;
	g_bload_buf[1] = (g_bload_flashaddr & 0x00FF0000) >> 16;
	g_bload_buf[2] = (g_bload_flashaddr & 0x0000FF00) >> 8;
	g_bload_buf[3] = (g_bload_flashaddr & 0x000000FF);
	g_bload_buf[4] =
	    g_bload_buf[0] ^ g_bload_buf[1] ^ g_bload_buf[2] ^ g_bload_buf[3];

	ret = ar0821_write(client, g_bload_buf, 5);
	if (ret < 0) {
		dev_err(&client->dev,"Write Failed \n");
		return -1;
	}

	/*   Wait for ACK or NACK */
	ret = ar0821_read(client, g_bload_buf, 1);
	if (ret < 0) {
		dev_err(&client->dev,"Read Failed \n");
		return -1;
	}

	if (g_bload_buf[0] != RESP_ACK) {
		/*   NACK Received */
		dev_err(&client->dev," NACK Received... exiting.. \n");
		return -1;
	}

	g_bload_buf[0] = len - 1;
	g_bload_buf[1] = ~(len - 1);

	ret = ar0821_write(client, g_bload_buf, 2);
	if (ret < 0) {
		dev_err(&client->dev,"Write Failed \n");
		return -1;
	}

	/*   Wait for ACK or NACK */
	ret = ar0821_read(client, g_bload_buf, 1);
	if (ret < 0) {
		dev_err(&client->dev,"Read Failed \n");
		return -1;
	}

	if (g_bload_buf[0] != RESP_ACK) {
		/*   NACK Received */
		dev_err(&client->dev," NACK Received... exiting.. \n");
		return -1;
	}

	ret = ar0821_read(client, bytearray, len);
	if (ret < 0) {
		dev_err(&client->dev,"Read Failed \n");
		return -1;
	}

	return 0;
}

static int mcu_bload_verify_flash(struct i2c_client *client,
			   unsigned short int orig_crc)
{
	char bytearray[FLASH_READ_LEN];
	unsigned short int calc_crc = 0;
	unsigned int flash_addr = FLASH_START_ADDRESS, i = 0;

	while ((i + FLASH_READ_LEN) <= FLASH_SIZE) {
		memset(bytearray, 0x0, FLASH_READ_LEN);

		if (mcu_bload_read
		    (client, flash_addr + i, bytearray, FLASH_READ_LEN) < 0) {
			dev_err(&client->dev," i2c_bload_read FAIL !! \n");
			return -1;
		}

		calc_crc ^= mcu_bload_calc_crc16(bytearray, FLASH_READ_LEN);
		i += FLASH_READ_LEN;
	}

	if ((FLASH_SIZE - i) > 0) {
		memset(bytearray, 0x0, FLASH_READ_LEN);

		if (mcu_bload_read
		    (client, flash_addr + i, bytearray, (FLASH_SIZE - i))
		    < 0) {
			dev_err(&client->dev," i2c_bload_read FAIL !! \n");
			return -1;
		}

		calc_crc ^= mcu_bload_calc_crc16(bytearray, FLASH_READ_LEN);
	}

	if (orig_crc != calc_crc) {
		dev_err(&client->dev," CRC verification fail !! 0x%04x != 0x%04x \n",
		       orig_crc, calc_crc);
		return -1;
	}

	debug_printk(" CRC Verification Success 0x%04x == 0x%04x \n",
		     orig_crc, calc_crc);

	return 0;
}

static int mcu_fw_update(struct i2c_client *client, unsigned char *mcu_fw_version)
{
	int ret = 0;
	g_bload_crc16 = 0;

	/* Read Firmware version from bootloader MCU */
	ret = mcu_bload_get_version(client);
	if (ret < 0) {
		dev_err(&client->dev," Error in Get Version \n");
		goto exit;
	}

	debug_printk(" Get Version SUCCESS !! \n");

	/* Erase firmware present in the MCU and flash new firmware*/
	ret = mcu_bload_erase_flash(client);
	if (ret < 0) {
		dev_err(&client->dev," Error in Erase Flash \n");
		goto exit;
	}

	debug_printk("Erase Flash Success !! \n");

	/* Read the firmware present in the text file */
	if ((ret = mcu_bload_update_fw(client)) < 0) {
		dev_err(&client->dev," Write Flash FAIL !! \n");
		goto exit;
	}

	/* Verify the checksum for the update firmware */
	if ((ret = mcu_bload_verify_flash(client, g_bload_crc16)) < 0) {
		dev_err(&client->dev," verify_flash FAIL !! \n");
		goto exit;
	}

	/* Reverting from bootloader mode */
	/* I2C GO Command */
	if ((ret = mcu_bload_go(client)) < 0) {
		dev_err(&client->dev," i2c_bload_go FAIL !! \n");
		goto exit;
	}
	
	if(mcu_fw_version) {
	dev_dbg(&client->dev, "(%s) - Firmware Updated - (%.32s)\n",
				__func__, mcu_fw_version);
	}
 exit:
	return ret;
}

static int ar0821_probe(struct i2c_client *client,
			const struct i2c_device_id *id)
{
	struct camera_common_data *common_data;
	struct device_node *node = client->dev.of_node;
	struct ar0821 *priv;
	int gpio;

	unsigned char fw_version[32] = {0}, txt_fw_version[32] = {0};
	int ret, frm_fmt_size = 0, loop;
	uint16_t sensor_id = 0;
	int16_t reset_gpio = 0, pwdn_gpio = 0;
	int retry = 5;
	uint32_t mipi_lane = 0, mipi_clk = 0;
	int err = 0, pwdn_gpio_toggle = 0;
	bool framesync_disabled;
	if (!IS_ENABLED(CONFIG_OF) || !node)
		return -EINVAL;


	reset_gpio = of_get_named_gpio(node, "reset-gpios", 0);
	printk("RESET = %x \n",reset_gpio);
	if(reset_gpio < 0) {
		dev_err(&client->dev, "Unable to toggle RESET GPIO\n");
		return -EINVAL;
	}

	pwdn_gpio = of_get_named_gpio(node, "pwdn-gpios", 0);
	printk("BOOT = %x \n",pwdn_gpio);
	if(pwdn_gpio < 0) {
		dev_err(&client->dev, "Unable to toggle BOOT GPIO\n");
		return -EINVAL;
	}


	err = devm_gpio_request(&client->dev, reset_gpio,"cam-reset");
	if (err < 0) {
		dev_err(&client->dev,"%s[%d]:GPIO reset Fail, err:%d",__func__,__LINE__, err);
		return -EINVAL;
	}


	err = devm_gpio_request(&client->dev, pwdn_gpio,"cam-boot");
	if (err < 0) {
		dev_err(&client->dev,"%s[%d]:%dGPIO boot Fail\n",__func__,__LINE__,err);
		return -EINVAL;
	}
	err = of_property_read_u32(node, "camera_mipi_lanes", &mipi_lane);
        if (err < 0) {
                dev_err(&client->dev, "Can not get Camera MIPI Lanes\n");
                return -EINVAL;
        }

	err = of_property_read_u32(node, "camera-mipi-clk", &mipi_clk);
	if (err) {
		dev_err(&client->dev, "camera mipi clk is missing or invalid\n");
		return err;
	}
	else
		pr_info("camera-mipi-clk = %dMHz\n",mipi_clk);

	/*AR0821 for 4-lane by default works upto 896MHZ in latest bootdata.
	 * Sensor is not stable for mipi-clk above 896MHZ in 4-lane.
	 * */

	if ( mipi_lane == NUM_LANES_4 && mipi_clk > 896 )
		mipi_clk = 896;

	framesync_disabled = of_property_read_bool(node, "disable-framesync");
	common_data =
	    devm_kzalloc(&client->dev,
			 sizeof(struct camera_common_data), GFP_KERNEL);
	if (!common_data)
		return -ENOMEM;

	priv =
	    devm_kzalloc(&client->dev,
			 sizeof(struct ar0821) +
			 sizeof(struct v4l2_ctrl *) * AR0821_NUM_CONTROLS /**num_ctrls*/,
			 GFP_KERNEL);
	if (!priv)
		return -ENOMEM;

	/* MIPI SELECT GPIO for MIPI Lane select
         * For 2 Lane config:- Mipi select gpio LOW state
         *      gpio_set_value(gpio, 0);
         * For 4 Lane config:- Mipi select gpio HIGH state
         *      gpio_set_value(gpio, 1);
         */
        gpio = of_get_named_gpio(node, "mipi-select-gpios", 0);
	if (gpio < 0) {
		/* reset-gpio is not absoluctly needed */
		dev_info(&client->dev, "mipi select gpios not in DT\n");
		gpio = 0;
	} else {
		gpio_request(gpio, "mipi_select");
		gpio_direction_output(gpio, 1);
		if(mipi_lane == NUM_LANES_2)
			gpio_set_value(gpio, 0);
		else if(mipi_lane == NUM_LANES_4)
			gpio_set_value(gpio, 1);
	}

	priv->pdata = ar0821_parse_dt(client);
	if (!priv->pdata) {
		dev_err(&client->dev, "unable to get platform data\n");
		return -EFAULT;
	}
	priv->i2c_client = client;
	priv->s_data = common_data;
	priv->subdev = &common_data->subdev;
	priv->subdev->dev = &client->dev;
	priv->s_data->dev = &client->dev;
	common_data->priv = (void *)priv;
	priv->mipi_lane_config = mipi_lane;
	priv->mipi_clk_config = mipi_clk;
	priv->framesync_enabled = !framesync_disabled;
	if (priv->framesync_enabled)
	  printk("mentioned in dtb");
	else
	  printk("Not mentioned in dtb");
	
#ifdef FRAMESYNC_ENABLE	
	priv->last_sync_mode = 1;
#endif	

	dev_info(&client->dev, "MIPI Clock = %d, MIPI Lanes = %d \n", priv->mipi_clk_config, priv->mipi_lane_config);

	err = ar0821_power_get(priv);
	if (err)
		return err;


	err = ar0821_power_on(common_data);
	if (err)
		return err;

	/* Reset Release for MCU */
	toggle_gpio(pwdn_gpio, 0);
	msleep(10);
	toggle_gpio(reset_gpio, 0);
	msleep(10);
	//toggle_gpio(pwdn_gpio, 1);
	//msleep(10);
	toggle_gpio(reset_gpio, 1);
	msleep(10);

	ret = mcu_get_fw_version(client, fw_version, txt_fw_version);
	printk("mcu_get_fw_version returned: %d.\n", ret);
	if (ret != 0) {

	  	if(ret > 0) {
			if((err = mcu_jump_bload(client)) < 0) {
				dev_err(&client->dev," Cannot go into bootloader mode\n");
				return -EIO;
			}			
			msleep(1);
		} else {
			dev_err(&client->dev," Trying to Detect Bootloader mode\n");	
			toggle_gpio(reset_gpio, 0);
                	msleep(10);
                	toggle_gpio(pwdn_gpio, 1);
                	msleep(100);
                	toggle_gpio(reset_gpio, 1);
                	msleep(100);
		}
		
		for(loop = 0;loop < 10; loop++) {
			err = mcu_bload_get_version(client);
			if (err < 0) {
				/* Trial and Error for 1 second (100ms * 10) */
				msleep(100);
				continue;
			} else {
				dev_err(&client->dev," Get Bload Version Success\n");
				pwdn_gpio_toggle = 1;
				break;
			}
		}
		
		if(loop == 10) {
			dev_err(&client->dev, "Error updating firmware \n");
			return -EINVAL;
		}

		if (mcu_fw_update(client, NULL) < 0)
			return -EFAULT;


		if( pwdn_gpio_toggle == 1)
			toggle_gpio(pwdn_gpio, 0);

		/* Allow FW Updated Driver to reboot */
		msleep(10);

		for(loop = 0;loop < 100; loop++) {
			err = mcu_get_fw_version(client, fw_version, txt_fw_version);
			if (err < 0) {
				/* Trial and Error for 10 seconds (100ms * 100) */
				msleep(100);
				continue;
			} else {
				dev_err(&client->dev," Get FW Version Success\n");
				break;
			}
		}
		if(loop == 100) {
			dev_err(&client->dev, "Error updating firmware \n");
			return -EINVAL;
		}						
		debug_printk("Current Firmware Version - (%.32s)",fw_version);

	} else {
		/* Same firmware version in MCU and Text File */
		debug_printk("Current Firmware Version - (%.32s)",fw_version);
	}

	/* Configure MIPI Lanes of the Sensor */
	retry = 5;
	while (--retry >= 0) {
		if (mcu_mipi_configuration(client, priv, CMD_ID_LANE_CONFIG) < 0) {
			dev_err(&client->dev, "%s, mcu_mipi_configuration lane failure. retrying \n", __func__);
			continue;
		} else {
		  	break;
		}
	}
	if (retry < 0) {
	  	dev_err(&client->dev, "%s, Failed mcu_mipi_configuration lane \n", __func__);
		return -EFAULT;
	}

	retry = 5;
	while (--retry >= 0) {
		if (mcu_mipi_configuration(client, priv, CMD_ID_MIPI_CLK_CONFIG) < 0) {
			dev_err(&client->dev, "%s, mcu_mipi_configuration clk failure. retrying \n", __func__);
			continue;
		} else {
		  	break;
		}
	}
	if (retry < 0) {
	  	dev_err(&client->dev, "%s, Failed mcu_mipi_configuration clk \n", __func__);
		return -EFAULT;
	}	
	
	/* Query the number of controls from MCU*/
	retry = 5;
	while (--retry >= 0) {
		if(mcu_list_ctrls(client, NULL, priv) < 0) {
			dev_err(&client->dev, "%s, init controls failure. retrying \n", __func__);
			continue;
		} else {
		  	break;
		}
	}
	if (retry < 0) {
	  	dev_err(&client->dev, "%s, Failed to init controls \n", __func__);
		return -EFAULT;
	}

	/*Query the number for Formats available from MCU */
	retry = 5;
	while (--retry >= 0) {
		if(mcu_list_fmts(client, NULL, &frm_fmt_size, priv) < 0) {
			dev_err(&client->dev, "%s, init formats failure. retrying \n", __func__);
			continue;
		} else {
		  	break;
		}
	}

	if (retry < 0) {
	  	dev_err(&client->dev, "%s, Failed to init formats!\n", __func__);
	  	return -EFAULT;
	}

	priv->mcu_ctrl_info = devm_kzalloc(&client->dev, sizeof(ISP_CTRL_INFO) * priv->num_ctrls, GFP_KERNEL);
	if(!priv->mcu_ctrl_info) {
		dev_err(&client->dev, "Unable to allocate memory \n");
		return -ENOMEM;
	}

	priv->ctrldb = devm_kzalloc(&client->dev, sizeof(uint32_t) * priv->num_ctrls, GFP_KERNEL);
	if(!priv->ctrldb) {
		dev_err(&client->dev, "Unable to allocate memory \n");
		return -ENOMEM;
	}

	priv->stream_info = devm_kzalloc(&client->dev, sizeof(ISP_STREAM_INFO) * (frm_fmt_size + 1), GFP_KERNEL);

	priv->streamdb = devm_kzalloc(&client->dev, sizeof(int) * (frm_fmt_size + 1), GFP_KERNEL);
	if(!priv->streamdb) {
		dev_err(&client->dev,"Unable to allocate memory \n");
		return -ENOMEM;
	}

	priv->mcu_cam_frmfmt = devm_kzalloc(&client->dev, sizeof(struct camera_common_frmfmt) * (frm_fmt_size), GFP_KERNEL);
	if(!priv->mcu_cam_frmfmt) {
		dev_err(&client->dev, "Unable to allocate memory \n");
		return -ENOMEM;
	}

	/* MCU Init */
	retry = 5;
	while (--retry >= 0) {
		if (mcu_cam_init(client) < 0) {
			dev_err(&client->dev, "INIT ISP failure. retrying\n");
			continue;
		} else {
		  	break;
		}
	}

	if (retry < 0) {
	  	dev_err(&client->dev, "Unable to INIT ISP \n");
	  	return -EFAULT;
	}
	/* mcu_get_sensor_id */
	retry = 5;
	while (--retry >= 0) {
		if (mcu_get_sensor_id(client, &sensor_id) < 0) {
			dev_err(&client->dev, "Get MCU Sensor ID failure. retrying \n");
			continue;
		} else {
		  	break;
		}
	}
	if (retry < 0) {
	  	dev_err(&client->dev, "Unable to get MCU Sensor ID.");
	  	return -EFAULT;
	}
	printk("SENSOR ID=0x%04x\n",sensor_id);

	/* mcu_cam_stream_off */
	retry = 5;
	while (--retry >= 0) {
	  	err = mcu_cam_stream_off(client);
	  	if (err != 0) {
	    		dev_err(&client->dev, "Stream_Off failure. retrying \n");
	    		continue;
	  	} else {
	    		break;
	  	}
	}
	if(retry < 0){
		dev_err(&client->dev,"%s (%d) Stream_Off \n", __func__, __LINE__);
		return err;
	}

	for(loop = 0; loop < frm_fmt_size; loop++) {
		priv->mcu_cam_frmfmt[loop].framerates = devm_kzalloc(&client->dev, sizeof(int) * MAX_NUM_FRATES, GFP_KERNEL);
		if(!priv->mcu_cam_frmfmt[loop].framerates) {
			dev_err(&client->dev, "Unable to allocate memory \n");
			return -ENOMEM;
		}
	}

	/* Enumerate Formats */
	retry = 5;
	while (--retry >= 0) {
		if (mcu_list_fmts(client, priv->stream_info, &frm_fmt_size, priv) < 0) {
			dev_err(&client->dev, "List Fmts failure. retrying\n");
			continue;
		} else {
		  	break;
		}
	}

	if (retry < 0) {
	  	dev_err(&client->dev, "Unable to List Fmts. \n");
	  	return -EFAULT;
	}

	common_data->ops = NULL;
	common_data->ctrl_handler = &priv->ctrl_handler;
	common_data->frmfmt = priv->mcu_cam_frmfmt;
	common_data->colorfmt =
	camera_common_find_datafmt(AR0821_DEFAULT_DATAFMT);
	common_data->power = &priv->power;
	common_data->ctrls = priv->ctrls;
	common_data->priv = (void *)priv;
	common_data->numctrls = priv->num_ctrls;
	common_data->numfmts = frm_fmt_size;
	common_data->def_mode = AR0821_DEFAULT_MODE;
	common_data->def_width = AR0821_DEFAULT_WIDTH;
	common_data->def_height = AR0821_DEFAULT_HEIGHT;
	common_data->fmt_width = common_data->def_width;
	common_data->fmt_height = common_data->def_height;
	common_data->def_clk_freq = 24000000;

	priv->i2c_client = client;
	priv->s_data = common_data;
	priv->subdev = &common_data->subdev;
	priv->subdev->dev = &client->dev;
	priv->s_data->dev = &client->dev;
	priv->prev_index = 0xFFFE;

	err = camera_common_initialize(common_data, "ar0821");
	if (err) {
		dev_err(&client->dev, "Failed to initialize ar0821.\n");
		return err;
	}


	/* Get CAM FW version to find the availabity of MC chip */

	v4l2_i2c_subdev_init(priv->subdev, client, &ar0821_subdev_ops);

	/* Enumerate Ctrls */
	err = ar0821_ctrls_init(priv, priv->mcu_ctrl_info);
	if (err)
		return err;

	priv->subdev->internal_ops = &ar0821_subdev_internal_ops;
	priv->subdev->flags |=
	    V4L2_SUBDEV_FL_HAS_DEVNODE | V4L2_SUBDEV_FL_HAS_EVENTS;

	/*
	  To unload the module driver module,
	  Set (struct v4l2_subdev *)priv->subdev->sd to NULL.
	  Refer tegracam_v4l2subdev_register() in tegracam_v4l2.c
         */

	if (priv->subdev->owner == THIS_MODULE) {
	  	common_data->owner = priv->subdev->owner;
		priv->subdev->owner = NULL;
	} else {
	  // It shouldn't come here  in probe();
	  ;
	}
	
#if defined(CONFIG_MEDIA_CONTROLLER)
	priv->pad.flags = MEDIA_PAD_FL_SOURCE;
	priv->subdev->entity.ops = &ar0821_media_ops;
	err = tegra_media_entity_init(&priv->subdev->entity, 1, &priv->pad, true, true);
	if (err < 0) {
		dev_err(&client->dev, "unable to init media entity\n");
		return err;
	}
#endif

	err = v4l2_async_register_subdev(priv->subdev);
	if (err)
		return err;

#ifdef FRAMESYNC_ENABLE
	err = pca9685_init(client);
	if(err){
		dev_err(&client->dev, "unable to init pca9685\n");
		return err;
	}
#endif	
	printk("Detected ar0821 sensor\n");

	return 0;
}

#define FREE_SAFE(dev, ptr) \
	if(ptr) { \
		devm_kfree(dev, ptr); \
	}

static int ar0821_remove(struct i2c_client *client)
{
	struct camera_common_data *s_data = to_camera_common_data(&client->dev);
	struct ar0821 *priv = (struct ar0821 *)s_data->priv;
	struct device_node *node = client->dev.of_node;
	int loop = 0;
	int reset_gpio = 0, pwdn_gpio = 0;

	if (!priv || !priv->pdata)
		return -1;

	reset_gpio = of_get_named_gpio(node, "reset-gpios", 0);
	if(reset_gpio < 0) {
		dev_err(&client->dev, "Unable to get reset GPIO\n");
		return -EINVAL;
	}


	pwdn_gpio = of_get_named_gpio(node, "pwdn-gpios", 0);
	if(pwdn_gpio < 0) {
		dev_err(&client->dev, "Unable to get power GPIO\n");
		return -EINVAL;
	}

	gpio_free(reset_gpio);
	gpio_free(pwdn_gpio);	

	v4l2_async_unregister_subdev(priv->subdev);
#if defined(CONFIG_MEDIA_CONTROLLER)
	media_entity_cleanup(&priv->subdev->entity);
#endif
	//calibration_exit();	

	v4l2_ctrl_handler_free(&priv->ctrl_handler);
	ar0821_power_put(priv);
	camera_common_remove_debugfs(s_data);

	/* Free up memory */
	for(loop = 0; loop < priv->mcu_ctrl_info->ctrl_ui_data.ctrl_menu_info.num_menu_elem
			; loop++) {
		FREE_SAFE(&client->dev, priv->mcu_ctrl_info->ctrl_ui_data.ctrl_menu_info.menu[loop]);
	}

	FREE_SAFE(&client->dev, priv->mcu_ctrl_info->ctrl_ui_data.ctrl_menu_info.menu);

	FREE_SAFE(&client->dev, priv->mcu_ctrl_info);

	for(loop = 0; loop < s_data->numfmts; loop++ ) {
		FREE_SAFE(&client->dev, (void *)priv->mcu_cam_frmfmt[loop].framerates);
	}

	FREE_SAFE(&client->dev, priv->mcu_cam_frmfmt);

	FREE_SAFE(&client->dev, priv->ctrldb);
	FREE_SAFE(&client->dev, priv->streamdb);

	FREE_SAFE(&client->dev, priv->stream_info);
	FREE_SAFE(&client->dev, fw_version);
	FREE_SAFE(&client->dev, priv->pdata);
	FREE_SAFE(&client->dev, priv->s_data);
	FREE_SAFE(&client->dev, priv);
	return 0;
}

static const struct i2c_device_id ar0821_id[] = {
	{"ar0821", 0},
	{}
};

MODULE_DEVICE_TABLE(i2c, ar0821_id);

static struct i2c_driver ar0821_i2c_driver = {
	.driver = {
		   .name = "ar0821",
		   .owner = THIS_MODULE,
		   .of_match_table = of_match_ptr(ar0821_of_match),
		   },
	.probe = ar0821_probe,
	.remove = ar0821_remove,
	.id_table = ar0821_id,
};

module_i2c_driver(ar0821_i2c_driver);

MODULE_DESCRIPTION("V4L2 driver for e-con cameras");
MODULE_AUTHOR("E-Con Systems");
MODULE_LICENSE("GPL v2");
