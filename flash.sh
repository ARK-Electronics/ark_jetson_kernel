#!/bin/bash

sudo -v

while ! lsusb | grep -q "0955:7623"; do
    sleep 1
done

pushd .
cd $ARK_JETSON_KERNEL_DIR/prebuilt/Linux_for_Tegra/
sudo ./tools/kernel_flash/l4t_initrd_flash.sh --external-device nvme0n1p1 \
	-p "-c ./bootloader/generic/cfg/flash_t234_qspi.xml" -c ./tools/kernel_flash/flash_l4t_t234_nvme.xml \
	--erase-all --showlogs --network usb0 jetson-orin-nano-devkit nvme0n1p1
popd
