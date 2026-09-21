#!/bin/bash

# Usage: ./scripts/build_uefi.sh
#
# Builds the reduced NVMe-only UEFI (uefi/ark_nvme.defconfig) from NVIDIA's pinned
# r36.5 edk2 sources into staging/uefi/. Runs in its own container (uefi/Dockerfile:
# edk2 wants a cross gcc, iasl and nasm the kernel builder lacks), so it needs
# docker and network but no sudo; touches no device and no product staging tree.
# One build serves every target: enable it per product with
# scripts/enable_fast_boot.sh. See docs/fast_boot.md.

set -e -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
export ARK_JETSON_KERNEL_DIR="$ROOT_DIR"
source "$SCRIPT_DIR/container_runner.sh"

IMAGE="ark-uefi-builder:r36.5"
ARTIFACT="uefi_ark_nvme_RELEASE.bin"
# The T234 cpu-bootloader partitions are 3.5 MiB.
MAX_SIZE=3670016

# ── Host side: re-exec in the UEFI builder container ─────────────────────────
if [ -z "$IN_UEFI_CONTAINER" ]; then
    ensure_docker
    want_sha=$(sha256sum "$ROOT_DIR/uefi/Dockerfile" | cut -d' ' -f1)
    have_sha=$("${DOCKER_CMD[@]}" image inspect \
        --format '{{ if .Config.Labels }}{{ index .Config.Labels "dockerfile_sha" }}{{ end }}' \
        "$IMAGE" 2>/dev/null) || have_sha=""
    if [ "$want_sha" != "$have_sha" ]; then
        echo "Building $IMAGE..."
        "${DOCKER_CMD[@]}" build --label "dockerfile_sha=$want_sha" -t "$IMAGE" "$ROOT_DIR/uefi/"
    fi
    tty_flags=(-i)
    [ -t 0 ] && [ -t 1 ] && tty_flags+=(-t)
    echo "Re-executing $(basename "$0") in the UEFI build container..."
    exec "${DOCKER_CMD[@]}" run --rm "${tty_flags[@]}" \
        -v "$ROOT_DIR:/workspace" -w /workspace \
        -e IN_UEFI_CONTAINER=1 \
        "$IMAGE" bash "/workspace/scripts/$(basename "$0")"
fi

# ── Container side ───────────────────────────────────────────────────────────
BUILD_DIR="$ROOT_DIR/staging/uefi"
SRC_DIR="$BUILD_DIR/src"
mkdir -p "$SRC_DIR"
exec > >(tee "$BUILD_DIR/build.log.txt") 2>&1

# Fetch each repo at its pinned commit. An existing checkout is reused as-is and
# must still sit on that commit: a local edit or a moved HEAD fails loud rather
# than silently building different firmware.
while read -r repo rev url; do
    [[ -z "$repo" || "$repo" == \#* ]] && continue
    dir="$SRC_DIR/$repo"
    if [ ! -e "$dir/.git" ]; then
        echo "Fetching $repo @ ${rev:0:12}..."
        git init -q "$dir"
        git -C "$dir" remote add origin "${url:-https://github.com/NVIDIA/$repo.git}"
        git -C "$dir" fetch -q --depth 1 origin "$rev"
        git -C "$dir" checkout -q --detach FETCH_HEAD
    fi
    if [ "$(git -C "$dir" rev-parse HEAD)" != "$rev" ] || ! git -C "$dir" diff --quiet HEAD; then
        echo "ERROR: $dir is not a clean checkout of $rev (uefi/sources.lock)." >&2
        echo "       Remove staging/uefi/src/ to refetch." >&2
        exit 1
    fi
done < "$ROOT_DIR/uefi/sources.lock"
git -C "$SRC_DIR/edk2" submodule update -q --init --depth 1 --jobs 4

cd "$SRC_DIR"

# stuart's own venv step pulls the newest kconfiglib and a setuptools without
# pkg_resources, which edk2's pip-requirements still import; make the venv here
# with both pinned and tell prepare_stuart.sh to skip it. UEFI_SKIP_UPDATE skips
# stuart_update (a mono/nuget download) in favour of the container's iasl/nasm.
test -x venv/bin/python || python3 -m venv venv
venv/bin/pip install -q --disable-pip-version-check \
    -r edk2/pip-requirements.txt setuptools==75.8.2 kconfiglib==14.1.0
. venv/bin/activate

# NVIDIA merges a leftover .config over the requested defconfig. The image name
# comes from the defconfig's stem: ark_nvme -> images/uefi_ark_nvme_RELEASE.bin.
rm -f "nvidia-config/ark_nvme/.config" "images/$ARTIFACT"
UEFI_SKIP_UPDATE=1 UEFI_SKIP_VENV=1 UEFI_RELEASE_ONLY=1 \
FIRMWARE_VERSION_BASE=36.5.0 GIT_SYNC_REVISION=79ad0c17-ark \
    bash edk2-nvidia/Platform/NVIDIA/Tegra/build.sh --init-defconfig "$ROOT_DIR/uefi/ark_nvme.defconfig"

if [ ! -s "images/$ARTIFACT" ]; then
    echo "ERROR: build produced no $ARTIFACT (see staging/uefi/build.log.txt)." >&2
    exit 1
fi
if [ "$(stat -c %s "images/$ARTIFACT")" -gt "$MAX_SIZE" ]; then
    echo "ERROR: $ARTIFACT exceeds the 3.5 MiB cpu-bootloader partition." >&2
    exit 1
fi
cp "images/$ARTIFACT" "$BUILD_DIR/$ARTIFACT"
cp nvidia-config/ark_nvme/.config "$BUILD_DIR/resolved.config"

echo ""
echo "========================================="
echo "  Built: staging/uefi/$ARTIFACT ($(stat -c %s "$BUILD_DIR/$ARTIFACT") bytes)"
echo "  Enable with: ./scripts/enable_fast_boot.sh <TARGET>"
echo "========================================="
