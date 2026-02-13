#!/bin/bash

# Log output to file while keeping terminal output
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec > >(tee "$SCRIPT_DIR/flash.log.txt") 2>&1

# Pre-flash target confirmation
LAST_TARGET_FILE="$SCRIPT_DIR/source_build/LAST_BUILT_TARGET"
if [ -f "$LAST_TARGET_FILE" ]; then
    LAST_TARGET=$(cat "$LAST_TARGET_FILE")
    echo "========================================="
    echo "  Built target: $LAST_TARGET"
    echo "========================================="
    read -p "Flash this target? (y/N): " confirm
    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        echo "Flash aborted."
        exit 0
    fi
else
    echo "WARNING: No LAST_BUILT_TARGET file found — cannot confirm which target was built."
    read -p "Continue anyway? (y/N): " confirm
    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        echo "Flash aborted."
        exit 0
    fi
fi

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
