#!/bin/bash

# Flash a Jetson from a prebuilt ARK flash package.
# Downloads the package from GitHub Releases and flashes.
#
# Usage:
#   ./flash_from_package.sh <tag>        # specific release (e.g. pab-6.2.1.1)
#   ./flash_from_package.sh <product>    # latest release for a product (pab, jaj, pab-v3)
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

# --- Check Ubuntu version ---

if [ -f /etc/lsb-release ]; then
    DISTRO_VER=$(grep "DISTRIB_RELEASE" /etc/lsb-release | cut -d= -f2)
    DISTRO_VER_NUM=$(echo "$DISTRO_VER" | sed 's/\.//')
    if [ "$DISTRO_VER_NUM" -lt 2204 ] 2>/dev/null; then
        echo "ERROR: Ubuntu 22.04 or newer is required (found $DISTRO_VER)."
        exit 1
    fi
else
    echo "WARNING: Could not detect Ubuntu version. Ubuntu 22.04+ is required."
fi

# --- Install prerequisites ---

FLASH_PREREQS=(abootimg binfmt-support binutils cpio cpp curl
    device-tree-compiler dosfstools
    iproute2 iputils-ping lbzip2 libxml2-utils lz4
    netcat-openbsd nfs-kernel-server openssl
    python3-yaml qemu-user-static rsync sshpass
    udev usbutils uuid-runtime whois xmlstarlet zstd)

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
    releases_json=$(curl -sL "${API_URL}?per_page=50")

    RELEASE_TAG=$(echo "$releases_json" \
        | grep -o '"tag_name": *"[^"]*"' \
        | sed 's/"tag_name": *"//;s/"//' \
        | while read -r tag; do
            case "$tag" in
                ${INPUT}-[0-9]*) echo "$tag"; break ;;
            esac
        done)

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
            curl -L --progress-bar -o "$CACHE_DIR/$filename.tmp" "$url"
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
    sudo tar xf "$TARBALL" -C "$EXTRACT_DIR"
    touch "$CACHE_DIR/.extract-done"

    FLASH_SCRIPT=$(find_flash_script)
    if [ -z "$FLASH_SCRIPT" ]; then
        rm -f "$CACHE_DIR/.extract-done"
        echo "ERROR: l4t_initrd_flash.sh not found in extracted package."
        exit 1
    fi
fi

FLASH_DIR=$(dirname "$FLASH_SCRIPT")

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

echo ""
echo "========================================="
echo "  Ready to flash"
echo "========================================="
echo ""
echo "Waiting for Jetson in recovery mode..."
echo "  Connect USB and hold Force Recovery button while powering on."
echo ""

while true; do
    for pid in 7323 7423 7523 7623; do
        if lsusb -d 0955:${pid} > /dev/null 2>&1; then
            break 2
        fi
    done
    sleep 1
done

echo "Jetson detected!"
echo ""

cd "$FLASH_DIR"
sudo ./l4t_initrd_flash.sh --flash-only --massflash 1 --network usb0

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
