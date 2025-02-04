# ARK Jetson Carrier Setup

This repository contains instructions and scripts for flashing your Jetson **Orin Nano** or **Orin NX** on an ARK Jetson Carrier. Clone this repository on your Host PC and follow the instructions below.

**Note:** This has only been tested with Ubuntu 22.04

# Installing the OS
Run the **setup.sh** script to download and build the Jetson Orin Nano / NX Jetpack 6 kernel.

The script will configure the default user, password, and hostname as `jetson`.
```
./setup.sh
```
Once the setup script is finished you can build the kernel.
```
./build_kernel.sh
```
You can optionally add your WiFi network:
```
./add_wifi_network.sh <SSID> <PASSWORD>
```
You can now flash the image.

### Flashing
Connect a micro USB cable to the port adjacent to the mini HDMI. Power on with the Force Recovery button held. You can verify the Jetson is in recovery mode by checking `lsusb`.
> Bus 001 Device 012: ID 0955:7523 NVIDIA Corp. APX

Flash the image
````
./flash.sh
````

Once complete, SSH in via Micro USB or WiFi.
```
ssh jetson@jetson.local
```

#### Install ARK software
You can now optionally install the ARK software packages <br>
https://github.com/ARK-Electronics/ARK-OS

#### Flashing the QSPI bootloader
You can flash just the QSPI bootloader and install a pre-flashed NVME afterwards ([NVIDIA Docs](https://docs.nvidia.com/jetson/archives/r36.3/DeveloperGuide/SD/FlashingSupport.html#examples)). If you are upgrading from Jetpack5 to Jetpack6 you must reflash the QSPI bootloader.
```
cd prebuilt/Linux_for_Tegra/
sudo ./flash.sh --no-systemimg -c bootloader/generic/cfg/flash_t234_qspi.xml jetson-orin-nano-devkit-super nvme0n1p1
```

#### Changing Jetson Clock Speeds
To show the current settings:
```
sudo jetson_clocks --show
```
To store the current settings:
```
sudo jetson_clocks --store
```
To maximize Jetson Orin performance
```
sudo jetson_clocks
```
To restore the previous settings
```
sudo jetson_clocks --restore
```

#### Jetson Super Mode
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

# Building from source explained
The following steps have been automated for you in **build_kernel.sh**.

### Building the kernel, modules, and dtbs
Navigate to the root of the kernel sources and build the kernel, modules, and dtbs
```
export CROSS_COMPILE=$HOME/l4t-gcc/aarch64--glibc--stable-2022.08-1/bin/aarch64-buildroot-linux-gnu-
export KERNEL_HEADERS=$PWD/source_build/Linux_for_Tegra/source/kernel/kernel-jammy-src
export INSTALL_MOD_PATH=$PWD/prebuilt/Linux_for_Tegra/rootfs/
cd source_build/Linux_for_Tegra/source
make -C kernel && make modules && make dtbs
```
Install the kernel rootfs into the prebuilt directory
```
sudo -E make install -C kernel
```
And copy the kernel image to the prebuilt directory
```
cp kernel/kernel-jammy-src/arch/arm64/boot/Image ../../../prebuilt/Linux_for_Tegra/kernel/
```
And copy the dtbs if you've made changes to the device tree
```
copy_dtbs_to_prebuilt.sh
```
Navigate back to prebuilt workspace and flash the image
````
cd prebuilt/Linux_for_Tegra/
sudo ./tools/kernel_flash/l4t_initrd_flash.sh --external-device nvme0n1p1 -p "-c ./bootloader/generic/cfg/flash_t234_qspi.xml" -c ./tools/kernel_flash/flash_l4t_t234_nvme.xml --erase-all --showlogs --network usb0 jetson-orin-nano-devkit-super nvme0n1p1
````

## Building the camera overlay DTBS
The camera overlays can be built and installed onto the Jetson without needing to reflash.
```
export CROSS_COMPILE=$HOME/l4t-gcc/aarch64--glibc--stable-2022.08-1/bin/aarch64-buildroot-linux-gnu-
export KERNEL_HEADERS=$PWD/source_build/Linux_for_Tegra/source/kernel/kernel-jammy-src
cd source_build/Linux_for_Tegra/source/
make dtbs
```

Copy the overlay DTB to the Jetson via Micro-USB
```
DTB_PATH="$PWD/source_build/Linux_for_Tegra/source/kernel-devicetree/generic-dts/dtbs/"
OVERLAY_DTB=<your_overlay>
scp $DTB_PATH/$OVERLAY_DTB jetson@192.168.55.1:~
```
Installing the overlay require sudo so you will then need to SSH into the Jetson and move the overlay into **/boot**.
```
ssh jetson@192.168.55.1
sudo mv <your_overlay> /boot
```
You can then select your overlay using the Jetson-IO tool. List the available overlays to ensure yours is available.
```
sudo /opt/nvidia/jetson-io/config-by-hardware.py -l
```
For example
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

Apply your overlay, for example
```
sudo /opt/nvidia/jetson-io/config-by-hardware.py -n 2="Camera ARK IMX477 Single"
```

Reboot and your new device tree will be active.
```
sudo reboot
```
You can check that LibArgus can find your camera sensor
```
nvargus_nvraw --lps
```

---

### Notes on building the kernel and modifying the device tree
To make changes to the kernel device tree you must build the kernel from source. After building from source you will copy over the new **tegra234-p3768-0000+p3767-<SKU>-nv.dtb** device tree binary to the corresponding location in the prebuilt directory and flash using the same method.

Note that there are different device tree binaries depending on the module and RAM. <br>
https://docs.nvidia.com/jetson/archives/r36.3/DeveloperGuide/HR/JetsonModuleAdaptationAndBringUp/JetsonOrinNxNanoSeries.html#porting-the-linux-kernel-device-tree <br>
**Orin NX 16GB-DRAM**   : tegra234-p3768-0000+p3767-**0000**-nv.dtb <br>
**Orin NX 8GB-DRAM**    : tegra234-p3768-0000+p3767-**0001**-nv.dtb <br>
**Orin Nano 8GB-DRAM**  : tegra234-p3768-0000+p3767-**0003**-nv.dtb <br>
**Orin Nano 4GB-DRAM**  : tegra234-p3768-0000+p3767-**0004**-nv.dtb <br>

The device tree files for Jetson Orin Nano/NX can be found in the kernel source directory at **Linux_for_Tegra/source/hardware/nvidia/t23x/nv-public**. We maintain a [repository](https://github.com/ARK-Electronics/ark_jetson_orin_nano_nx_device_tree ) with these files which is cloned into **source_build/** in the source build setup script. If you want to modify the device tree you will need to modify the files in this repo and and copy them into the correct location in the source build. <br>
