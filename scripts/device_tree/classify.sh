#!/bin/bash
# Overlay drift check. Every file under products/<target>/device_tree/source/ should be a
# genuine ARK delta (the ark-<target>-overrides.dtsi fragment, an HDMI DCB, etc.) — never a
# byte-identical copy of the pinned BSP. Stock copies go stale silently: they pin the tree to
# one BSP and revert everything the BSP later changes, which is exactly what the device-tree
# overlay refactor removed. Run this after a BSP bump (or in CI) to catch re-introduced copies.
#
# Usage: scripts/device_tree/classify.sh [PAB|JAJ|PAB_V3|all]   (default: all)
# Needs the BSP source tarball in downloads/ (run ./setup.sh first). Exits non-zero on a DUPLICATE.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO"
# shellcheck disable=SC1091
source "$REPO/versions.env"

TARGET="${1:-all}"
case "$TARGET" in
    PAB|JAJ|PAB_V3) TARGETS=("$TARGET") ;;
    all) TARGETS=(PAB JAJ PAB_V3) ;;
    *) echo "usage: $0 [PAB|JAJ|PAB_V3|all]" >&2; exit 1 ;;
esac

PUB="$REPO/downloads/$PUBLIC_SOURCES_FILE"
[ -f "$PUB" ] || { echo "ERROR: $PUB not found — run ./setup.sh to fetch the BSP." >&2; exit 1; }

# Extract the stock nv-public tree once, cached under downloads/ (gitignored), keyed on the
# BSP version embedded in the tarball name. The device-tree sources live inside the nested
# kernel_oot_modules_src.tbz2.
REF="$REPO/downloads/.dt-ref-${PUBLIC_SOURCES_FILE%.tbz2}"
STOCK_NVP="$REF/hardware/nvidia/t23x/nv-public"
if [ ! -d "$STOCK_NVP" ]; then
    echo "Extracting stock nv-public from $(basename "$PUB") (one-time)..."
    rm -rf "$REF"; mkdir -p "$REF"
    tmp="$(mktemp -d)"
    tar xjf "$PUB" -C "$tmp" Linux_for_Tegra/source/kernel_oot_modules_src.tbz2
    tar xf "$tmp/Linux_for_Tegra/source/kernel_oot_modules_src.tbz2" -C "$REF" hardware/nvidia/t23x/nv-public
    rm -rf "$tmp"
fi

NVP_REL="device_tree/source/hardware/nvidia/t23x/nv-public"
rc=0
for t in "${TARGETS[@]}"; do
    base="products/$t/$NVP_REL"
    [ -d "$base" ] || continue
    dup=0; delta=0; new=0
    echo "=== $t ==="
    while IFS= read -r f; do
        rel="${f#"$base"/}"
        stock="$STOCK_NVP/$rel"
        if [ ! -f "$stock" ]; then
            new=$((new+1))
        elif cmp -s "$f" "$stock"; then
            dup=$((dup+1)); rc=1
            echo "  DUPLICATE (identical to BSP — remove it or express as a delta): $rel"
        else
            delta=$((delta+1))
        fi
    done < <(find "$base" -type f | sort)
    echo "  ARK files: $new   ARK deltas: $delta   duplicates: $dup"
done

echo "(bootloader BCT files are not checked here — they are ARK pinmux/gpio deltas by design.)"
if [ "$rc" -eq 0 ]; then echo "OK: no stock duplicates."; else echo "FAIL: stock duplicates found above."; fi
exit $rc
