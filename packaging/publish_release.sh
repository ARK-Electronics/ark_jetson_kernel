#!/bin/bash

# Creates a per-product release tag and pushes it. CI handles the build and
# release creation.
#
# Usage: ./publish_release.sh <version>
#   e.g. ./publish_release.sh 6.2.1.1
#
# Reads LAST_BUILT_TARGET to determine the product prefix.
# The resulting tag is e.g. pab-6.2.1.1

set -e

if [ $# -lt 1 ]; then
    echo "Usage: ./publish_release.sh <version>"
    echo "  e.g. ./publish_release.sh 6.2.1.1"
    echo ""
    echo "Reads LAST_BUILT_TARGET to determine the product prefix."
    echo "Creates and pushes a tag — CI handles the rest."
    exit 1
fi

VERSION="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if ! echo "$VERSION" | grep -qE '^[0-9]+(\.[0-9]+)*$'; then
    echo "ERROR: Version must be digits and dots (e.g. 6.2.1.1), got: $VERSION"
    exit 1
fi

LAST_TARGET_FILE="$ROOT_DIR/source_build/LAST_BUILT_TARGET"
if [ ! -f "$LAST_TARGET_FILE" ]; then
    echo "ERROR: No LAST_BUILT_TARGET found. Run build_kernel.sh first."
    exit 1
fi

TARGET=$(cat "$LAST_TARGET_FILE")
PRODUCT=$(echo "$TARGET" | tr '[:upper:]' '[:lower:]' | tr '_' '-')
TAG="${PRODUCT}-${VERSION}"

if git -C "$ROOT_DIR" rev-parse "$TAG" >/dev/null 2>&1; then
    echo "ERROR: Tag '$TAG' already exists."
    exit 1
fi

echo "Target:  $TARGET"
echo "Product: $PRODUCT"
echo "Tag:     $TAG"
echo ""
read -p "Create and push tag '$TAG'? [y/N] " confirm
if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
    echo "Aborted."
    exit 0
fi

git -C "$ROOT_DIR" tag -a "$TAG" -m "$TAG"
git -C "$ROOT_DIR" push origin "$TAG"

echo ""
echo "Tag '$TAG' pushed. CI will build and create the release."
echo "Monitor: https://github.com/ARK-Electronics/ark_jetson_kernel/actions"
