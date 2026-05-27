#!/bin/bash

# Usage: ./setup.sh [--force | -y]
#   --force, -y   Skip confirmation prompts (intended for CI / scripted runs).
#
# Downloads the NVIDIA L4T BSP, sample root filesystem, public kernel sources,
# and the bootlin cross-toolchain into a local cache (downloads/).  Does NOT
# extract or configure anything — that happens per-product in build.sh.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export ARK_JETSON_KERNEL_DIR="$SCRIPT_DIR"

source "$SCRIPT_DIR/scripts/check_bsp.sh"
source "$SCRIPT_DIR/scripts/container_runner.sh"

if ! needs_container; then
    mkdir -p "$SCRIPT_DIR/staging"
    exec > >(tee "$SCRIPT_DIR/staging/setup.log.txt") 2>&1
fi

FORCE=0
for arg in "$@"; do
    case "$arg" in
        --force|-y) FORCE=1 ;;
        *)
            echo "Unknown option: $arg"
            echo "Usage: ./setup.sh [--force | -y]"
            exit 1
            ;;
    esac
done

# ── Legacy migration ────────────────────────────────────────────────────────
# Warn users coming from the old prebuilt/ + source_build/ layout.
LEGACY_DIRS=()
[ -d "$SCRIPT_DIR/prebuilt" ]     && LEGACY_DIRS+=("prebuilt/")
[ -d "$SCRIPT_DIR/source_build" ] && LEGACY_DIRS+=("source_build/")
[ -d "$SCRIPT_DIR/device_tree" ]  && LEGACY_DIRS+=("device_tree/")

if [ ${#LEGACY_DIRS[@]} -gt 0 ]; then
    echo "========================================="
    echo "  Legacy directory layout detected"
    echo "========================================="
    echo ""
    echo "The build system has been reorganized. The following directories are"
    echo "from the old layout and are no longer used:"
    echo ""
    for d in "${LEGACY_DIRS[@]}"; do
        echo "  • $d"
    done
    echo ""
    echo "The new layout uses:"
    echo "  • downloads/          — cached tarballs (shared across products)"
    echo "  • staging/{TARGET}/   — per-product build trees (created by build.sh)"
    echo "  • products/{TARGET}/  — per-product config (device tree, defconfig)"
    echo ""
    echo "These legacy directories will be DELETED.  Any local edits inside"
    echo "them will be lost.  You will need to re-run ./build.sh <TARGET> to"
    echo "stage and build each product from scratch."
    echo ""

    if [ $FORCE -eq 0 ]; then
        read -p "Delete legacy directories and continue? (y/N): " confirm
        if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
            echo "Setup aborted."
            exit 0
        fi
    else
        echo "(--force specified, proceeding without confirmation)"
    fi

    echo "Removing legacy directories..."
    for d in "${LEGACY_DIRS[@]}"; do
        sudo rm -rf "$SCRIPT_DIR/$d"
    done
    echo "Legacy cleanup complete."
    echo ""
fi

# Also clean stale staging dirs when a BSP version bump makes them invalid.
if [ -d "$SCRIPT_DIR/staging" ]; then
    for target_dir in "$SCRIPT_DIR/staging"/*/; do
        [ -d "$target_dir" ] || continue
        target_name=$(basename "$target_dir")
        release_file="$target_dir/Linux_for_Tegra/rootfs/etc/nv_tegra_release"
        if [ -f "$release_file" ]; then
            _det_rel=$(grep -oE '^# R[0-9]+' "$release_file" | awk '{print $2}')
            _det_rev=$(grep -oE 'REVISION: [0-9.]+' "$release_file" | awk '{print $2}')
            if [ "$_det_rel" != "$EXPECTED_BSP_RELEASE" ] || [ "$_det_rev" != "$EXPECTED_BSP_REVISION" ]; then
                echo "Stale staging/$target_name/ detected (BSP ${_det_rel}.${_det_rev}, expected ${EXPECTED_BSP_RELEASE}.${EXPECTED_BSP_REVISION})."
                if [ $FORCE -eq 0 ]; then
                    read -p "Delete staging/$target_name/? (y/N): " confirm
                    if [ "$confirm" = "y" ] || [ "$confirm" = "Y" ]; then
                        sudo rm -rf "$target_dir"
                        echo "  Removed."
                    else
                        echo "  Skipped (build.sh will refuse to build against a mismatched BSP)."
                    fi
                else
                    sudo rm -rf "$target_dir"
                    echo "  Removed (--force)."
                fi
            fi
        fi
    done
fi

if needs_container; then
    run_in_container "$0" "$@"
fi

set -e -o pipefail

function cleanup() {
    if [ -n "${SUDO_PID:-}" ]; then
        kill -9 "$SUDO_PID" 2>/dev/null || true
    fi
}

function sudo_refresh_loop() {
    while true; do
        sudo -v
        sleep 60
    done
}

function download_with_retry() {
    local url=$1
    local dest_dir=$2
    local filename
    filename=$(basename "$url")
    local retries=3
    local count=0

    if [ -f "$dest_dir/$filename" ]; then
        echo "  $filename already downloaded, skipping."
        return 0
    fi

    while [ $count -lt $retries ]; do
        if wget -P "$dest_dir" "$url"; then
            return 0
        fi
        echo "Download failed. Retrying... ($((count+1))/$retries)"
        count=$((count+1))
        sleep 5
    done

    echo "Failed to download $url after $retries attempts."
    exit 1
}

trap cleanup EXIT

sudo -v
sudo_refresh_loop &
SUDO_PID=$!
START_TIME=$(date +%s)

DOWNLOADS_DIR="$SCRIPT_DIR/downloads"
mkdir -p "$DOWNLOADS_DIR"

echo "Downloading BSP, root filesystem, and kernel sources to downloads/"
download_with_retry "$BSP_URL" "$DOWNLOADS_DIR"
download_with_retry "$ROOT_FS_URL" "$DOWNLOADS_DIR"
download_with_retry "$PUBLIC_SOURCES_URL" "$DOWNLOADS_DIR"

echo "Installing build prerequisites"
sudo apt-get install -y -qq make build-essential bc flex bison libssl-dev

# Toolchain
mkdir -p "$HOME/l4t-gcc"
TOOLCHAIN_FILENAME=$(basename "$TOOLCHAIN_URL")
TOOLCHAIN_DIRNAME=${TOOLCHAIN_FILENAME%.tar.bz2}

if [ ! -d "$HOME/l4t-gcc/$TOOLCHAIN_DIRNAME" ]; then
    download_with_retry "$TOOLCHAIN_URL" "$HOME/l4t-gcc"
    echo "Extracting bootlin toolchain"
    tar xf "$HOME/l4t-gcc/$TOOLCHAIN_FILENAME" -C "$HOME/l4t-gcc/"
else
    echo "Bootlin toolchain already extracted, skipping."
fi

END_TIME=$(date +%s)
TOTAL_TIME=$((END_TIME - START_TIME))
echo ""
echo "Setup complete in $(date -d@${TOTAL_TIME} -u +%H:%M:%S)"
echo "Downloads cached in: $DOWNLOADS_DIR"
echo ""
echo "Next: ./build.sh <TARGET>   (PAB | JAJ | PAB_V3 | all)"
