#!/bin/bash
# Shared BSP version detection. Source this from setup.sh, build_kernel.sh,
# and flash.sh — bsp_version.env is the single source of truth for both the
# expected version and the download URLs.

_check_bsp_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$_check_bsp_dir/../bsp_version.env"
unset _check_bsp_dir

# Reads prebuilt/Linux_for_Tegra/rootfs/etc/nv_tegra_release (NVIDIA writes it
# during the BSP extract). Sets DETECTED_BSP_RELEASE / DETECTED_BSP_REVISION.
# Returns: 0 = match, 1 = nothing to detect, 2 = present but wrong version.
detect_bsp_version() {
    local repo_dir="$1"
    local release_file="$repo_dir/prebuilt/Linux_for_Tegra/rootfs/etc/nv_tegra_release"
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

# Aborts the calling script with a remediation hint if the BSP isn't ready.
# Use from build_kernel.sh and flash.sh — both require a matching prebuilt/.
require_bsp() {
    local repo_dir="$1"
    detect_bsp_version "$repo_dir"
    case $? in
        0)
            return 0
            ;;
        1)
            echo "ERROR: BSP not set up — prebuilt/ is missing or incomplete."
            echo "       Expected: ${EXPECTED_BSP_RELEASE}.${EXPECTED_BSP_REVISION}"
            echo ""
            echo "Run ./setup.sh to download the BSP."
            exit 1
            ;;
        2)
            echo "ERROR: BSP version mismatch."
            echo "       Found:    ${DETECTED_BSP_RELEASE}.${DETECTED_BSP_REVISION}"
            echo "       Expected: ${EXPECTED_BSP_RELEASE}.${EXPECTED_BSP_REVISION}"
            echo ""
            echo "Re-run ./setup.sh to upgrade the BSP."
            echo "(setup.sh will prompt before deleting your existing prebuilt/ and source_build/)"
            exit 1
            ;;
    esac
}
