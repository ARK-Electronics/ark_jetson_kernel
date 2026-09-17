#!/bin/bash
# Package policy shared by provisioning and its offline tests. No side effects
# occur until a caller invokes one of these functions.

select_provision_packages() {
    local rootfs="$1" codename
    codename=$(bash -c '. "$1/etc/os-release"; printf "%s" "$VERSION_CODENAME"' -- "$rootfs") || return 1
    case "${EXPECTED_BSP_RELEASE}:$codename" in
        R36:jammy)
            ARK_OS_PKG=ark-os-jetson-jammy
            ARK_OS_PYTHON=python3.10
            NV_CAMERA_POLICY=repack-r36
            NV_CAMERA_POOL=t234
            [ "$NV_CAMERA_STACK_VERSION" = 36.4.4-20250616085344 ] &&
                [ -n "${NV_CAMERA_PIN_VERSION:-}" ] || {
                echo 'ERROR: the R36 camera workaround requires its audited source and installed version pins.' >&2
                return 1
            }
            NV_CAMERA_INSTALLED_VERSION="$NV_CAMERA_PIN_VERSION"
            ;;
        R39:noble)
            ARK_OS_PKG=ark-os-jetson-noble
            ARK_OS_PYTHON=python3.12
            NV_CAMERA_POLICY=native
            NV_CAMERA_POOL=som
            case "$NV_CAMERA_STACK_VERSION" in
                "${EXPECTED_BSP_RELEASE#R}.${EXPECTED_BSP_REVISION}-"*) ;;
                *) echo 'ERROR: R39 requires camera packages from the selected BSP release.' >&2; return 1 ;;
            esac
            NV_CAMERA_INSTALLED_VERSION="$NV_CAMERA_STACK_VERSION"
            ;;
        *)
            echo "ERROR: unsupported BSP/rootfs pairing: ${EXPECTED_BSP_RELEASE}/$codename." >&2
            return 1
            ;;
    esac
    ARK_OS_DEB="${ARK_OS_PKG}_${ARK_OS_VERSION}_arm64.deb"
    ARK_OS_URL="https://github.com/ARK-Electronics/ARK-OS/releases/download/v${ARK_OS_VERSION}/${ARK_OS_DEB}"
}

validate_ark_os_deb() {
    local path="$1" actual field expected
    for field in Package Architecture Version; do
        case "$field" in
            Package) expected="$ARK_OS_PKG" ;;
            Architecture) expected=arm64 ;;
            Version) expected="$ARK_OS_VERSION" ;;
        esac
        actual=$(dpkg-deb -f "$path" "$field") || return 1
        if [ "$actual" != "$expected" ]; then
            echo "ERROR: ARK-OS $field is '$actual'; expected '$expected' for this image." >&2
            return 1
        fi
    done
    # A renamed Jammy artifact still carries a Python 3.10 venv and cannot run on
    # Noble. Require the native runtime dependency as well as the package name.
    actual=$(dpkg-deb -f "$path" Depends) || return 1
    if ! printf '%s\n' "$actual" | grep -Eq "(^|,)[[:space:]]*${ARK_OS_PYTHON//./\\.}([[:space:]]*\\([^)]*\\))?([[:space:]]*,|[[:space:]]*$)"; then
        echo "ERROR: ARK-OS does not require the expected $ARK_OS_PYTHON runtime." >&2
        return 1
    fi
}


# R39 publishes the selected GPU path at first boot. Offline validation must not
# run its device-detecting selector against the build host's /proc. All supported
# carriers use Orin (tegra23x), whose default backend is nvgpu-l4t; retain an
# explicit rootfs override when present. Return only a process-local search path.
provision_gpu_library_path() {
    local rootfs="$1" target="$2" variant=nvgpu-l4t path
    [ "$EXPECTED_BSP_RELEASE" = R39 ] || return 0
    case "$target" in
        JAJ|PAB|PAB_V3) ;;
        *) echo "ERROR: no audited R39 GPU backend for target $target." >&2; return 1 ;;
    esac
    if [ -s "$rootfs/etc/nvidia-gpu-driver-override" ]; then
        variant=$(tr -d '\n' < "$rootfs/etc/nvidia-gpu-driver-override") || return 1
        variant="${variant,,}"
        [ -n "$variant" ] || variant=nvgpu-l4t
    fi
    case "$variant" in
        nvgpu-l4t) path=/opt/nvidia/l4t-gpu-libs/nvgpu ;;
        openrm-l4t) path=/opt/nvidia/l4t-gpu-libs/openrm ;;
        *) echo "ERROR: unsupported staged R39 GPU variant: $variant." >&2; return 1 ;;
    esac
    if [ ! -s "$rootfs$path/libcuda.so.1" ]; then
        echo "ERROR: staged $variant CUDA library is missing from $path." >&2
        return 1
    fi
    printf '%s\n' "$path"
}

# This compatibility workaround is deliberately unavailable to R39. Its native
# packages must keep all ABI bounds and the selected nvgpu/openrm backend.
relax_l4t_deps() {
    local input="$1" output="$2" work
    if [ "${NV_CAMERA_POLICY:-}" != repack-r36 ] || [ "$EXPECTED_BSP_RELEASE" != R36 ]; then
        echo 'ERROR: camera dependency rewriting is restricted to the R36 workaround.' >&2
        return 1
    fi
    work=$(mktemp -d) || return 1
    if ! dpkg-deb -R "$input" "$work"; then rm -rf "$work"; return 1; fi
    sed -i \
        -e 's/nvidia-l4t-core (<< [0-9.]*-0)/nvidia-l4t-core (<< 37.0-0)/' \
        -e 's/nvidia-l4t-cuda (= [^)]*)/nvidia-l4t-cuda/' \
        -e 's/nvidia-l4t-nvsci (= [^)]*)/nvidia-l4t-nvsci/' \
        -e "s/(= ${NV_CAMERA_STACK_VERSION})/(= ${NV_CAMERA_PIN_VERSION})/g" \
        -e "s/^Version: .*/Version: ${NV_CAMERA_PIN_VERSION}/" \
        "$work/DEBIAN/control" || { rm -rf "$work"; return 1; }
    dpkg-deb -b --root-owner-group "$work" "$output"
    local rc=$?
    rm -rf "$work"
    return "$rc"
}
