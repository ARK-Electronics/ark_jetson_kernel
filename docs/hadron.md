# FLIR Hadron EO (OmniVision OV64B40, 64MP)

The visible camera of the FLIR Hadron 640R / 640R+, connected through the ARK Hadron Adapter. The Hadron's Boson thermal camera is separate: it enumerates over USB as a UVC device (`09cb:4007`) and needs no overlay.

**Status:** V4L2 RAW10 capture works in all four modes, with exposure, gain and frame-rate controls. Argus (`nvarguscamerasrc`) works in all four, with the ISP's default colour. Both have been bench-tested on JAJ only, on CAM1 (4-lane) and CAM0 (2-lane).

## Driver

`nv_ov64b40`, vendored under `kernel_overlay/`. Its register tables are generated from the OV64B imgsensor driver in realme's GPL-2.0 Narzo 50 / 8i kernel release (24 MHz input, D-PHY 4-lane); see [scripts/camera/hadron](../scripts/camera/hadron/README.md). The Hadron's own oscillator is 24 MHz, so every mode runs at its designed rate. On a 2-lane port the driver sets the sensor's MIPI lane count to two (`0x3012` = `0x21`, as the same-generation ov08x40 driver does) and doubles each mode's line length. Every mode then keeps its lane rate and runs at half the frame rate.

| Mode | Size | fps, 4-lane | fps, 2-lane | Lane rate | Notes |
|------|------|-------------|-------------|-----------|-------|
| 0 | 4624x3468 | 30 | 15 | 1699.2 Mbps | 4-cell binned (default) |
| 1 | 3840x2160 | 60 | 30 | 2300 Mbps | |
| 2 | 2304x1296 | 120 | 60 | 1699.2 Mbps | |
| 3 | 9248x6936 | 13.2 | 6.6 | 2400 Mbps | Full array, on-sensor remosaic |

The full-array mode uses default crosstalk values. The Hadron's EEPROMs read blank, so there is no per-module calibration to load.

## Overlays

| Carrier | Port | Lanes | Overlay name | Notes |
|---------|------|-------|--------------|-------|
| JAJ | CAM0 (J28) | 2 | Camera FLIR Hadron EO CAM0 2-lane | CSI0_CLK is not connected, and L4T runs x4 only on CSI0_CLK or CSI2_CLK. Bench-tested |
| JAJ | CAM1 (J25) | 4 | Camera FLIR Hadron EO CAM1 4-lane | Bench-tested |
| PAB_V3 | CAM0 (J28) | 4 | Camera FLIR Hadron EO CAM0 4-lane | Untested on hardware |
| PAB_V3 | CAM1 (J25) | 4 | Camera FLIR Hadron EO CAM1 4-lane | Untested on hardware |
| PAB | CAM0–CAM3 | 2 | Camera FLIR Hadron EO CAM*n* 2-lane | 15-pin connectors: needs a 22-to-15-pin FFC adapter. Untested on hardware |

Filenames are `tegra234-p3767-camera-p3768-hadron-eo-cam<n>.dtbo`.

## Capture

The Hadron EO is a `vi-output, ov64b40` node. Its `/dev/video*` number depends on whether the Boson's UVC nodes enumerate first.

```
v4l2-ctl --list-devices
v4l2-ctl -d /dev/videoN --set-fmt-video=width=4624,height=3468,pixelformat=BG10 \
  --set-ctrl bypass_mode=0,sensor_mode=0,exposure=8000,gain=32 \
  --stream-mmap --stream-count=30 --stream-to=hadron.raw
```

- **Pixel format:** BGGR RAW10, stored MSB-aligned in 16-bit words (`value >> 6`).
- **Exposure and gain:** exposure is in microseconds. Gain is in units of 1/16, so 16 is 1x and 248 is 15.5x.
- **Frame rate:** `frame_rate` is fps × 10^6. Each mode starts at its own rate until a rate is set.
- **Setting controls:** all three can be set before streaming or while streaming. A set value carries to every mode, limited to that mode's range. Each control reads the value in use.

## Argus

```
gst-launch-1.0 -e nvarguscamerasrc sensor-id=0 sensor-mode=2 ! \
  'video/x-raw(memory:NVMM),width=2304,height=1296,framerate=60/1' ! \
  nvvidconv ! 'video/x-raw,format=I420,width=1280,height=720' ! \
  x264enc tune=zerolatency speed-preset=ultrafast bitrate=6000 ! h264parse ! mp4mux ! \
  filesink location=hadron.mp4
```

- **Modes:** `sensor-mode` is the mode number in the table above, and the caps take a frame rate up to that mode's on the port.
- **Full array:** mode 3 runs on an Orin NX 16GB. On an Orin Nano 4GB Argus runs out of memory at about 1.5 GB of buffers. For a smaller picture, put the size in the caps so Argus scales it: an 8x downscale in `nvvidconv` leaves bands.
- **120 fps:** mode 2 on a 4-lane port delivers about 100 fps through Argus unless `sudo jetson_clocks` is running.
- **Sensor ID:** each overlay disables the other camera modules of the flash-time default, so the Hadron EO is `sensor-id=0`.
- **ISP file:** Argus has no tuning for this sensor. [ark_hadron_rear_OV64B40.isp](../camera_isp/ark_hadron_rear_OV64B40.isp) sets its black level of 64, without which the picture is hazy. Colour is still the ISP's default. The image installs the file. On an image built before it did:

```
sudo install -m 664 -o root -g root camera_isp/ark_hadron_rear_OV64B40.isp /var/nvidia/nvcam/settings/
sudo systemctl restart nvargus-daemon
```

## Live view

`scripts/camera/hadron/view/` shows the EO and the Boson side by side on the Jetson's display, with the adapter's ICM-45686 plotted against the frame timing. Install and use: [scripts/camera/hadron](../scripts/camera/hadron/README.md).

## Hardware notes

- **I2C:** the sensor is at 7-bit `0x36`, with a second SCCB ID at `0x21`. Its chip ID, read 24-bit from `0x300a`, is `0x566442`. `I2C_CAM` also carries the Boson CCI at `0x6a` and EEPROMs at `0x50`/`0x51`, which answer even while the sensor is held in reset.
- **Reset:** the carrier's `CAMn_PWDN` drives Hadron `/HADRON_RST` through the adapter, and high releases the sensor. FLIR documents the line as shared with the Boson, so the driver releases it once and never drives it low. Standby is software (`0x0100`).
- **No MCLK:** the Hadron connector has no clock pin, because the EO has its own oscillator. The adapter routes the carrier's `CAMn_MCLK` to Hadron pin 43 `EXT_VSYNC` instead, so the overlays declare no clocks and make that pad an input (see [Frame sync](#frame-sync)).
- **Orientation:** with the tables' phone-module mirror and flip, the EO reads out rotated 180° relative to the Boson in the same housing. The overlays set `rotation = <180>`, and the driver undoes both while keeping BGGR.
- **Adapter I2C:** the adapter's ICM-45686 IMU (`0x68`) is on the Hadron control I2C, not on the camera bus. Its cable goes to either I2C connector of the carrier, and the scripts find the IMU by its ID. A second device answers at `0x28`. It is not the Hadron's own ICM-20602, which does not answer on this bus.
- **IMU rate:** the scripts poll the IMU at 200 Hz. The I2C0 connector's bus runs at 100 kHz, which carries accel and gyro at up to 400 Hz. With that bus at 400 kHz, the I2C1 connector's speed, 1600 Hz held on the bench.

## Frame sync

Hadron pin 43 `EXT_VSYNC` is the Hadron's frame-sync line. On the ARK Hadron Adapter, R11 joins it to the carrier's `CAMn_MCLK`, and R12 joins it to the ICM-45686's FSYNC pin.

- **Boson:** the ext sync mode is set over its USB serial port, and it lasts until the Hadron is power-cycled. When disabled (its power-on setting), the Boson holds the line low. As master it outputs a short high pulse for each 60 Hz core frame. As slave its frames follow pulses on the line, and it sends none without them.
- **EO:** with these tables the OV64B40 neither drives the line nor follows it. It free-runs on its own oscillator.
- **IR frames:** with the Boson's smart averager on, USB carries 30 fps, each frame the average of two core frames. Each frame's UVC timestamp comes about 9.4 ms after every second pulse.
- **Carrier pad:** the bootloader leaves `CAMn_MCLK` a driven clock output, which would fight the Boson as master. Each overlay makes the port's pad a pulled-down input: CAM0 `PP.00` and CAM1 `PP.01` on every carrier, plus PAB CAM2 `PQ.05` and CAM3 `PQ.06`. The pulses can then be timestamped on that GPIO line. The setting sits in the pinmux state that jetson-io's header configurations fill, so it combines with a 40-pin header configuration.
- **IMU:** the ICM-45686 tags the first sample after each pulse with the pulse-to-sample delay, at 1 µs resolution. Its pin 9 has to be switched from the power-on INT2 function to FSYNC first.
