# FLIR Hadron EO (OmniVision OV64B40, 64MP)

The FLIR Hadron 640R and 640R+ carry two cameras. The visible one, the EO, connects to an ARK carrier through the ARK Hadron Adapter and needs the driver and overlays below. The thermal one, the Boson, shows up over USB as a UVC device (`09cb:4007`) and needs no overlay.

**Status:** raw capture through V4L2 works in all four modes, and you can set exposure, gain and frame rate. Argus (`nvarguscamerasrc`) works in all four modes too, with the ISP's default colour. Both have been bench-tested on a JAJ only, on CAM1 with four lanes and on CAM0 with two.

## Driver

The driver is `nv_ov64b40`, and it lives under `kernel_overlay/`. Its register tables are generated from the OV64B imgsensor driver in realme's GPL-2.0 Narzo 50 / 8i kernel release, which runs a 24 MHz input clock on a 4-lane D-PHY link. [scripts/camera/hadron](../scripts/camera/hadron/README.md) says how to regenerate them. The Hadron's own oscillator is also 24 MHz, so every mode runs at the rate it was designed for.

On a 2-lane port the driver switches the sensor to two MIPI lanes and doubles each mode's line length. Every mode then keeps its lane rate and runs at half the frame rate. The lane switch is register `0x3012` set to `0x21`, the value the ov08x40 driver of the same generation uses.

| Mode | Size | fps, 4-lane | fps, 2-lane | Lane rate | Notes |
|------|------|-------------|-------------|-----------|-------|
| 0 | 4624x3468 | 30 | 15 | 1699.2 Mbps | 4-cell binned (default) |
| 1 | 3840x2160 | 60 | 30 | 2300 Mbps | |
| 2 | 2304x1296 | 120 | 60 | 1699.2 Mbps | |
| 3 | 9248x6936 | 13.2 | 6.6 | 2400 Mbps | Full array, on-sensor remosaic |

The full-array mode uses default crosstalk values. The Hadron's EEPROMs read blank, so there is no per-module calibration to load.

## Overlays

There is one overlay for each CSI port the adapter can plug into. Apply the one for your port with `jetson-io`.

| Carrier | Port | Lanes | Overlay name | Notes |
|---------|------|-------|--------------|-------|
| JAJ | CAM0 (J28) | 2 | Camera FLIR Hadron EO CAM0 2-lane | CSI0_CLK is not connected, and L4T runs x4 only on CSI0_CLK or CSI2_CLK. Bench-tested |
| JAJ | CAM1 (J25) | 4 | Camera FLIR Hadron EO CAM1 4-lane | Bench-tested |
| PAB_V3 | CAM0 (J28) | 4 | Camera FLIR Hadron EO CAM0 4-lane | Untested on hardware |
| PAB_V3 | CAM1 (J25) | 4 | Camera FLIR Hadron EO CAM1 4-lane | Untested on hardware |
| PAB | CAM0–CAM3 | 2 | Camera FLIR Hadron EO CAM*n* 2-lane | 15-pin connectors: needs a 22-to-15-pin FFC adapter. Untested on hardware |

The overlay files are named `tegra234-p3767-camera-p3768-hadron-eo-cam<n>.dtbo`, where `<n>` is the port number.

## Capture

The EO appears as a video device named `vi-output, ov64b40`. Its `/dev/video*` number depends on whether the Boson's UVC nodes come up first, so list the devices to find it. The second command captures 30 raw frames in mode 0. Put the number you found in place of `N`.

```
v4l2-ctl --list-devices
v4l2-ctl -d /dev/videoN --set-fmt-video=width=4624,height=3468,pixelformat=BG10 \
  --set-ctrl bypass_mode=0,sensor_mode=0,exposure=8000,gain=32 \
  --stream-mmap --stream-count=30 --stream-to=hadron.raw
```

- **Pixel format:** frames are BGGR RAW10. Each sample sits in the top ten bits of a 16-bit word, so shift it right by 6 to read it (`value >> 6`).
- **Exposure and gain:** exposure is in microseconds. Gain is in units of 1/16, so 16 is 1x and 248 is 15.5x.
- **Frame rate:** `frame_rate` is the rate in fps × 10^6. Each mode runs at the rate in the table above until you set one.
- **Setting controls:** you can set all three before streaming or while streaming. A value you set carries over to every mode, limited to that mode's range. Reading a control gives you the value the sensor is using.

## Argus

Argus runs the sensor through the Jetson's ISP. This pipeline records mode 2 at 60 fps, scaled to 720p, into `hadron.mp4`:

```
gst-launch-1.0 -e nvarguscamerasrc sensor-id=0 sensor-mode=2 ! \
  'video/x-raw(memory:NVMM),width=2304,height=1296,framerate=60/1' ! \
  nvvidconv ! 'video/x-raw,format=I420,width=1280,height=720' ! \
  x264enc tune=zerolatency speed-preset=ultrafast bitrate=6000 ! h264parse ! mp4mux ! \
  filesink location=hadron.mp4
```

- **Modes:** `sensor-mode` is the mode number from the table above. The frame rate in the caps can be anything up to that mode's rate on your port.
- **Full array:** Argus keeps twelve raw frames in memory, and in mode 3 they take 1.5 GB. An Orin Nano 4GB therefore runs mode 3 only when the caps ask for a smaller picture, such as `width=1280,height=960`, which Argus then scales. That leaves a few hundred MB free with the desktop running, and about 800 MB after `sudo systemctl stop gdm`. At full size the output buffers do not fit, and the pipeline fails with `InsufficientMemory`. An Orin NX 16GB runs mode 3 at full size. Scale in the caps and not in `nvvidconv`, where an 8x downscale leaves bands in the picture.
- **120 fps:** mode 2 on a 4-lane port delivers about 100 fps through Argus. Run `sudo jetson_clocks` first to get the full rate.
- **Sensor ID:** the Hadron EO is always `sensor-id=0`, because each overlay disables the other camera modules that the image enables at flash time.
- **ISP file:** Argus has no tuning for this sensor. [ark_hadron_rear_OV64B40.isp](../camera_isp/ark_hadron_rear_OV64B40.isp) sets the black level to 64, and without it the picture looks hazy. Colour is still the ISP's default. The image installs the file for you. If your image was built before it did, install the file by hand:

```
sudo install -m 664 -o root -g root camera_isp/ark_hadron_rear_OV64B40.isp /var/nvidia/nvcam/settings/
sudo systemctl restart nvargus-daemon
```

## Demo

The demo shows the visible and thermal cameras side by side in your browser, and you can change their settings while you watch. Run it from your PC, in a checkout of this repo:

```
scripts/camera/hadron/demo.sh jetson@jetson.local
```

The script copies the demo to the Jetson over SSH, starts it there and opens `http://jetson.local:8642/` in your browser. Press Ctrl-C in the terminal to stop it. If your Jetson has a different login or address, change `jetson@jetson.local` to match.

On the page you can:

- Change the visible camera's mode and frame rate, use auto exposure or set the exposure and gain yourself, and adjust white balance and saturation.
- Pick a colour palette for the thermal camera and run its flat-field correction.
- See the exposure, gain and frame rate the visible camera is running at.
- See the Hadron's roll and pitch on an attitude indicator. They come from the IMU on the adapter.
- Turn on **Boson drives sync** to make the thermal camera the frame-sync master. The page then counts the sync pulses that reach the Jetson and the ones the IMU tags.

The visible camera's picture comes through Argus, so it uses the ISP file described above. Its stream restarts when you change the mode, the frame rate or the exposure. When the demo stops, it puts the thermal camera's palette and sync mode back as it found them.

## Live view

The live view in `scripts/camera/hadron/view/` shows the EO and the Boson side by side on the Jetson's own display. A panel beside them plots the adapter's ICM-45686 against the frame timing. [scripts/camera/hadron](../scripts/camera/hadron/README.md) says how to install and run it.

## Hardware notes

- **I2C:** the sensor answers at 7-bit address `0x36`, and at a second SCCB ID, `0x21`. Its chip ID is `0x566442`, read as 24 bits from `0x300a`. `I2C_CAM` also carries the Boson CCI at `0x6a` and EEPROMs at `0x50`/`0x51`, which answer even while the sensor is held in reset.
- **Reset:** the carrier's `CAMn_PWDN` drives Hadron `/HADRON_RST` through the adapter, and high releases the sensor. FLIR documents the line as shared with the Boson, so the driver releases it once and never drives it low. Standby is done in software, through register `0x0100`.
- **No MCLK:** the Hadron connector has no clock pin, because the EO has its own oscillator. The adapter routes the carrier's `CAMn_MCLK` to Hadron pin 43 `EXT_VSYNC` instead, so the overlays declare no clocks and make that pad an input. [Frame sync](#frame-sync) has the details.
- **Orientation:** with the tables' phone-module mirror and flip, the EO reads out rotated 180° relative to the Boson in the same housing. The overlays set `rotation = <180>`, and the driver undoes both while keeping BGGR.
- **Adapter I2C:** the Hadron has no IMU of its own, so the adapter carries an ICM-45686 at `0x68`. It is on the Hadron control I2C, not on the camera bus. That cable goes to either I2C connector of the carrier, and the scripts find the IMU by its ID. Another device answers at `0x28` on the same bus.
- **IMU rate:** the scripts poll the IMU at 200 Hz. The I2C0 connector's bus runs at 100 kHz, which carries accel and gyro at up to 400 Hz. At 400 kHz, the speed of the I2C1 connector's bus, 1600 Hz held on the bench.

## Frame sync

Hadron pin 43 `EXT_VSYNC` is the Hadron's frame-sync line. On the ARK Hadron Adapter, R11 joins it to the carrier's `CAMn_MCLK`, and R12 joins it to the ICM-45686's FSYNC pin.

- **Boson:** you set the Boson's ext sync mode over its USB serial port, and it lasts until the Hadron is power-cycled. The Boson powers on with sync disabled, and it then holds the line low. As master it puts out a short high pulse for each 60 Hz core frame. As slave its frames follow pulses on the line, and it sends none without them.
- **EO:** with these tables the OV64B40 neither drives the line nor follows it. It free-runs on its own oscillator.
- **IR frames:** with the Boson's smart averager on, USB carries 30 fps, and each frame is the average of two core frames. A frame's UVC timestamp comes about 9.4 ms after every second pulse.
- **Carrier pad:** the bootloader leaves CAM0's and CAM1's `CAMn_MCLK` a driven clock output, which would fight the Boson as master. PAB's CAM2 and CAM3 pads boot as pulled-up inputs. Each overlay makes the port's pad a pulled-down input: CAM0 `PP.00` and CAM1 `PP.01` on every carrier, plus PAB CAM2 `PQ.05` and CAM3 `PQ.06`. You can then timestamp the pulses on that GPIO line. The setting sits in the pinmux state that jetson-io's header configurations fill, so it combines with a 40-pin header configuration.
- **IMU:** the ICM-45686 tags the first sample after each pulse with the pulse-to-sample delay, at 1 µs resolution. You have to switch its pin 9 from INT2, its power-on function, to FSYNC first.
