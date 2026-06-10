#!/bin/bash

# Generates a self-contained flash package (.tar.gz) from a built (and ideally
# --provision'd) staging tree. The package IS the staged Linux_for_Tegra tree
# (minus the kernel build sources); flash_from_package.sh flashes it with
# NVIDIA's initrd flasher, which reads the connected module's EEPROM at flash
# time and selects the matching bootloader + SDRAM config. One package therefore
# flashes every Orin Nano/NX variant (4GB/8GB/16GB) — no per-SKU build.
#
# This replaces the old massflash "mfi" package, which pre-baked a single
# BOARDSKU and could only flash that one module: NVIDIA massflash requires every
# unit be identical hardware (tools/kernel_flash/README_initrd_flash.txt).
#
# Prerequisites: run setup.sh and build.sh <TARGET> --provision first.
#
# Usage: ./generate_flash_package.sh <TARGET> [--no-super]

set -euo pipefail

STORAGE_DEV="nvme0n1p1"
FLASH_TARGET="jetson-orin-nano-devkit-super"
TARGET=""

while [[ $# -gt 0 ]]; do
    case $1 in
        PAB|JAJ|PAB_V3) TARGET="$1"; shift ;;
        --no-super)     FLASH_TARGET="jetson-orin-nano-devkit"; shift ;;
        *)
            echo "Unknown option: $1" >&2
            echo "Usage: ./generate_flash_package.sh <PAB | JAJ | PAB_V3> [--no-super]" >&2
            exit 1 ;;
    esac
done

if [ -z "$TARGET" ]; then
    echo "ERROR: target required (PAB | JAJ | PAB_V3)." >&2
    echo "Usage: ./generate_flash_package.sh <PAB | JAJ | PAB_V3> [--no-super]" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
STAGING_DIR="$ROOT_DIR/staging/$TARGET"
L4T_DIR="$STAGING_DIR/Linux_for_Tegra"

# staging/ is root-owned (created by the build container); tee the log through sudo.
sudo -v
exec > >(sudo tee "$STAGING_DIR/generate_flash_package.log.txt") 2>&1

if [ ! -d "$L4T_DIR" ]; then
    echo "ERROR: staging/$TARGET/Linux_for_Tegra not found. Run build.sh $TARGET first." >&2
    exit 1
fi
if [ ! -f "$L4T_DIR/kernel/Image" ]; then
    echo "ERROR: Kernel Image not found in staging/$TARGET/. Run build.sh $TARGET first." >&2
    exit 1
fi

GIT_COMMIT=$(git -C "$ROOT_DIR" rev-parse HEAD 2>/dev/null || echo "unknown")
GIT_DESCRIBE=$(git -C "$ROOT_DIR" describe --always --dirty --tags 2>/dev/null || echo "unknown")
GIT_BRANCH=$(git -C "$ROOT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
BUILD_DATE=$(date -Iseconds)
BUILD_HOST=$(hostname)
BUILD_USER=$(whoami)

TARGET_LOWER=$(echo "$TARGET" | tr '[:upper:]' '[:lower:]' | tr '_' '-')
PACKAGE_NAME="ark-${TARGET_LOWER}-nvme"
if [[ "$FLASH_TARGET" == *"-super" ]]; then
    PACKAGE_NAME="${PACKAGE_NAME}-super"
fi

echo "========================================="
echo "  Generating flash package (auto-detect, all module variants)"
echo "  Target:  $TARGET"
echo "  Board:   $FLASH_TARGET"
echo "  Storage: $STORAGE_DEV"
echo "  Commit:  $GIT_COMMIT"
echo "  Output:  ${PACKAGE_NAME}.tar.gz"
echo "========================================="

# Flash parameters consumed by flash_from_package.sh. The module variant is read
# from the module EEPROM at flash time, so nothing here pins a BOARDSKU.
sudo tee "$L4T_DIR/ark_flash.conf" >/dev/null <<EOF
FLASH_TARGET="$FLASH_TARGET"
STORAGE_DEV="$STORAGE_DEV"
QSPI_CFG="bootloader/generic/cfg/flash_t234_qspi.xml"
EXTERNAL_CFG="tools/kernel_flash/flash_l4t_t234_nvme.xml"
EOF

sudo tee "$L4T_DIR/BUILD_INFO.txt" >/dev/null <<EOF
ark_jetson_kernel flash package
===============================
Build date:      $BUILD_DATE
Build host:      $BUILD_HOST
Build user:      $BUILD_USER
Branch:          $GIT_BRANCH
Commit:          $GIT_COMMIT
Describe:        $GIT_DESCRIBE

Target:          $TARGET
Flash target:    $FLASH_TARGET
Storage:         $STORAGE_DEV
Package name:    ${PACKAGE_NAME}.tar.gz
Module variants: auto-detected at flash time (Orin Nano/NX 4/8/16GB)
EOF

# Drop build-only files: the flasher builds the images from rootfs/bootloader/
# kernel/tools at flash time, so the kernel source tree (several GB), any stale
# pre-generated images, and the L4T debs (apply_binaries already installed them
# into the rootfs at build time) are all dead weight in the package.
# Also never archive the rootfs pseudo-filesystems: if a chroot bind mount leaked
# (see build.sh cleanup_chroot), tar would otherwise capture live /sys + /proc
# (tens of thousands of bogus entries, and /proc/kcore is a 128TB trap). Keep the
# empty mount-point dirs by excluding only their contents.
PRUNE=(
    --exclude='Linux_for_Tegra/source'
    --exclude='Linux_for_Tegra/tools/kernel_flash/images'
    --exclude='Linux_for_Tegra/nv_tegra/l4t_deb_packages'
    --exclude='Linux_for_Tegra/rootfs/proc/*'
    --exclude='Linux_for_Tegra/rootfs/sys/*'
)

OUTPUT_FILE="$ROOT_DIR/${PACKAGE_NAME}.tar.gz"
rm -f "$OUTPUT_FILE"

# pigz parallelizes the multi-GB compress; fall back to gzip when it's absent.
# Single-threaded gzip on the full rootfs takes tens of minutes, so say which
# path we're on rather than silently crawling (CI installs pigz).
if command -v pigz >/dev/null 2>&1; then
    COMPRESS=(--use-compress-program=pigz)
    echo "Compressing with pigz (parallel)."
else
    COMPRESS=(-z)
    echo "WARNING: pigz not found — using single-threaded gzip (slow). Install pigz to speed this up."
fi

echo "Archiving Linux_for_Tegra tree (this takes a few minutes)..."
# --numeric-owner + --xattrs preserve rootfs ownership and file capabilities so
# the flashed OS matches the staged rootfs exactly. rootfs/sys (and /proc) are live
# mount points — their contents are excluded above, but tar still stats the dir and
# trips "file changed as we read it", which is exit code 1. That's a warning, not a
# failure: accept rc<=1 and only abort on a real error (rc>=2, e.g. pigz failure).
tar_rc=0
sudo tar "${COMPRESS[@]}" --numeric-owner --xattrs --xattrs-include='*' \
    --warning=no-file-changed --warning=no-file-shrank \
    "${PRUNE[@]}" \
    -cpf "$OUTPUT_FILE" -C "$STAGING_DIR" Linux_for_Tegra || tar_rc=$?
if [ "$tar_rc" -gt 1 ]; then
    echo "ERROR: tar failed (exit $tar_rc)." >&2
    exit 1
fi
sudo chown "$(id -u):$(id -g)" "$OUTPUT_FILE"

FILE_SIZE=$(stat -c%s "$OUTPUT_FILE")
MAX_SIZE=$((2 * 1024 * 1024 * 1024))

if [ "$FILE_SIZE" -gt "$MAX_SIZE" ]; then
    echo ""
    echo "Package exceeds 2GB ($(numfmt --to=iec-i --suffix=B "$FILE_SIZE")) — splitting for GitHub Releases..."
    SPLIT_DIR="$ROOT_DIR/${PACKAGE_NAME}_split"
    rm -rf "$SPLIT_DIR"
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
