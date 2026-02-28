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

# Find tarballs and/or split part files (not reassemble.sh — the flash script handles that)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RELEASE_FILES=()

for f in "$ROOT_DIR"/ark-*.tar.gz; do
    [ -f "$f" ] || continue
    RELEASE_FILES+=("$f")
done

for d in "$ROOT_DIR"/ark-*_split; do
    [ -d "$d" ] || continue
    for part in "$d"/*.part.*; do
        [ -f "$part" ] || continue
        RELEASE_FILES+=("$part")
    done
done

if [ ${#RELEASE_FILES[@]} -eq 0 ]; then
    echo "ERROR: No ark-*.tar.gz or ark-*_split/ found in project root."
    echo "Run packaging/generate_flash_package.sh first."
    exit 1
fi

# Always include flash_from_package.sh
if [ -f "$SCRIPT_DIR/flash_from_package.sh" ]; then
    RELEASE_FILES+=("$SCRIPT_DIR/flash_from_package.sh")
else
    echo "WARNING: flash_from_package.sh not found, release will not include flash script."
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

# Build release notes
NOTES="## ARK Carrier Board Image $VERSION

### Flashing

Download and run the flash script:
\`\`\`
curl -LO https://github.com/ARK-Electronics/ark_jetson_kernel/releases/download/${VERSION}/flash_from_package.sh
chmod +x flash_from_package.sh
./flash_from_package.sh ${VERSION}
\`\`\`

The script downloads the package, reassembles it if split, and flashes the Jetson.

**Requirements:** Ubuntu 22.04 host with USB connection. Put the Jetson in recovery mode (hold Force Recovery button while powering on) before or during the script — it will wait for detection.

Supports all Orin Nano/NX module variants — the correct DTB is selected automatically at flash time."

# Create release, then upload files one at a time
echo ""
echo "Creating GitHub release..."
gh release create "$VERSION" \
    --title "$VERSION" \
    --notes "$NOTES"

for f in "${RELEASE_FILES[@]}"; do
    echo "Uploading $(basename "$f") ($(du -h "$f" | cut -f1))..."
    gh release upload "$VERSION" "$f"
    echo "  Done."
done

echo ""
echo "All uploads complete!"
echo "Release: $(gh release view "$VERSION" --json url -q .url)"
