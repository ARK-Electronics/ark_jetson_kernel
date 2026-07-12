#!/bin/bash

# Usage: ./flash.sh <TARGET> [--sdcard] [--usb]
#
# TARGET: PAB | JAJ | PAB_V3 | PAB_CAN
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
        PAB|JAJ|PAB_V3|PAB_CAN)
            TARGET="$1"
            shift ;;
        --sdcard)
            STORAGE_DEV="mmcblk0p1"
            USE_INITRD=false
            shift ;;
        --usb)
            STORAGE_DEV="sda"
            shift ;;
        *)
            echo "Unknown option: $1" >&2
            echo "Usage: ./flash.sh <PAB | JAJ | PAB_V3 | PAB_CAN> [--sdcard] [--usb]" >&2
            exit 1 ;;
    esac
done

if [ -z "$TARGET" ]; then
    if [ -t 0 ]; then
        echo "Please select the target to flash:"
        echo "1) PAB"
        echo "2) JAJ"
        echo "3) PAB_V3"
        echo "4) PAB_CAN"
        read -p "Enter your choice (1-4): " choice
        case $choice in
            1) TARGET="PAB" ;;
            2) TARGET="JAJ" ;;
            3) TARGET="PAB_V3" ;;
            4) TARGET="PAB_CAN" ;;
            *) echo "Invalid choice. Exiting."; exit 1 ;;
        esac
    else
        echo "ERROR: target required (PAB | JAJ | PAB_V3 | PAB_CAN) when running non-interactively." >&2
        echo "Usage: ./flash.sh <PAB | JAJ | PAB_V3 | PAB_CAN> [--sdcard] [--usb]" >&2
        exit 1
    fi
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "$SCRIPT_DIR/scripts/check_bsp.sh"

L4T_DIR="$SCRIPT_DIR/staging/$TARGET/Linux_for_Tegra"

# staging/ is root-owned (created by the build container), so the user can't write
# the log here. Flashing needs root anyway: prime sudo and tee through it.
sudo -v || { echo "ERROR: sudo is required to flash." >&2; exit 1; }
exec > >(sudo tee "$SCRIPT_DIR/staging/$TARGET/flash.log.txt") 2>&1

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

# ── Resolve product default device-tree overlay(s) to bake into the image ────
# Some products ship a default camera (e.g. PAB = quad IMX219). We hand its dtbo
# to tegraflash via ADDITIONAL_DTB_OVERLAY, which appends it to OVERLAY_DTB_FILE so
# it is merged into the base DTB *at flash time* — on top of whichever Orin Nano/NX
# SKU the flasher detects, so one image still covers every SKU and the camera is
# live on the first boot with no jetson-io step. The bootloader hands that merged
# DTB to the kernel; an extlinux OVERLAYS line would instead be applied to the
# symbol-stripped UEFI DTB and silently fail to resolve. jetson-io can still switch
# cameras later: it boots its own FDT'd entry off the clean /boot/dtb kernel DTB,
# which supersedes this with no duplicate-node collision. Each dtbo must have been
# built into kernel/dtb/ by build.sh; fail loud if not.
DEFAULT_OVERLAYS_FILE="$SCRIPT_DIR/products/$TARGET/default_overlays"
ADDITIONAL_DTB_OVERLAY=""
if [ -f "$DEFAULT_OVERLAYS_FILE" ]; then
    while IFS= read -r name; do
        name="${name%%#*}"
        name="$(echo "$name" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
        [ -z "$name" ] && continue
        if [ ! -f "$L4T_DIR/kernel/dtb/$name" ]; then
            echo "ERROR: default overlay '$name' (from $DEFAULT_OVERLAYS_FILE) is not in" >&2
            echo "       staging/$TARGET/Linux_for_Tegra/kernel/dtb/ — rebuild with ./build.sh $TARGET" >&2
            echo "       (is it listed in products/$TARGET/overlay/dtbo.list?)." >&2
            exit 1
        fi
        ADDITIONAL_DTB_OVERLAY="${ADDITIONAL_DTB_OVERLAY:+$ADDITIONAL_DTB_OVERLAY,}$name"
    done < "$DEFAULT_OVERLAYS_FILE"
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

# NetworkManager must not touch the flash-time USB NIC: host profiles matched on
# the gadget drivers (ark-jetson-usb) auto-activate on the initrd's rndis/ncm
# interface and tear down the flasher's NFS link mid-write. Mark those drivers
# unmanaged for the duration of the flash.
NM_FLASH_GUARD=/etc/NetworkManager/conf.d/99-ark-jetson-flash-guard.conf
if command -v nmcli > /dev/null 2>&1 && systemctl is-active --quiet NetworkManager 2>/dev/null; then
    printf '[keyfile]\nunmanaged-devices+=driver:rndis_host;driver:cdc_ncm\n' | \
        sudo tee "$NM_FLASH_GUARD" > /dev/null
    sudo nmcli general reload
    trap 'sudo rm -f "$NM_FLASH_GUARD"; sudo nmcli general reload' EXIT
fi

cd "$L4T_DIR"

if [ -n "$ADDITIONAL_DTB_OVERLAY" ]; then
    echo "Baking default device-tree overlay(s) into the image: $ADDITIONAL_DTB_OVERLAY"
fi

# dtbo basenames carry no spaces, so the unquoted ${var:+NAME=$var} prefix passes
# cleanly as a single sudo environment assignment (and expands to nothing when no
# product default is set).
if [ "$USE_INITRD" = true ]; then
    # l4t_initrd_flash reads ADDITIONAL_DTB_OVERLAY_OPT and forwards it to flash.sh.
    sudo ${ADDITIONAL_DTB_OVERLAY:+ADDITIONAL_DTB_OVERLAY_OPT=$ADDITIONAL_DTB_OVERLAY} \
        ./tools/kernel_flash/l4t_initrd_flash.sh --external-device "$STORAGE_DEV" \
        -p "-c ./bootloader/generic/cfg/flash_t234_qspi.xml" \
        -c ./tools/kernel_flash/flash_l4t_t234_nvme.xml \
        --showlogs --network usb0 \
        "$FLASH_TARGET" "$STORAGE_DEV"
else
    # Classic flash.sh reads ADDITIONAL_DTB_OVERLAY directly.
    sudo ${ADDITIONAL_DTB_OVERLAY:+ADDITIONAL_DTB_OVERLAY=$ADDITIONAL_DTB_OVERLAY} \
        ./flash.sh "$FLASH_TARGET" "$STORAGE_DEV"
fi
