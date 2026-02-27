#!/bin/bash

# Publishes a generated flash package to GitHub Releases.
# Renames the dev tarball with the given version, tags the commit, and uploads.
#
# Usage: ./publish_release.sh <version>
#   e.g. ./publish_release.sh v1.0.0

set -e

if [ $# -lt 1 ]; then
    echo "Usage: ./publish_release.sh <version>"
    echo "  e.g. ./publish_release.sh v1.0.0"
    exit 1
fi

VERSION="$1"

# Find the dev tarball(s)
DEV_FILES=(ark-*-dev.tar.gz)
if [ ! -f "${DEV_FILES[0]}" ]; then
    echo "ERROR: No ark-*-dev.tar.gz found. Run generate_flash_package.sh first."
    exit 1
fi

# Check gh is available
if ! command -v gh &>/dev/null; then
    echo "ERROR: gh (GitHub CLI) not installed. Install with: sudo apt install gh"
    exit 1
fi

# Rename dev tarballs with version
RELEASE_FILES=()
for f in "${DEV_FILES[@]}"; do
    RENAMED="${f/-dev/-$VERSION}"
    mv "$f" "$RENAMED"
    echo "Renamed: $f -> $RENAMED"
    RELEASE_FILES+=("$RENAMED")
done

# Also handle split directories
for d in ark-*-dev_split/; do
    [ -d "$d" ] || continue
    RENAMED_DIR="${d/-dev/-$VERSION}"
    mv "$d" "$RENAMED_DIR"
    echo "Renamed: $d -> $RENAMED_DIR"
    RELEASE_FILES+=("$RENAMED_DIR"/*)
done

# Tag and push
git tag -a "$VERSION" -m "$VERSION"
git push origin "$VERSION"
echo "Tagged and pushed: $VERSION"

# Create release
echo "Creating GitHub release..."
gh release create "$VERSION" "${RELEASE_FILES[@]}" \
    --title "$VERSION" \
    --notes "ARK Carrier Board Image $VERSION"

echo ""
echo "Done! Release: $(gh release view "$VERSION" --json url -q .url)"
