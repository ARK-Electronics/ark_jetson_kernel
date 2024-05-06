This repository contains instructions and helper scripts to ease to process of installing the Jetson OS from
prebuilt binaries or source files.

# Installing the OS from binaries
A single setup script is provided for your convenience. It will download the prebuilt release (35.5.0) and apply
the [custom ARK compiled](https://github.com/ARK-Electronics/ark_jetson_compiled_device_tree_files) device tree files. The script will also configure the default user, password, and hostname as "jetson".
```
./setup_prebuilt.sh
```
Alternatively you can visit NVIDIA's [official documentation](https://docs.nvidia.com/jetson/archives/r35.5.0/DeveloperGuide/index.html) for flashing the release.

### Flashing
Connect a micro USB cable to the port adjacent to the mini HDMI. Power on with the Force Recovery button held. You can verify the Jetson is in recovery mode by checking `lsusb`.
> Bus 001 Device 012: ID 0955:7523 NVIDIA Corp. APX

Connect an FTDI cable to the debug port of the Jetson. This will allow you to monitor the progress and ensure there are no errors during the flashing process. After flashing is complete you will use this port to configure the wifi network connection.
```
picocom /dev/ttyUSB0 -b 115200
```

Flash the image
```
cd prebuilt/Linux_for_Tegra/
sudo ./tools/kernel_flash/l4t_initrd_flash.sh --external-device nvme0n1p1 \
  -c tools/kernel_flash/flash_l4t_external.xml -p "\
  -c bootloader/t186ref/cfg/flash_t234_qspi.xml" \
  --showlogs --network usb0 jetson-orin-nano-devkit internal
```

Once flashing has been completed successfully, power cycle the Jetson.

#### Setting up WiFi (headless)
You will need an FTDI attached to the debug connector setup the wifi interface. Alternatively you can connect a display and configure it using the GUI.

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

---

# Installing and building from source
If you want to further modify the device tree, you will need to build the kernel from source. A helper script is
provided that will download the necessary files and toolchain.
```
./source_build_setup.sh
```
Alternatively you can visit NVIDIA's [official documentation](https://docs.nvidia.com/jetson/archives/r35.5.0/DeveloperGuide/SD/Kernel/KernelCustomization.html) for kernel customization and building from source.

Once setup is complete you can build the kernel:
```
cd source_build/Linux_for_Tegra/source/public
mkdir -p kernel_out
./nvbuild.sh -o $PWD/kernel_out
```

### Modifying the device tree
The device tree files for Jetson Orin Nano/NX can be found in the kernel source directory at **Linux_for_Tegra/source/public/hardware/nvidia/platform/t23x/p3768/kernel-dts/**. <br>
You can download the github repository for the device tree source files here:
```
cd source_build
git clone -b ark_35.5.0 git@github.com:ARK-Electronics/ark_jetson_orin_nano_nx_device_tree.git
```
Once you've made your modifications to the device tree files, copy them into the kernel source directory. For example if you've
made changes to support the imx219 cameras over mipi:
```
cp ark_jetson_orin_nano_nx_device_tree/cvb/tegra234-camera-ark-imx219.dtsi \
  Linux_for_Tegra/source/public/hardware/nvidia/platform/t23x/p3768/kernel-dts/cvb/

cp ark_jetson_orin_nano_nx_device_tree/cvb/tegra234-p3768-camera-ark-imx219.dtsi \
  Linux_for_Tegra/source/public/hardware/nvidia/platform/t23x/p3768/kernel-dts/cvb/

cp ark_jetson_orin_nano_nx_device_tree/cvb/tegra234-p3768-0000-a0.dtsi \
  Linux_for_Tegra/source/public/hardware/nvidia/platform/t23x/p3768/kernel-dts/cvb/
```
Rebuild the kernel to build the device tree .dtb file
```
cd Linux_for_Tegra/source/public
./nvbuild.sh -o $PWD/kernel_out
```
Note that there are different device tree binaries depending on the module and RAM. <br>
**Orin NX 16GB-DRAM**   : tegra234-p3767-**0000**-p3768-0000-a0.dtb <br>
**Orin NX 8GB-DRAM**    : tegra234-p3767-**0001**-p3768-0000-a0.dtb <br>
**Orin Nano 8GB-DRAM**  : tegra234-p3767-**0003**-p3768-0000-a0.dtb <br>
**Orin Nano 4GB-DRAM**  : tegra234-p3767-**0004**-p3768-0000-a0.dtb <br>

You can now update the device tree binary in the prebuilt directory and reflash:
```
sudo cp kernel_out/arch/arm64/boot/dts/nvidia/tegra234-p3767-0003-p3768-0000-a0.dtb \
  $ARK_JETSON_KERNEL_DIR/prebuilt/Linux_for_Tegra/bootloader/
sudo cp kernel_out/arch/arm64/boot/dts/nvidia/tegra234-p3767-0003-p3768-0000-a0.dtb \
  $ARK_JETSON_KERNEL_DIR/prebuilt/Linux_for_Tegra/kernel/dtb/
```
Or you can copy the binary directly to the device at **/boot/dtb/**. On reboot your new device tree will be active. <br>
Note that **kernel_** must be prepended to the file name *kernel_tegra234-p3767-0003-p3768-0000-a0.dtb*
