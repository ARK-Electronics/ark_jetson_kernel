#!/bin/bash

# Usage: ./scripts/enable_fast_boot.sh <TARGET> [--restore]
#
# Opt-in step between build.sh and flash.sh: swaps the staged tree's boot firmware
# for the reduced NVMe-only UEFI and silences MB1/MB2 and BPMP logging on the debug
# UART, NVIDIA's documented boot-time steps for R36.5. flash.sh sees the marker and
# bakes BootOrderNvme.dtbo in. --restore puts the stock files back. A full build
# re-stages these files, so re-run this after each one; --fast rebuilds keep it.
# See docs/fast_boot.md.

set -e -o pipefail

TARGET=""
RESTORE=false
for arg in "$@"; do
    case "$arg" in
        PAB|JAJ|PAB_V3) TARGET="$arg" ;;
        --restore)      RESTORE=true ;;
        *)
            echo "Unknown option: $arg" >&2
            echo "Usage: $0 <PAB | JAJ | PAB_V3> [--restore]" >&2
            exit 1 ;;
    esac
done
if [ -z "$TARGET" ]; then
    echo "Usage: $0 <PAB | JAJ | PAB_V3> [--restore]" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
source "$SCRIPT_DIR/check_bsp.sh"

STAGING_DIR="$ROOT_DIR/staging/$TARGET"
L4T_DIR="$STAGING_DIR/Linux_for_Tegra"
MARKER="$STAGING_DIR/.fast-boot"
UEFI_BIN="$ROOT_DIR/staging/uefi/uefi_ark_nvme_RELEASE.bin"
STAMP="$L4T_DIR/rootfs/etc/ark_jetson_kernel"
MISC_BCT="bootloader/generic/BCT/tegra234-mb1-bct-misc-p3767-0000.dts"
BPMP_DTBS=(bootloader/generic/tegra234-bpmp-3767-{0000,0001,0003,0004}-3768-super.dtb)
# Every file this script replaces; each keeps a .stock copy for --restore.
FILES=(bootloader/uefi_jetson.bin "$MISC_BCT" "${BPMP_DTBS[@]}")

if [ ! -f "$L4T_DIR/kernel/Image" ]; then
    echo "ERROR: staging/$TARGET/ has no built image. Run ./build.sh $TARGET first." >&2
    exit 1
fi
require_bsp_staging "$STAGING_DIR"

# staging/ is root-owned (build container).
sudo -v

if [ "$RESTORE" = true ]; then
    if [ ! -f "$MARKER" ]; then
        echo "Fast boot is not enabled for $TARGET; nothing to restore."
        exit 0
    fi
    for f in "${FILES[@]}"; do
        sudo mv -f "$L4T_DIR/$f.stock" "$L4T_DIR/$f"
    done
    sudo sed -i 's/^fast_boot=.*/fast_boot=0/' "$STAMP"
    sudo rm -f "$MARKER"
    echo "Restored stock boot firmware for $TARGET."
    exit 0
fi

if [ -f "$MARKER" ]; then
    echo "Fast boot is already enabled for $TARGET (use --restore to undo)."
    exit 0
fi
if ! command -v fdtput >/dev/null 2>&1; then
    echo "ERROR: fdtput not found. Install it: sudo apt-get install device-tree-compiler" >&2
    exit 1
fi
if [ ! -f "$UEFI_BIN" ]; then
    echo "ERROR: $UEFI_BIN not found. Build it first: ./scripts/build_uefi.sh" >&2
    exit 1
fi
for f in "${FILES[@]}"; do
    if [ ! -f "$L4T_DIR/$f" ]; then
        echo "ERROR: $f missing from the staged BSP; layout changed, re-check this script." >&2
        exit 1
    fi
done
# The MB1 BCT gates its log_level on NVIDIA's own preprocessor switch; make sure
# this BSP still has it before defining it.
if ! grep -q '^#ifdef DISABLE_UART_MB1_MB2' "$L4T_DIR/bootloader/tegra234-mb1-bct-misc-common.dtsi"; then
    echo "ERROR: tegra234-mb1-bct-misc-common.dtsi no longer switches log_level on" >&2
    echo "       DISABLE_UART_MB1_MB2; re-check how this BSP quiets MB1/MB2." >&2
    exit 1
fi

for f in "${FILES[@]}"; do
    sudo cp -p "$L4T_DIR/$f" "$L4T_DIR/$f.stock"
done

echo "Installing the reduced NVMe-only UEFI..."
sudo cp "$UEFI_BIN" "$L4T_DIR/bootloader/uefi_jetson.bin"

echo "Silencing MB1/MB2 logging (log_level 4 -> 0)..."
sudo sed -i '0,/^#include/s//#define DISABLE_UART_MB1_MB2\n#include/' "$L4T_DIR/$MISC_BCT"
grep -q '^#define DISABLE_UART_MB1_MB2' "$L4T_DIR/$MISC_BCT"

# BPMP logs on the combined UART through its /serial node; an empty node is
# NVIDIA's documented way to stop it. One DTB per Orin Nano/NX SKU.
echo "Silencing BPMP logging..."
for dtb in "${BPMP_DTBS[@]}"; do
    path="$L4T_DIR/$dtb"
    for prop in $(fdtget -p "$path" /serial); do
        sudo fdtput -d "$path" /serial "$prop"
    done
    for child in $(fdtget -l "$path" /serial); do
        sudo fdtput -r "$path" "/serial/$child"
    done
    if [ -n "$(fdtget -p "$path" /serial)$(fdtget -l "$path" /serial)" ]; then
        echo "ERROR: /serial in $dtb is not empty after editing." >&2
        exit 1
    fi
done

sudo sed -i 's/^fast_boot=.*/fast_boot=1/' "$STAMP"
sudo touch "$MARKER"

echo ""
echo "Fast boot enabled for $TARGET. Flash with: ./flash.sh $TARGET"
