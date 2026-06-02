#!/bin/bash
# Shared BSP version detection. Source this from setup.sh, build.sh, and
# flash.sh — versions.env is the single source of truth for both the
# expected version and the download URLs.

_check_bsp_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$_check_bsp_dir/../versions.env"
unset _check_bsp_dir

# Reads rootfs/etc/nv_tegra_release from a staging directory.
# Sets DETECTED_BSP_RELEASE / DETECTED_BSP_REVISION.
# Returns: 0 = match, 1 = nothing to detect, 2 = present but wrong version.
#
# $1: staging directory (e.g. staging/PAB) — must contain Linux_for_Tegra/
detect_bsp_version() {
    local staging_dir="$1"
    local release_file="$staging_dir/Linux_for_Tegra/rootfs/etc/nv_tegra_release"
    DETECTED_BSP_RELEASE=""
    DETECTED_BSP_REVISION=""

    if [ ! -f "$release_file" ]; then
        return 1
    fi

    # Format: "# R36 (release), REVISION: 4.4, GCID: ..."
    DETECTED_BSP_RELEASE=$(grep -oE '^# R[0-9]+' "$release_file" | awk '{print $2}')
    DETECTED_BSP_REVISION=$(grep -oE 'REVISION: [0-9.]+' "$release_file" | awk '{print $2}')

    if [ -z "$DETECTED_BSP_RELEASE" ] || [ -z "$DETECTED_BSP_REVISION" ]; then
        return 1
    fi

    if [ "$DETECTED_BSP_RELEASE" = "$EXPECTED_BSP_RELEASE" ] \
        && [ "$DETECTED_BSP_REVISION" = "$EXPECTED_BSP_REVISION" ]; then
        return 0
    fi

    return 2
}

# Aborts the calling script if the staged BSP is missing or version-mismatched.
# $1: staging directory (e.g. staging/PAB)
require_bsp_staging() {
    local staging_dir="$1"
    detect_bsp_version "$staging_dir"
    case $? in
        0)
            return 0
            ;;
        1)
            echo "ERROR: BSP not staged — staging directory is missing or incomplete."
            echo "       Expected: ${EXPECTED_BSP_RELEASE}.${EXPECTED_BSP_REVISION}"
            echo ""
            echo "Run ./build.sh <TARGET> --clean to re-stage from downloads."
            exit 1
            ;;
        2)
            echo "ERROR: BSP version mismatch in staging directory."
            echo "       Found:    ${DETECTED_BSP_RELEASE}.${DETECTED_BSP_REVISION}"
            echo "       Expected: ${EXPECTED_BSP_RELEASE}.${EXPECTED_BSP_REVISION}"
            echo ""
            echo "Run ./setup.sh then ./build.sh <TARGET> --clean to upgrade."
            exit 1
            ;;
    esac
}
