#!/bin/bash

# Generates a self-contained massflash package (.tar.gz) from a built kernel image.
# The package can be flashed on any Linux host without build tools or kernel source.
#
# Prerequisites: Run setup.sh and build.sh first.
#
# Usage: ./generate_flash_package.sh <TARGET> [--sdcard] [--no-super]

# ── Argument parsing ────────────────────────────────────────────────────────

STORAGE="nvme"
STORAGE_DEV="nvme0n1p1"
FLASH_TARGET="jetson-orin-nano-devkit-super"
TARGET=""

while [[ $# -gt 0 ]]; do
    case $1 in
        PAB|JAJ|PAB_V3)
            TARGET="$1"
            shift ;;
        --sdcard)
            STORAGE="sdcard"
            STORAGE_DEV="mmcblk0p1"
            shift ;;
        --no-super)
            FLASH_TARGET="jetson-orin-nano-devkit"
            shift ;;
        *)
            echo "Unknown option: $1" >&2
            echo "Usage: ./generate_flash_package.sh <PAB | JAJ | PAB_V3> [--sdcard] [--no-super]" >&2
            exit 1 ;;
    esac
done

if [ -z "$TARGET" ]; then
    echo "ERROR: target required (PAB | JAJ | PAB_V3)." >&2
    echo "Usage: ./generate_flash_package.sh <PAB | JAJ | PAB_V3> [--sdcard] [--no-super]" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
exec > >(tee "$ROOT_DIR/staging/$TARGET/generate_flash_package.log.txt") 2>&1

GIT_COMMIT=$(git -C "$ROOT_DIR" rev-parse HEAD 2>/dev/null || echo "unknown")
GIT_DESCRIBE=$(git -C "$ROOT_DIR" describe --always --dirty --tags 2>/dev/null || echo "unknown")
GIT_BRANCH=$(git -C "$ROOT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
BUILD_DATE=$(date -Iseconds)
BUILD_HOST=$(hostname)
BUILD_USER=$(whoami)
echo "========================================="
echo "  ark_jetson_kernel"
echo "  Date:     $BUILD_DATE"
echo "  Branch:   $GIT_BRANCH"
echo "  Commit:   $GIT_COMMIT"
echo "  Describe: $GIT_DESCRIBE"
echo "========================================="

L4T_DIR="$ROOT_DIR/staging/$TARGET/Linux_for_Tegra"
if [ ! -d "$L4T_DIR" ]; then
    echo "ERROR: staging/$TARGET/Linux_for_Tegra not found. Run build.sh $TARGET first." >&2
    exit 1
fi

if [ ! -f "$L4T_DIR/kernel/Image" ]; then
    echo "ERROR: Kernel Image not found in staging/$TARGET/. Run build.sh $TARGET first." >&2
    exit 1
fi

TARGET_LOWER=$(echo "$TARGET" | tr '[:upper:]' '[:lower:]' | tr '_' '-')
PACKAGE_NAME="ark-${TARGET_LOWER}-${STORAGE}"
if [[ "$FLASH_TARGET" == *"-super" ]]; then
    PACKAGE_NAME="${PACKAGE_NAME}-super"
fi

echo "========================================="
echo "  Generating flash package"
echo "  Target:  $TARGET"
echo "  Storage: $STORAGE ($STORAGE_DEV)"
echo "  Board:   $FLASH_TARGET"
echo "  Output:  ${PACKAGE_NAME}.tar.gz"
echo "========================================="

export BOARDID=3767
export FAB=300
export BOARDSKU=0000

sudo -v

cd "$L4T_DIR"

sed -i 's/^fill_devpaths$/if [ "${no_flash}" = "0" ]; then fill_devpaths; fi/' \
    ./tools/kernel_flash/l4t_initrd_flash.sh

if [ "$STORAGE" = "nvme" ]; then
    sudo -E ./tools/kernel_flash/l4t_initrd_flash.sh \
        --no-flash --massflash 1 \
        --external-device "$STORAGE_DEV" \
        -p "-c ./bootloader/generic/cfg/flash_t234_qspi.xml" \
        -c ./tools/kernel_flash/flash_l4t_t234_nvme.xml \
        --erase-all --network usb0 \
        "$FLASH_TARGET" "$STORAGE_DEV"
else
    sudo -E ./tools/kernel_flash/l4t_initrd_flash.sh \
        --no-flash --massflash 1 \
        --network usb0 \
        "$FLASH_TARGET" "$STORAGE_DEV"
fi

if [ $? -ne 0 ]; then
    echo "ERROR: Flash package generation failed."
    exit 1
fi

MFI_FILE="mfi_${FLASH_TARGET}.tar.gz"
if [ ! -f "$MFI_FILE" ]; then
    echo "ERROR: Expected output file '$MFI_FILE' not found."
    echo "Looking for mfi files..."
    ls -la mfi_*.tar.gz 2>/dev/null
    exit 1
fi

BUILD_INFO_DIR=$(mktemp -d)
cat > "$BUILD_INFO_DIR/BUILD_INFO.txt" << EOF
ark_jetson_kernel flash package
===============================
Build date:    $BUILD_DATE
Build host:    $BUILD_HOST
Build user:    $BUILD_USER
Branch:        $GIT_BRANCH
Commit:        $GIT_COMMIT
Describe:      $GIT_DESCRIBE

Target:        $TARGET
Flash target:  $FLASH_TARGET
Storage:       $STORAGE ($STORAGE_DEV)
Package name:  ${PACKAGE_NAME}.tar.gz
EOF

echo "Embedding BUILD_INFO.txt (decompressing, appending, recompressing — may take a minute)..."
sudo gunzip "$MFI_FILE"
MFI_TAR="${MFI_FILE%.gz}"
sudo tar --append -f "$MFI_TAR" -C "$BUILD_INFO_DIR" BUILD_INFO.txt
sudo gzip "$MFI_TAR"
rm -rf "$BUILD_INFO_DIR"

OUTPUT_FILE="$ROOT_DIR/${PACKAGE_NAME}.tar.gz"
mv "$MFI_FILE" "$OUTPUT_FILE"

FILE_SIZE=$(stat -c%s "$OUTPUT_FILE")
MAX_SIZE=$((2 * 1024 * 1024 * 1024))

if [ "$FILE_SIZE" -gt "$MAX_SIZE" ]; then
    echo ""
    echo "Package exceeds 2GB ($(numfmt --to=iec-i --suffix=B "$FILE_SIZE")) — splitting for GitHub Releases..."
    SPLIT_DIR="$ROOT_DIR/${PACKAGE_NAME}_split"
    mkdir -p "$SPLIT_DIR"
    split -b 1900m "$OUTPUT_FILE" "$SPLIT_DIR/${PACKAGE_NAME}.part."
    SHA256=$(sha256sum "$OUTPUT_FILE" | cut -d' ' -f1)
    rm -f "$OUTPUT_FILE"

    cat > "$SPLIT_DIR/reassemble.sh" << 'EOF'
#!/bin/bash
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
