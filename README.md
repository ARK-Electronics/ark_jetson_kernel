# ARK Jetson Carrier Setup

These scripts build and flash an image for a Jetson **Orin Nano** or **Orin NX** on an ARK Jetson carrier.

| Component | Version |
| --- | --- |
| JetPack | 7.2.1 |
| L4T (BSP) | R39.2.1 |
| Kernel | Linux 6.8 |
| Device OS | Ubuntu 24.04 |
| Host OS | Ubuntu 22.04 |

> **Build host:** `setup.sh` and `build.sh` run themselves inside a 22.04 docker image that carries kmod 31, and install docker through apt if it is missing. See [docs/build_host.md](docs/build_host.md).

## Products

| Target | Carrier Board | Docs |
| --- | --- | --- |
| `PAB` | ARK Jetson PAB Carrier | [docs](https://docs.arkelectron.com/products/flight-controller/jetson-pabs/ark-jetson-pab-carrier) |
| `JAJ` | ARK Just a Jetson Carrier | [docs](https://docs.arkelectron.com/products/embedded-computers/ark-just-a-jetson) |
| `PAB_V3` | ARK Jetson PAB V3 Carrier | [docs](https://docs.arkelectron.com/products/flight-controller/jetson-pabs/ark-jetson-pab-carrier-v3) |

> **Note:** PAB Rev3 is a hardware revision of the PAB and uses `PAB`. PAB_V3 is a separate product.

## Flash a Prebuilt Image

If you only want the stock image, flash a prebuilt package. Each [release](https://downloads.arkelectron.com/jetson/) has one for every carrier, with the same image that ARK's bundles ship with.

Connect the Jetson to a Debian or Ubuntu host over USB, and boot it into recovery by holding the Force Recovery button while you power it on. Then run:

```
curl -LO https://raw.githubusercontent.com/ARK-Electronics/ark_jetson_kernel/main/packaging/flash_from_package.sh
chmod +x flash_from_package.sh
./flash_from_package.sh pab        # or jaj / pab-v3, or a specific tag like pab-6.2.2.4
```

The script flashes the QSPI bootloader and writes the rootfs to NVMe. One package covers every Orin Nano and NX module variant. [packaging/README.md](packaging/README.md) has the details.

To customize the image, build it from source as described below.

## Build & Flash

### 1. Setup

Download the BSP, root filesystem and kernel source tarballs. You only need to do this once.

```
./setup.sh
```

### 2. Build

Build the image for your carrier:

```
./build.sh PAB
```

- The target is `PAB`, `JAJ`, `PAB_V3` or `all`.
- By default a build stages `staging/{TARGET}/` again from scratch and provisions the image, which preinstalls [ARK-OS](https://github.com/ARK-Electronics/ARK-OS) and tooling. To add your own packages to the image, edit [`provision.sh`](provision.sh).
- `--fast` reuses the staged tree and only recompiles the kernel and device tree. Use it for quick changes after a full build.
- `--no-provision` builds a bare image without ARK-OS. `--clean` and `--provision` are still accepted, but they are the default now.

### 3. Add WiFi (optional)

You can put a WiFi profile into the image before you flash it:

```
./scripts/add_wifi_network.sh PAB YourNetworkName YourPassword
```

Or add the network later, over the USB connection:

```
ssh jetson@jetson.local
sudo nmcli dev wifi connect YourNetworkName password YourPassword
```

### 4. Flash

Connect the carrier's USB port to your PC, then power the Jetson on with the Force Recovery button held.

```
./flash.sh PAB
```

`flash.sh` writes to NVMe by default. For other storage, add a flag:

```
./flash.sh PAB --sdcard     # SD card (NVIDIA dev kit modules)
./flash.sh PAB --usb        # USB thumb drive
```

When the flash finishes, log in over Micro USB or WiFi:

```
ssh jetson@jetson.local
```

> **Tip:** Run `ssh-copy-id jetson@jetson.local` once so you don't type the password on every connection.

> **Tip:** Run `./scripts/add_ssh_config.sh` once to add a `jetson` host to your `~/.ssh/config`. Then `ssh jetson` works, and reflashing won't trip the "REMOTE HOST IDENTIFICATION HAS CHANGED" warning.

## Cameras

Every carrier ships with an IMX219 overlay already selected, so IMX219 cameras work on the first boot. **PAB** has the quad overlay, and **JAJ** and **PAB_V3** have the dual. To use a different camera, select its overlay with NVIDIA's `jetson-io` tool. First list what is available:

```
sudo /opt/nvidia/jetson-io/config-by-hardware.py -l
```
```
 Header 1 [default]: Jetson 40pin Header
   ...
 Header 2: Jetson 22pin CSI Connector
   Available hardware modules:
   1. Camera IMX219 Quad
   2. Camera IMX477 Quad
   3. Camera IMX708 Quad
```

Then apply one by name and reboot:

```
sudo /opt/nvidia/jetson-io/config-by-hardware.py -n 2="Camera IMX477 Quad"
sudo reboot
```

[docs/cameras.md](docs/cameras.md) lists the supported sensors and shows how to test and stream a camera. [docs/camera_overlays.md](docs/camera_overlays.md) shows how to build, install or write your own camera overlay.

## More documentation

| Page | Covers |
| --- | --- |
| [docs/cameras.md](docs/cameras.md) | Supported sensors, streaming, and known issues |
| [docs/camera_overlays.md](docs/camera_overlays.md) | Build, install, and author camera device-tree overlays |
| [docs/hadron.md](docs/hadron.md) | FLIR Hadron EO camera: driver, overlays, capture, demo and frame sync |
| [docs/argus_relaunch_regression.md](docs/argus_relaunch_regression.md) | The JetPack 6.2.2 Argus regression and the camera stack pin that fixes it |
| [docs/gpio.md](docs/gpio.md) | 40-pin / I2S header GPIO and boot-time pad defaults |
| [docs/i2c.md](docs/i2c.md) | I2C bus map and scanning |
| [docs/servo_expander.md](docs/servo_expander.md) | PWM / servo output via the ARK Servo Expander (PCA9685) over I2C |
| [docs/10gbe_ethernet.md](docs/10gbe_ethernet.md) | Auvidea M20E 10GbE adapter |
| [docs/share_wifi.md](docs/share_wifi.md) | Share the host PC's WiFi with the Jetson over USB |
| [docs/power.md](docs/power.md) | Power modes, clock speeds, and measured power draw |
| [docs/kernel_development.md](docs/kernel_development.md) | Manual kernel builds and device tree changes |
| [docs/device-tree.md](docs/device-tree.md) | Where a device-tree change belongs, and how the build applies ARK's changes |
| [docs/qspi_bootloader.md](docs/qspi_bootloader.md) | Flashing only the QSPI bootloader |
| [docs/build_host.md](docs/build_host.md) | Why the build pins to Ubuntu 22.04 |
