# ARK Jetson Carrier Setup

This repository contains instructions and scripts for flashing your Jetson **Orin Nano** or **Orin NX** on an ARK Jetson Carrier.

| Component | Version |
| --- | --- |
| JetPack | 6.2.2 |
| L4T (BSP) | R36.5.0 |
| Kernel | Linux 5.15 |
| Host OS | Ubuntu 22.04 |

> **Building on a non-22.04 host?** `setup.sh` and `build.sh` auto-containerize themselves in a 22.04 docker image (docker is auto-installed via apt if missing). See [docs/build_host.md](docs/build_host.md) for why we pin to 22.04.

## Products

| Target | Carrier Board | Notes |
| --- | --- | --- |
| `PAB` | ARK Jetson PAB Carrier | DP bidir pinmux |
| `JAJ` | ARK Just a Jetson Carrier | HDMI pinmux |
| `PAB_V3` | ARK Jetson PAB V3 Carrier | Separate product (not PAB Rev3), KSZ8795 ethernet switch |

Each product has its own device tree overlay and optional kernel config in `products/{TARGET}/`. Build artifacts are fully isolated in per-product staging directories — you can build all three and flash any of them without cross-contamination.

## Prebuilt Images (Recommended)

Flash a Jetson without building from source. Download and run the flash script for your carrier board:

```
curl -LO https://github.com/ARK-Electronics/ark_jetson_kernel/releases/download/<tag>/flash_from_package.sh
chmod +x flash_from_package.sh
./flash_from_package.sh <tag>
```

Replace `<tag>` with a release tag for your product (e.g. `pab-6.2.1.1`, `jaj-6.2.1.1`, `pab-v3-6.2.1.1`). Or pass just the product name to flash the latest release:

```
./flash_from_package.sh pab       # latest PAB release
./flash_from_package.sh jaj       # latest JAJ release
./flash_from_package.sh pab-v3    # latest PAB_V3 release
```

Requires a Debian/Ubuntu host with USB connection. Put the Jetson in recovery mode before running. See [Releases](https://github.com/ARK-Electronics/ark_jetson_kernel/releases) for available versions.

## Building from Source

If you need to customize the kernel or device tree, clone this repository and follow the steps below.

### 1. Setup
Download the BSP, root filesystem, and kernel source tarballs:
```
./setup.sh
```

### 2. Build
Build a target. The first build stages the full L4T tree (extract, configure rootfs, apply patches) which takes several minutes. Subsequent builds skip staging and just recompile.
```
./build.sh PAB        # build one target
./build.sh all        # build all three
./build.sh PAB --clean  # wipe staging and rebuild from scratch
```

### 3. Add WiFi (optional)

You can optionally add your WiFi network after building and before flashing:
```
./scripts/add_wifi_network.sh YourNetworkName YourPassword
```

If you didn't configure the WiFi network before flashing, you can ssh in over the micro USB and use network manager to add your network:
```
ssh jetson@jetson.local
sudo nmcli dev wifi connect YourNetworkName password YourPassword
```

### 4. Flash
Connect a micro USB cable to the port adjacent to the mini HDMI. Power on with the Force Recovery button held. You can verify the Jetson is in recovery mode by checking `lsusb`.
> Bus 001 Device 012: ID 0955:7523 NVIDIA Corp. APX

Flash the image:
```
./flash.sh PAB
```

#### Flash options

By default, `flash.sh` targets NVMe + Orin Super. For other configurations:

```
# Flash to SD card (NVIDIA dev kit modules)
./flash.sh PAB --sdcard

# Flash to USB thumb drive
./flash.sh PAB --usb

# Flash non-super module variant
./flash.sh PAB --no-super

# Both
./flash.sh PAB --sdcard --no-super
```

Once complete, SSH in via Micro USB or WiFi.
```
ssh jetson@jetson.local
```

### 5. Generate & Publish Flash Package (optional)

See [packaging/](packaging/) for how to generate distributable flash packages and publish them to GitHub Releases.

### 6. Install ARK Software (optional)
You can now optionally install the ARK software packages, which provide handy tools for working with the Jetson on an ARK carrier.

https://github.com/ARK-Electronics/ARK-OS

---

## Additional Documentation

The sections below cover advanced topics for reference. Most users will not need these for initial setup.


### Flashing the QSPI Bootloader
You can flash just the QSPI bootloader and install a pre-flashed NVME afterwards ([NVIDIA Docs](https://docs.nvidia.com/jetson/archives/r36.3/DeveloperGuide/SD/FlashingSupport.html#examples)). If you are upgrading from Jetpack5 to Jetpack6 you must reflash the QSPI bootloader.
```
cd staging/PAB/Linux_for_Tegra/
sudo ./flash.sh --no-systemimg -c bootloader/generic/cfg/flash_t234_qspi.xml jetson-orin-nano-devkit-super nvme0n1p1
```
If the ./flash.sh command fails, try the l4t_initrd_flash.sh command:
```
sudo ./tools/kernel_flash/l4t_initrd_flash.sh -p "--no-systemimg -c bootloader/generic/cfg/flash_t234_qspi.xml" --network usb0 jetson-orin-nano-devkit-super nvme0n1p1
```

---

### Changing Jetson Clock Speeds
To show the current settings:
```
sudo jetson_clocks --show
```
To store the current settings:
```
sudo jetson_clocks --store
```
To maximize Jetson Orin performance:
```
sudo jetson_clocks
```
To restore the previous settings:
```
sudo jetson_clocks --restore
```

---

### Jetson Super Mode
After flashing or updating to JetPack 6.2, run the following command to start the newly available Super Mode.

MAXN SUPER mode on Jetson Orin Nano Modules:
```
sudo nvpmodel -m 2
```
MAXN SUPER mode on Jetson Orin NX Modules:
```
sudo nvpmodel -m 0
```

---

### Camera Support
See [docs/cameras.md](docs/cameras.md) for tested cameras, overlays, and test commands (GStreamer, v4l2-ctl).

### 10GbE Ethernet (Auvidea M20E)
See [docs/10gbe_ethernet.md](docs/10gbe_ethernet.md) for using the Auvidea M20E M.2 10GbE adapter with a USB boot drive.

---

### Manual Kernel Build (Advanced)
The following steps are automated by `build.sh`. For manual builds, adjust the target directory as needed:

#### Building the kernel, modules, and dtbs
```
export CROSS_COMPILE=$HOME/l4t-gcc/aarch64--glibc--stable-2022.08-1/bin/aarch64-buildroot-linux-gnu-
export KERNEL_HEADERS=$PWD/staging/PAB/Linux_for_Tegra/source/kernel/kernel-jammy-src
export INSTALL_MOD_PATH=$PWD/staging/PAB/Linux_for_Tegra/rootfs/
cd staging/PAB/Linux_for_Tegra/source
make -C kernel && make modules && make dtbs
```
Install the kernel into the staging directory:
```
sudo -E make install -C kernel
```
Copy the kernel image:
```
cp kernel/kernel-jammy-src/arch/arm64/boot/Image ../../kernel/
```

---

### Building the Camera Overlay DTBs
The camera overlays can be built and installed onto the Jetson without needing to reflash.
```
export CROSS_COMPILE=$HOME/l4t-gcc/aarch64--glibc--stable-2022.08-1/bin/aarch64-buildroot-linux-gnu-
export KERNEL_HEADERS=$PWD/staging/PAB/Linux_for_Tegra/source/kernel/kernel-jammy-src
cd staging/PAB/Linux_for_Tegra/source/
make dtbs
```

Copy the overlay DTB to the Jetson via Micro-USB:
```
DTB_PATH="staging/PAB/Linux_for_Tegra/source/kernel-devicetree/generic-dts/dtbs/"
OVERLAY_DTB=<your_overlay>
scp $DTB_PATH/$OVERLAY_DTB jetson@192.168.55.1:~
```
Installing the overlay requires sudo so you will then need to SSH into the Jetson and move the overlay into **/boot**.
```
ssh jetson@192.168.55.1
sudo mv <your_overlay> /boot
```
You can then select your overlay using the Jetson-IO tool. List the available overlays to ensure yours is available.
```
sudo /opt/nvidia/jetson-io/config-by-hardware.py -l
```
For example:
```
 Header 1 [default]: Jetson 40pin Header
   Available hardware modules:
   1. Adafruit SPH0645LM4H
   2. Adafruit UDA1334A
   3. FE-PI Audio V1 and Z V2
   4. ReSpeaker 4 Mic Array
   5. ReSpeaker 4 Mic Linear Array
 Header 2: Jetson 24pin CSI Connector
   Available hardware modules:
   1. Camera ARK IMX219 Quad
   2. Camera ARK IMX477 Single
```

Apply your overlay, for example:
```
sudo /opt/nvidia/jetson-io/config-by-hardware.py -n 2="Camera ARK IMX477 Single"
```

Reboot and your new device tree will be active.
```
sudo reboot
```
You can check that LibArgus can find your camera sensor:
```
nvargus_nvraw --lps
```

---

### Notes on Building the Kernel and Modifying the Device Tree
To make changes to the kernel device tree you must build the kernel from source. After building from source, the device tree binaries are installed into the staging directory and included in the next flash.

Note that there are different device tree binaries depending on the module and RAM. <br>
https://docs.nvidia.com/jetson/archives/r36.3/DeveloperGuide/HR/JetsonModuleAdaptationAndBringUp/JetsonOrinNxNanoSeries.html#porting-the-linux-kernel-device-tree <br>
**Orin NX 16GB-DRAM**   : tegra234-p3768-0000+p3767-**0000**-nv.dtb <br>
**Orin NX 8GB-DRAM**    : tegra234-p3768-0000+p3767-**0001**-nv.dtb <br>
**Orin Nano 8GB-DRAM**  : tegra234-p3768-0000+p3767-**0003**-nv.dtb <br>
**Orin Nano 4GB-DRAM**  : tegra234-p3768-0000+p3767-**0004**-nv.dtb <br>

The device tree source files for each product live in `products/{TARGET}/device_tree/`. These are overlaid onto the NVIDIA kernel source during build. To modify the device tree, edit the files in the product directory and re-run `./build.sh <TARGET>`.
