#!/bin/bash

# Flash a Jetson from a prebuilt ARK flash package.
# Downloads the package from GitHub Releases, reassembles if split, and flashes.
# No build tools or kernel source needed — just a Linux host with USB.
#
# Usage: ./flash_from_package.sh [version]
#        ./flash_from_package.sh              # uses latest release
#        ./flash_from_package.sh 0.0.6        # specific version
#        ./flash_from_package.sh package.tar.gz  # local file

set -e

REPO="ARK-Electronics/ark_jetson_kernel"
API_URL="https://api.github.com/repos/$REPO/releases"

# --- Determine input mode ---

INPUT="${1:-}"

resolve_tarball_from_local() {
    local input="$1"
    if [ -d "$input" ]; then
        if ls "$input"/*.part.* &>/dev/null; then
            TARBALL="$input/reassembled.tar.gz"
            echo "Reassembling split parts..."
            cat "$input"/*.part.* > "$TARBALL"
            echo "Reassembled: $TARBALL"
        else
            echo "ERROR: No split parts found in $input"
            exit 1
        fi
    elif [ -f "$input" ]; then
        TARBALL="$input"
    else
        echo "ERROR: File not found: $input"
        exit 1
    fi
}

download_release() {
    local version="$1"
    local release_url

    if [ -z "$version" ]; then
        echo "Fetching latest release..."
        release_url="$API_URL/latest"
    else
        echo "Fetching release $version..."
        release_url="$API_URL/tags/$version"
    fi

    local release_json
    release_json=$(curl -sL "$release_url")

    # Check for errors
    if echo "$release_json" | grep -q '"message"'; then
        local msg
        msg=$(echo "$release_json" | grep -o '"message":"[^"]*"' | head -1)
        echo "ERROR: Release not found ($msg)"
        echo ""
        echo "Available releases:"
        curl -sL "$API_URL" | grep -o '"tag_name":"[^"]*"' | sed 's/"tag_name":"//;s/"//' | head -10
        exit 1
    fi

    local tag
    tag=$(echo "$release_json" | grep -o '"tag_name":"[^"]*"' | sed 's/"tag_name":"//' | sed 's/"//')
    echo "Release: $tag"

    # Get asset URLs (exclude source code archives)
    local assets
    assets=$(echo "$release_json" | grep -o '"browser_download_url":"[^"]*"' | sed 's/"browser_download_url":"//' | sed 's/"//' | grep -v '/archive/')

    if [ -z "$assets" ]; then
        echo "ERROR: No downloadable assets found in release $tag"
        exit 1
    fi

    # Download all assets (skip flash_from_package.sh — that's us)
    DOWNLOAD_DIR=$(mktemp -d)
    echo "Downloading to $DOWNLOAD_DIR ..."
    echo ""

    local has_parts=false
    while IFS= read -r url; do
        local filename
        filename=$(basename "$url")

        # Skip downloading ourselves
        if [ "$filename" = "flash_from_package.sh" ]; then
            continue
        fi

        if [[ "$filename" == *.part.* ]]; then
            has_parts=true
        fi

        echo "  Downloading $filename..."
        curl -L --progress-bar -o "$DOWNLOAD_DIR/$filename" "$url"
    done <<< "$assets"

    echo ""

    # Resolve to a tarball
    if [ "$has_parts" = true ]; then
        TARBALL="$DOWNLOAD_DIR/reassembled.tar.gz"
        echo "Reassembling split parts..."
        cat "$DOWNLOAD_DIR"/*.part.* > "$TARBALL"
        rm -f "$DOWNLOAD_DIR"/*.part.*
        echo "Reassembled: $TARBALL"
    else
        TARBALL=$(ls "$DOWNLOAD_DIR"/*.tar.gz 2>/dev/null | head -1)
        if [ -z "$TARBALL" ]; then
            echo "ERROR: No .tar.gz found in downloaded assets"
            ls -la "$DOWNLOAD_DIR"/
            exit 1
        fi
    fi
}

# Decide: local file/dir, or download from GitHub
if [ -n "$INPUT" ] && { [ -f "$INPUT" ] || [ -d "$INPUT" ]; }; then
    # Local file or directory
    resolve_tarball_from_local "$INPUT"
else
    # Version tag or latest
    download_release "$INPUT"
fi

# --- Check host prerequisites ---

MISSING=()
for cmd in lsusb python3 ssh; do
    if ! command -v "$cmd" &>/dev/null; then
        MISSING+=("$cmd")
    fi
done

if [ ${#MISSING[@]} -gt 0 ]; then
    echo "Missing required tools: ${MISSING[*]}"
    echo "Install them with: sudo apt-get install usbutils python3 openssh-client"
    exit 1
fi

# --- Extract and flash ---

WORK_DIR=$(mktemp -d)
cleanup() {
    echo "Cleaning up temp directories..."
    sudo rm -rf "$WORK_DIR"
    [ -n "${DOWNLOAD_DIR:-}" ] && rm -rf "$DOWNLOAD_DIR"
}
trap cleanup EXIT

echo ""
echo "Extracting flash package to $WORK_DIR ..."
tar xf "$TARBALL" -C "$WORK_DIR"

# Find the l4t_initrd_flash.sh inside the extracted package
FLASH_SCRIPT=$(find "$WORK_DIR" -name "l4t_initrd_flash.sh" -path "*/tools/kernel_flash/*" | head -1)
if [ -z "$FLASH_SCRIPT" ]; then
    echo "ERROR: l4t_initrd_flash.sh not found in package."
    echo "This doesn't appear to be a valid massflash package."
    exit 1
fi

FLASH_DIR=$(dirname "$FLASH_SCRIPT")

echo ""
echo "Waiting for Jetson in recovery mode..."
echo "  (Connect USB and hold Force Recovery button while powering on)"
echo "  Looking for: NVIDIA Corp. APX in lsusb"
echo ""

while ! lsusb | grep -q "NVIDIA Corp. APX"; do
    sleep 1
done

echo "Jetson detected in recovery mode!"
echo ""
echo "Starting flash..."

cd "$FLASH_DIR"
sudo ./l4t_initrd_flash.sh --flash-only --massflash 1 --network usb0

echo ""
echo "Flash complete! The Jetson will reboot automatically."
echo "Once booted, connect via: ssh jetson@jetson.local"
