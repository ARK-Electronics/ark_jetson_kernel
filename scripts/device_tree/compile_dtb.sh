#!/bin/bash
# Compile one p3768 device-tree blob from an nv-public source tree, mirroring NVIDIA's
# cpp + dtc invocation. Lets you validate device-tree edits — especially the per-product
# ark-<target>-overrides.dtsi fragment — without a full kernel build.
#
# A staged tree exists after `./build.sh <target>` at:
#   staging/<target>/Linux_for_Tegra/source/hardware/nvidia/t23x/nv-public
# (or extract one from the BSP yourself). Then:
#
#   scripts/device_tree/compile_dtb.sh \
#       staging/PAB/Linux_for_Tegra/source/hardware/nvidia/t23x/nv-public \
#       tegra234-p3768-0000+p3767-0000-nv.dts  /tmp/out.dtb
#   dtc -I dtb -O dts /tmp/out.dtb | less   # inspect the result
#
# Usage: compile_dtb.sh <nv-public-dir> <dts> [out.dtb]
#   <dts>  a path if it exists, else resolved under <nv-public-dir>/nv-platform/
#          (e.g. tegra234-p3768-0000+p3767-0003-nv-super.dts).
set -euo pipefail

die() { echo "ERROR: $*" >&2; exit 1; }

command -v cpp >/dev/null 2>&1 || die "cpp not found (install gcc/cpp)"
command -v dtc >/dev/null 2>&1 || die "dtc not found (apt install device-tree-compiler)"
[ $# -ge 2 ] || die "usage: $0 <nv-public-dir> <dts> [out.dtb]"

NVP="${1%/}"
DTS_ARG="$2"
[ -d "$NVP/nv-platform" ] || die "$NVP is not an nv-public dir (no nv-platform/)"

if [ -f "$DTS_ARG" ]; then DTS="$DTS_ARG"; else DTS="$NVP/nv-platform/$DTS_ARG"; fi
[ -f "$DTS" ] || die "dts not found: $DTS_ARG"
OUT="${3:-$(basename "${DTS%.dts}").dtb}"

INC=(-I "$NVP/include/kernel" -I "$NVP/include/nvidia-oot" -I "$NVP/include/platforms"
     -I "$NVP" -I "$NVP/nv-platform" -I "$NVP/nv-soc")

PRE="$(mktemp --suffix=.dts)"
ERR="$(mktemp)"
trap 'rm -f "$PRE" "$ERR"' EXIT

cpp -nostdinc -undef -x assembler-with-cpp -D__DTS__ "${INC[@]}" "$DTS" -o "$PRE"

# dtc emits "also defined at" / unit-name notes for the NVIDIA overlay-style tree; those
# are normal. Fail only on real errors.
if ! dtc -I dts -O dtb -@ -o "$OUT" "$PRE" 2>"$ERR"; then
    grep -iE 'error|fatal' "$ERR" >&2 || cat "$ERR" >&2
    die "dtc failed compiling $(basename "$DTS")"
fi
echo "Compiled $OUT ($(stat -c%s "$OUT") bytes)"
