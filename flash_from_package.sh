#!/bin/bash

# Flash a Jetson from a prebuilt ARK flash package (.tar.gz).
# No build tools or kernel source needed — just a Linux host with USB.
#
# Usage: ./flash_from_package.sh <package.tar.gz>
#        ./flash_from_package.sh <split_dir/>

set -e

if [ $# -lt 1 ]; then
    echo "Usage: ./flash_from_package.sh <package.tar.gz | split_dir/>"
    echo ""
    echo "Flash a prebuilt ARK Jetson image package."
    echo "Put the Jetson in recovery mode before running this script."
    echo ""
    echo "Download packages from:"
    echo "  https://github.com/ARK-Electronics/ark_jetson_kernel/releases"
    exit 1
fi

INPUT="$1"

# Handle split packages (directory with .part.* files)
TARBALL="$INPUT"
if [ -d "$INPUT" ]; then
    if ls "$INPUT"/*.part.* &>/dev/null; then
        TARBALL="$INPUT/reassembled.tar.gz"
        echo "Reassembling split parts..."
        cat "$INPUT"/*.part.* > "$TARBALL"
        echo "Reassembled: $TARBALL"
    else
        echo "ERROR: No split parts found in $INPUT"
        exit 1
    fi
fi

if [ ! -f "$TARBALL" ]; then
    echo "ERROR: File not found: $TARBALL"
    exit 1
fi

# Check for required host tools
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

# Extract to temp directory
WORK_DIR=$(mktemp -d)
cleanup() {
    echo "Cleaning up temp directory..."
    rm -rf "$WORK_DIR"
}
trap cleanup EXIT

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
