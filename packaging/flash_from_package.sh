#!/bin/bash

# Flash a Jetson from a prebuilt ARK flash package.
# Downloads the package from GitHub Releases, reassembles if split, and flashes.
# No build tools or kernel source needed — just a Linux host with USB.
#
# Progress is cached in ~/.ark-flash/ so re-running skips completed steps
# (no re-downloading or re-extracting on retry).
#
# Usage: ./flash_from_package.sh [version]
#        ./flash_from_package.sh              # uses latest release
#        ./flash_from_package.sh 0.0.6        # specific version
#        ./flash_from_package.sh package.tar.gz  # local file
#        ./flash_from_package.sh --clean       # remove cached data

set -e

REPO="ARK-Electronics/ark_jetson_kernel"
API_URL="https://api.github.com/repos/$REPO/releases"
CACHE_BASE="$HOME/.ark-flash"

# --- Handle --clean ---

if [ "${1:-}" = "--clean" ]; then
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
        echo "ERROR: Ubuntu 22.04 is required for flashing Jetson devices (found $DISTRO_VER)."
        exit 1
    fi
else
    echo "WARNING: Could not detect Ubuntu version. Ubuntu 22.04 is required."
fi

# --- Install all prerequisites upfront ---
# These packages are required by l4t_initrd_flash.sh (from NVIDIA's l4t_flash_prerequisites.sh)

FLASH_PREREQS=(abootimg binfmt-support binutils cpio cpp curl
    device-tree-compiler dosfstools
    iproute2 iputils-ping lbzip2 libxml2-utils lz4
    netcat-openbsd nfs-kernel-server openssl
    python3-yaml qemu-user-static rsync sshpass
    udev usbutils uuid-runtime whois xmlstarlet zstd)

echo "Installing prerequisites..."
sudo apt-get update -qq
sudo apt-get install -y "${FLASH_PREREQS[@]}"
echo "All prerequisites satisfied."

# --- Determine input and cache directory ---

INPUT="${1:-}"
TARBALL=""
CACHE_DIR=""
NEED_DOWNLOAD=false

if [ -n "$INPUT" ] && [ -f "$INPUT" ]; then
    # Local tarball
    TARBALL="$(realpath "$INPUT")"
    CACHE_DIR="$CACHE_BASE/local-$(basename "$INPUT" .tar.gz)"
    echo "Using local tarball: $TARBALL"

elif [ -n "$INPUT" ] && [ -d "$INPUT" ]; then
    # Local directory with split parts
    local_dir="$(realpath "$INPUT")"
    CACHE_DIR="$CACHE_BASE/local-$(basename "$INPUT")"
    TARBALL="$CACHE_DIR/package.tar.gz"

    if [ ! -f "$TARBALL" ]; then
        if ls "$local_dir"/*.part.* &>/dev/null; then
            mkdir -p "$CACHE_DIR"
            echo "Reassembling split parts from $local_dir..."
            cat "$local_dir"/*.part.* > "$TARBALL"
            echo "Reassembled: $TARBALL"
        else
            echo "ERROR: No split parts found in $local_dir"
            exit 1
        fi
    else
        echo "Using cached reassembled tarball."
    fi

else
    # GitHub release
    NEED_DOWNLOAD=true

    if [ -z "$INPUT" ]; then
        echo "Fetching latest release..."
        release_url="$API_URL/latest"
    else
        echo "Fetching release $INPUT..."
        release_url="$API_URL/tags/$INPUT"
    fi

    release_json=$(curl -sL "$release_url")
    release_json=$(echo "$release_json" | sed 's/" *: *"/":"/g')

    if echo "$release_json" | grep -q '"message"'; then
        msg=$(echo "$release_json" | grep -o '"message":"[^"]*"' | head -1)
        echo "ERROR: Release not found ($msg)"
        echo ""
        echo "Available releases:"
        curl -sL "$API_URL" | sed 's/" *: *"/":"/g' | grep -o '"tag_name":"[^"]*"' | sed 's/"tag_name":"//;s/"//' | head -10
        exit 1
    fi

    RELEASE_TAG=$(echo "$release_json" | grep -o '"tag_name":"[^"]*"' | sed 's/"tag_name":"//' | sed 's/"//')
    if [ -z "$RELEASE_TAG" ]; then
        echo "ERROR: Could not parse release tag from GitHub API response"
        exit 1
    fi
    echo "Release: $RELEASE_TAG"

    CACHE_DIR="$CACHE_BASE/$RELEASE_TAG"
    TARBALL="$CACHE_DIR/package.tar.gz"
fi

EXTRACT_DIR="$CACHE_DIR/extracted"

# --- Check if already extracted (skip download/reassemble/extract entirely) ---

FLASH_SCRIPT=""
if [ -d "$EXTRACT_DIR" ]; then
    FLASH_SCRIPT=$(sudo find "$EXTRACT_DIR" -name "l4t_initrd_flash.sh" -path "*/tools/kernel_flash/*" 2>/dev/null | head -1)
fi

if [ -n "$FLASH_SCRIPT" ]; then
    echo "Using cached extraction."
else
    # --- Step 1: Download (GitHub releases only) ---

    if [ "$NEED_DOWNLOAD" = true ]; then
        if [ -f "$TARBALL" ]; then
            echo "Using cached tarball."
        elif ls "$CACHE_DIR"/*.part.* &>/dev/null 2>&1; then
            echo "Using cached download parts."
        else
            assets=$(echo "$release_json" | grep -o '"browser_download_url":"[^"]*"' | sed 's/"browser_download_url":"//' | sed 's/"//' | grep -v '/archive/')

            if [ -z "$assets" ]; then
                echo "ERROR: No downloadable assets found in release $RELEASE_TAG"
                exit 1
            fi

            mkdir -p "$CACHE_DIR"
            echo "Downloading to $CACHE_DIR ..."
            echo ""

            while IFS= read -r url; do
                filename=$(basename "$url")

                if [ "$filename" = "flash_from_package.sh" ]; then
                    continue
                fi

                if [ -f "$CACHE_DIR/$filename" ]; then
                    echo "  Already exists: $filename (skipping)"
                    continue
                fi

                echo "  Downloading $filename..."
                curl -L --progress-bar -o "$CACHE_DIR/$filename" "$url"
            done <<< "$assets"

            echo ""
        fi

        # --- Step 2: Reassemble split parts if needed ---

        if [ ! -f "$TARBALL" ]; then
            if ls "$CACHE_DIR"/*.part.* &>/dev/null; then
                echo "Reassembling split parts..."
                cat "$CACHE_DIR"/*.part.* > "$TARBALL"
                echo "Reassembled: $TARBALL"
            else
                found=$(ls "$CACHE_DIR"/*.tar.gz 2>/dev/null | head -1)
                if [ -n "$found" ]; then
                    TARBALL="$found"
                else
                    echo "ERROR: No .tar.gz or split parts found in $CACHE_DIR"
                    exit 1
                fi
            fi
        fi
    fi

    # --- Step 3: Extract ---

    mkdir -p "$EXTRACT_DIR"
    echo ""
    echo "Extracting flash package to $EXTRACT_DIR ..."
    sudo tar xf "$TARBALL" -C "$EXTRACT_DIR"

    FLASH_SCRIPT=$(sudo find "$EXTRACT_DIR" -name "l4t_initrd_flash.sh" -path "*/tools/kernel_flash/*" | head -1)
    if [ -z "$FLASH_SCRIPT" ]; then
        echo "ERROR: l4t_initrd_flash.sh not found in package."
        echo "This doesn't appear to be a valid massflash package."
        exit 1
    fi
fi

FLASH_DIR=$(dirname "$FLASH_SCRIPT")

# --- Wait for Jetson and flash ---

echo ""
echo "Waiting for Jetson in recovery mode..."
echo "  (Connect USB and hold Force Recovery button while powering on)"
echo "  Looking for NVIDIA recovery device (0955:7323) via lsusb..."
echo ""

while ! lsusb -d 0955:7323 > /dev/null 2>&1; do
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
echo ""
echo "Cached data is stored in $CACHE_DIR"
echo "To free disk space: ./flash_from_package.sh --clean"
