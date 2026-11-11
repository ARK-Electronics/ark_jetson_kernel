# Camera Support

Here you will find the camera sensors tested on ARK carriers, the name each overlay has in `jetson-io`, the commands to test a camera, and the known issues. To build, install or write a camera overlay, see [camera_overlays.md](camera_overlays.md).

## Tested Cameras

| Sensor | Lanes | Resolution | Overlays | Status |
|--------|-------|------------|----------|--------|
| IMX219 | 2     | 3280x2464  | dual (JAJ/PAB_V3), quad (PAB) | Working |
| IMX477 | 2     | 4056x3040  | dual (JAJ/PAB_V3), quad (PAB) | Working |
| IMX708 | 2     | 4608x2592  | dual (JAJ/PAB_V3), quad (PAB) | Working |
| [OV64B40 (FLIR Hadron EO)](hadron.md) | 4 or 2 | 9248x6936 | one per CSI port | Working |

Each carrier ships one overlay per sensor. JAJ and PAB_V3 have two CSI ports, so theirs is a dual overlay. PAB has four, so its overlay is a quad. The FLIR Hadron EO is the exception. It is a single camera, so it ships one overlay for each port.

The image already includes every overlay for its product, and you switch between them with `jetson-io`, as [camera_overlays.md](camera_overlays.md) describes. Each carrier also has an IMX219 overlay applied at flash time, so IMX219 cameras work on the first boot without a `jetson-io` step. That default is the quad on PAB and the dual on JAJ and PAB_V3.

## IMX219 (Sony, 8MP)

The IMX219 works in 2-lane mode. It has been tested on all 4 CSI ports of PAB with the quad overlay, and on both CSI ports of JAJ and PAB_V3 with the dual overlay.

### Overlays

| Overlay | Filename | Ports |
|---------|----------|-------|
| Dual    | `tegra234-p3767-camera-p3768-imx219-dual.dtbo` | CAM0 + CAM1 (JAJ/PAB_V3 default) |
| Quad    | `tegra234-p3767-camera-p3768-imx219-quad.dtbo` | All 4 ports (PAB default) |

## IMX477 (Sony Starvis, 12.3MP)

The IMX477 works in 2-lane mode on all carrier boards.

### Overlays

| Overlay | Filename | Ports |
|---------|----------|-------|
| Dual    | `tegra234-p3767-camera-p3768-imx477-dual.dtbo` | CAM0 + CAM1 (JAJ/PAB_V3) |
| Quad    | `tegra234-p3767-camera-p3768-imx477-quad.dtbo` | All 4 ports (PAB) |

The quad overlay is new and has not been tested on hardware yet. It pairs the port wiring of the IMX219 quad, the PAB default, with the sensor modes of the retired single overlay.

### 4-Lane Mode (Not Working)

The IMX477 4-lane overlays have been removed. The sensor itself supports 4 lanes, but the 4-lane register tables in RidgeRun's `nv_imx477` driver are wrong. Correct values need Sony's sensor documentation, which is under NDA. NVIDIA has acknowledged that its own `imx477-dual-4lane.dts` reference overlay is broken.

2-lane mode still gives you the full 12MP at 30fps.

## IMX708 (Sony, 12MP — Raspberry Pi / Arducam Camera Module 3)

The driver is RidgeRun's `nv_imx708`, which lives under `kernel_overlay/`. It has one 10-bit mode, 4608x2592 at about 14 fps, with fixed focus.

### Overlays

| Overlay | Filename | Ports |
|---------|----------|-------|
| Dual    | `tegra234-p3767-camera-p3768-imx708-dual.dtbo` | CAM0 + CAM1 (JAJ/PAB_V3) |
| Quad    | `tegra234-p3767-camera-p3768-imx708-quad.dtbo` | All 4 ports (PAB) |

## Selecting an overlay (runtime)

List the overlays that `jetson-io` offers, select one by name, then reboot:

```
sudo /opt/nvidia/jetson-io/config-by-hardware.py -l
sudo /opt/nvidia/jetson-io/config-by-hardware.py -n 2="Camera IMX477 Quad"
sudo reboot
```

Use the overlay's name exactly as the list shows it. The number before the `=` is the header number of the 22pin CSI connector, which is usually 2. [camera_overlays.md](camera_overlays.md) covers building overlays, writing your own, the flash-time defaults and troubleshooting.

## Test Commands

### Verify Camera Detection

This lists the sensors Argus can see:

```
nvargus_nvraw --lps
```

### v4l2-ctl

This captures 300 frames from `/dev/video0`:

```
v4l2-ctl --set-fmt-video=width=3840,height=2160,pixelformat=RG10 --stream-mmap --stream-count=300 -d /dev/video0
```

### GStreamer

A provisioned image, which is what `build.sh` makes by default, already has the Tegra GStreamer plugins `nvarguscamerasrc`, `nvvidconv` and `nvv4l2*`. They come from the `nvidia-l4t-gstreamer` package. Install `nvidia-jetpack` only if you also need the full CUDA and TensorRT stack.

This sends an H.264 stream over UDP. Change the address and port to your receiver's:

```
gst-launch-1.0 nvarguscamerasrc ! nvvidconv ! \
  x264enc key-int-max=15 bitrate=2500 tune=zerolatency speed-preset=ultrafast ! \
  video/x-h264,stream-format=byte-stream ! \
  rtph264pay config-interval=1 name=pay0 pt=96 ! \
  udpsink host=192.168.0.96 port=5600 sync=false
```

This records to a file:

```
gst-launch-1.0 nvarguscamerasrc ! \
  'video/x-raw(memory:NVMM), width=3840, height=2160, format=NV12, framerate=30/1' ! \
  nvvidconv ! x264enc key-int-max=15 bitrate=2500 tune=zerolatency speed-preset=ultrafast ! \
  h264parse ! mp4mux ! filesink location=output.mp4
```

With more than one camera, pick the sensor with `sensor-id`:

```
gst-launch-1.0 nvarguscamerasrc sensor-id=0 ...
gst-launch-1.0 nvarguscamerasrc sensor-id=1 ...
```

## Known Issues

**Argus regression in JetPack 6.2.2 / R36.5.0.** With NVIDIA's camera packages for this release, every `nvarguscamerasrc` teardown ends in an error, `CANCELLED` or `Argus Correctable Error Status`. Roughly 1 in 60–120 camera relaunches fails outright: no frames arrive, then nvargus-daemon segfaults and video is out for about 40 s. It reproduces on the bench on a PAB_V3 with an Orin NX and dual IMX219.

NVIDIA introduced it in the L4T 36.4.7 userspace refresh, and it is still present in 36.5.0. The kernel, the device tree and the RCE firmware are not involved. NVIDIA's forum workaround, keeping the camera RTCPU powered, does **not** prevent it on Orin NX.

This repo ships a fix. Provisioning, which `build.sh` does by default, pins the camera userspace packages for gstreamer, camera and multimedia to the last good release, through `NV_CAMERA_STACK_VERSION` in `versions.env`. [argus_relaunch_regression.md](argus_relaunch_regression.md) has the full evidence, the steps to reproduce it and the bench data. The issue is [#107](https://github.com/ARK-Electronics/ark_jetson_kernel/issues/107).
