# FLIR Hadron scripts

| Path | What |
|------|------|
| `demo.sh`, `demo/` | Browser demo: both cameras and their settings on one page. See [docs/hadron.md](../../../docs/hadron.md#demo) |
| `view/` | Live view of the EO and the Boson, with the adapter's ICM-45686 plotted against the frame timing |
| `gen_ov64b40_tables.py` | Generates the driver's `ov64b40_tables.h` |

The camera, its overlays and the frame sync: [docs/hadron.md](../../../docs/hadron.md).

## Live view

The EO and the Boson side by side, fullscreen on the Jetson's display. A panel beside them plots the adapter's ICM-45686 against the frame timing. Every timestamp is on `CLOCK_MONOTONIC`, so the panel shows where IMU samples land within each EO exposure and relative to each IR frame's sync pulse.

Install on the Jetson, which needs `gcc` and `xdotool`:

```
scp -r scripts/camera/hadron/view jetson@<ip>:hadron-view
ssh jetson@<ip> hadron-view/install-launcher.sh
```

Run it from the desktop launcher, or with `start.sh` over SSH. Quit with q, Esc or a right click, or with `stop.sh` over SSH. Status lines go to `view.log`.

- **Driver:** if `nv_ov64b40` isn't loaded, `start.sh` loads it after a password prompt. A locally built `nv_ov64b40.ko` next to the scripts takes precedence over the installed module.
- **EO:** mode 2 (2304x1296) at 25 fps, so each frame spans a whole number of 200 Hz IMU samples. There is no ISP tuning, so the viewer renders the raw Bayer itself: 2x2 binning, gray-world white balance, auto exposure and gamma.
- **Latency:** about 11 ms for the EO from mid-exposure (about 20 ms on a 2-lane port, where the readout takes twice as long), and about 23 ms for the IR from its sync pulse (13 ms from its UVC timestamp). Both are measured up to the display sink, before the wait for vsync. The panel shows them live.
- **IMU:** found by its ID on whichever I2C connector the adapter's cable is on, and polled at 200 Hz, because INT1 is not wired.
- **Frame sync:** while the view runs, the Boson is the Hadron's sync master, and quitting puts back the mode it was in. The ICM-45686 tags each pulse through FSYNC, and the Jetson timestamps the same pulses on the port's MCLK pad. A line fitted between the two puts the IMU samples on `CLOCK_MONOTONIC` to about 2 µs rms. Host read times alone place them about 0.3 ms late. `--sync-line` names a GPIO line other than the overlay's pad (CAM0 is `PP.00`), and `--no-sync` leaves the Boson alone.

## Register tables

```
scripts/camera/hadron/gen_ov64b40_tables.py ov64b_Sensor_setting.h \
  kernel_overlay/nvidia-oot/drivers/media/i2c/ov64b40_tables.h
```

The input is `drivers/misc/mediatek/imgsensor/src/common/v1/miami_ov64b/ov64b_Sensor_setting.h` from realme's GPL-2.0 [Narzo 50 / 8i kernel release](https://github.com/realme-kernel-opensource/realme_Narzo-50_8i-AndroidT-kernel-source), commit `3c6fe902b4a5`.
