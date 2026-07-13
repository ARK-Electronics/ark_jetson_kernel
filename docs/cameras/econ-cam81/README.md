# e-con e-CAM81_CUONX (AR0821) — Just a Jetson (JAJ)

The e-CAM81_CUONX is an 8MP MIPI camera from e-con Systems built around the onsemi AR0821 (1/1.7", rolling shutter, >140dB HDR) with an S-mount (M12) lens holder. Unlike the Raspberry Pi-style sensors (IMX219/477/708), the module carries its **own ISP and an MCU**: the Jetson receives fully processed **UYVY (YUV 4:2:2)**, and the driver talks to the MCU over I2C (`0x42`) rather than to bare sensor registers. Consequences worth internalizing before using it:

- **No `nvarguscamerasrc` / Argus.** The Tegra ISP is not in the path. Capture with `v4l2src` (CPU buffers) or `nvv4l2camerasrc` (NVMM/hardware buffers).
- Image tuning (exposure, white balance, HDR mode, denoise, ...) is done through **V4L2 controls served by the camera's MCU**, not through Argus/ISP tuning files.
- The driver embeds the MCU firmware and **reflashes the camera over I2C at probe when versions mismatch** — expect one slow first boot per camera (progress in `dmesg`; don't power off mid-update).

Status: driver and overlays are ported and build cleanly; **not yet validated on JAJ hardware**. e-con's own release only officially validated the dev kit's CAM1 port (the dual overlay's CAM0 wiring is enabled but was never shipped working by e-con — see [CAM0 caveat](#cam0-caveat)).

## What ships in this repo

| Piece | Location |
|-------|----------|
| Driver (`ar0821_module.ko`) | `kernel_overlay/nvidia-oot/drivers/media/i2c/` (registered by `build.sh`) |
| MCU firmware (embedded in driver) | `kernel_overlay/nvidia-oot/drivers/media/i2c/ar0821_dev.txt` |
| Dual overlay (both ports, 2-lane) | `products/JAJ/overlay/tegra234-p3767-camera-p3768-ar0821-dual.dts` |
| 4-lane overlay (CAM1 only) | `products/JAJ/overlay/tegra234-p3767-camera-p3768-ar0821-4lane.dts` |

The sources are vendored from e-con's release `e-CAM81_CUONX_JETSON_ONX_ONANO_L4T35.4.1_19-DEC-2023_R01_RC2` and ported from L4T 35 (kernel 5.10) to this repo's L4T r36 (kernel 5.15, `nvidia-oot`); the port delta is listed in the header of `ar0821_module.c`.

## Choosing a configuration

| Overlay (`jetson-io` name) | Ports | Lanes | Video nodes | 4K limit |
|---------------------------|-------|-------|-------------|----------|
| `Camera e-CAM81 Dual` | CAM0 + CAM1 | 2 per camera | `/dev/video0` (CAM0), `/dev/video1` (CAM1) | ~16 fps |
| `Camera e-CAM81 CAM1 4-lane` | CAM1 only | 4 | `/dev/video0` | 30 fps |

Only CAM1 has 4 data lanes wired (on JAJ as on the dev kit); CAM0 is 2-lane-only. Pick the 4-lane overlay when you need 4K30 from a single camera, the dual overlay for two cameras. 720p/1080p rates are identical in both.

## Hardware hookup

The camera kit is three pieces: the camera module (rigid-flex, sensor + ISP boards), the **ACC-RB-WTB-ADP** adapter board (supplies power, level-shifts I2C 3.3V→1.8V, breaks out the trigger connector), and a 15cm 22-pin FPC.

1. With the Jetson **powered off**, connect the FPC between the adapter's CN1 and the JAJ camera connector (CAM1 for the 4-lane overlay). On both ends the exposed contacts face the board; lift the connector actuator, seat the cable, press it closed. A reversed cable can damage the camera and the carrier.
2. Power on. The greenish-yellow LED on the adapter board indicates the camera module has power — if it's dark, re-seat the FPC.
3. The camera draws up to ~1.5W from the 3.3V camera rail.

The 4-pin JST (CN3) on the adapter is the external trigger input: pin 1 = 3.3V out, pin 2 = TRIGGER (3.3V active-high, 4.7k pull-down), pin 4 = GND. Trigger mode is armed via the `trigger` V4L2 control; see e-con's *External Trigger Setup Guide* for timing.

## Enabling the overlay

Flashed JAJ images already contain both `.dtbo`s in `/boot`. Select one with jetson-io and reboot:

```
sudo /opt/nvidia/jetson-io/config-by-hardware.py -l
sudo /opt/nvidia/jetson-io/config-by-hardware.py -n 2="Camera e-CAM81 Dual"      # or "Camera e-CAM81 CAM1 4-lane"
sudo reboot
```

To ship it as the flash-time default instead (active on first boot, no jetson-io step), replace the IMX219 line in `products/JAJ/default_overlays` with the ar0821 dtbo of your choice and re-flash — see `docs/cameras.md` for how the baked default works and why an `extlinux` `OVERLAYS` line does not.

## First boot and verifying detection

On the first boot with a new camera (or after a driver update that carries newer MCU firmware), probe may spend a while erasing and reflashing the camera's MCU over I2C — let it finish. Check state with:

```
dmesg | grep -i ar0821
```

Expected on success: `subdev ar0821 10-0042 bound` (bus 9 for CAM0) and `Detected ar0821 sensor`. Then:

```
ls /dev/video*
v4l2-ctl -d /dev/video0 --list-formats-ext   # UYVY: 1280x720, 1920x1080, 3840x2160
```

## Formats and frame rates

Single format: UYVY 4:2:2, 16 bit/pixel. Rates depend on the camera mode (`camera_mode` control — the MCU's HDR pipeline) and lane count:

| Resolution | Day HDR | Night HDR | Linear |
|------------|---------|-----------|--------|
| 1280x720 | 30 | 60 | 60 |
| 1920x1080 | 30 | 60 | 60 |
| 3840x2160 (2-lane dual) | 16 | 16 | 16 |
| 3840x2160 (4-lane) | 30 | 30 | 30 |

Listed rates assume manual exposure (auto exposure can lower them in dim scenes) and a maxed-out clock profile:

```
sudo nvpmodel -m 0 && sudo jetson_clocks
```

Select the frame rate with the standard V4L2 `parm` ioctl (GStreamer does this from caps automatically):

```
v4l2-ctl -d /dev/video0 --set-parm 60
```

## Controls

All image controls are enumerated live from the MCU — list them with `v4l2-ctl -d /dev/video0 -l`. The set includes brightness, contrast, saturation, white balance (auto + temperature 1000–10000K), gamma, gain, horizontal/vertical flip, sharpness, denoise, powerline frequency, special effects, ROI-based auto exposure (window size + position), exposure compensation, frame-rate control, external trigger, and the two HDR-related menus. The ones that trip people up:

- `camera_mode` (Day HDR / Night HDR / Linear): **manual exposure and exposure compensation only work in Linear mode**; Day HDR caps every resolution at 30fps.
- Exposure (manual) is in units of 100µs (e.g. 312 ≈ 31.2ms, 10000 = 1s); exposures longer than the frame period drop the frame rate.
- Gain only applies in manual exposure. High gain at very low exposure produces blue noise at high resolutions — raise exposure instead.
- Controls are global: they persist across resolution switches and apply to whichever camera's node you set them on.

Example:

```
v4l2-ctl -d /dev/video0 -c contrast=6,saturation=20
```

## GStreamer recipes

Preview (hardware path, 4K → 1080p display):

```
gst-launch-1.0 nvv4l2camerasrc device=/dev/video0 ! \
  'video/x-raw(memory:NVMM), format=UYVY, width=3840, height=2160' ! \
  nvvidconv ! 'video/x-raw(memory:NVMM), format=I420, width=1920, height=1080' ! nv3dsink sync=false
```

Record 4K H.264 — **Orin NX** (hardware encoder):

```
gst-launch-1.0 nvv4l2camerasrc device=/dev/video0 ! \
  'video/x-raw(memory:NVMM), format=UYVY, width=3840, height=2160' ! \
  nvvidconv ! 'video/x-raw(memory:NVMM), format=I420' ! \
  nvv4l2h264enc ! h264parse ! matroskamux ! queue ! filesink location=out.mkv
```

**Orin Nano has no hardware video encoder** — substitute software x264:

```
gst-launch-1.0 -e nvv4l2camerasrc device=/dev/video0 ! \
  'video/x-raw(memory:NVMM), format=UYVY, width=1920, height=1080' ! \
  nvvidconv ! 'video/x-raw, format=I420' ! \
  x264enc tune=zerolatency ! h264parse ! matroskamux ! queue ! filesink location=out.mkv
```

UDP stream (1080p, receiver at IP:PORT):

```
gst-launch-1.0 nvv4l2camerasrc device=/dev/video0 ! \
  'video/x-raw(memory:NVMM), format=UYVY, width=1920, height=1080' ! \
  nvvidconv ! 'video/x-raw(memory:NVMM), format=I420' ! \
  nvv4l2h264enc ! rtph264pay mtu=1400 ! udpsink clients=IP:PORT sync=false
```

Still capture (JPEG via CPU):

```
gst-launch-1.0 v4l2src device=/dev/video0 num-buffers=1 ! \
  'video/x-raw, format=UYVY, width=3840, height=2160' ! jpegenc ! filesink location=still.jpg
```

## Troubleshooting

- **No `/dev/video*` / no `ar0821` in dmesg**: check the adapter LED (module power), then that the expander and MCU answer on the camera I2C leg — with the dual overlay, CAM0 is i2c bus 9 and CAM1 bus 10: `i2cdetect -y -r 10` should show `0x20` (expander) and `0x42` (MCU). All-blank means cable/connector; only `0x20` means the camera flex to the adapter is loose.
- **Probe fails mid "firmware update"**: power-cycle the Jetson and let the driver retry — the MCU boots its ROM bootloader when its application image is invalid, and the driver detects and reflashes it.
- **Black/frozen preview** (known e-con issue): toggle `camera_mode` between HDR and Linear, or reload the driver: `sudo rmmod ar0821_module && sudo modprobe ar0821_module`.
- **Low 4K frame rate on the dual overlay**: that's the 2-lane ceiling (~16fps); use the 4-lane overlay on CAM1 for 4K30.
- **Frame drops with multiple consumers**: one process per video node; multiple simultaneous viewers is a known e-con limitation.

## CAM0 caveat

e-con's release wires the dev kit's CAM0 to `serial_b`/port-index 1 and its docs state only CAM1 (4-lane) is supported; NVIDIA's stock (and ARK's validated) JAJ overlays wire CAM0 to `serial_a`/port-index 0 with `lane_polarity 6`. The dual overlay here uses the ARK/NVIDIA wiring, which is proven for IMX219/477/708 on JAJ CAM0 — but AR0821-on-CAM0 has no prior art from e-con, so validate it on the bench before depending on it; if CAM0 yields no frames while CAM1 streams, the overlay's CAM0 `tegra_sinterface`/`port-index` (serial_a/0 vs serial_b/1) is the first knob to try.

## Vendor documentation

The full e-con doc set (datasheet, developer guide, GStreamer guide, Linux app manual, MCU protocol app note, external trigger guide) ships in the release bundle `e-CAM81_CUONX_Documents_R01_RC2.zip`; get it from e-con's developer resources for the camera.
