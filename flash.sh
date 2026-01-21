#!/bin/bash

# Log output to file while keeping terminal output
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec > >(tee "$SCRIPT_DIR/flash.log.txt") 2>&1

sudo -v

echo "Waiting for device..."

while ! lsusb | grep -q "NVIDIA Corp. APX"; do
    sleep 1
done

pushd .
cd prebuilt/Linux_for_Tegra/
sudo ./tools/kernel_flash/l4t_initrd_flash.sh --external-device nvme0n1p1 \
	-p "-c ./bootloader/generic/cfg/flash_t234_qspi.xml" -c ./tools/kernel_flash/flash_l4t_t234_nvme.xml \
	--erase-all --showlogs --network usb0 jetson-orin-nano-devkit-super nvme0n1p1
popd
