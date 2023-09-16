## Installing the OS from binaries
A single setup script is provided for your convenience. It will download the prebuilt release (35.3.1) and apply
the modfied device tree binaries (dtb).
```
./prebuilt_setup.sh
```
Alternatively you can visit the [nvidia official documentation](https://docs.nvidia.com/jetson/archives/r35.3.1/DeveloperGuide/text/IN/QuickStart.html#to-flash-the-jetson-developer-kit-operating-software).

### Flashing
Power on with the Force Recovery button held. You can verify the Jetson is in recovery mode by checking `lsusb`
> Bus 001 Device 012: ID 0955:7523 NVIDIA Corp. APX

```
cd Linux_for_Tegra
sudo ./tools/kernel_flash/l4t_initrd_flash.sh --external-device nvme0n1p1 \
  -c tools/kernel_flash/flash_l4t_external.xml -p "\
  -c bootloader/t186ref/cfg/flash_t234_qspi.xml" \
  --showlogs --network usb0 jetson-orin-nano-devkit internal
```
Once flashing is complete use the mini display port to finish setting up. <br>
TODO: use /dev/ttyACM0 to finish setup (didn't work for me, the USB is RNDIS) <br>
TODO: configure user/password before flashing to (bypasss?) the setup before being able to access
the console on the hardware UART.

---

## Building from source
If you want to further modify the device tree, you will need to build the kernel from source. A helper script is
provided that will download the necessary files and toolchain.
```
./source_build_setup.sh
```
Alternatively you can visit the [nvidia official documentation](https://docs.nvidia.com/jetson/archives/r35.3.1/DeveloperGuide/text/SD/Kernel/KernelCustomization.html#building-the-kernel).

Once setup is complete you can build the kernel:
```
cd $ARK_JETSON_CORE_DIR/source_build/Linux_for_Tegra/source/public
mkdir -p kernel_out
./nvbuild.sh -o $PWD/kernel_out
```

### Modifying the device tree
The device tree files for Jetson Orin Nano/NX can be found in the kernel source directory at **Linux_for_Tegra/source/public/hardware/nvidia/platform/t23x/p3768/kernel-dts/**. <br>
You can download the github repository for the device tree source files here:
```
cd $ARK_JETSON_CORE_DIR/source_build
git clone git@github.com:ARK-Electronics/ark_jetson_orin_nano_nx_device_tree.git
```
Once you've made your modifications to the device tree files, copy them into the kernel source directory. For example if you've
made changes to the **tegra234-p3768-0000-a0.dtsi** file:
```
cp ark_jetson_orin_nano_nx_device_tree/cvb/tegra234-camera-ark-imx219.dtsi \
  $ARK_JETSON_CORE_DIR/source_build/Linux_for_Tegra/source/public/hardware/nvidia/platform/t23x/p3768/kernel-dts/cvb/
```
Rebuild the kernel to build the device tree .dtb file
```
cd $ARK_JETSON_CORE_DIR/source_build/Linux_for_Tegra/source/public
./nvbuild.sh -o $PWD/kernel_out
```
Note that there are different device tree binaries depending on the module and RAM. <br>
**Orin NX 16GB-DRAM**   : tegra234-p3767-**0000**-p3768-0000-a0.dtb <br>
**Orin NX 8GB-DRAM**    : tegra234-p3767-**0001**-p3768-0000-a0.dtb <br>
**Orin Nano 8GB-DRAM**  : tegra234-p3767-**0003**-p3768-0000-a0.dtb <br>
**Orin Nano 4GB-DRAM**  : tegra234-p3767-**0004**-p3768-0000-a0.dtb <br>

You can now update the device tree binary in the prebuilt directory:
```
cp $ARK_JETSON_CORE_DIR/source_build/Linux_for_Tegra/source/public/kernel_out/arch/arm64/boot/dts/nvidia/tegra234-p3767-0003-p3768-0000-a0.dtb \
  $ARK_JETSON_CORE_DIR/prebuilt/Linux_for_Tegra/bootloader/
cp $ARK_JETSON_CORE_DIR/source_build/Linux_for_Tegra/source/public/kernel_out/arch/arm64/boot/dts/nvidia/tegra234-p3767-0003-p3768-0000-a0.dtb \
  $ARK_JETSON_CORE_DIR/prebuilt/Linux_for_Tegra/kernel/dtb/
```
Or you can copy the binary directly to the device at **/boot/dtb/**. On reboot your new device tree will be active. <br>
Note that **kernel_** must be prepended to the file name *kernel_tegra234-p3767-0003-p3768-0000-a0.dtb*
