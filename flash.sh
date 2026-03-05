#!/bin/bash

set -eufo pipefail

# Defaults: NVMe + super (ARK PAB carrier)
STORAGE_DEV="nvme0n1"
USE_INITRD=true
FLASH_XML="flash_l4t_external.xml"
FLASH_TARGET="jetson-orin-nano-devkit-super"

while [[ $# -gt 0 ]]; do
    case $1 in
        --sdcard)
            STORAGE_DEV="mmcblk0"
            USE_INITRD=false
            shift ;;
        --usb)
            STORAGE_DEV="sda"
            USE_INITRD=true
            shift ;;
        --no-super)
            FLASH_TARGET="jetson-orin-nano-devkit"
            shift ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: ./flash.sh [--sdcard] [--usb] [--no-super]"
            exit 1 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec > >(tee "$SCRIPT_DIR/flash.log.txt") 2>&1

LAST_TARGET_FILE="$SCRIPT_DIR/source_build/LAST_BUILT_TARGET"
if [ -f "$LAST_TARGET_FILE" ]; then
    LAST_TARGET=$(cat "$LAST_TARGET_FILE")
    echo "========================================="
    echo "  Built target: $LAST_TARGET"
    echo "========================================="
    read -p "Flash this target? (y/N): " confirm
    [[ "$confirm" =~ ^[Yy]$ ]] || exit 0
fi

sudo -v

echo "Waiting for Jetson in recovery mode..."

while ! lsusb | grep -q "NVIDIA Corp. APX"; do
    sleep 1
done

pushd prebuilt/Linux_for_Tegra/ || exit 1

if [ "$USE_INITRD" = true ]; then
    sudo ./tools/kernel_flash/l4t_initrd_flash.sh \
        --external-device "$STORAGE_DEV" \
        -p "-c ./bootloader/generic/cfg/flash_t234_qspi.xml" \
        -c ./tools/kernel_flash/$FLASH_XML \
        --erase-all \
        --showlogs \
        --network usb0 \
        "$FLASH_TARGET" "$STORAGE_DEV"
else
    sudo ./flash.sh "$FLASH_TARGET" "$STORAGE_DEV"
fi

popd
