# Camera Support

This document covers tested camera sensors, available device tree overlays, and test commands.

## Tested Cameras

| Sensor | Lanes | Resolution | Overlays | Status |
|--------|-------|------------|----------|--------|
| IMX477 | 2     | 4056x3040  | single, dual | Working |
| IMX219 | 2     | 3280x2464  | single, dual, quad (PAB only) | Working |

## IMX219 (Sony, 8MP)

Tested and working in 2-lane mode.

- **PAB**: Tested on all 4 CSI ports (quad overlay)
- **JAJ / PAB_V3**: Tested on both CSI ports (dual overlay)

### Overlays

| Overlay | Filename | Ports |
|---------|----------|-------|
| Single  | `tegra234-p3767-camera-p3768-ark-imx219-single.dtbo` | CAM0 |
| Dual    | `tegra234-p3767-camera-p3768-imx219-dual.dtbo` | CAM0 + CAM1 |
| Quad    | `tegra234-p3767-camera-p3768-ark-imx219-quad.dtbo` | All 4 ports (PAB only) |

## IMX477 (Sony Starvis, 12.3MP)

Tested and working in 2-lane mode on all carrier boards.

### Overlays

| Overlay | Filename | Ports |
|---------|----------|-------|
| Single  | `tegra234-p3767-camera-p3768-ark-imx477-single.dtbo` | CAM0 |
| Dual    | `tegra234-p3767-camera-p3768-imx477-dual.dtbo` | CAM0 + CAM1 |

### 4-Lane Mode (Not Working)

IMX477 4-lane overlays have been removed. While the Sony IMX477 sensor silicon supports 4 lanes, the RidgeRun `nv_imx477` driver's 4-lane register initialization tables are incorrect. Getting correct values requires access to the Sony sensor NDA documentation. NVIDIA has acknowledged their own `imx477-dual-4lane.dts` reference overlay is broken.

2-lane mode provides full 12MP at 30fps which is sufficient for most use cases.

## Installing a Camera Overlay

Build the overlay DTBs (from host):
```
./build_kernel.sh
```

Copy the overlay to the Jetson:
```
DTB_PATH="source_build/Linux_for_Tegra/source/kernel-devicetree/generic-dts/dtbs"
scp $DTB_PATH/<overlay>.dtbo jetson@192.168.55.1:~
```

On the Jetson, install and activate:
```
sudo mv <overlay>.dtbo /boot
sudo /opt/nvidia/jetson-io/config-by-hardware.py -n 2="Camera ARK IMX477 Single"
sudo reboot
```

List available overlays:
```
sudo /opt/nvidia/jetson-io/config-by-hardware.py -l
```

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

Install GStreamer (if not already present):
```
sudo apt-get install nvidia-jetpack -y
```

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
