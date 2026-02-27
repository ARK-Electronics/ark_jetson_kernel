#!/bin/bash

# Publishes generated flash package(s) to GitHub Releases.
# Tags the commit, creates a release, and uploads the files.
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

# Check gh is available
if ! command -v gh &>/dev/null; then
    echo "ERROR: gh (GitHub CLI) not installed. Install with: sudo apt install gh"
    exit 1
fi

# Find tarballs and/or split directories
RELEASE_FILES=()

for f in ark-*.tar.gz; do
    [ -f "$f" ] || continue
    RELEASE_FILES+=("$f")
done

for d in ark-*_split; do
    [ -d "$d" ] || continue
    for part in "$d"/*; do
        RELEASE_FILES+=("$part")
    done
done

if [ ${#RELEASE_FILES[@]} -eq 0 ]; then
    echo "ERROR: No ark-*.tar.gz or ark-*_split/ found."
    echo "Run generate_flash_package.sh first."
    exit 1
fi

echo "Files to upload:"
for f in "${RELEASE_FILES[@]}"; do
    echo "  $(basename "$f") ($(du -h "$f" | cut -f1))"
done
echo ""

# Tag and push
git tag -a "$VERSION" -m "$VERSION"
git push origin "$VERSION"
echo "Tagged and pushed: $VERSION"

# Create release, then upload files one at a time
echo ""
echo "Creating GitHub release..."
gh release create "$VERSION" \
    --title "$VERSION" \
    --notes "ARK Carrier Board Image $VERSION"

for f in "${RELEASE_FILES[@]}"; do
    echo "Uploading $(basename "$f") ($(du -h "$f" | cut -f1))..."
    gh release upload "$VERSION" "$f"
    echo "  Done."
done

echo ""
echo "All uploads complete!"
echo "Release: $(gh release view "$VERSION" --json url -q .url)"
