# Flashing the QSPI Bootloader

You can flash just the QSPI bootloader and then install an NVMe drive that already has an image on it, as the [NVIDIA docs](https://docs.nvidia.com/jetson/archives/r39.2/DeveloperGuide/SD/FlashingSupport.html#examples) describe. A major JetPack upgrade, such as 5 to 6 or 6 to 7, also needs the QSPI bootloader flashed again.

Run the flash from the staged BSP directory:

```
cd staging/PAB/Linux_for_Tegra/
sudo ./flash.sh --no-systemimg -c bootloader/generic/cfg/flash_t234_qspi.xml jetson-orin-nano-devkit-super nvme0n1p1
```

If `./flash.sh` fails, try `l4t_initrd_flash.sh`:

```
sudo ./tools/kernel_flash/l4t_initrd_flash.sh -p "--no-systemimg -c bootloader/generic/cfg/flash_t234_qspi.xml" --network usb0 jetson-orin-nano-devkit-super nvme0n1p1
```
