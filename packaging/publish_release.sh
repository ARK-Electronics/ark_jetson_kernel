#!/bin/bash

# Creates a per-product release tag and pushes it. CI builds the package and
# publishes it as a draft on downloads.arkelectron.com.
#
# Usage: ./publish_release.sh <TARGET> <version>
#   e.g. ./publish_release.sh PAB 6.2.2.7
#
# The resulting tag is e.g. pab-6.2.1.1

set -e

if [ $# -lt 2 ]; then
    echo "Usage: ./publish_release.sh <PAB | JAJ | PAB_V3> <version>"
    echo "  e.g. ./publish_release.sh PAB 6.2.2.7"
    echo ""
    echo "Creates and pushes a tag — CI handles the rest."
    exit 1
fi

TARGET="$1"
VERSION="$2"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

case "$TARGET" in
    PAB|JAJ|PAB_V3) ;;
    *)
        echo "ERROR: Invalid target '$TARGET'. Must be PAB, JAJ, or PAB_V3."
        exit 1
        ;;
esac

if ! echo "$VERSION" | grep -qE '^[0-9]+(\.[0-9]+)*$'; then
    echo "ERROR: Version must be digits and dots (e.g. 6.2.2.7), got: $VERSION"
    exit 1
fi

PRODUCT=$(echo "$TARGET" | tr '[:upper:]' '[:lower:]' | tr '_' '-')
TAG="${PRODUCT}-${VERSION}"

if ! git -C "$ROOT_DIR" diff-index --quiet HEAD --; then
    echo "ERROR: Working tree has uncommitted changes. Commit or stash before releasing."
    exit 1
fi

if ! awk -v version="$VERSION" '/^## / && $2 == version {found = 1} END {exit !found}' "$ROOT_DIR/products/$TARGET/CHANGELOG.md"; then
    echo "ERROR: products/$TARGET/CHANGELOG.md has no '## $VERSION - <date>' section. Merge it to main first."
    exit 1
fi

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
echo "Tag '$TAG' pushed. CI will build it and publish an unlisted draft."
echo "Validate it with ./packaging/flash_from_package.sh $TAG, then release it:"
echo "  gh workflow run promote.yml -f tag=$TAG"
echo "Monitor: https://github.com/ARK-Electronics/ark_jetson_kernel/actions"
