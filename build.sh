#!/bin/bash

# Usage: ./build.sh <PAB|JAJ|PAB_V3|all> [--clean] [--provision]
#   --clean      wipe staging/{TARGET}/ and re-stage from downloads before building
#   --provision  install ARK-OS into the rootfs (staging only: first build or --clean)
#
# First build per target stages the L4T tree under its own staging/{TARGET}/;
# later builds just recompile. Products share no mutable state.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export ARK_JETSON_KERNEL_DIR="$SCRIPT_DIR"

source "$SCRIPT_DIR/scripts/container_runner.sh"
source "$SCRIPT_DIR/scripts/check_bsp.sh"

# ── Argument parsing ────────────────────────────────────────────────────────

CLEAN=0
PROVISION=0
TARGET=""

for arg in "$@"; do
    case "$arg" in
        PAB|JAJ|PAB_V3) TARGET="$arg" ;;
        all)            TARGET="all" ;;
        --clean)        CLEAN=1 ;;
        --provision)    PROVISION=1 ;;
        *)
            echo "Invalid argument: $arg" >&2
            echo "Usage: $0 <PAB | JAJ | PAB_V3 | all> [--clean] [--provision]" >&2
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
            1|PAB)    TARGET="PAB" ;;
            2|JAJ)    TARGET="JAJ" ;;
            3|PAB_V3) TARGET="PAB_V3" ;;
            4|all)    TARGET="all" ;;
            *) echo "Invalid choice. Exiting."; exit 1 ;;
        esac
    else
        echo "ERROR: target required (PAB | JAJ | PAB_V3 | all) when running non-interactively." >&2
        echo "Usage: $0 <PAB | JAJ | PAB_V3 | all> [--clean] [--provision]" >&2
        exit 1
    fi
fi

# ── Ensure BSP downloads are present (before container handoff or build) ─────
# build.sh doesn't download — if any BSP tarball is missing, run setup.sh rather
# than dead-ending. We re-verify after, so a real download failure still aborts.

DOWNLOADS_DIR="$SCRIPT_DIR/downloads"
L4T_RELEASE_PACKAGE=$(basename "$BSP_URL")
SAMPLE_FS_PACKAGE=$(basename "$ROOT_FS_URL")
REQUIRED_TARBALLS=("$L4T_RELEASE_PACKAGE" "$PUBLIC_SOURCES_FILE" "$SAMPLE_FS_PACKAGE")

downloads_present() {
    for f in "${REQUIRED_TARBALLS[@]}"; do
        [ -f "$DOWNLOADS_DIR/$f" ] || return 1
    done
}

if ! downloads_present; then
    echo "BSP tarballs for ${EXPECTED_BSP_RELEASE}.${EXPECTED_BSP_REVISION} are missing from downloads/ — running ./setup.sh first..."
    if ! "$SCRIPT_DIR/setup.sh"; then
        echo "ERROR: ./setup.sh failed — aborting build." >&2
        exit 1
    fi
    if ! downloads_present; then
        echo "ERROR: BSP tarballs still missing after ./setup.sh — aborting build." >&2
        exit 1
    fi
fi

# Capture host OS before any container handoff — inside the build container,
# /etc/os-release describes the container (always 22.04), not the actual host.
[ -z "$ARK_BUILD_OS" ] && export ARK_BUILD_OS=$(. /etc/os-release && echo "$PRETTY_NAME")

# Resolve the build commit on the host: inside the container the bind-mounted repo
# is host-owned, so git there refuses with "dubious ownership".
[ -z "$ARK_BUILD_COMMIT" ] && export ARK_BUILD_COMMIT=$(git -C "$SCRIPT_DIR" rev-parse HEAD)

# Prime sudo once so sub-processes and docker don't each prompt.
sudo -v

if [ "$TARGET" = "all" ]; then
    trap 'echo ""; echo "Aborted."; exit 130' INT
    for t in PAB JAJ PAB_V3; do
        echo ""
        echo "========================================="
        echo "  Building $t"
        echo "========================================="
        ARGS=("$t")
        [ "$CLEAN" -eq 1 ] && ARGS+=("--clean")
        [ "$PROVISION" -eq 1 ] && ARGS+=("--provision")
        "$0" "${ARGS[@]}" || exit $?
    done
    exit 0
fi

export TARGET

# Re-exec in the 22.04 container if needed, passing the resolved TARGET so it
# doesn't re-prompt.
if needs_container; then
    CONTAINER_ARGS=("$TARGET")
    [ "$CLEAN" -eq 1 ] && CONTAINER_ARGS+=("--clean")
    [ "$PROVISION" -eq 1 ] && CONTAINER_ARGS+=("--provision")
    run_in_container "$0" "${CONTAINER_ARGS[@]}"
fi

set -e -o pipefail

sudo -v

START_TIME=$(date +%s)

STAGING_DIR="$SCRIPT_DIR/staging/$TARGET"

# Clean before opening the log: a later rm -rf of $STAGING_DIR would unlink the
# just-created build.log.txt and leave tee writing to a dead inode.
if [ "$CLEAN" -eq 1 ] && [ -d "$STAGING_DIR" ]; then
    # A leaked bind mount under $STAGING_DIR (interrupted provisioning) would make
    # rm -rf recurse into host /proc,/sys,/dev. Refuse to clean until it's unmounted.
    mounts_under=$(mount | awk -v d="$STAGING_DIR/" 'index($3, d)==1 {print $3}')
    if [ -n "$mounts_under" ]; then
        echo "ERROR: active mount(s) under $STAGING_DIR — refusing to 'rm -rf' it" >&2
        echo "       (recursing into them could destroy host /dev). Unmount first:" >&2
        echo "$mounts_under" | sed 's/^/         sudo umount /' >&2
        exit 1
    fi
    echo "Cleaning staging/$TARGET/..."
    sudo rm -rf "$STAGING_DIR"
fi

mkdir -p "$STAGING_DIR"
if ! needs_container; then
    exec > >(tee "$STAGING_DIR/build.log.txt") 2>&1
fi
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

# ── Stage target (first build only) ────────────────────────────────────────

if [ ! -d "$L4T_DIR" ]; then
    echo "========================================="
    echo "  Staging $TARGET (first build)"
    echo "========================================="

    mkdir -p "$STAGING_DIR"

    echo "Extracting L4T BSP..."
    tar xf "$DOWNLOADS_DIR/$L4T_RELEASE_PACKAGE" -C "$STAGING_DIR/"

    echo "Extracting sample root filesystem (this takes a while)..."
    sudo tar xpf "$DOWNLOADS_DIR/$SAMPLE_FS_PACKAGE" -C "$L4T_DIR/rootfs/"

    echo "Satisfying flash prerequisites..."
    sudo "$L4T_DIR/tools/l4t_flash_prerequisites.sh"

    # qemu-aarch64 binfmt handler: lets the host run aarch64 binaries when we chroot
    # into the rootfs during provisioning.
    if [ ! -e /proc/sys/fs/binfmt_misc/qemu-aarch64 ]; then
        echo "Registering qemu-aarch64 binfmt handler"
        sudo update-binfmts --enable qemu-aarch64
    fi

    echo "Applying binaries..."
    sudo "$L4T_DIR/apply_binaries.sh" --debug

    echo "Setting up login credentials..."
    sudo "$L4T_DIR/tools/l4t_create_default_user.sh" \
        -u jetson -p jetson -n jetson -a --accept-license

    if [ "$PROVISION" -eq 1 ]; then
        PROVISION_SCRIPT="$SCRIPT_DIR/provision.sh"
        if [ ! -f "$PROVISION_SCRIPT" ]; then
            echo "ERROR: --provision specified but provision.sh not found." >&2
            exit 1
        fi

        echo "========================================="
        echo "  Provisioning rootfs for $TARGET"
        echo "========================================="

        ROOTFS_DIR="$L4T_DIR/rootfs"

        cleanup_chroot() {
            # /proc and /sys pull in nested submounts (binfmt_misc, cgroup, ...) via
            # mount propagation, so a plain umount fails "busy" and silently leaves them
            # mounted — later bloating the flash package's rootfs tar with live
            # /sys + /proc. -R tears down the whole subtree, -l detaches even if briefly
            # held. Warn loudly if anything survives rather than leaving a dirty rootfs.
            for mp in dev/pts dev sys proc; do
                sudo umount -R -l "$ROOTFS_DIR/$mp" 2>/dev/null || true
            done
            if mount | grep -q " on $ROOTFS_DIR/"; then
                echo "WARNING: mounts still present under $ROOTFS_DIR after cleanup:" >&2
                mount | grep " on $ROOTFS_DIR/" >&2
            fi
        }
        trap cleanup_chroot EXIT

        sudo mount --bind /proc "$ROOTFS_DIR/proc"
        sudo mount --bind /sys "$ROOTFS_DIR/sys"
        sudo mount --bind /dev "$ROOTFS_DIR/dev"
        sudo mount --bind /dev/pts "$ROOTFS_DIR/dev/pts"
        sudo cp /etc/resolv.conf "$ROOTFS_DIR/etc/resolv.conf"

        export ROOTFS_DIR TARGET
        if ! bash "$PROVISION_SCRIPT"; then
            echo "ERROR: rootfs provisioning failed (see output above); aborting build." >&2
            exit 1
        fi

        cleanup_chroot
        trap - EXIT

        echo "Rootfs provisioning complete."
        echo ""
    fi

    echo "Extracting kernel sources..."
    tar -xjf "$DOWNLOADS_DIR/$PUBLIC_SOURCES_FILE" -C "$STAGING_DIR/"
    pushd "$SOURCE_DIR" > /dev/null
    tar xf kernel_src.tbz2
    tar xf kernel_oot_modules_src.tbz2
    tar xf nvidia_kernel_display_driver_source.tbz2
    popd > /dev/null

    DEFCONFIG="$SOURCE_DIR/kernel/kernel-jammy-src/arch/arm64/configs/defconfig"
    echo "Applying shared defconfig fragment..."
    cat "$SCRIPT_DIR/defconfig.fragment" >> "$DEFCONFIG"

    if [ -f "$PRODUCT_DIR/defconfig.fragment" ]; then
        echo "Applying $TARGET defconfig fragment..."
        cat "$PRODUCT_DIR/defconfig.fragment" >> "$DEFCONFIG"
    fi

    echo "Staging complete for $TARGET."
    echo ""
else
    if [ "$PROVISION" -eq 1 ]; then
        echo "WARNING: --provision has no effect — staging/$TARGET/ already exists." >&2
        echo "         Use --clean --provision to re-stage with provisioning." >&2
    fi
fi

# ── BSP version check ──────────────────────────────────────────────────────

require_bsp_staging "$STAGING_DIR"

# ── Copy product device tree overlay ────────────────────────────────────────
# Runs every build (not just staging) to pick up device-tree edits.

echo "Copying $TARGET device tree overlay into source tree..."
cp -r "$PRODUCT_DIR/device_tree/"* "$L4T_DIR/"

# Inject the ARK target name into super DTS model strings. The pattern also matches
# an already-injected name, so rebuilds after a device-tree update stay correct.
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

# Wrap the cross-compiler with ccache when present (kernel C rarely changes, so warm
# builds are mostly hits). Passing CC on the command line overrides kbuild's default
# and reaches the NVIDIA wrapper's sub-makes; no-op without ccache.
KERNEL_MAKE_ARGS=()
if command -v ccache >/dev/null 2>&1; then
    KERNEL_MAKE_ARGS+=("CC=ccache ${CROSS_COMPILE}gcc")
fi

make -C kernel "${KERNEL_MAKE_ARGS[@]}" \
    && make modules "${KERNEL_MAKE_ARGS[@]}" \
    && make dtbs "${KERNEL_MAKE_ARGS[@]}"

echo "Installing in-tree modules and dtbs..."
sudo -E make install -C kernel

echo "Installing out-of-tree modules..."
sudo -E make modules_install

# ── Fix module symlinks ─────────────────────────────────────────────────────

# Derive the kernel release from the build itself so a BSP kernel bump can't leave
# these symlinks stale. (`make kernelrelease` drops the -tegra suffix here; this file
# matches the installed lib/modules/<release>.)
JETSON_KERNEL_VERSION=$(cat "$KERNEL_HEADERS/include/config/kernel.release" 2>/dev/null || true)
if [ -z "$JETSON_KERNEL_VERSION" ]; then
    echo "ERROR: could not read kernel release from" >&2
    echo "       $KERNEL_HEADERS/include/config/kernel.release — did the kernel build complete?" >&2
    exit 1
fi
echo "Built kernel release: $JETSON_KERNEL_VERSION"

MODULES_PATH="$INSTALL_MOD_PATH/lib/modules/$JETSON_KERNEL_VERSION"
HEADERS_TARGET="/usr/src/linux-headers-${JETSON_KERNEL_VERSION}-ubuntu22.04_aarch64/3rdparty/canonical/linux-jammy/kernel-source"

if [ ! -d "$MODULES_PATH" ]; then
    echo "ERROR: module path $MODULES_PATH not found after modules_install" >&2
    echo "       (kernel release '$JETSON_KERNEL_VERSION' does not match the installed modules)." >&2
    exit 1
fi
echo "Fixing kernel module symlinks in rootfs..."
sudo rm -f "$MODULES_PATH/build" "$MODULES_PATH/source"
sudo ln -sfn "$HEADERS_TARGET" "$MODULES_PATH/build"
sudo ln -sfn "$HEADERS_TARGET" "$MODULES_PATH/source"

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

# ── Record build metadata ───────────────────────────────────────────────────

BUILD_COMMIT=$ARK_BUILD_COMMIT
BUILD_DATE=$(date -Iseconds)

echo "Recording build metadata to rootfs/etc/ark_jetson_kernel..."
sudo tee "$L4T_DIR/rootfs/etc/ark_jetson_kernel" >/dev/null <<EOF
commit=$BUILD_COMMIT
date=$BUILD_DATE
build_os=$ARK_BUILD_OS
target=$TARGET
EOF

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
