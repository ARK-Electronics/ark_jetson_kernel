# ARK Jetson Carrier Setup

This repository contains instructions and helper scripts for flashing a Jetson Orin Nano or NX installed on an ARK Jetson Carrier. It is reccomended that you install via pre-built binaries. The ARK Jetson Carrier compiled device tree binaries will be downloaded and added into the prebuilt directory.

# Installing the OS from binaries
A single setup script is provided for your convenience. It will download the prebuilt Jetpack 6 release (36.3.0) and apply
the [precompiled ARK device tree binaries](https://github.com/ARK-Electronics/ark_jetson_compiled_device_tree_files). The script will also configure the default user, password, and hostname as "jetson".
```
./setup_prebuilt.sh
```
Alternatively you can visit NVIDIA's [official documentation](https://docs.nvidia.com/jetson/archives/r36.3/DeveloperGuide/IN/QuickStart.html#to-flash-the-jetson-developer-kit-operating-software) for flashing the release.

### Flashing
Connect a micro USB cable to the port adjacent to the mini HDMI. Power on with the Force Recovery button held. You can verify the Jetson is in recovery mode by checking `lsusb`.
> Bus 001 Device 012: ID 0955:7523 NVIDIA Corp. APX

Flash the image
````
cd prebuilt/Linux_for_Tegra/
sudo ./tools/kernel_flash/l4t_initrd_flash.sh --external-device nvme0n1p1 -p "-c ./bootloader/generic/cfg/flash_t234_qspi.xml" -c ./tools/kernel_flash/flash_l4t_t234_nvme.xml --erase-all --showlogs --network usb0 jetson-orin-nano-devkit nvme0n1p1
````

Install Jetpack and missing WiFi driver
```
sudo apt update && sudo apt install -y nvidia-jetpack backport-iwlwifi-dkms
```

#### Flashing only the QSPI
You can also just flash the QSPI bootloader and install a pre-flashed NVME afterwards.
```
cd $ARK_JETSON_KERNEL_DIR/prebuilt/Linux_for_Tegra/
sudo /flash.sh --no-systemimg -c bootloader/generic/cfg/flash_t234_qspi.xml jetson-orin-nano-devkit nvme0n1p1
```
https://docs.nvidia.com/jetson/archives/r36.3/DeveloperGuide/SD/FlashingSupport.html#examples


#### Setting up WiFi (headless)
After flashing is complete you can use the USB connection to configure the wifi network connection.
```
picocom /dev/ttyUSB0 -b 115200
```

Check that the wifi interface exists
```
nmcli device
```
Check that your wifi network is visible
```
nmcli device wifi list
```
Connect to your network
```
sudo nmcli device wifi connect <MY_WIFI_AP> password <MY_WIFI_PASSWORD>
```

### Known issues
- The first time a newly flashed NVME is booted the kernel will panic if a WiFi card is installed. Install the WiFi card after the NVME has been booted at least once.

- The Intel 9260 AC WiFi driver is not supported. You must connect via ethernet and install the backport driver.
  ```
  sudo apt-get install -y backport-iwlwifi-dkms
  ```

---

# Building from source
If you want to further modify the device tree, you will need to build the kernel from source. A helper script is
provided that will download the necessary files and toolchain. This process involves building the device tree binaries and copying them into the prebuilt directory. You should run the **setup_prebuilt.sh** script first.
```
./setup_source_build.sh
```
Alternatively you can visit NVIDIA's [official documentation](https://docs.nvidia.com/jetson/archives/r36.3/DeveloperGuide/SD/Kernel/KernelCustomization.html) for kernel customization and building from source.

### Update device tree for ARK carrier
```
cd source_build
git clone -b ark_36.3.0 git@github.com:ARK-Electronics/ark_jetson_orin_nano_nx_device_tree.git
cp -rf ark_jetson_orin_nano_nx_device_tree/* $ARK_JETSON_KERNEL_DIR/source_build/Linux_for_Tegra/source/hardware/nvidia/t23x/nv-public
```

Once setup is complete you can build the kernel:
```
export CROSS_COMPILE=$HOME/l4t-gcc/aarch64--glibc--stable-2022.08-1/bin/aarch64-buildroot-linux-gnu-
cd $ARK_JETSON_KERNEL_DIR/source_build/Linux_for_Tegra/source/
mkdir -p kernel_out
./nvbuild.sh -o $PWD/kernel_out
```
And copy over the newly built device tree binaries to the prebuilt directory
```
$ARK_JETSON_KERNEL_DIR/copy_dtbs_to_prebuilt.sh
```
Flash the image
```
cd prebuilt/Linux_for_Tegra/
sudo ./tools/kernel_flash/l4t_initrd_flash.sh --external-device nvme0n1p1 -p "-c ./bootloader/generic/cfg/flash_t234_qspi.xml" -c ./tools/kernel_flash/flash_l4t_t234_nvme.xml --erase-all --showlogs --network usb0 jetson-orin-nano-devkit nvme0n1p1
```

### Generating a new kernel device tree
To make changes to the kernel device tree you must build the kernel from source. After building from source you will copy over the new **tegra234-p3768-0000+p3767-<SKU>-nv.dtb** device tree binary to the corresponding location in the prebuilt directory and flash using the same method.

The device tree files for Jetson Orin Nano/NX can be found in the kernel source directory at **Linux_for_Tegra/source/hardware/nvidia/t23x/nv-public**. <br>


To just build the device tree (kernel must be built first)
```
export CROSS_COMPILE=$HOME/l4t-gcc/aarch64--glibc--stable-2022.08-1/bin/aarch64-buildroot-linux-gnu-
export KERNEL_HEADERS=/home/jake/code/ark/ark_jetson_kernel/source_build/Linux_for_Tegra/source/kernel_out/kernel/kernel-jammy-src
./nvbuild.sh -m -o $PWD/kernel_out
```
Note that there are different device tree binaries depending on the module and RAM. <br>
https://docs.nvidia.com/jetson/archives/r36.3/DeveloperGuide/HR/JetsonModuleAdaptationAndBringUp/JetsonOrinNxNanoSeries.html#porting-the-linux-kernel-device-tree <br>
**Orin NX 16GB-DRAM**   : tegra234-p3768-0000+p3767-**0000**-nv.dtb <br>
**Orin NX 8GB-DRAM**    : tegra234-p3768-0000+p3767-**0001**-nv.dtb <br>
**Orin Nano 8GB-DRAM**  : tegra234-p3768-0000+p3767-**0003**-nv.dtb <br>
**Orin Nano 4GB-DRAM**  : tegra234-p3768-0000+p3767-**0004**-nv.dtb <br>

You can reflash the image or you can just copy the device tree binary overlay directly to the jetson. This will scp via micro-USB to the home directory on the Jetson. You will then need to move this file into **/boot**
```
scp $ARK_JETSON_KERNEL_DIR/source_build/Linux_for_Tegra/source/kernel_out/nvidia-oot/device-tree/platform/generic-dts/dtbs/tegra234-p3767-camera-p3768-imx219-ark-quad.dtbo jetson@192.168.55.1:~

```
On reboot your new device tree will be active. <br>


### Adding a device tree overlay
Device tree overlays can be found in **Linux_for_Tegra/source/hardware/nvidia/t23x/nv-public/overlay** and have the **.dts** extension. To include an overlay in the kernel you must add the **.dtbo** to the Makefile in the same directory. Alternatively you can copy the **.dtbo** at run time as explained above.
