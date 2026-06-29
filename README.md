# ARK Jetson Carrier Setup

Scripts for building and flashing a Jetson **Orin Nano** or **Orin NX** on an ARK Jetson Carrier.

| Component | Version |
| --- | --- |
| JetPack | 6.2.2 |
| L4T (BSP) | R36.5.0 |
| Kernel | Linux 5.15 |
| Host OS | Ubuntu 22.04 |

> **Building on a non-22.04 host?** `setup.sh` and `build.sh` auto-containerize themselves in a 22.04 docker image (docker is auto-installed via apt if missing). See [docs/build_host.md](docs/build_host.md) for why we pin to 22.04.

## Products

| Target | Carrier Board |
| --- | --- |
| `PAB` | ARK Jetson PAB Carrier |
| `JAJ` | ARK Just a Jetson Carrier |
| `PAB_V3` | ARK Jetson PAB V3 Carrier |

## Build & Flash

### 1. Setup
Download the BSP, root filesystem, and kernel source tarballs (one time):
```
./setup.sh
```

### 2. Build
```
./build.sh PAB --clean --provision
```
- Targets are `PAB`, `JAJ`, `PAB_V3`, or `all`.
- `--clean` wipes `staging/{TARGET}/` and re-stages from scratch.
- `--provision` preinstalls [ARK-OS](https://github.com/ARK-Electronics/ARK-OS) and tooling into the image. Omit it for a bare image. To bake in your own packages, edit [`provision.sh`](provision.sh).

### 3. Add WiFi (optional)
Bake a WiFi profile into the image before flashing:
```
./scripts/add_wifi_network.sh PAB YourNetworkName YourPassword
```
Or add it later over the USB connection:
```
ssh jetson@jetson.local
sudo nmcli dev wifi connect YourNetworkName password YourPassword
```

### 4. Flash
Connect to the USB port, then power on with the Force Recovery button held.
```
./flash.sh PAB
```
By default `flash.sh` targets NVMe on an Orin Super module. Other layouts:
```
./flash.sh PAB --sdcard     # SD card (NVIDIA dev kit modules)
./flash.sh PAB --usb        # USB thumb drive
./flash.sh PAB --no-super   # non-super module variant
```

When flashing completes, SSH in over Micro USB or WiFi:
```
ssh jetson@jetson.local
```

> **Tip:** Run `ssh-copy-id jetson@jetson.local` once so you don't type the password on every connection.

> **Tip:** Run `./scripts/add_ssh_config.sh` once to add a `jetson` host to your `~/.ssh/config`. Then `ssh jetson` works, and reflashing won't trip the "REMOTE HOST IDENTIFICATION HAS CHANGED" warning.

## Cameras

Every carrier ships with an IMX219 overlay already selected, so cameras work out of the box: the quad overlay on **PAB**, the dual overlay on **JAJ** and **PAB_V3**. To use a different camera, select an overlay with NVIDIA's `jetson-io` tool. List what's available:
```
sudo /opt/nvidia/jetson-io/config-by-hardware.py -l
```
```
 Header 1 [default]: Jetson 40pin Header
   ...
 Header 2: Jetson 22pin CSI Connector
   Available hardware modules:
   1. Camera ARK IMX219 Quad
   2. Camera ARK IMX477 Single
```
Apply one and reboot:
```
sudo /opt/nvidia/jetson-io/config-by-hardware.py -n 2="Camera ARK IMX477 Single"
sudo reboot
```
See [docs/cameras.md](docs/cameras.md) for supported sensors, overlay names, and how to verify and stream a camera.

## More documentation
- [docs/cameras.md](docs/cameras.md) — supported sensors, overlays, and streaming
- [docs/gpio.md](docs/gpio.md) — 40-pin / I2S header GPIO and boot-time pad defaults
- [docs/i2c.md](docs/i2c.md) — I2C bus map and scanning
- [docs/servo_expander.md](docs/servo_expander.md) — PWM / servo output via the ARK Servo Expander (PCA9685) over I2C
- [docs/10gbe_ethernet.md](docs/10gbe_ethernet.md) — Auvidea M20E 10GbE adapter
- [docs/performance.md](docs/performance.md) — clock speeds and Super Mode
- [docs/kernel_development.md](docs/kernel_development.md) — manual kernel builds and device tree changes
- [docs/build_host.md](docs/build_host.md) — why the build pins to Ubuntu 22.04
