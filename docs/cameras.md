# Camera Support

This document covers tested camera sensors, available device tree overlays, and test commands.

## Tested Cameras

| Sensor | Lanes | Resolution | Overlays | Status |
|--------|-------|------------|----------|--------|
| IMX219 | 2     | 3280x2464  | dual (JAJ/PAB_V3/PAB_CAN), quad (PAB) | Working |
| IMX477 | 2     | 4056x3040  | dual (JAJ/PAB_V3/PAB_CAN), quad (PAB) | Working |
| IMX708 | 2     | 4608x2592  | dual (JAJ/PAB_V3/PAB_CAN), quad (PAB) | Working |

Each carrier ships exactly one overlay per sensor: dual on JAJ / PAB_V3 / PAB_CAN (two CSI ports), quad on PAB (four CSI ports).

## IMX219 (Sony, 8MP)

Tested and working in 2-lane mode.

- **PAB**: Tested on all 4 CSI ports (quad overlay)
- **JAJ / PAB_V3**: Tested on both CSI ports (dual overlay)
- **PAB_CAN**: same dev-kit CSI wiring as PAB_V3 (dual overlay); not yet bench-tested

### Overlays

| Overlay | Filename | Ports |
|---------|----------|-------|
| Dual    | `tegra234-p3767-camera-p3768-imx219-dual.dtbo` | CAM0 + CAM1 (JAJ/PAB_V3/PAB_CAN default) |
| Quad    | `tegra234-p3767-camera-p3768-imx219-quad.dtbo` | All 4 ports (PAB default) |

## IMX477 (Sony Starvis, 12.3MP)

Tested and working in 2-lane mode on all carrier boards.

### Overlays

| Overlay | Filename | Ports |
|---------|----------|-------|
| Dual    | `tegra234-p3767-camera-p3768-imx477-dual.dtbo` | CAM0 + CAM1 (JAJ/PAB_V3/PAB_CAN) |
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
| Dual    | `tegra234-p3767-camera-p3768-imx708-dual.dtbo` | CAM0 + CAM1 (JAJ/PAB_V3/PAB_CAN) |
| Quad    | `tegra234-p3767-camera-p3768-imx708-quad.dtbo` | All 4 ports (PAB) |

## Installing a Camera Overlay

A full flash already includes every overlay — `build.sh` copies them into the image's `/boot`, so after flashing you can skip straight to `jetson-io` below. The build-and-copy steps here are for **iterating on an overlay without reflashing**: rebuild the `.dtbo`, drop it on the running target, and re-select it.

Each carrier ships with an IMX219 overlay baked into the image at flash time, so cameras work on the first boot with no `jetson-io` step: the quad overlay on PAB, the dual overlay on JAJ and PAB_V3. `flash.sh` reads `products/<TARGET>/default_overlays` and hands each dtbo to `tegraflash` as `ADDITIONAL_DTB_OVERLAY`, which merges it into the base DTB on top of whichever Orin Nano/NX SKU the flasher detects — so one image still covers every SKU. The bootloader hands that merged DTB to the kernel; an `extlinux` `OVERLAYS` line would instead be applied to the symbol-stripped UEFI DTB and silently fail to resolve, which is why the default is baked at flash time rather than pre-selected in `extlinux.conf`. A later `jetson-io` choice still supersedes it cleanly — `jetson-io` boots its own `FDT`'d entry off the clean `/boot/dtb` kernel DTB, so selecting another camera doesn't collide. To change the shipped default, edit `products/<TARGET>/default_overlays` and re-flash.

The overlays ARK ships live under `products/<TARGET>/overlay/` (the `.dts`/`.dtsi` sources) and are enumerated in `products/<TARGET>/overlay/dtbo.list` (the explicit built set); `build.sh` layers them onto the BSP's stock overlay tree at build time. Add or drop a camera overlay by editing those two — not a BSP source mirror.

Build the overlay DTBs (from host):
```
./build.sh PAB   # or JAJ, PAB_V3, PAB_CAN
```

Copy the overlay to the Jetson:
```
DTB_PATH="staging/PAB/Linux_for_Tegra/source/kernel-devicetree/generic-dts/dtbs"
scp $DTB_PATH/<overlay>.dtbo jetson@192.168.55.1:~
```

On the Jetson, install and activate:
```
sudo mv <overlay>.dtbo /boot
sudo /opt/nvidia/jetson-io/config-by-hardware.py -n 2="Camera IMX477 Quad"
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

Images built with `--provision` already ship the Tegra GStreamer plugins (`nvarguscamerasrc`, `nvvidconv`, `nvv4l2*`) via `nvidia-l4t-gstreamer`. Install `nvidia-jetpack` only if you also need the full CUDA/TensorRT compute stack.

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
