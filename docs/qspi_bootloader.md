# Flashing the QSPI Bootloader

You can flash just the QSPI bootloader and install a pre-flashed NVMe afterwards ([NVIDIA docs](https://docs.nvidia.com/jetson/archives/r36.3/DeveloperGuide/SD/FlashingSupport.html#examples)). Upgrading from JetPack 5 to JetPack 6 also requires reflashing the QSPI bootloader.

```
cd staging/PAB/Linux_for_Tegra/
sudo ./flash.sh --no-systemimg -c bootloader/generic/cfg/flash_t234_qspi.xml jetson-orin-nano-devkit-super nvme0n1p1
```

If `./flash.sh` fails, try `l4t_initrd_flash.sh`:
```
sudo ./tools/kernel_flash/l4t_initrd_flash.sh -p "--no-systemimg -c bootloader/generic/cfg/flash_t234_qspi.xml" --network usb0 jetson-orin-nano-devkit-super nvme0n1p1
```
