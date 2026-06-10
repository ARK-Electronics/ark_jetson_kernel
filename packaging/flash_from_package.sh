#!/bin/bash

# Flash a Jetson from a prebuilt ARK flash package.
# Downloads the package from GitHub Releases and flashes the QSPI bootloader plus
# the rootfs to NVMe. NVIDIA's initrd flasher reads the connected module's EEPROM
# and selects the matching bootloader/SDRAM config, so one package flashes any
# Orin Nano/NX variant (4GB/8GB/16GB).
#
# A successful flash leaves the generated images behind, and when the next
# module is the same variant (verified against its EEPROM) they are reflashed
# directly with --flash-only, skipping the ~5 min image build.
#
# Usage:
#   ./flash_from_package.sh <tag>        # specific release (e.g. pab-6.2.1.1)
#   ./flash_from_package.sh <product>    # latest release for a product (pab, jaj, pab-v3)
#   ./flash_from_package.sh <tag> --full # regenerate images even if cached ones match
#   ./flash_from_package.sh --clean      # remove all cached data

set -euo pipefail

REPO="ARK-Electronics/ark_jetson_kernel"
API_URL="https://api.github.com/repos/$REPO/releases"
CACHE_BASE="$HOME/.ark-jetson-cache"

usage() {
    echo "Usage: $(basename "$0") <tag|product>"
    echo ""
    echo "  $(basename "$0") pab-6.2.1.1    Flash a specific release"
    echo "  $(basename "$0") pab             Flash the latest PAB release"
    echo "  $(basename "$0") jaj             Flash the latest JAJ release"
    echo "  $(basename "$0") pab-v3          Flash the latest PAB_V3 release"
    echo "  $(basename "$0") pab --full      Regenerate flash images even if cached ones match"
    echo "  $(basename "$0") --clean         Remove all cached data"
    echo ""
    echo "Note: PAB Rev3 is not the same as PAB_V3. PAB_V3 is a separate product."
    exit 1
}

is_product_name() {
    case "$1" in
        pab|jaj|pab-v3) return 0 ;;
        *) return 1 ;;
    esac
}

# --- Handle no args / --help / --clean ---

FORCE_FULL=0
args=()
for arg in "$@"; do
    if [ "$arg" = "--full" ]; then
        FORCE_FULL=1
    else
        args+=("$arg")
    fi
done
set -- "${args[@]}"

if [ $# -eq 0 ] || [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    usage
fi

if [ "$1" = "--clean" ]; then
    if [ -d "$CACHE_BASE" ]; then
        echo "Removing cached data in $CACHE_BASE ..."
        sudo rm -rf "$CACHE_BASE"
        echo "Done."
    else
        echo "No cached data found."
    fi
    exit 0
fi

# --- Check prerequisites available ---

if ! command -v apt-get &>/dev/null; then
    echo "ERROR: apt-get not found. This script requires a Debian/Ubuntu host."
    exit 1
fi

# --- Install prerequisites ---

# Superset of NVIDIA's tools/l4t_flash_prerequisites.sh (plus curl/lz4): the
# flasher builds the QSPI + rootfs images on this host at flash time, so it needs
# the full partitioning/imaging toolchain (gdisk, parted, xxd, file, ...), not
# the minimal set the old --flash-only replay got away with.
FLASH_PREREQS=(abootimg binfmt-support binutils cpio cpp curl
    device-tree-compiler dosfstools file gdisk
    iproute2 iputils-ping lbzip2 libxml2-utils lz4
    netcat-openbsd nfs-kernel-server openssl
    parted python3-yaml qemu-user-static rsync sshpass
    udev usbutils uuid-runtime whois xmlstarlet xxd zstd zlib1g)

missing=()
for pkg in "${FLASH_PREREQS[@]}"; do
    if ! dpkg -s "$pkg" &>/dev/null; then
        missing+=("$pkg")
    fi
done

if [ ${#missing[@]} -gt 0 ]; then
    echo "Installing ${#missing[@]} missing prerequisites..."
    sudo apt-get update -qq
    sudo apt-get install -y "${missing[@]}"
else
    echo "All prerequisites installed."
fi

# --- Resolve input to a release tag ---

INPUT="$1"
RELEASE_TAG=""

if is_product_name "$INPUT"; then
    echo "Finding latest $INPUT release..."
    releases_json=$(curl -sfL "${API_URL}?per_page=100")

    RELEASE_TAG=$(echo "$releases_json" \
        | grep -o '"tag_name": *"[^"]*"' \
        | sed 's/"tag_name": *"//;s/"//' \
        | grep "^${INPUT}-[0-9]" \
        | sort -V \
        | tail -1)

    if [ -z "$RELEASE_TAG" ]; then
        echo "ERROR: No releases found for product '$INPUT'"
        echo ""
        echo "Available releases:"
        echo "$releases_json" | grep -o '"tag_name": *"[^"]*"' | sed 's/"tag_name": *"//;s/"//' | head -10
        exit 1
    fi
    echo "Latest: $RELEASE_TAG"
else
    RELEASE_TAG="$INPUT"
fi

CACHE_DIR="$CACHE_BASE/$RELEASE_TAG"
EXTRACT_DIR="$CACHE_DIR/extracted"

# --- Log output ---

mkdir -p "$CACHE_DIR/logs"
LOG_FILE="$CACHE_DIR/logs/flash-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee "$LOG_FILE") 2>&1

# --- Helpers ---

find_tarball() {
    if [ -f "$CACHE_DIR/package.tar.gz" ]; then
        echo "$CACHE_DIR/package.tar.gz"
    else
        ls "$CACHE_DIR"/*.tar.gz 2>/dev/null | grep -v '\.tmp$' | head -1 || true
    fi
}

find_flash_script() {
    sudo find "$EXTRACT_DIR" -name "l4t_initrd_flash.sh" -path "*/tools/kernel_flash/*" 2>/dev/null | head -1 || true
}

wait_for_jetson() {
    while true; do
        for pid in 7323 7423 7523 7623; do
            if lsusb -d "0955:${pid}" > /dev/null 2>&1; then
                return 0
            fi
        done
        sleep 1
    done
}

# Identity of the module the flash images were built for. BOARDID/FAB/BOARDSKU/
# BOARDREV are the EEPROM fields that parameterize NVIDIA's image generation
# (tools/kernel_flash/README_initrd_flash.txt, offline mode), and generation also
# branches on the chip SKU and DRAM ramcode (p3767.conf.common selects e.g.
# Micron vs Samsung memory config by ramcode), so those are part of the key too.
# The ark_flash.conf values pin the flash configuration. Reads bootloader/cvm.bin
# and chip_info.bin_bak, the EEPROM/chip dumps that both nvautoflash.sh and a
# full flash run leave behind. Must run from the Linux_for_Tegra directory.
module_key() {
    local id fab sku rev chip ram
    [ -f bootloader/cvm.bin ] && [ -f bootloader/chip_info.bin_bak ] || return 1
    id=$(./bootloader/chkbdinfo -i bootloader/cvm.bin 2>/dev/null | tr -d '[:space:]')
    fab=$(./bootloader/chkbdinfo -f bootloader/cvm.bin 2>/dev/null | tr -d '[:space:]')
    sku=$(./bootloader/chkbdinfo -k bootloader/cvm.bin 2>/dev/null | tr -d '[:space:]')
    rev=$(./bootloader/chkbdinfo -r bootloader/cvm.bin 2>/dev/null | tr -d '[:space:]')
    chip=$(./bootloader/chkbdinfo -C bootloader/chip_info.bin_bak 2>/dev/null | tr -d '[:space:]')
    ram=$(./bootloader/chkbdinfo -R bootloader/chip_info.bin_bak 2>/dev/null | tr -d '[:space:]')
    # chkbdinfo reports errors on stdout, so validate the fields rather than
    # trusting non-empty output.
    if ! [[ "$id" =~ ^[0-9]+$ && "$sku" =~ ^[0-9]+$ && "$chip" =~ ^[0-9A-Fa-f:]+$ && "$ram" =~ ^[0-9A-Fa-f:]+$ ]]; then
        return 1
    fi
    echo "boardid=$id fab=$fab boardsku=$sku boardrev=$rev chipsku=$chip ramcode=$ram target=$FLASH_TARGET storage=$STORAGE_DEV qspi=$QSPI_CFG external=$EXTERNAL_CFG"
}

# --- Check if already extracted ---

FLASH_SCRIPT=""
if [ -f "$CACHE_DIR/.extract-done" ] && [ -d "$EXTRACT_DIR" ]; then
    FLASH_SCRIPT=$(find_flash_script)
fi

if [ -n "$FLASH_SCRIPT" ]; then
    echo "Using cached flash package."
else
    # --- Download if not cached ---

    TARBALL=$(find_tarball)

    if [ -n "$TARBALL" ]; then
        echo "Using cached download: $(basename "$TARBALL")"
    else
        echo "Fetching release $RELEASE_TAG..."
        release_json=$(curl -sL "$API_URL/tags/$RELEASE_TAG")

        if echo "$release_json" | grep -q '"message"'; then
            msg=$(echo "$release_json" | grep -o '"message": *"[^"]*"' | head -1)
            echo "ERROR: Release '$RELEASE_TAG' not found ($msg)"
            echo ""
            echo "Available releases:"
            curl -sL "$API_URL" | grep -o '"tag_name": *"[^"]*"' | sed 's/"tag_name": *"//;s/"//' | head -10
            exit 1
        fi

        assets=$(echo "$release_json" | grep -o '"browser_download_url": *"[^"]*"' | sed 's/"browser_download_url": *"//;s/"//' | grep -v '/archive/')

        if [ -z "$assets" ]; then
            echo "ERROR: No downloadable assets found in release $RELEASE_TAG"
            exit 1
        fi

        mkdir -p "$CACHE_DIR"
        rm -f "$CACHE_DIR"/*.tmp

        echo ""
        echo "Downloading to $CACHE_DIR ..."

        while IFS= read -r url; do
            filename=$(basename "$url")
            [ "$filename" = "flash_from_package.sh" ] && continue

            if [ -f "$CACHE_DIR/$filename" ]; then
                echo "  Already downloaded: $filename"
                continue
            fi

            echo "  Downloading $filename..."
            curl -fL --progress-bar -o "$CACHE_DIR/$filename.tmp" "$url"
            mv "$CACHE_DIR/$filename.tmp" "$CACHE_DIR/$filename"
        done <<< "$assets"
        echo ""

        # Reassemble split parts if needed
        if ls "$CACHE_DIR"/*.part.* &>/dev/null; then
            echo "Reassembling split parts..."
            cat "$CACHE_DIR"/*.part.* > "$CACHE_DIR/package.tar.gz.tmp"
            mv "$CACHE_DIR/package.tar.gz.tmp" "$CACHE_DIR/package.tar.gz"
            rm -f "$CACHE_DIR"/*.part.*
        fi

        TARBALL=$(find_tarball)
        if [ -z "$TARBALL" ]; then
            echo "ERROR: No .tar.gz found in $CACHE_DIR after download"
            exit 1
        fi
    fi

    # --- Extract ---

    if [ -d "$EXTRACT_DIR" ] && [ ! -f "$CACHE_DIR/.extract-done" ]; then
        echo "Cleaning up incomplete extraction..."
        sudo rm -rf "$EXTRACT_DIR"
    fi

    mkdir -p "$EXTRACT_DIR"
    echo "Extracting $(basename "$TARBALL") ..."
    # --numeric-owner + --xattrs restore rootfs ownership and file capabilities
    # so the flashed OS matches what was staged at build time.
    sudo tar --numeric-owner --xattrs --xattrs-include='*' -xpf "$TARBALL" -C "$EXTRACT_DIR"
    touch "$CACHE_DIR/.extract-done"

    FLASH_SCRIPT=$(find_flash_script)
    if [ -z "$FLASH_SCRIPT" ]; then
        rm -f "$CACHE_DIR/.extract-done"
        echo "ERROR: l4t_initrd_flash.sh not found in extracted package."
        exit 1
    fi
fi

FLASH_DIR=$(dirname "$FLASH_SCRIPT")          # .../tools/kernel_flash
L4T_DIR=$(cd "$FLASH_DIR/../.." && pwd)        # .../Linux_for_Tegra

# Flash parameters (board config + storage) travel with the package in
# ark_flash.conf. Defaults match flash.sh for older packages that predate it.
FLASH_TARGET="jetson-orin-nano-devkit-super"
STORAGE_DEV="nvme0n1p1"
QSPI_CFG="bootloader/generic/cfg/flash_t234_qspi.xml"
EXTERNAL_CFG="tools/kernel_flash/flash_l4t_t234_nvme.xml"
if [ -f "$L4T_DIR/ark_flash.conf" ]; then
    # shellcheck disable=SC1091
    source "$L4T_DIR/ark_flash.conf"
fi

# --- Display package build info ---

BUILD_INFO=$(sudo find "$EXTRACT_DIR" -maxdepth 3 -name "BUILD_INFO.txt" 2>/dev/null | head -1 || true)
if [ -n "$BUILD_INFO" ]; then
    echo ""
    echo "========================================="
    echo "  Package build info"
    echo "========================================="
    sudo cat "$BUILD_INFO"
    echo "========================================="
fi

# --- Wait for Jetson and flash ---

cd "$L4T_DIR"

echo ""
echo "========================================="
echo "  Ready to flash"
echo "========================================="
echo ""
echo "Waiting for Jetson in recovery mode..."
echo "  Connect USB and hold Force Recovery button while powering on."
echo ""

wait_for_jetson

echo "Jetson detected!"
echo ""

# A successful flash leaves everything needed to flash again without rebuilding:
# the flash images (tools/kernel_flash/images), the signed boot binaries
# (bootloader/), and the saved flash arguments (initrdflashparam.txt), which
# l4t_initrd_flash.sh --flash-only replays verbatim. Those images are SKU-locked
# — the rcmboot blob embeds the generating module's SDRAM config, and replaying
# it on a different variant hangs in RCM boot — so replay only happens after
# probing the connected module's EEPROM and matching it against the recorded
# generation state. The marker is written only after a fully successful run, so
# anything interrupted falls back to full regeneration.
GEN_MARKER="$L4T_DIR/.ark-flash-gen"
REPLAY=0
if [ "$FORCE_FULL" = "1" ]; then
    echo "--full: regenerating flash images."
elif [ -f "$GEN_MARKER" ] && [ -f tools/kernel_flash/initrdflashparam.txt ] && [ -d tools/kernel_flash/images ]; then
    echo "Found flash images from a previous run. Probing module EEPROM..."
    sudo rm -f bootloader/cvm.bin bootloader/chip_info.bin_bak
    if ! sudo ./nvautoflash.sh --print_boardid; then
        echo "ERROR: EEPROM probe failed. Power-cycle the Jetson into recovery mode and retry." >&2
        exit 1
    fi
    MODULE_KEY=$(module_key) || { echo "ERROR: cannot parse module EEPROM dump." >&2; exit 1; }
    # The probe ends by rebooting the module back into recovery mode; wait for
    # the USB device to re-enumerate before flashing.
    sleep 2
    wait_for_jetson
    # After "reboot recovery" an ECID read must precede any other RCM operation
    # (nvautoflash.sh documents this; in NVIDIA's own probe-then-flash chain the
    # next device op, flash.sh's get_fuse_level, is exactly this read). Going
    # straight to the rcmboot download session here stalls at "Sending mb1".
    if ! sudo sh -c 'cd bootloader && ./tegrarcm_v2 --new_session --chip 0x23 --uid' | grep BR_CID; then
        echo "ERROR: Jetson did not respond after the EEPROM probe. Power-cycle it into recovery mode and retry." >&2
        exit 1
    fi
    if [ "$MODULE_KEY" = "$(cat "$GEN_MARKER")" ]; then
        REPLAY=1
        echo "Module matches the existing images. Flashing without regenerating (~5 min faster)."
    else
        echo "Module differs from the existing images. Regenerating."
        echo "  images: $(cat "$GEN_MARKER")"
        echo "  module: $MODULE_KEY"
    fi
fi

if [ "$REPLAY" = "1" ]; then
    # Same invocation as the full flash plus --flash-only: the flasher replays
    # the recorded images instead of regenerating them.
    if ! sudo ./tools/kernel_flash/l4t_initrd_flash.sh \
        --flash-only \
        --external-device "$STORAGE_DEV" \
        -p "-c ./$QSPI_CFG" \
        -c "./$EXTERNAL_CFG" \
        --showlogs --network usb0 \
        "$FLASH_TARGET" "$STORAGE_DEV"; then
        echo "" >&2
        echo "ERROR: flashing from existing images failed." >&2
        echo "Power-cycle the Jetson into recovery mode and rerun (add --full to regenerate the images from scratch)." >&2
        exit 1
    fi
else
    # Drop the marker (and any stale EEPROM dump) before generating, so an
    # interrupted run is never mistaken for valid replay state and the marker
    # below provably reflects this run's EEPROM read.
    sudo rm -f "$GEN_MARKER" bootloader/cvm.bin bootloader/chip_info.bin_bak
    # The initrd flasher reads the connected module's EEPROM and picks the matching
    # bootloader + SDRAM config, so one package flashes any Orin Nano/NX variant. It
    # writes the QSPI bootloader (-p) and the rootfs to the external device (-c) in
    # a single pass.
    sudo ./tools/kernel_flash/l4t_initrd_flash.sh \
        --external-device "$STORAGE_DEV" \
        -p "-c ./$QSPI_CFG" \
        -c "./$EXTERNAL_CFG" \
        --showlogs --network usb0 \
        "$FLASH_TARGET" "$STORAGE_DEV"

    # Key the cache off the EEPROM dump the generation step itself read, not the
    # probe, so the marker always describes what the images were built for.
    if MODULE_KEY=$(module_key); then
        echo "$MODULE_KEY" | sudo tee "$GEN_MARKER" > /dev/null
        echo "Recorded module info: same-variant reflashes will skip image generation."
    else
        echo "WARNING: could not read the module EEPROM dump; the next flash will regenerate images."
    fi
fi

echo ""
echo "========================================="
echo "  Flash complete!"
echo "========================================="
echo ""
echo "The Jetson will reboot automatically."
echo "Once booted, connect via: ssh jetson@jetson.local"
echo ""
echo "Cached data: $CACHE_DIR"
echo "To free disk space: $0 --clean"
