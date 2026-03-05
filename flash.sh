#!/bin/bash

# Defaults: NVMe + super (ARK PAB carrier)
STORAGE_DEV="nvme0n1p1"
USE_INITRD=true
FLASH_TARGET="jetson-orin-nano-devkit-super"

while [[ $# -gt 0 ]]; do
    case $1 in
        --sdcard)
            STORAGE_DEV="mmcblk0p1"
            USE_INITRD=false
            shift ;;
        --no-super)
            FLASH_TARGET="jetson-orin-nano-devkit"
            shift ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: ./flash.sh [--sdcard] [--no-super]"
            exit 1 ;;
    esac
done

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

while ! lsusb -d 0955:7323 > /dev/null 2>&1; do
    sleep 1
done

pushd .
cd prebuilt/Linux_for_Tegra/
if [ "$USE_INITRD" = true ]; then
    # NVMe flash
    sudo ./tools/kernel_flash/l4t_initrd_flash.sh --external-device "$STORAGE_DEV" \
        -p "-c ./bootloader/generic/cfg/flash_t234_qspi.xml" \
        -c ./tools/kernel_flash/flash_l4t_t234_nvme.xml \
        --erase-all --showlogs --network usb0 \
        "$FLASH_TARGET" "$STORAGE_DEV"
else
    # SD card flash
    sudo ./flash.sh "$FLASH_TARGET" "$STORAGE_DEV"
fi
popd
