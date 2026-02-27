#!/bin/bash

# Generates a self-contained massflash package (.tar.gz) from a built kernel image.
# The package can be flashed on any Linux host without build tools or kernel source.
#
# Prerequisites: Run setup.sh and build_kernel.sh first.
#
# Usage: ./generate_flash_package.sh [--sdcard] [--no-super] [--offline]

# Defaults: NVMe + super (same as flash.sh)
STORAGE="nvme"
STORAGE_DEV="nvme0n1p1"
FLASH_TARGET="jetson-orin-nano-devkit-super"
OFFLINE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --sdcard)
            STORAGE="sdcard"
            STORAGE_DEV="mmcblk0p1"
            shift ;;
        --no-super)
            FLASH_TARGET="jetson-orin-nano-devkit"
            shift ;;
        --offline)
            OFFLINE=true
            shift ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: ./generate_flash_package.sh [--sdcard] [--no-super] [--offline]"
            exit 1 ;;
    esac
done

# Log output to file while keeping terminal output
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec > >(tee "$SCRIPT_DIR/generate_flash_package.log.txt") 2>&1

# Validate that a kernel has been built
LAST_TARGET_FILE="$SCRIPT_DIR/source_build/LAST_BUILT_TARGET"
if [ ! -f "$LAST_TARGET_FILE" ]; then
    echo "ERROR: No LAST_BUILT_TARGET file found. Run build_kernel.sh first."
    exit 1
fi
TARGET=$(cat "$LAST_TARGET_FILE")

L4T_DIR="$SCRIPT_DIR/prebuilt/Linux_for_Tegra"
if [ ! -d "$L4T_DIR" ]; then
    echo "ERROR: prebuilt/Linux_for_Tegra not found. Run setup.sh and build_kernel.sh first."
    exit 1
fi

if [ ! -f "$L4T_DIR/kernel/Image" ]; then
    echo "ERROR: Kernel Image not found in prebuilt. Run build_kernel.sh first."
    exit 1
fi

# Determine version from git tag (falls back to "dev")
VERSION=$(git -C "$SCRIPT_DIR" describe --tags --exact-match 2>/dev/null || echo "dev")

# Build descriptive output filename
# e.g. ark-pab-v3-nvme-super-v1.0.0.tar.gz
TARGET_LOWER=$(echo "$TARGET" | tr '[:upper:]' '[:lower:]' | tr '_' '-')
PACKAGE_NAME="ark-${TARGET_LOWER}-${STORAGE}"
if [[ "$FLASH_TARGET" == *"-super" ]]; then
    PACKAGE_NAME="${PACKAGE_NAME}-super"
fi
PACKAGE_NAME="${PACKAGE_NAME}-${VERSION}"

echo "========================================="
echo "  Generating flash package"
echo "  Target:  $TARGET"
echo "  Storage: $STORAGE ($STORAGE_DEV)"
echo "  Board:   $FLASH_TARGET"
echo "  Version: $VERSION"
echo "  Output:  ${PACKAGE_NAME}.tar.gz"
echo "========================================="

# Offline mode: set board IDs so no Jetson needs to be connected
if [ "$OFFLINE" = true ]; then
    echo "Offline mode: using default board IDs (Orin NX 16GB)"
    echo "  BOARDID=3767  FAB=300  BOARDSKU=0000"
    export BOARDID=3767
    export FAB=300
    export BOARDSKU=0000
fi

sudo -v

cd "$L4T_DIR"

# Generate the massflash package
if [ "$STORAGE" = "nvme" ]; then
    sudo -E ./tools/kernel_flash/l4t_initrd_flash.sh \
        --no-flash --massflash 1 \
        --external-device "$STORAGE_DEV" \
        -p "-c ./bootloader/generic/cfg/flash_t234_qspi.xml" \
        -c ./tools/kernel_flash/flash_l4t_t234_nvme.xml \
        --erase-all --network usb0 \
        "$FLASH_TARGET" "$STORAGE_DEV"
else
    # SD card
    sudo -E ./tools/kernel_flash/l4t_initrd_flash.sh \
        --no-flash --massflash 1 \
        --network usb0 \
        "$FLASH_TARGET" "$STORAGE_DEV"
fi

if [ $? -ne 0 ]; then
    echo "ERROR: Flash package generation failed."
    exit 1
fi

# Find the generated mfi tarball
MFI_FILE="mfi_${FLASH_TARGET}.tar.gz"
if [ ! -f "$MFI_FILE" ]; then
    echo "ERROR: Expected output file '$MFI_FILE' not found."
    echo "Looking for mfi files..."
    ls -la mfi_*.tar.gz 2>/dev/null
    exit 1
fi

# Move to project root with descriptive name
OUTPUT_FILE="$SCRIPT_DIR/${PACKAGE_NAME}.tar.gz"
mv "$MFI_FILE" "$OUTPUT_FILE"

# Check size and split if >2GB (GitHub Releases limit)
FILE_SIZE=$(stat -c%s "$OUTPUT_FILE")
MAX_SIZE=$((2 * 1024 * 1024 * 1024))

if [ "$FILE_SIZE" -gt "$MAX_SIZE" ]; then
    echo ""
    echo "Package exceeds 2GB ($(numfmt --to=iec-i --suffix=B "$FILE_SIZE")) — splitting for GitHub Releases..."
    SPLIT_DIR="$SCRIPT_DIR/${PACKAGE_NAME}_split"
    mkdir -p "$SPLIT_DIR"
    split -b 1900m "$OUTPUT_FILE" "$SPLIT_DIR/${PACKAGE_NAME}.part."
    SHA256=$(sha256sum "$OUTPUT_FILE" | cut -d' ' -f1)
    rm "$OUTPUT_FILE"

    # Create reassembly script
    cat > "$SPLIT_DIR/reassemble.sh" << 'EOF'
#!/bin/bash
# Reassemble split flash package parts into a single tarball
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT=$(ls "$SCRIPT_DIR"/*.part.aa | sed 's/\.part\.aa$/.tar.gz/')
echo "Reassembling parts..."
cat "$SCRIPT_DIR"/*.part.* > "$OUTPUT"
echo "Output: $OUTPUT"
echo "SHA256: $(sha256sum "$OUTPUT" | cut -d' ' -f1)"
EOF
    chmod +x "$SPLIT_DIR/reassemble.sh"

    echo ""
    echo "========================================="
    echo "  Package split into parts:"
    echo "  Directory: $SPLIT_DIR"
    ls -lh "$SPLIT_DIR"/
    echo ""
    echo "  Original SHA256: $SHA256"
    echo "========================================="
else
    echo ""
    echo "========================================="
    echo "  Flash package generated successfully"
    echo "  File: $OUTPUT_FILE"
    echo "  Size: $(numfmt --to=iec-i --suffix=B "$FILE_SIZE")"
    echo "  SHA256: $(sha256sum "$OUTPUT_FILE" | cut -d' ' -f1)"
    echo "========================================="
fi
