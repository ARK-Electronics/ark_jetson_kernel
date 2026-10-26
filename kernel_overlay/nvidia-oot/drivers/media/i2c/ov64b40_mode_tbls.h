/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * OV64B40 register tables. The init and mode tables in ov64b40_tables.h are
 * generated from the GPL-2.0 OV64B imgsensor driver in realme's Narzo 50 / 8i
 * (MIAMI, MT6781) kernel release: 24 MHz input, MIPI D-PHY 4-lane.
 */

#ifndef __OV64B40_MODE_TBLS_H__
#define __OV64B40_MODE_TBLS_H__

#include <media/camera_common.h>
#include <media/ov64b40.h>

#define ov64b40_reg struct reg_8

#define OV64B40_TABLE_WAIT_MS	0
#define OV64B40_TABLE_END		1

#include "ov64b40_tables.h"

static const ov64b40_reg ov64b40_start[] = {
	{OV64B40_STREAM_ADDR, OV64B40_STREAM_ON},
	{OV64B40_TABLE_WAIT_MS, OV64B40_START_WAIT_MS},
	{OV64B40_TABLE_END, 0x00}
};

static const ov64b40_reg ov64b40_stop[] = {
	{OV64B40_STREAM_ADDR, OV64B40_STREAM_OFF},
	{OV64B40_TABLE_END, 0x00}
};

/* The first four entries must remain in DT mode0..mode3 order. */
enum {
	OV64B40_MODE_4624x3468,
	OV64B40_MODE_3840x2160,
	OV64B40_MODE_2304x1296,
	OV64B40_MODE_9248x6936,
	OV64B40_MODE_COMMON,
	OV64B40_START_STREAM,
	OV64B40_STOP_STREAM,
};

static const ov64b40_reg *mode_table[] = {
	[OV64B40_MODE_4624x3468] = ov64b40_mode_4624x3468,
	[OV64B40_MODE_3840x2160] = ov64b40_mode_3840x2160,
	[OV64B40_MODE_2304x1296] = ov64b40_mode_2304x1296,
	[OV64B40_MODE_9248x6936] = ov64b40_mode_9248x6936,
	[OV64B40_MODE_COMMON] = ov64b40_global,
	[OV64B40_START_STREAM] = ov64b40_start,
	[OV64B40_STOP_STREAM] = ov64b40_stop,
};

static const int ov64b40_30_fr[] = {
	30,
};

static const int ov64b40_60_fr[] = {
	60,
};

static const int ov64b40_120_fr[] = {
	120,
};

static const int ov64b40_13_fr[] = {
	13,
};

static const struct camera_common_frmfmt ov64b40_frmfmt[] = {
	{{4624, 3468}, ov64b40_30_fr, 1, 0, OV64B40_MODE_4624x3468},
	{{3840, 2160}, ov64b40_60_fr, 1, 0, OV64B40_MODE_3840x2160},
	{{2304, 1296}, ov64b40_120_fr, 1, 0, OV64B40_MODE_2304x1296},
	{{9248, 6936}, ov64b40_13_fr, 1, 0, OV64B40_MODE_9248x6936},
};

#endif /* __OV64B40_MODE_TBLS_H__ */
