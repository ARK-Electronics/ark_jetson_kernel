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

export CROSS_COMPILE="$HOME/l4t-gcc/${TOOLCHAIN_CROSS_PREFIX}"
export KERNEL_HEADERS="$SOURCE_DIR/kernel/${KERNEL_SRC_DIR}"
# OOT modules (nvidia-oot) select kernel-noble vs kernel-jammy via this.
export kernel_name="${KERNEL_NAME}"
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

    # Snapshot the pristine stock overlay Makefile so the per-build ARK overlay step
    # re-derives its dtbo set from a clean base (idempotent across rebuilds; a BSP
    # layout change then fails loud instead of compounding edits).
    cp "$SOURCE_DIR/hardware/nvidia/t23x/nv-public/overlay/Makefile" \
       "$STAGING_DIR/.overlay-makefile.stock"

    DEFCONFIG="$SOURCE_DIR/kernel/${KERNEL_SRC_DIR}/arch/arm64/configs/defconfig"
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

# ── Layer ARK's device-tree delta onto the stock BSP ─────────────────────────
# products/<target>/device_tree/ carries ONLY ARK's delta: the BCT pinmux/gpio
# files, the per-product ark-<target>-overrides.dtsi fragment, and any product-
# specific .dtsi it #includes. Everything else tracks the BSP. ARK's fragment is
# applied by appending an #include to the stock nv-common (so it layers last over
# the pristine tree), and per-SKU model strings are stamped from dtb_models.env.
# Runs every build to pick up edits. See docs/device-tree.md.

echo "Copying $TARGET device tree delta into source tree..."
cp -r "$PRODUCT_DIR/device_tree/"* "$L4T_DIR/"

NV_PLATFORM="$SOURCE_DIR/hardware/nvidia/t23x/nv-public/nv-platform"
NV_COMMON="$NV_PLATFORM/tegra234-p3768-0000+p3767-xxxx-nv-common.dtsi"
ARK_FRAGMENT="ark-${TARGET}-overrides.dtsi"

# Append the fragment #include to the stock nv-common so ARK's nodes apply last.
# Idempotent (skip if already there); fail loud if the BSP renamed nv-common or the
# fragment is missing, rather than silently building without ARK's overrides.
if [ ! -f "$NV_PLATFORM/$ARK_FRAGMENT" ]; then
    echo "ERROR: $ARK_FRAGMENT not found under products/$TARGET/device_tree/ —" >&2
    echo "       cannot apply ARK's device-tree delta." >&2
    exit 1
fi
if [ ! -f "$NV_COMMON" ]; then
    echo "ERROR: stock $(basename "$NV_COMMON") missing — BSP device-tree layout" >&2
    echo "       changed; refusing to build without ARK's overrides applied." >&2
    exit 1
fi
if ! grep -q "$ARK_FRAGMENT" "$NV_COMMON"; then
    echo "Applying ARK device-tree fragment ($ARK_FRAGMENT)..."
    printf '\n#include "%s"\n' "$ARK_FRAGMENT" >> "$NV_COMMON"
fi

# Stamp per-SKU model strings onto the stock DTS: the listed model goes on the SKU's
# "-nv" DTB and "<model> Super" on its "-nv-super" DTB. Re-derived every build (drop
# any prior ARK-MODEL block, re-append) so a model edit needs no re-stage.
MODELS_FILE="$PRODUCT_DIR/dtb_models.env"
if [ -f "$MODELS_FILE" ]; then
    echo "Stamping $TARGET model strings..."
    while IFS='=' read -r sku model; do
        sku="${sku%%#*}"; sku="${sku//[[:space:]]/}"
        [ -z "$sku" ] && continue
        model="$(echo "$model" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
        for variant in "-nv" "-nv-super"; do
            dts="$NV_PLATFORM/tegra234-p3768-0000+p3767-${sku}${variant}.dts"
            [ -f "$dts" ] || continue
            m="$model"; [ "$variant" = "-nv-super" ] && m="$model Super"
            awk '/\/\* ARK-MODEL \*\//{exit} {print}' "$dts" > "$dts.tmp" && mv "$dts.tmp" "$dts"
            printf '\n/* ARK-MODEL */\n/ {\n\tmodel = "%s";\n};\n' "$m" >> "$dts"
        done
    done < "$MODELS_FILE"
fi

# ── Inject ARK kernel source overlay ────────────────────────────────────────
# nvidia-oot has no version-controlled hook of its own, so out-of-tree sensor
# sources we add (e.g. the IMX708 driver) live under kernel_overlay/ and are
# layered onto the L4T source tree here. Runs every build so edits to the
# vendored sources are picked up without a re-stage.

if [ -d "$SCRIPT_DIR/kernel_overlay" ]; then
    echo "Injecting ARK kernel source overlay..."
    # Copy only the source subtrees (e.g. nvidia-oot/), not top-level docs.
    for dir in "$SCRIPT_DIR"/kernel_overlay/*/; do
        cp -r "$dir" "$SOURCE_DIR/"
    done

    # Register the IMX708 driver in the OOT media Makefile (idempotent). Fail loud
    # if the anchor moves on a BSP bump rather than silently building without it.
    OOT_I2C_MAKEFILE="$SOURCE_DIR/nvidia-oot/drivers/media/i2c/Makefile"
    if ! grep -q 'nv_imx708.o' "$OOT_I2C_MAKEFILE"; then
        if ! grep -q '^obj-m += nv_imx477.o' "$OOT_I2C_MAKEFILE"; then
            echo "ERROR: anchor 'obj-m += nv_imx477.o' not found in" >&2
            echo "       $OOT_I2C_MAKEFILE — OOT Makefile layout changed;" >&2
            echo "       refusing to build without the IMX708 driver registered." >&2
            exit 1
        fi
        sed -i '/^obj-m += nv_imx477.o/a obj-m += nv_imx708.o' "$OOT_I2C_MAKEFILE"
    fi
fi

# ── Stage ARK device-tree overlays ───────────────────────────────────────────
# ARK overlay sources live in products/<target>/overlay/, not the BSP source
# mirror. Copy them into the stock overlay dir and make the built dtbo set
# explicit: disable the stock p3768 camera overlays, then build exactly the dtbos
# in overlay/dtbo.list. The stock overlay Makefile is re-derived from the
# stage-time snapshot each build, so this is idempotent and a BSP layout change
# fails loud. Non-camera stock overlays (audio/csi/hdr/AGX) keep building from the
# BSP untouched. See docs/cameras.md and products/<target>/overlay/dtbo.list.

OVERLAY_SRC_DIR="$PRODUCT_DIR/overlay"
if [ -d "$OVERLAY_SRC_DIR" ]; then
    echo "Staging ARK overlays from products/$TARGET/overlay/..."
    STAGED_OVERLAY_DIR="$SOURCE_DIR/hardware/nvidia/t23x/nv-public/overlay"
    OVERLAY_MK="$STAGED_OVERLAY_DIR/Makefile"
    STOCK_OVERLAY_MK="$STAGING_DIR/.overlay-makefile.stock"

    if [ ! -f "$STOCK_OVERLAY_MK" ]; then
        echo "ERROR: $STOCK_OVERLAY_MK not found — the staged tree predates the" >&2
        echo "       products/<target>/overlay/ refactor. Re-stage: ./build.sh $TARGET --clean" >&2
        exit 1
    fi

    # Re-derive the overlay Makefile from the pristine stock snapshot, then layer
    # ARK's sources on top (overwriting the stock file where ARK ships its own
    # version, e.g. the 22pin-renamed imx219 overlays).
    cp "$STOCK_OVERLAY_MK" "$OVERLAY_MK"
    for f in "$OVERLAY_SRC_DIR"/*.dts "$OVERLAY_SRC_DIR"/*.dtsi; do
        [ -e "$f" ] && cp "$f" "$STAGED_OVERLAY_DIR/"
    done

    # Disable the stock p3768 camera overlays so overlay/dtbo.list is authoritative.
    # We operate on a pristine Makefile, so a zero match means the BSP renamed them —
    # fail loud rather than ship a camera set that no longer matches dtbo.list.
    if [ "$(grep -cE '^dtbo-y[[:space:]]*\+=[[:space:]]*tegra234-p3767-camera-p3768-' "$OVERLAY_MK")" -eq 0 ]; then
        echo "ERROR: no stock 'tegra234-p3767-camera-p3768-*' dtbo-y entries in $OVERLAY_MK" >&2
        echo "       — BSP overlay layout changed; refusing to build a mismatched camera set." >&2
        exit 1
    fi
    sed -i -E 's|^(dtbo-y[[:space:]]*\+=[[:space:]]*tegra234-p3767-camera-p3768-)|# ARK (see overlay/dtbo.list): \1|' "$OVERLAY_MK"

    # Build exactly the dtbos in overlay/dtbo.list, asserting each has a source file.
    DTBO_LIST="$OVERLAY_SRC_DIR/dtbo.list"
    if [ ! -f "$DTBO_LIST" ]; then
        echo "ERROR: $DTBO_LIST missing." >&2
        exit 1
    fi
    ark_dtbo_lines=""
    while IFS= read -r entry; do
        entry="${entry%%#*}"
        entry="$(echo "$entry" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
        [ -z "$entry" ] && continue
        if [ ! -f "$STAGED_OVERLAY_DIR/${entry%.dtbo}.dts" ]; then
            echo "ERROR: $DTBO_LIST lists '$entry' but ${entry%.dtbo}.dts is not in the" >&2
            echo "       overlay dir (missing from products/$TARGET/overlay/?)." >&2
            exit 1
        fi
        ark_dtbo_lines+="dtbo-y += $entry"$'\n'
    done < "$DTBO_LIST"

    # Insert the ARK block before the first dtbo path-prefix guard so the entries get
    # the t23x/nv-public/overlay/ prefix like the rest.
    awk -v block="$ark_dtbo_lines" '
        /^ifneq \(\$\(dtbo?-y\),\)/ && !inserted {
            printf "# >>> ARK overlays (products/'"$TARGET"'/overlay/dtbo.list)\n%s# <<< ARK overlays\n\n", block
            inserted = 1
        }
        { print }
    ' "$OVERLAY_MK" > "$OVERLAY_MK.tmp" && mv "$OVERLAY_MK.tmp" "$OVERLAY_MK"

    if ! grep -q '^# >>> ARK overlays' "$OVERLAY_MK"; then
        echo "ERROR: failed to inject ARK overlays into $OVERLAY_MK (no dtbo prefix guard found)." >&2
        exit 1
    fi
fi

# ── Build ───────────────────────────────────────────────────────────────────

echo ""
echo "========================================="
echo "  Building kernel for $TARGET"
echo "========================================="

cd "$SOURCE_DIR"

# Some toolchains default to -fPIE/-pie, so a bare `$(CC) -v` (no input) links PIE
# startfiles and fails — its LAST line is a collect2 error, which NVIDIA's nv_compiler.h
# recipe bakes into the module's /proc version banner via `tail -1`. Repoint it at
# `--version | head -1`, which never links. Fail loud if the BSP moved the recipe; drop the
# stale header so it regenerates.
NVIDIA_KBUILD="$SOURCE_DIR/nvdisplay/kernel-open/nvidia/nvidia.Kbuild"
if grep -qF -- '-v 2>&1 | tail -n 1' "$NVIDIA_KBUILD"; then
    sed -i 's/\$(CC) -v 2>&1 | tail -n 1/$(CC) --version 2>\&1 | head -n 1/' "$NVIDIA_KBUILD"
    rm -f "$SOURCE_DIR/nvdisplay/kernel-open/nv_compiler.h"
elif ! grep -qF -- '--version 2>&1 | head -n 1' "$NVIDIA_KBUILD"; then
    echo "ERROR: nv_compiler.h version probe in nvidia.Kbuild is neither the known-bad nor" >&2
    echo "       the patched form — BSP layout changed; re-check the probe patch in build.sh." >&2
    exit 1
fi

# ccache wraps the cross-compiler for the kernel proper only (kernel C rarely changes → warm
# hits). The OOT NVIDIA modules build without it on purpose: their conftest/version steps
# regenerate headers and ccache direct-mode can serve a stale object across that — a silent
# nondeterministic miscompile, not worth the small speedup on these modules. dtbs are no-C.
KERNEL_MAKE_ARGS=()
if command -v ccache >/dev/null 2>&1; then
    KERNEL_MAKE_ARGS+=("CC=ccache ${CROSS_COMPILE}gcc")
fi

make -C kernel "${KERNEL_MAKE_ARGS[@]}" \
    && make modules CC="${CROSS_COMPILE}gcc" \
    && make dtbs CC="${CROSS_COMPILE}gcc"

# Sanity-check the display-driver build: nv_compiler.h must read as a real compiler version
# (probe fixed above) and the three display .kos must be non-empty — catches a broken or
# missing cross-compiler instead of silently shipping a bad module.
NV_COMPILER_H="$SOURCE_DIR/nvdisplay/kernel-open/nv_compiler.h"
if [ ! -s "$NV_COMPILER_H" ] || ! grep -qE 'version|[0-9]+\.[0-9]+\.[0-9]+' "$NV_COMPILER_H"; then
    echo "ERROR: NVIDIA compiler-version probe produced no sane version string:" >&2
    echo "       $NV_COMPILER_H: $(cat "$NV_COMPILER_H" 2>/dev/null)" >&2
    exit 1
fi
for ko in nvidia.ko nvidia-modeset.ko nvidia-drm.ko; do
    [ -s "$SOURCE_DIR/nvdisplay/kernel-open/$ko" ] \
        || { echo "ERROR: NVIDIA module $ko missing or empty after build" >&2; exit 1; }
done

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

if [ ! -d "$MODULES_PATH" ]; then
    echo "ERROR: module path $MODULES_PATH not found after modules_install" >&2
    echo "       (kernel release '$JETSON_KERNEL_VERSION' does not match the installed modules)." >&2
    exit 1
fi

# Headers tree path changes with the distro (ubuntu22.04/jammy vs ubuntu24.04/noble).
# Resolve it from the rootfs that apply_binaries installed rather than hardcoding.
HEADERS_HOST=$(find "$INSTALL_MOD_PATH/usr/src" -type d \
    -path "*/linux-headers-${JETSON_KERNEL_VERSION}*/**/kernel-source" 2>/dev/null | head -1)
if [ -z "$HEADERS_HOST" ]; then
    # Fallback: top-level headers dir (some layouts skip the 3rdparty nest).
    HEADERS_HOST=$(find "$INSTALL_MOD_PATH/usr/src" -maxdepth 1 -type d \
        -name "linux-headers-${JETSON_KERNEL_VERSION}*" 2>/dev/null | head -1)
fi
if [ -z "$HEADERS_HOST" ]; then
    echo "ERROR: no linux-headers-${JETSON_KERNEL_VERSION}* under rootfs/usr/src/" >&2
    echo "       after apply_binaries — cannot fix modules build/source symlinks." >&2
    ls -la "$INSTALL_MOD_PATH/usr/src/" 2>/dev/null || true
    exit 1
fi
# Symlink target as seen on the device (absolute under /).
HEADERS_TARGET="${HEADERS_HOST#"$INSTALL_MOD_PATH"}"

echo "Fixing kernel module symlinks in rootfs (headers -> $HEADERS_TARGET)..."
sudo rm -f "$MODULES_PATH/build" "$MODULES_PATH/source"
sudo ln -sfn "$HEADERS_TARGET" "$MODULES_PATH/build"
sudo ln -sfn "$HEADERS_TARGET" "$MODULES_PATH/source"

# ── Install build outputs ───────────────────────────────────────────────────

echo "Installing kernel Image..."
cp "$SOURCE_DIR/kernel/${KERNEL_SRC_DIR}/arch/arm64/boot/Image" "$L4T_DIR/kernel/"

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
