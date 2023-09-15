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
  -c tools/kernel_flash/flash_l4t_external.xml -p "-c bootloader/t186ref/cfg/flash_t234_qspi.xml" \
  --showlogs --network usb0 jetson-orin-nano-devkit internal
```


---

## Building from source
If you want to further modify the device tree, you will need to build the kernel from source. A helper script is
provided that will download the necessary files.
```
./source_build_setup.sh
```
Alternatively you can visit the [nvidia official documentation](https://docs.nvidia.com/jetson/archives/r35.3.1/DeveloperGuide/text/SD/Kernel/KernelCustomization.html#building-the-kernel).

