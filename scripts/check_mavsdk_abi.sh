#!/usr/bin/env bash
#
# Fail the build if the prebuilt MAVSDK .so needs newer glibc/libstdc++ symbols than
# the target rootfs provides. We ship upstream's debian12 (Bookworm) arm64 build on
# rootfilesystems it wasn't built against (no matching asset exists), which only works
# while the rootfs's symbol versions are >= what the .so references. Static check, run
# on the build host; the likely trigger is a MAVSDK_VERSION bump raising that floor.
#
# Usage: check_mavsdk_abi.sh <rootfs_dir> [arch_tuple]   (arch defaults to arm64)

set -euo pipefail

ROOTFS="${1:?usage: check_mavsdk_abi.sh <rootfs_dir> [arch_tuple]}"
ARCH="${2:-aarch64-linux-gnu}"

command -v readelf >/dev/null 2>&1 || {
    echo "ERROR: readelf not found — install binutils on the build host." >&2
    exit 1
}

# The versioned real file, not the .so symlink; the deb installs it under /usr/lib.
mavsdk_so=$(find "$ROOTFS/usr/lib" "$ROOTFS/usr/lib/$ARCH" -maxdepth 1 \
                -name 'libmavsdk.so.*' -type f 2>/dev/null | sort | head -1 || true)
[ -n "$mavsdk_so" ] || {
    echo "ERROR: libmavsdk.so.* not found under $ROOTFS/usr/lib — is MAVSDK installed?" >&2
    exit 1
}

# Locate a provider lib by soname; /usr-merge layout varies, so try all the usual spots.
find_lib() {
    local soname="$1" p
    for p in "$ROOTFS/lib/$ARCH/$soname" "$ROOTFS/usr/lib/$ARCH/$soname" \
             "$ROOTFS/lib/$soname" "$ROOTFS/usr/lib/$soname"; do
        [ -e "$p" ] && { echo "$p"; return 0; }
    done
    return 1
}

# Highest version of a family present: what the .so needs, or what a provider offers.
max_ver() {
    local file="$1" prefix="$2"
    readelf -V "$file" 2>/dev/null \
        | grep -oE "${prefix}_[0-9]+(\.[0-9]+)+" \
        | sort -uV | tail -1 || true
}

libc=$(find_lib "libc.so.6")           || { echo "ERROR: libc.so.6 not found in $ROOTFS ($ARCH)." >&2; exit 1; }
libstdcxx=$(find_lib "libstdc++.so.6") || { echo "ERROR: libstdc++.so.6 not found in $ROOTFS ($ARCH)." >&2; exit 1; }

echo "MAVSDK ABI check"
echo "  library: ${mavsdk_so#"$ROOTFS"}"
echo "  rootfs:  $ROOTFS ($ARCH)"
printf '  %-9s %-15s %-15s %s\n' family required provided result

fail=0
# Compatible iff required <= provided; these namespaces are backward-compatible, so
# comparing the max of each is enough.
check_family() {
    local family="$1" provider="$2" req prov
    req=$(max_ver "$mavsdk_so" "$family")
    [ -n "$req" ] || return 0   # nothing needed from this family
    prov=$(max_ver "$provider" "$family")
    if [ -n "$prov" ] && [ "$(printf '%s\n%s\n' "$req" "$prov" | sort -V | tail -1)" = "$prov" ]; then
        printf '  %-9s %-15s %-15s %s\n' "$family" "$req" "$prov" "ok"
    else
        printf '  %-9s %-15s %-15s %s\n' "$family" "$req" "${prov:-(none)}" "FAIL"
        fail=1
    fi
}

check_family GLIBC   "$libc"
check_family GLIBCXX "$libstdcxx"
check_family CXXABI  "$libstdcxx"

if [ "$fail" -ne 0 ]; then
    echo "ERROR: the prebuilt MAVSDK library needs newer symbols than this rootfs provides." >&2
    echo "       The shipped image would fail at load with a 'version not found' error." >&2
    echo "       Fix: ship a MAVSDK build matching the rootfs (or a newer rootfs), or pin" >&2
    echo "       MAVSDK_VERSION / the deb variant in versions.env to a compatible one." >&2
    exit 1
fi

echo "MAVSDK ABI check passed."
