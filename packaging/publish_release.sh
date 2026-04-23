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
SIDECARS=()  # BUILD_INFO.txt paths for same-commit verification

for f in "$ROOT_DIR"/ark-*.tar.gz; do
    [ -f "$f" ] || continue
    RELEASE_FILES+=("$f")
    sidecar="${f%.tar.gz}.BUILD_INFO.txt"
    [ -f "$sidecar" ] && SIDECARS+=("$sidecar")
done

for d in "$ROOT_DIR"/ark-*_split; do
    [ -d "$d" ] || continue
    for part in "$d"/*.part.*; do
        [ -f "$part" ] || continue
        RELEASE_FILES+=("$part")
    done
    [ -f "$d/BUILD_INFO.txt" ] && SIDECARS+=("$d/BUILD_INFO.txt")
done

if [ ${#RELEASE_FILES[@]} -eq 0 ]; then
    echo "ERROR: No ark-*.tar.gz or ark-*_split/ found in project root."
    echo "Run packaging/generate_flash_package.sh first."
    exit 1
fi

# --- Verify all three carrier targets are present ---
# build_kernel.sh supports PAB, PAB_V3, and JAJ. Releases must include all
# three so flash_from_package.sh can pick the right one.
REQUIRED_TARGETS=("pab" "jaj" "pab-v3")
declare -A TARGETS_FOUND=()
for f in "${RELEASE_FILES[@]}"; do
    base=$(basename "$f")
    for t in "${REQUIRED_TARGETS[@]}"; do
        if [[ "$base" =~ ^ark-$t-(nvme|sdcard) ]]; then
            TARGETS_FOUND[$t]=1
            break
        fi
    done
done
MISSING_TARGETS=()
for t in "${REQUIRED_TARGETS[@]}"; do
    [ -z "${TARGETS_FOUND[$t]:-}" ] && MISSING_TARGETS+=("$t")
done
if [ ${#MISSING_TARGETS[@]} -gt 0 ]; then
    echo "ERROR: Missing targets in release: ${MISSING_TARGETS[*]}"
    echo "For each missing target, run: ./build_kernel.sh (pick target) and ./packaging/generate_flash_package.sh"
    exit 1
fi

# --- Verify all packages were built from the same commit ---
# Reads the sidecar BUILD_INFO.txt files written by generate_flash_package.sh.
# Catches the case where one target was rebuilt (new commit, uncommitted
# changes) and the others weren't.
if [ ${#SIDECARS[@]} -lt ${#REQUIRED_TARGETS[@]} ]; then
    echo "ERROR: Expected at least ${#REQUIRED_TARGETS[@]} BUILD_INFO.txt sidecar files, found ${#SIDECARS[@]}."
    echo "Re-run ./packaging/generate_flash_package.sh for any target built before this fix."
    exit 1
fi
UNIQUE_COMMITS=$(for s in "${SIDECARS[@]}"; do awk '/^Commit:/ {print $2}' "$s"; done | sort -u)
COMMIT_COUNT=$(printf '%s\n' "$UNIQUE_COMMITS" | grep -c .)
if [ "$COMMIT_COUNT" -ne 1 ]; then
    echo "ERROR: Packages were built from different commits:"
    for s in "${SIDECARS[@]}"; do
        tgt=$(awk '/^Target:/ {print $2}' "$s")
        cmt=$(awk '/^Commit:/ {print $2}' "$s")
        echo "  $tgt: $cmt ($(basename "$s"))"
    done
    echo ""
    echo "Rebuild all targets from the same commit before publishing."
    exit 1
fi
BUILD_COMMIT="$UNIQUE_COMMITS"
echo "All targets built from commit: $BUILD_COMMIT"

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

Contains prebuilt flash packages for all three ARK carriers: **PAB**, **PAB_V3**, and **JAJ**. \`flash_from_package.sh\` prompts you to pick one.

Built from commit \`${BUILD_COMMIT}\`.

### Flashing

Download and run the flash script:
\`\`\`
curl -LO https://github.com/ARK-Electronics/ark_jetson_kernel/releases/download/${VERSION}/flash_from_package.sh
chmod +x flash_from_package.sh
./flash_from_package.sh ${VERSION}
\`\`\`

The script downloads the package for your selected carrier, reassembles it if split, and flashes the Jetson.

**Requirements:** Ubuntu 22.04 host with USB connection. Put the Jetson in recovery mode (hold Force Recovery button while powering on) before or during the script — it will wait for detection.

All Orin Nano/NX module variants (4GB/8GB Nano, 8GB/16GB NX) are auto-detected at flash time within the selected carrier's package."

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
