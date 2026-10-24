# Camera Support

Tested camera sensors, overlay names as they appear to jetson-io, verification commands, and known issues. To build, install, or write a camera device-tree overlay, see [camera_overlays.md](camera_overlays.md).

## Tested Cameras

| Sensor | Lanes | Resolution | Overlays | Status |
|--------|-------|------------|----------|--------|
| IMX219 | 2     | 3280x2464  | dual (JAJ/PAB_V3), quad (PAB) | Working |
| IMX477 | 2     | 4056x3040  | dual (JAJ/PAB_V3), quad (PAB) | Working |
| IMX708 | 2     | 4608x2592  | dual (JAJ/PAB_V3), quad (PAB) | Working |
| OV64B40 (FLIR Hadron EO) | 4 or 2 | 9248x6944 | one per CSI port | No driver yet |

Each carrier ships exactly one overlay per sensor: dual on JAJ / PAB_V3 (two CSI ports), quad on PAB (four CSI ports). The FLIR Hadron EO is the exception: it is one camera, so it ships one overlay per port. The image already includes every overlay for that product; switch with `jetson-io` (details in [camera_overlays.md](camera_overlays.md)). Each carrier also bakes an IMX219 overlay at flash time so cameras work on first boot without a jetson-io step (quad on PAB, dual on JAJ and PAB_V3).

## IMX219 (Sony, 8MP)

Tested and working in 2-lane mode.

- **PAB**: Tested on all 4 CSI ports (quad overlay)
- **JAJ / PAB_V3**: Tested on both CSI ports (dual overlay)

### Overlays

| Overlay | Filename | Ports |
|---------|----------|-------|
| Dual    | `tegra234-p3767-camera-p3768-imx219-dual.dtbo` | CAM0 + CAM1 (JAJ/PAB_V3 default) |
| Quad    | `tegra234-p3767-camera-p3768-imx219-quad.dtbo` | All 4 ports (PAB default) |

## IMX477 (Sony Starvis, 12.3MP)

Tested and working in 2-lane mode on all carrier boards.

### Overlays

| Overlay | Filename | Ports |
|---------|----------|-------|
| Dual    | `tegra234-p3767-camera-p3768-imx477-dual.dtbo` | CAM0 + CAM1 (JAJ/PAB_V3) |
| Quad    | `tegra234-p3767-camera-p3768-imx477-quad.dtbo` | All 4 ports (PAB) |

The quad overlay is new and not yet hardware-validated: it pairs the port wiring of the IMX219 quad (the PAB default) with the sensor modes of the retired single overlay.

### 4-Lane Mode (Not Working)

IMX477 4-lane overlays have been removed. While the Sony IMX477 sensor silicon supports 4 lanes, the RidgeRun `nv_imx477` driver's 4-lane register initialization tables are incorrect. Getting correct values requires access to the Sony sensor NDA documentation. NVIDIA has acknowledged their own `imx477-dual-4lane.dts` reference overlay is broken.

2-lane mode provides full 12MP at 30fps which is sufficient for most use cases.

## IMX708 (Sony, 12MP — Raspberry Pi / Arducam Camera Module 3)

Driver is RidgeRun's `nv_imx708`, vendored under `kernel_overlay/`. One 10-bit mode: 4608x2592 @ ~14 fps, fixed focus.

### Overlays

| Overlay | Filename | Ports |
|---------|----------|-------|
| Dual    | `tegra234-p3767-camera-p3768-imx708-dual.dtbo` | CAM0 + CAM1 (JAJ/PAB_V3) |
| Quad    | `tegra234-p3767-camera-p3768-imx708-quad.dtbo` | All 4 ports (PAB) |

## FLIR Hadron EO (OmniVision OV64B40, 64MP)

The visible camera of the FLIR Hadron 640R / 640R+, connected through the ARK Hadron Adapter. The Hadron's Boson thermal camera is separate: it enumerates over USB as a UVC device (`09cb:4007`) and needs no overlay.

**Status: overlays only.** Nothing binds `ovti,ov64b40` yet, so selecting an overlay creates the device-tree nodes but no `/dev/video*` and no Argus sensor.

TODO(driver): L4T has no OV64B40 driver and its register tables are not public. FLIR's Jetson package is ticket-only and targets the Jetson Nano through an ISP board. A tegracam driver under `kernel_overlay/` needs the init and mode tables from FLIR or OmniVision. Once those exist, reconcile the provisional mode values in `tegra234-camera-hadron-eo.dtsi`: `line_length`, exposure and gain limits, `pixel_phase` and `discontinuous_clk`. Also confirm that the sensor supports the 2-lane modes, or drop them.

### Overlays

| Carrier | Port | Lanes | Overlay name | Notes |
|---------|------|-------|--------------|-------|
| JAJ | CAM0 (J28) | 2 | Camera FLIR Hadron EO CAM0 2-lane | CSI0_CLK is not connected, and L4T runs x4 only on CSI0_CLK or CSI2_CLK. |
| JAJ | CAM1 (J25) | 4 | Camera FLIR Hadron EO CAM1 4-lane | |
| PAB_V3 | CAM0 (J28) | 4 | Camera FLIR Hadron EO CAM0 4-lane | |
| PAB_V3 | CAM1 (J25) | 4 | Camera FLIR Hadron EO CAM1 4-lane | |
| PAB | CAM0–CAM3 | 2 | Camera FLIR Hadron EO CAM*n* 2-lane | 15-pin connectors: needs a 22-to-15-pin FFC adapter. |

Filenames are `tegra234-p3767-camera-p3768-hadron-eo-cam<n>.dtbo`. The 4-lane overlays carry the four modes ModalAI runs on VOXL2: 4624x3472@30 (4-cell binned), 3840x2160@60, 1920x1080@30, and 9248x6944@10. The 2-lane overlays run the same per-lane rates at half the frame rate.

### Hardware notes

Bench-verified on JAJ CAM0 and CAM1 with `i2c-tools`:

- **I2C:** the sensor is at 7-bit `0x36`, with a second SCCB ID at `0x21`. Its chip ID, read 24-bit from `0x300a`, is `0x566442`. `I2C_CAM` also carries the Boson CCI at `0x6a` and EEPROMs at `0x50`/`0x51`, which answer even while the sensor is held in reset.
- **Reset:** the carrier's `CAMn_PWDN` drives Hadron `/HADRON_RST` through the adapter. High releases the sensor. FLIR says this line also resets the Boson, but a 1 s low pulse did not drop the Boson off USB.
- **No MCLK:** the Hadron connector has no clock pin, because the EO has its own oscillator. The adapter routes the carrier's `CAMn_MCLK` to Hadron pin 43 `EXT_VSYNC`, so the overlays declare no clocks. Keep the port's extperiph clock off.
- **Adapter I2C:** the adapter's ICM-45686 IMU (`0x68`) and the Hadron's internal IMU (`0x28`) are on the Hadron control I2C (JAJ J36, module I2C0), not on the camera bus.

## Selecting an overlay (runtime)

```
sudo /opt/nvidia/jetson-io/config-by-hardware.py -l
sudo /opt/nvidia/jetson-io/config-by-hardware.py -n 2="Camera IMX477 Quad"
sudo reboot
```

Use the `overlay-name` string from the list (header number for the 22pin CSI connector is typically 2). Build, custom overlays, flash-time defaults, and troubleshooting: [camera_overlays.md](camera_overlays.md).

## Test Commands

### Verify Camera Detection

```
nvargus_nvraw --lps
```

### v4l2-ctl

Capture 300 frames from /dev/video0:
```
v4l2-ctl --set-fmt-video=width=3840,height=2160,pixelformat=RG10 --stream-mmap --stream-count=300 -d /dev/video0
```

### GStreamer

A provisioned image (the build default) already ships the Tegra GStreamer plugins (`nvarguscamerasrc`, `nvvidconv`, `nvv4l2*`) via `nvidia-l4t-gstreamer`. Install `nvidia-jetpack` only if you also need the full CUDA/TensorRT compute stack.

UDP h.264 stream (replace IP/port):
```
gst-launch-1.0 nvarguscamerasrc ! nvvidconv ! \
  x264enc key-int-max=15 bitrate=2500 tune=zerolatency speed-preset=ultrafast ! \
  video/x-h264,stream-format=byte-stream ! \
  rtph264pay config-interval=1 name=pay0 pt=96 ! \
  udpsink host=192.168.0.96 port=5600 sync=false
```

Record to file:
```
gst-launch-1.0 nvarguscamerasrc ! \
  'video/x-raw(memory:NVMM), width=3840, height=2160, format=NV12, framerate=30/1' ! \
  nvvidconv ! x264enc key-int-max=15 bitrate=2500 tune=zerolatency speed-preset=ultrafast ! \
  h264parse ! mp4mux ! filesink location=output.mp4
```

Select a specific sensor (for dual camera setups):
```
gst-launch-1.0 nvarguscamerasrc sensor-id=0 ...
gst-launch-1.0 nvarguscamerasrc sensor-id=1 ...
```

## Known Issues

**JetPack 6.2.2 / R36.5.0 Argus regression** (bench-reproduced on PAB_V3/Orin NX with dual IMX219): every `nvarguscamerasrc` teardown errors (`CANCELLED` / `Argus Correctable Error Status`), and roughly 1 in 60–120 camera relaunches fails outright — no frames, then a nvargus-daemon segfault and a ~40 s video outage. Introduced by NVIDIA in the L4T 36.4.7 userspace refresh, still present in 36.5.0; the kernel/device tree/RCE firmware are not involved, and NVIDIA's forum workaround (keeping the camera RTCPU powered) does **not** prevent it on Orin NX. Fix shipped in this repo: provisioning (the build default) pins the camera userspace stack (gstreamer/camera/multimedia debs) to the last good release via `NV_CAMERA_STACK_VERSION` in `versions.env`. Full evidence, repro protocol, and bench data: [argus_relaunch_regression.md](argus_relaunch_regression.md) (issue [#107](https://github.com/ARK-Electronics/ark_jetson_kernel/issues/107)).
