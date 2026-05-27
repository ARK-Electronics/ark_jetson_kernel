#!/bin/bash

# Usage: ./build.sh <TARGET> [--clean]
#        ./build.sh all [--clean]
#
# TARGET: PAB | JAJ | PAB_V3 | all
# --clean: wipe staging/{TARGET}/ and re-stage from downloads before building.
#
# On first build for a target (or after --clean), this script extracts the BSP,
# root filesystem, and kernel source from downloads/, configures the rootfs,
# applies defconfig fragments and patches, then builds the kernel.  Subsequent
# builds skip the staging step and just recompile.
#
# Each product gets its own self-contained staging/{TARGET}/Linux_for_Tegra/
# directory — no shared mutable state between products.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export ARK_JETSON_KERNEL_DIR="$SCRIPT_DIR"

source "$SCRIPT_DIR/scripts/container_runner.sh"
source "$SCRIPT_DIR/scripts/check_bsp.sh"

if ! needs_container; then
    exec > >(tee "$SCRIPT_DIR/build.log.txt") 2>&1
fi

# ── Argument parsing ────────────────────────────────────────────────────────

CLEAN=0
TARGET=""

for arg in "$@"; do
    case "$arg" in
        PAB|JAJ|PAB_V3) TARGET="$arg" ;;
        all)            TARGET="all" ;;
        --clean)        CLEAN=1 ;;
        *)
            echo "Invalid argument: $arg" >&2
            echo "Usage: $0 <PAB | JAJ | PAB_V3 | all> [--clean]" >&2
            exit 1
            ;;
    esac
done

if [ -z "$TARGET" ]; then
    if [ -t 0 ]; then
        echo "Please select the target platform:"
        echo -e "\033[3mNote: PAB Rev3 is not the same as PAB_V3. PAB_V3 is a separate product.\033[0m"
        echo "1) PAB"
        echo "2) JAJ"
        echo "3) PAB_V3"
        echo "4) all"
        read -p "Enter your choice (1-4): " choice
        case $choice in
            1) TARGET="PAB" ;;
            2) TARGET="JAJ" ;;
            3) TARGET="PAB_V3" ;;
            4) TARGET="all" ;;
            *) echo "Invalid choice. Exiting."; exit 1 ;;
        esac
    else
        echo "ERROR: target required (PAB | JAJ | PAB_V3 | all) when running non-interactively." >&2
        echo "Usage: $0 <PAB | JAJ | PAB_V3 | all> [--clean]" >&2
        exit 1
    fi
fi

# ── Validate downloads (before container handoff or build) ──────────────────

DOWNLOADS_DIR="$SCRIPT_DIR/downloads"
L4T_RELEASE_PACKAGE=$(basename "$BSP_URL")
SAMPLE_FS_PACKAGE=$(basename "$ROOT_FS_URL")

if [ "$TARGET" != "all" ]; then
    for f in "$L4T_RELEASE_PACKAGE" "public_sources.tbz2" "$SAMPLE_FS_PACKAGE"; do
        if [ ! -f "$DOWNLOADS_DIR/$f" ]; then
            echo "ERROR: $f not found in downloads/." >&2
            echo "       Run ./setup.sh first to download the BSP." >&2
            exit 1
        fi
    done
fi

if [ "$TARGET" = "all" ]; then
    for t in PAB JAJ PAB_V3; do
        echo ""
        echo "========================================="
        echo "  Building $t"
        echo "========================================="
        ARGS=("$t")
        [ "$CLEAN" -eq 1 ] && ARGS+=("--clean")
        "$0" "${ARGS[@]}"
    done
    exit 0
fi

export TARGET

# Re-exec inside the 22.04 build container if needed. Pass the resolved
# TARGET (not the original $@) so the container doesn't re-prompt.
if needs_container; then
    CONTAINER_ARGS=("$TARGET")
    [ "$CLEAN" -eq 1 ] && CONTAINER_ARGS+=("--clean")
    run_in_container "$0" "${CONTAINER_ARGS[@]}"
fi

set -e -o pipefail

sudo -v

START_TIME=$(date +%s)

STAGING_DIR="$SCRIPT_DIR/staging/$TARGET"
L4T_DIR="$STAGING_DIR/Linux_for_Tegra"
SOURCE_DIR="$L4T_DIR/source"
PRODUCT_DIR="$SCRIPT_DIR/products/$TARGET"

if [ ! -d "$PRODUCT_DIR" ]; then
    echo "ERROR: products/$TARGET/ does not exist." >&2
    exit 1
fi

export CROSS_COMPILE=$HOME/l4t-gcc/aarch64--glibc--stable-2022.08-1/bin/aarch64-buildroot-linux-gnu-
export KERNEL_HEADERS="$SOURCE_DIR/kernel/kernel-jammy-src"
export INSTALL_MOD_PATH="$L4T_DIR/rootfs/"

# ── Clean if requested ──────────────────────────────────────────────────────

if [ "$CLEAN" -eq 1 ] && [ -d "$STAGING_DIR" ]; then
    echo "Cleaning staging/$TARGET/..."
    sudo rm -rf "$STAGING_DIR"
fi

# ── Stage target (first build only) ────────────────────────────────────────

if [ ! -d "$L4T_DIR" ]; then
    echo "========================================="
    echo "  Staging $TARGET (first build)"
    echo "========================================="

    mkdir -p "$STAGING_DIR"

    # Extract BSP
    echo "Extracting L4T BSP..."
    tar xf "$DOWNLOADS_DIR/$L4T_RELEASE_PACKAGE" -C "$STAGING_DIR/"

    # Extract root filesystem
    echo "Extracting sample root filesystem (this takes a while)..."
    sudo tar xpf "$DOWNLOADS_DIR/$SAMPLE_FS_PACKAGE" -C "$L4T_DIR/rootfs/"

    # Flash prerequisites
    echo "Satisfying flash prerequisites..."
    sudo "$L4T_DIR/tools/l4t_flash_prerequisites.sh"

    # qemu binfmt handler for chroot into aarch64 rootfs
    if [ ! -e /proc/sys/fs/binfmt_misc/qemu-aarch64 ]; then
        echo "Registering qemu-aarch64 binfmt handler"
        sudo update-binfmts --enable qemu-aarch64
    fi

    # Apply NVIDIA binary packages into rootfs
    echo "Applying binaries..."
    sudo "$L4T_DIR/apply_binaries.sh" --debug

    # Configure default user (jetson/jetson)
    echo "Setting up login credentials..."
    sudo "$L4T_DIR/tools/l4t_create_default_user.sh" \
        -u jetson -p jetson -n jetson -a --accept-license

    # Extract kernel source
    echo "Extracting kernel sources..."
    tar -xjf "$DOWNLOADS_DIR/public_sources.tbz2" -C "$STAGING_DIR/"
    pushd "$SOURCE_DIR" > /dev/null
    tar xf kernel_src.tbz2
    tar xf kernel_oot_modules_src.tbz2
    tar xf nvidia_kernel_display_driver_source.tbz2
    popd > /dev/null

    # Apply defconfig fragments
    DEFCONFIG="$SOURCE_DIR/kernel/kernel-jammy-src/arch/arm64/configs/defconfig"
    echo "Applying shared defconfig fragment..."
    cat "$SCRIPT_DIR/defconfig.fragment" >> "$DEFCONFIG"

    if [ -f "$PRODUCT_DIR/defconfig.fragment" ]; then
        echo "Applying $TARGET defconfig fragment..."
        cat "$PRODUCT_DIR/defconfig.fragment" >> "$DEFCONFIG"
    fi

    # Apply patches
    pushd "$SOURCE_DIR" > /dev/null

    apply_patch() {
        local patch_file="$1"
        local label="$2"
        local apply_dir="$3"

        pushd "$apply_dir" > /dev/null
        echo "Checking if $label patch has already been applied..."
        if patch -p1 -R --dry-run --force < "$patch_file" &>/dev/null; then
            echo "  $label patch is already applied."
        elif patch -p1 --dry-run --force < "$patch_file" &>/dev/null; then
            echo "  Applying $label patch..."
            if ! patch -p1 --force < "$patch_file"; then
                echo "  Error: Failed to apply $label patch."
                popd > /dev/null
                exit 1
            fi
            echo "  $label patch applied successfully."
        else
            echo "  Error: $label patch cannot be applied cleanly to $apply_dir."
            popd > /dev/null
            exit 1
        fi
        popd > /dev/null
    }

    apply_patch \
        "$SCRIPT_DIR/patches/JetsonOrinNX_OrinNano_JetPack6.2_L4T36.4.3_Jetvariety.patch" \
        "Jetvariety" \
        "."

    apply_patch \
        "$SCRIPT_DIR/patches/pinctrl-tegra-sfsel.patch" \
        "pinctrl-tegra-sfsel" \
        "kernel/kernel-jammy-src"

    popd > /dev/null

    echo "Staging complete for $TARGET."
    echo ""
fi

# ── BSP version check ──────────────────────────────────────────────────────

require_bsp_staging "$STAGING_DIR"

# ── Copy product device tree overlay ────────────────────────────────────────
# Always runs — picks up device tree edits between builds.

echo "Copying $TARGET device tree overlay into source tree..."
cp -r "$PRODUCT_DIR/device_tree/"* "$L4T_DIR/"

# Inject ARK target identifier into super DTS model strings.  The pattern
# matches both the original NVIDIA string and any previously-injected ARK
# string, so re-builds after a device tree update work correctly.
find "$SOURCE_DIR/hardware/nvidia/t23x/nv-public/nv-platform/" \
    -name "*-nv-super.dts" \
    -exec sed -i \
        "s/\(Engineering Reference Developer Kit\|ARK [A-Z_0-9]* Jetson Carrier\) Super/ARK ${TARGET} Jetson Carrier Super/" {} +

# ── Build ───────────────────────────────────────────────────────────────────

echo ""
echo "========================================="
echo "  Building kernel for $TARGET"
echo "========================================="

cd "$SOURCE_DIR"

make -C kernel && make modules && make dtbs
if [ $? -ne 0 ]; then
    echo "Kernel build failed."
    exit 1
fi

echo "Installing in-tree modules and dtbs..."
sudo -E make install -C kernel
if [ $? -ne 0 ]; then
    echo "Failed to install in-tree modules and dtbs."
    exit 1
fi

echo "Installing out-of-tree modules..."
sudo -E make modules_install
if [ $? -ne 0 ]; then
    echo "Failed to install out-of-tree modules."
    exit 1
fi

# ── Fix module symlinks ─────────────────────────────────────────────────────

JETSON_KERNEL_VERSION="5.15.148-tegra"
MODULES_PATH="$INSTALL_MOD_PATH/lib/modules/$JETSON_KERNEL_VERSION"
HEADERS_TARGET="/usr/src/linux-headers-5.15.148-tegra-ubuntu22.04_aarch64/3rdparty/canonical/linux-jammy/kernel-source"

if [ -d "$MODULES_PATH" ]; then
    echo "Fixing kernel module symlinks in rootfs..."
    sudo rm -f "$MODULES_PATH/build" "$MODULES_PATH/source"
    sudo ln -sfn "$HEADERS_TARGET" "$MODULES_PATH/build"
    sudo ln -sfn "$HEADERS_TARGET" "$MODULES_PATH/source"
else
    echo "WARNING: Module path $MODULES_PATH not found!"
fi

# ── Install build outputs ───────────────────────────────────────────────────

echo "Installing kernel Image..."
cp "$SOURCE_DIR/kernel/kernel-jammy-src/arch/arm64/boot/Image" "$L4T_DIR/kernel/"

DTBS_SOURCE="$SOURCE_DIR/kernel-devicetree/generic-dts/dtbs"

echo "Installing DTBs..."
for variant in 0000 0001 0003 0004 0005; do
    for suffix in "" "-super"; do
        dtb="tegra234-p3768-0000+p3767-${variant}-nv${suffix}.dtb"
        if [ -f "$DTBS_SOURCE/$dtb" ]; then
            sudo cp "$DTBS_SOURCE/$dtb" "$L4T_DIR/rootfs/boot/"
            sudo cp "$DTBS_SOURCE/$dtb" "$L4T_DIR/kernel/dtb/"
        fi
    done
done

echo "Installing overlay dtbo files..."
source_dtbos=()
for dtbo in "$DTBS_SOURCE"/*.dtbo; do
    [ -e "$dtbo" ] || continue
    filename=$(basename "$dtbo")
    source_dtbos+=("$filename")
    sudo cp "$dtbo" "$L4T_DIR/rootfs/boot/"
    sudo cp "$dtbo" "$L4T_DIR/kernel/dtb/"
done

# Remove stale dtbo files (tegra*/ark_* only — preserve UEFI dtbos)
for dir in "$L4T_DIR/rootfs/boot" "$L4T_DIR/kernel/dtb"; do
    for f in "$dir"/tegra*.dtbo "$dir"/ark_*.dtbo; do
        [ -e "$f" ] || continue
        filename=$(basename "$f")
        found=false
        for src in "${source_dtbos[@]}"; do
            if [ "$filename" = "$src" ]; then
                found=true
                break
            fi
        done
        if [ "$found" = false ]; then
            echo "  Removing stale: $filename"
            sudo rm -f "$f"
        fi
    done
done

echo "Installing arducam_csi2.ko..."
sudo cp "$SOURCE_DIR/nvidia-oot/drivers/media/i2c/arducam_csi2.ko" \
    "$L4T_DIR/rootfs/usr/lib/modules/$JETSON_KERNEL_VERSION/updates/drivers/media/i2c/"

# ── Done ────────────────────────────────────────────────────────────────────

END_TIME=$(date +%s)
TOTAL_TIME=$((END_TIME - START_TIME))
echo ""
echo "========================================="
echo "  Build complete: $TARGET"
echo "  Time: $(date -d@${TOTAL_TIME} -u +%H:%M:%S)"
echo "  Staged at: staging/$TARGET/Linux_for_Tegra/"
echo "========================================="
echo ""
echo "Flash with: ./flash.sh $TARGET"
