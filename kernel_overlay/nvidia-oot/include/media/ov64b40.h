/* SPDX-License-Identifier: GPL-2.0-only */
/* OV64B40 register definitions and control conversions. */

#ifndef __OV64B40_H__
#define __OV64B40_H__

#include <linux/errno.h>
#include <linux/kernel.h>
#include <linux/math64.h>
#include <linux/overflow.h>
#include <linux/types.h>
#include <media/camera_common.h>

/* Verified on the Hadron EO; use only its primary 7-bit I2C address. */
#define OV64B40_I2C_ADDR			0x36
#define OV64B40_CHIP_ID_ADDR_MSB		0x300a
#define OV64B40_CHIP_ID_ADDR_MID		0x300b
#define OV64B40_CHIP_ID_ADDR_LSB		0x300c
#define OV64B40_CHIP_ID			0x566442
#define OV64B40_SW_RESET_ADDR		0x0103
#define OV64B40_SW_RESET			0x01
#define OV64B40_STREAM_ADDR		0x0100
#define OV64B40_STREAM_ON			0x01
#define OV64B40_STREAM_OFF		0x00

#define OV64B40_BYTE_MASK			0xff
#define OV64B40_BYTE_SHIFT		8
#define OV64B40_WORD_SHIFT		16

/* Group 10 start, end and launch, as the MIAMI OV64B imgsensor driver uses. */
#define OV64B40_GROUP_HOLD_ADDR		0x3208
#define OV64B40_GROUP_HOLD_START		0x0a
#define OV64B40_GROUP_HOLD_END		0x1a
#define OV64B40_GROUP_HOLD_LAUNCH		0xaa

/*
 * Coarse exposure is a 24-bit whole-line count. MIAMI and ov64a40 both use a
 * 16-line minimum; ov64a40's 32-line VTS margin covers MIAMI's 31. MIAMI keeps
 * exposure and VTS even ("odd might happen unexpected results").
 */
#define OV64B40_EXPOSURE_ADDR_MSB		0x3500
#define OV64B40_EXPOSURE_ADDR_MID		0x3501
#define OV64B40_EXPOSURE_ADDR_LSB		0x3502
#define OV64B40_EXPOSURE_MSB_MASK		0xff
#define OV64B40_MIN_EXPOSURE_LINES	16U
#define OV64B40_EXPOSURE_MARGIN		32U

/* Linear analog gain in Q8: 0x0100 is 1x, 0x0f80 is 15.5x. */
#define OV64B40_GAIN_ADDR_MSB		0x3508
#define OV64B40_GAIN_ADDR_LSB		0x3509
#define OV64B40_GAIN_SCALE		256U
#define OV64B40_MIN_GAIN			0x0100U
#define OV64B40_MAX_GAIN			0x0f80U

/*
 * VTS is a 24-bit line count, 0x3840 holding bits 23:16. DT line_length is
 * pix_clk_hz * HTS / 115.2 MHz, so pix_clk_hz / line_length is the line rate.
 */
#define OV64B40_FRAME_LENGTH_ADDR_HSB	0x3840
#define OV64B40_FRAME_LENGTH_ADDR_MSB	0x380e
#define OV64B40_FRAME_LENGTH_ADDR_LSB	0x380f
#define OV64B40_LINE_LENGTH_ADDR_MSB	0x380c
#define OV64B40_LINE_LENGTH_ADDR_LSB	0x380d
#define OV64B40_MIN_FRAME_LENGTH		(OV64B40_EXPOSURE_MARGIN + \
					 OV64B40_MIN_EXPOSURE_LINES)
#define OV64B40_MAX_FRAME_LENGTH		0xfffffeU

/*
 * Readout orientation. 0x3820 bit 2 is vertical flip; 0x3821 bit 2 is mirror,
 * inverted (set = not mirrored). 0x3811/0x3813 are the ISP window X/Y offsets
 * and 0x383d the PD window offset, nudged by one to keep the Bayer phase.
 */
#define OV64B40_TIMING_FORMAT1_ADDR		0x3820
#define OV64B40_TIMING_FORMAT2_ADDR		0x3821
#define OV64B40_VFLIP_BIT			BIT(2)
#define OV64B40_MIRROR_OFF_BIT		BIT(2)
#define OV64B40_ISP_X_OFFSET_ADDR		0x3811
#define OV64B40_ISP_Y_OFFSET_ADDR		0x3813
#define OV64B40_PD_X_OFFSET_ADDR		0x383d

/* Raw test-pattern byte; 0x01 is vertical colour bars (Xiaomi OV64B driver). */
#define OV64B40_TEST_PATTERN_ADDR		0x50c1

/* ov64a40 waits 5 ms from XSHUTDOWN release to the first SCCB access. */
#define OV64B40_RESET_SETTLE_US		10000U
#define OV64B40_RESET_SETTLE_MAX_US	11000U
#define OV64B40_SW_RESET_WAIT_US		10000U
#define OV64B40_SW_RESET_WAIT_MAX_US	11000U
#define OV64B40_START_WAIT_MS		3U

static inline u32 ov64b40_chip_id_from_regs(u8 msb, u8 mid, u8 lsb)
{
	return ((u32)msb << OV64B40_WORD_SHIFT) |
	       ((u32)mid << OV64B40_BYTE_SHIFT) | lsb;
}

static inline u32 ov64b40_exposure_from_regs(u8 msb, u8 mid, u8 lsb)
{
	return ((u32)(msb & OV64B40_EXPOSURE_MSB_MASK) <<
		OV64B40_WORD_SHIFT) | ((u32)mid << OV64B40_BYTE_SHIFT) | lsb;
}

static inline void ov64b40_get_exposure_regs(struct reg_8 *regs, u32 lines)
{
	regs[0].addr = OV64B40_EXPOSURE_ADDR_MSB;
	regs[0].val = (lines >> OV64B40_WORD_SHIFT) &
		     OV64B40_EXPOSURE_MSB_MASK;
	regs[1].addr = OV64B40_EXPOSURE_ADDR_MID;
	regs[1].val = (lines >> OV64B40_BYTE_SHIFT) & OV64B40_BYTE_MASK;
	regs[2].addr = OV64B40_EXPOSURE_ADDR_LSB;
	regs[2].val = lines & OV64B40_BYTE_MASK;
}

static inline void ov64b40_get_gain_regs(struct reg_8 *regs, u16 gain)
{
	regs[0].addr = OV64B40_GAIN_ADDR_MSB;
	regs[0].val = (gain >> OV64B40_BYTE_SHIFT) & OV64B40_BYTE_MASK;
	regs[1].addr = OV64B40_GAIN_ADDR_LSB;
	regs[1].val = gain & OV64B40_BYTE_MASK;
}

static inline void ov64b40_get_frame_length_regs(struct reg_8 *regs, u32 vts)
{
	regs[0].addr = OV64B40_FRAME_LENGTH_ADDR_HSB;
	regs[0].val = (vts >> OV64B40_WORD_SHIFT) & OV64B40_BYTE_MASK;
	regs[1].addr = OV64B40_FRAME_LENGTH_ADDR_MSB;
	regs[1].val = (vts >> OV64B40_BYTE_SHIFT) & OV64B40_BYTE_MASK;
	regs[2].addr = OV64B40_FRAME_LENGTH_ADDR_LSB;
	regs[2].val = vts & OV64B40_BYTE_MASK;
}

/* gain_reg = gain_control * 256 / DT gain_factor (16 means 1x). */
static inline int ov64b40_gain_to_reg(s64 val, u32 gain_factor, u16 *gain)
{
	u64 max_control;
	u64 scaled;

	if (!gain_factor)
		return -EINVAL;

	max_control = div_u64((u64)OV64B40_MAX_GAIN * gain_factor,
			      OV64B40_GAIN_SCALE);
	/* Clamp before multiplying, including hostile s64 control inputs. */
	val = clamp_t(s64, val, 0, max_control);
	scaled = div_u64((u64)val * OV64B40_GAIN_SCALE, gain_factor);
	*gain = clamp_t(u64, scaled, OV64B40_MIN_GAIN, OV64B40_MAX_GAIN);

	return 0;
}

/* Require at least active height + the exposure margin, kept even. */
static inline u32 ov64b40_min_frame_length(u32 active_height)
{
	return round_up(clamp_t(u64, (u64)active_height + OV64B40_EXPOSURE_MARGIN,
				OV64B40_MIN_FRAME_LENGTH,
				OV64B40_MAX_FRAME_LENGTH), 2);
}

/* Caller supplies a valid VTS, greater than OV64B40_EXPOSURE_MARGIN. */
static inline u32 ov64b40_clamp_exposure(u64 lines, u32 frame_length)
{
	return clamp_t(u64, lines, OV64B40_MIN_EXPOSURE_LINES,
		       frame_length - OV64B40_EXPOSURE_MARGIN) & ~1U;
}

/* lines = exposure_us * pix_clk_hz / exposure_factor / line_length. */
static inline int ov64b40_exposure_to_lines(s64 val, u64 pixel_clock,
					  u32 line_length, u32 exposure_factor,
					  u32 frame_length, u32 *lines)
{
	u64 cycles;
	u64 coarse;

	if (val < 0 || !pixel_clock || !line_length || !exposure_factor ||
	    frame_length < OV64B40_MIN_FRAME_LENGTH ||
	    frame_length > OV64B40_MAX_FRAME_LENGTH)
		return -EINVAL;
	if (check_mul_overflow((u64)val, pixel_clock, &cycles))
		return -ERANGE;

	coarse = div_u64(div_u64(cycles, exposure_factor), line_length);
	*lines = ov64b40_clamp_exposure(coarse, frame_length);

	return 0;
}

/* VTS = pix_clk_hz * framerate_factor / line_length / scaled_fps. */
static inline int ov64b40_framerate_to_vts(s64 val, u64 pixel_clock,
					u32 line_length, u32 framerate_factor,
					u32 active_height, u32 *frame_length)
{
	u64 scaled_clock;
	u64 vts;

	if (val <= 0 || !pixel_clock || !line_length || !framerate_factor ||
	    !active_height ||
	    active_height > OV64B40_MAX_FRAME_LENGTH - OV64B40_EXPOSURE_MARGIN)
		return -EINVAL;
	if (check_mul_overflow(pixel_clock, (u64)framerate_factor, &scaled_clock))
		return -ERANGE;

	/* Keep the signed control's positive 64-bit denominator intact. */
	vts = div64_u64(div_u64(scaled_clock, line_length), (u64)val);
	*frame_length = clamp_t(u64, vts, ov64b40_min_frame_length(active_height),
				OV64B40_MAX_FRAME_LENGTH) & ~1U;

	return 0;
}

static inline s64 ov64b40_scaled_framerate(u32 fps, u32 framerate_factor)
{
	/* fps comes from the bounded, static frmfmt list. */
	return (u64)fps * framerate_factor;
}

#endif /* __OV64B40_H__ */
