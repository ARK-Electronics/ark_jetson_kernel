#!/usr/bin/env bash
# Build the pinned ARK-OS source for the Noble/arm64 ABI in a separate container.
# Usage: scripts/build_ark_os_noble.sh <fresh-build-directory> [output-directory]
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$REPO_DIR/versions.env"
BUILD_DIR="${1:?Usage: scripts/build_ark_os_noble.sh <fresh-build-directory> [output-directory]}"
OUTPUT_DIR="${2:-$REPO_DIR/downloads}"
if [ -e "$BUILD_DIR" ]; then
    echo "ERROR: build directory already exists; choose a fresh private directory." >&2
    exit 1
fi
mkdir -p "$BUILD_DIR" "$OUTPUT_DIR"
BUILD_DIR=$(realpath "$BUILD_DIR")
OUTPUT_DIR=$(realpath "$OUTPUT_DIR")
git init "$BUILD_DIR/source"
git -C "$BUILD_DIR/source" remote add origin https://github.com/ARK-Electronics/ARK-OS.git
git -C "$BUILD_DIR/source" fetch --depth 1 origin "refs/tags/v${ARK_OS_VERSION}:refs/tags/v${ARK_OS_VERSION}"
if [ "$(git -C "$BUILD_DIR/source" rev-parse "v${ARK_OS_VERSION}^{commit}")" != "$ARK_OS_SOURCE_COMMIT" ]; then
    echo "ERROR: ARK-OS version tag does not match ARK_OS_SOURCE_COMMIT." >&2
    exit 1
fi
git -C "$BUILD_DIR/source" checkout --detach "$ARK_OS_SOURCE_COMMIT"
git -C "$BUILD_DIR/source" submodule update --init --recursive --jobs 4
# Reads the application's pinned Node and MAVSDK versions for its own build.
source "$BUILD_DIR/source/packaging/versions.env"
# Snapshot the reviewed recipe so its recorded hash cannot change mid-build.
mkdir "$BUILD_DIR/builder"
cp "$REPO_DIR/packaging/ark-os-noble/Dockerfile" "$BUILD_DIR/builder/Dockerfile"
docker build --platform linux/arm64 --iidfile "$BUILD_DIR/image-id" \
    --build-arg "NODE_VERSION=$NODE_VERSION" "$BUILD_DIR/builder"
IMAGE_ID=$(cat "$BUILD_DIR/image-id")
docker run --rm --platform linux/arm64 \
    -v "$BUILD_DIR/source:/source" -w /source \
    -e "ARK_PACKAGE_VERSION=$ARK_OS_VERSION" -e "MAVSDK_VERSION=$MAVSDK_VERSION" \
    "$IMAGE_ID" bash -euc '
        test "$(uname -m)" = aarch64
        . /etc/os-release
        test "$VERSION_CODENAME" = noble
        # The isolated bind-mounted checkout belongs to the host user.
        git config --global --add safe.directory "*"
        deb="libmavsdk-dev_${MAVSDK_VERSION}_debian12_arm64.deb"
        curl -fsSL -o "$deb" "https://github.com/mavlink/MAVSDK/releases/download/v${MAVSDK_VERSION}/$deb"
        apt-get update
        apt-get install -y "./$deb"
        ./packaging/build.sh jetson --version="$ARK_PACKAGE_VERSION"
        runtime=/source/build/ark-os-jetson/usr/lib/ark-os
        OPENBLAS_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1 "$runtime/venv/bin/python3" -B -c "import sys, fastapi, uvicorn, requests, pymavlink, dronecan, bokeh.plotting, numpy, scipy, pandas; assert sys.version_info[:2] == (3, 12)"
        "$runtime/bin/node" --check "$runtime/ark-ui-backend/index.js"
    '
PACKAGE="ark-os-jetson-noble_${ARK_OS_VERSION}_arm64.deb"
# Check artifact identity before it enters the build's download cache.
source "$SCRIPT_DIR/provision_packages.sh"
ARK_OS_PKG=ark-os-jetson-noble
ARK_OS_PYTHON=python3.12
validate_ark_os_deb "$BUILD_DIR/source/$PACKAGE"
cp "$BUILD_DIR/source/$PACKAGE" "$OUTPUT_DIR/$PACKAGE.partial"
mv "$OUTPUT_DIR/$PACKAGE.partial" "$OUTPUT_DIR/$PACKAGE"
python3 - "$OUTPUT_DIR/$PACKAGE" "$ARK_OS_SOURCE_COMMIT" "$IMAGE_ID" "$BUILD_DIR/builder/Dockerfile" <<'PY'
import hashlib, json, pathlib, sys
package = pathlib.Path(sys.argv[1])
digest = hashlib.sha256()
with package.open("rb") as stream:
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
metadata = {"source_commit": sys.argv[2], "builder_image_id": sys.argv[3],
            "package": package.name, "sha256": digest.hexdigest(),
            "dockerfile_sha256": hashlib.sha256(pathlib.Path(sys.argv[4]).read_bytes()).hexdigest()}
package.with_suffix(".build.json").write_text(json.dumps(metadata, indent=2) + "\n")
PY
printf 'Built %s\n' "$OUTPUT_DIR/$PACKAGE"
