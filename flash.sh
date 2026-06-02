#!/bin/bash

# Usage: ./flash.sh <TARGET> [--sdcard] [--usb] [--no-super]
#
# TARGET: PAB | JAJ | PAB_V3
#
# Flashes a previously-built target directly from its staging directory.
# The device must be in USB recovery mode before running this script.

# ── Argument parsing ────────────────────────────────────────────────────────

STORAGE_DEV="nvme0n1p1"
USE_INITRD=true
FLASH_TARGET="jetson-orin-nano-devkit-super"
TARGET=""

while [[ $# -gt 0 ]]; do
    case $1 in
        PAB|JAJ|PAB_V3)
            TARGET="$1"
            shift ;;
        --sdcard)
            STORAGE_DEV="mmcblk0p1"
            USE_INITRD=false
            shift ;;
        --usb)
            STORAGE_DEV="sda"
            shift ;;
        --no-super)
            FLASH_TARGET="jetson-orin-nano-devkit"
            shift ;;
        *)
            echo "Unknown option: $1" >&2
            echo "Usage: ./flash.sh <PAB | JAJ | PAB_V3> [--sdcard] [--usb] [--no-super]" >&2
            exit 1 ;;
    esac
done

if [ -z "$TARGET" ]; then
    if [ -t 0 ]; then
        echo "Please select the target to flash:"
        echo "1) PAB"
        echo "2) JAJ"
        echo "3) PAB_V3"
        read -p "Enter your choice (1-3): " choice
        case $choice in
            1) TARGET="PAB" ;;
            2) TARGET="JAJ" ;;
            3) TARGET="PAB_V3" ;;
            *) echo "Invalid choice. Exiting."; exit 1 ;;
        esac
    else
        echo "ERROR: target required (PAB | JAJ | PAB_V3) when running non-interactively." >&2
        echo "Usage: ./flash.sh <PAB | JAJ | PAB_V3> [--sdcard] [--usb] [--no-super]" >&2
        exit 1
    fi
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "$SCRIPT_DIR/scripts/check_bsp.sh"

L4T_DIR="$SCRIPT_DIR/staging/$TARGET/Linux_for_Tegra"

exec > >(tee "$SCRIPT_DIR/staging/$TARGET/flash.log.txt") 2>&1

if [ ! -d "$L4T_DIR" ]; then
    echo "ERROR: staging/$TARGET/ not found." >&2
    echo "       Run ./build.sh $TARGET first." >&2
    exit 1
fi

require_bsp_staging "$SCRIPT_DIR/staging/$TARGET"

if [ ! -f "$L4T_DIR/kernel/Image" ]; then
    echo "ERROR: Kernel Image not found in staging/$TARGET/." >&2
    echo "       Run ./build.sh $TARGET first." >&2
    exit 1
fi

GIT_COMMIT=$(git -C "$SCRIPT_DIR" rev-parse HEAD 2>/dev/null || echo "unknown")
GIT_DESCRIBE=$(git -C "$SCRIPT_DIR" describe --always --dirty --tags 2>/dev/null || echo "unknown")
GIT_BRANCH=$(git -C "$SCRIPT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
echo "========================================="
echo "  ark_jetson_kernel"
echo "  Date:     $(date -Iseconds)"
echo "  Branch:   $GIT_BRANCH"
echo "  Commit:   $GIT_COMMIT"
echo "  Describe: $GIT_DESCRIBE"
echo "========================================="
echo "  Target:   $TARGET"
echo "  Storage:  $STORAGE_DEV"
echo "  Board:    $FLASH_TARGET"
echo "========================================="

read -p "Flash $TARGET? (y/N): " confirm
if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
    echo "Flash aborted."
    exit 0
fi

sudo -v

echo "Waiting for device in recovery mode..."

while true; do
    for pid in 7323 7423 7523 7623; do
        if lsusb -d 0955:${pid} > /dev/null 2>&1; then
            break 2
        fi
    done
    sleep 1
done

cd "$L4T_DIR"

if [ "$USE_INITRD" = true ]; then
    sudo ./tools/kernel_flash/l4t_initrd_flash.sh --external-device "$STORAGE_DEV" \
        -p "-c ./bootloader/generic/cfg/flash_t234_qspi.xml" \
        -c ./tools/kernel_flash/flash_l4t_t234_nvme.xml \
        --showlogs --network usb0 \
        "$FLASH_TARGET" "$STORAGE_DEV"
else
    sudo ./flash.sh "$FLASH_TARGET" "$STORAGE_DEV"
fi
