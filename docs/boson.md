# FLIR Boson+ (thermal, MIPI)

A MIPI-model FLIR Boson+ connects to an ARK carrier's CSI port through the ARK Boson Adapter. The `nv_boson` driver sets the Boson up over I2C and streams 640x512 at 60 fps, either colourised (UYVY) or as pre-AGC 14-bit data.

**Status:** both modes run at 60 fps through V4L2 on a JAJ with an Orin Nano 4GB, on CAM1. The camera was a Boson+ 640, part number 22640A095-6IAAX, software 4.1.27660. The CAM0 overlay has not been tested on hardware, and there are no overlays for PAB or PAB_V3 yet.

## Overlays

There is one overlay for each CSI port. Apply the one for your port with `jetson-io`.

| Carrier | Port | Overlay name | Notes |
|---------|------|--------------|-------|
| JAJ | CAM1 (J25) | Camera FLIR Boson+ CAM1 | CSI2, bench-tested |
| JAJ | CAM0 (J28) | Camera FLIR Boson+ CAM0 | CSI1, untested on hardware |

The overlay files are named `tegra234-p3767-camera-p3768-boson-cam<n>.dtbo`, where `<n>` is the port number.

## Modes

| Mode | Size | fps | V4L2 format | Boson output |
|------|------|-----|-------------|--------------|
| 0 | 640x512 | 60 | `UYVY` | Post-colorize, in the Boson's current palette (default) |
| 1 | 640x512 | 60 | `RG14` | Pre-AGC 14-bit, monochrome |

Both modes are the same size, so you pick mode 1 with the `sensor_mode` control.

## Capture

The Boson appears as a video device named `vi-output, boson`. The first command lists the devices so you can find its number. The second captures 300 colourised frames. Put the number you found in place of `N`.

```
v4l2-ctl --list-devices
v4l2-ctl -d /dev/videoN --set-fmt-video=width=640,height=512,pixelformat=UYVY \
  --set-ctrl sensor_mode=0 --stream-mmap --stream-count=300 --stream-to=boson.uyvy
```

This command captures pre-AGC frames instead:

```
v4l2-ctl -d /dev/videoN --set-fmt-video=width=640,height=512,pixelformat=RG14 \
  --set-ctrl sensor_mode=1 --stream-mmap --stream-count=300 --stream-to=boson.raw14
```

- **RAW14 layout:** each sample is a 16-bit word with the 14-bit value in the top 14 bits, so shift it right by 2 to read it (`value >> 2`). The VI copies the top two bits into the bottom two. `RG14` is a Bayer format only because the VI has no 14-bit monochrome one, so do not demosaic it.
- **Palette:** the colourised mode uses whatever palette the Boson has, and the factory default is white hot, which looks grey. Change it over the Boson's serial port.
- **Argus:** `nvarguscamerasrc` does not work with this camera, because the Boson's output does not go through the ISP. Use V4L2 or `v4l2src`.

## What the driver changes on the Boson

When a stream starts, the driver switches the Boson's video output to MIPI with a continuous clock, selects the mode's format, and turns off telemetry and the MIPI embedded-data tag. The VI would otherwise reject every frame for the embedded-data line. None of this is saved to the Boson's flash, so a power cycle brings back the camera's own settings. The driver changes nothing else, so palette, flat-field correction and frame sync stay under your control over the serial port.

## Wiring

Connect the adapter's J2 to the CSI port with a 22-pin 0.5 mm FFC. The adapter takes its power from the port's 3.3 V.

J3, the 6-pin Boson Control connector, carries the Boson's UART on pins 2 and 3 and the adapter IMU's I2C on pins 4 and 5. The video does not need it: the driver controls the Boson over the CSI port's own I2C. You need J3 only for the Boson's serial port (921600 8N1) or the IMU.

- **Do not use the JAJ's UART2/I2C0 connector for J3.** JAJ UART2 is the Jetson's debug console (`ttyTCU0`), which runs at a fixed 115200, so you cannot talk to the Boson there.
- **Rev 1.0 swaps the Boson's TX and RX.** Boson pin 33 is its UART input and pin 43 its output, and the Rev 1.0 adapter wires them the other way round. Cross pins 2 and 3 in the J3 cable.
- **On a JAJ UART connector, the IMU lands on RTS and CTS.** With J3 in UART1 (`/dev/ttyTHS1`), the IMU's SCL is on CTS (`PR.05`) and SDA on RTS (`PR.04`). On the bench the IMU answered over I2C bit-banged on those two pins, after setting both pads to drive and read (tristate off, input on).

## Hardware notes

- **I2C:** the Boson answers on the camera bus at 7-bit address `0x6a`. Its command protocol on I2C is FLIR's, framed as `8E A1`, a 16-bit length and the command, with no CRC. Function codes are from FLIR's Boson SDK.
- **Busy Boson:** starting MIPI keeps the Boson busy for about 300 ms. During that time it holds the I2C clock until the Jetson's controller gives up, and the read that times out still takes the first byte of the reply. The driver allows for that.
- **IMU:** the adapter carries an ICM-45686 at `0x68`, on J3's I2C, not on the camera bus.
- **Frame sync:** the adapter routes the port's `CAMn_MCLK` net to Boson pin 72 `EXT_SYNC` through R12. Each overlay makes that pad a pulled-down input, as the Hadron overlays do: CAM0 `PP.00` and CAM1 `PP.01`. R13 (not fitted) would route it to the IMU's FSYNC instead.
- **No reset or clock:** the adapter wires no reset, power-down or clock to the Boson, so the overlays declare none.
