#!/usr/bin/env bash
# Build pinned release-specific ARK NVMe firmware; never flash a device.
set -euo pipefail

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
config_dir="$repo_dir/products/JAJ/fastboot"
profile_dir="$config_dir"
bsp=R36.5.0
firmware_version=36.5.0
source_revision=79ad0c17-jaj
build_dir=${JAJ_UEFI_BUILD_DIR:-}
without_tpm=0
skip_uefi_ffc_pcie=0
docker_bin=${DOCKER:-docker}
image=${JAJ_UEFI_BUILD_IMAGE:-}

usage() {
    cat <<'EOF'
Usage: scripts/build_fast_boot_uefi.sh [--bsp R36.5.0|R39.2.1] [--build-dir DIRECTORY] [--without-tpm] [--skip-uefi-ffc-pcie]

Builds shared RELEASE UEFI for JAJ, PAB and PAB_V3 with NVMe/ext4.
The default BSP remains R36.5.0; select --bsp R39.2.1 for JetPack 7.2.1.
Each release has independent audited source pins and configuration.
The legacy jaj_nvme artifact name is shared; stage with the required --product.
Requires Linux, git, python3, flock, and Docker. Downloads source and builds a container.
Output: DIRECTORY/artifacts/uefi_jaj_nvme_RELEASE.bin (default directory:
/tmp/jaj-uefi-build for R36; staging/uefi-r39.2.1 for R39).
Does not copy into a BSP or access/flash any device.
--without-tpm builds an experimental profile without UEFI TPM measurements,
using /tmp/jaj-uefi-no-tpm by default. Secure Boot, OP-TEE variables and ESRT
remain enabled. Only use this for devices that do not require TPM measured boot.
--skip-uefi-ffc-pcie builds a separate JAJ T234 experiment that skips only PCIe
C7 (FFC) in UEFI, leaving the OS DTB and TPM/security/persistence unchanged.
Default directory: /tmp/jaj-uefi-skip-ffc. Do not combine with --without-tpm;
this candidate isolates controller selection with TPM enabled.
Overlapping builds in one directory are rejected. Successful rebuilds retain the
previous artifact set in the reported .artifacts.build.* directory.
DOCKER may name a Docker executable; JAJ_UEFI_BUILD_IMAGE selects the image tag.
EOF
}
while (($#)); do
    case "$1" in
        --build-dir) build_dir=${2:?--build-dir requires a directory}; shift 2 ;;
        --bsp) bsp=${2:?--bsp requires a release}; shift 2 ;;
        --without-tpm) without_tpm=1; shift ;;
        --skip-uefi-ffc-pcie) skip_uefi_ffc_pcie=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done
case "$bsp" in
    R36.5.0) ;;
    R39.2.1)
        profile_dir="$config_dir/r39.2.1"
        firmware_version=39.2.1
        source_revision=7e9f9ec4-jaj
        if ((skip_uefi_ffc_pcie)); then
            printf 'The C7 source patch is audited only for R36.5.0\n' >&2
            exit 2
        fi
        ;;
    *) printf 'Unsupported firmware BSP: %s\n' "$bsp" >&2; exit 2 ;;
esac
image=${image:-ark-jaj-uefi-builder:${bsp,,}}
if ((without_tpm && skip_uefi_ffc_pcie)); then
    printf 'Use separate candidates: --skip-uefi-ffc-pcie requires TPM enabled\n' >&2
    exit 2
fi
if [[ "$bsp" == R39.2.1 && -z "$build_dir" ]]; then
    build_dir="$repo_dir/staging/uefi-r39.2.1"
    if ((without_tpm)); then build_dir+="-no-tpm"; fi
fi
if [[ -z "$build_dir" ]]; then
    if ((skip_uefi_ffc_pcie)); then
        build_dir=/tmp/jaj-uefi-skip-ffc
    elif ((without_tpm)); then
        build_dir=/tmp/jaj-uefi-no-tpm
    else
        build_dir=/tmp/jaj-uefi-build
    fi
fi
mkdir -p -- "$build_dir"
build_dir=$(cd -- "$build_dir" && pwd -P)
# Keep this inode in place: unlinking a flock file can let another writer acquire
# a different lock. Lock before even generating config in a reused directory.
exec {build_lock_fd}>"$build_dir/.ark-uefi-build.lock"
if ! flock -n "$build_lock_fd"; then
    printf 'Another firmware build owns %s; use a separate --build-dir\n' "$build_dir" >&2
    exit 1
fi
patch_applied=0
boot_order_patch_applied=0
artifacts_work=""
artifacts_identity=""
cleanup_build() {
    build_status=$?
    trap - EXIT INT TERM
    if ((boot_order_patch_applied)); then
        if ! python3 - "$profile_dir" "$build_dir/src/edk2-nvidia" <<'PYBOOTCLEANUP'
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, sys.argv[1])
import uefi_single_boot_order as patch
if patch.digest(Path(sys.argv[2]) / patch.SOURCE) != patch.BEFORE:
    subprocess.run([sys.executable, str(Path(sys.argv[1]) / "uefi_single_boot_order.py"),
                    "restore", sys.argv[2]], check=True)
PYBOOTCLEANUP
        then
            printf 'Could not restore R39 boot-order source; inspect it before rebuilding\n' >&2
            build_status=1
        fi
    fi
    if ((patch_applied)); then
        # Also cover interruption while the apply helper is running. An exact
        # original file needs no restoration; every other state is checked by
        # the normal restore helper, which never resets an external edit.
        if ! python3 - "$config_dir" "$build_dir/src/edk2-nvidia" <<'PYCLEANUP'
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, sys.argv[1])
import uefi_pcie_filter as patch
if patch.digest(Path(sys.argv[2]) / patch.SOURCE) != patch.BEFORE:
    subprocess.run([sys.executable, str(Path(sys.argv[1]) / "uefi_pcie_filter.py"),
                    "restore", sys.argv[2]], check=True)
PYCLEANUP
        then
            printf 'Could not restore candidate source; inspect it before rebuilding\n' >&2
            build_status=1
        fi
    fi
    # Atomic exchange leaves the previous good artifact directory at the work
    # path. Only remove our new directory, never that previous generation (even
    # if interrupted immediately after publication).
    if [[ -n "$artifacts_work" && -d "$artifacts_work" ]] &&
       [[ $(stat -c '%d:%i' -- "$artifacts_work") == "$artifacts_identity" ]]; then
        rm -rf -- "$artifacts_work"
    fi
    exit "$build_status"
}
trap cleanup_build EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
mkdir -p -- "$build_dir/src" "$build_dir/generated-config"
artifacts_work=$(mktemp -d "$build_dir/.artifacts.build.XXXXXXXX")
artifacts_identity=$(stat -c '%d:%i' -- "$artifacts_work")
# Keep the base profile unchanged and record the actual requested configuration.
python3 - "$profile_dir/jaj_nvme.defconfig" "$build_dir/generated-config/jaj_nvme.defconfig" "$without_tpm" <<'PYCONFIG'
from pathlib import Path
import sys
source, destination, without_tpm = sys.argv[1:]
config = Path(source).read_text()
if without_tpm == "1":
    setting = "CONFIG_SECURITY_TPM_FIRMWARE=y"
    if config.count(setting) != 1:
        raise SystemExit("Expected exactly one firmware-TPM setting in base profile")
    config = config.replace(setting, "# Experimental: no UEFI TPM support or measured boot.\n# CONFIG_SECURITY_TPM_FIRMWARE is not set\nCONFIG_SECURITY_TPM_NONE=y")
Path(destination).write_text(config)
PYCONFIG

# Use commit hashes so an upstream branch/tag change cannot silently change a
# firmware build. Never reset or modify an existing checkout with another HEAD.
while read -r source_repo revision source_url; do
    [[ -z "$source_repo" || "$source_repo" == \#* ]] && continue
    source_dir="$build_dir/src/$source_repo"
    if [[ ! -e "$source_dir" ]]; then
        git init -q "$source_dir"
        git -C "$source_dir" remote add origin "${source_url:-https://github.com/NVIDIA/$source_repo.git}"
        git -C "$source_dir" fetch --depth 1 origin "$revision"
        git -C "$source_dir" checkout --detach -q FETCH_HEAD
    fi
    actual=$(git -C "$source_dir" rev-parse HEAD)
    if [[ "$actual" != "$revision" ]]; then
        printf 'Refusing source mismatch in %s: expected %s, got %s\n' "$source_dir" "$revision" "$actual" >&2
        exit 1
    fi
    if ! git -C "$source_dir" diff --quiet || ! git -C "$source_dir" diff --cached --quiet; then
        printf 'Refusing modified tracked source files in %s\n' "$source_dir" >&2
        exit 1
    fi
done < "$profile_dir/uefi-sources.lock"
git -C "$build_dir/src/edk2" submodule update --init --depth 1 --jobs 4

# Apply only to this explicitly selected candidate checkout. The helper checks
# both the pinned original and complete patched source hashes. Restore on exit;
# refuse cleanup if another writer changed the patched file during the build.
if ((skip_uefi_ffc_pcie)); then
    patch_applied=1
    python3 "$config_dir/uefi_pcie_filter.py" apply "$build_dir/src/edk2-nvidia"
fi
# R39's single-boot launcher must outrank a saved disk/EFI entry, including
# when that launcher already exists in persistent BootOrder. This correction
# is required for every R39 reduced build and leaves security variables intact.
if [[ "$bsp" == R39.2.1 ]]; then
    boot_order_patch_applied=1
    python3 "$profile_dir/uefi_single_boot_order.py" apply "$build_dir/src/edk2-nvidia"
fi

# Separate build directories may share this tag. Run the exact image produced
# by this build, even if another process subsequently retags it.
"$docker_bin" build --iidfile "$artifacts_work/container-image.txt" \
    -t "$image" -f "$profile_dir/Dockerfile" "$profile_dir"
build_image_id=$(cat -- "$artifacts_work/container-image.txt")
[[ "$build_image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || {
    printf 'Docker did not record a valid immutable image ID\n' >&2; exit 1;
}
# Require this build to produce the exported image; do not accept a cached export
# if the builder unexpectedly returns success without generating one.
artifact="$build_dir/src/images/uefi_jaj_nvme_RELEASE.bin"
rm -f -- "$artifact"
"$docker_bin" run --rm --user "$(id -u):$(id -g)" \
    -v "$build_dir:/build" -v "$config_dir:/config:ro" \
    -v "$artifacts_work:/artifacts" -w /build/src \
    -e UEFI_SKIP_UPDATE=1 -e UEFI_SKIP_VENV=1 -e UEFI_RELEASE_ONLY=1 \
    -e "FIRMWARE_VERSION_BASE=$firmware_version" -e "GIT_SYNC_REVISION=$source_revision" \
    "$build_image_id" bash -euc '
        # These EDK2 Python tools still import pkg_resources, removed from newer
        # setuptools. Pin that dependency rather than silently modifying EDK2.
        test -x venv/bin/python || python3 -m venv venv
        venv/bin/pip install --disable-pip-version-check \
            -r edk2/pip-requirements.txt setuptools==75.8.2 kconfiglib==14.1.0
        . venv/bin/activate
        # NVIDIA merges an existing .config after the requested defconfig.
        # Clear only our generated profile so changed inputs actually apply.
        rm -f nvidia-config/jaj_nvme/.config
        # R39 replaced the shell target loop with a Stuart --target option;
        # UEFI_RELEASE_ONLY alone is ignored and would compile DEBUG.
        build_args=()
        if [[ "$FIRMWARE_VERSION_BASE" == 39.2.1 ]]; then
            build_args+=(--target RELEASE)
        fi
        bash edk2-nvidia/Platform/NVIDIA/Tegra/build.sh \
            --init-defconfig /build/generated-config/jaj_nvme.defconfig "${build_args[@]}"
        dtc -@ -I dts -O dtb -o /artifacts/ark_fast_boot.dtbo /config/ark_fast_boot.dts
        python -m pip freeze > /artifacts/python-packages.txt
        aarch64-linux-gnu-gcc --version > /artifacts/compiler.txt
    ' 2>&1 | tee "$build_dir/uefi-build.log"

[[ -s "$artifact" ]] || { printf 'Missing firmware: %s\n' "$artifact" >&2; exit 1; }
# The standard T234 QSPI A/B cpu-bootloader partitions are 3.5 MiB.
(( $(stat -c %s "$artifact") <= 3670016 )) || {
    printf 'Firmware exceeds the standard T234 UEFI partition size\n' >&2; exit 1;
}
cp -- "$artifact" "$artifacts_work/"
cp -- "$build_dir/generated-config/jaj_nvme.defconfig" "$profile_dir/uefi-sources.lock" "$config_dir/ark_fast_boot.dts" "$artifacts_work/"
cp -- "$build_dir/src/nvidia-config/jaj_nvme/.config" "$artifacts_work/resolved.config"
"$docker_bin" image inspect --format '{{.Id}}' "$build_image_id" > "$artifacts_work/container-image.txt"
python3 - "$artifacts_work/build-options.json" "$without_tpm" "$skip_uefi_ffc_pcie" <<'PYOPTIONS'
import json
from pathlib import Path
import sys
path, without_tpm, skip_uefi_ffc_pcie = sys.argv[1:]
Path(path).write_text(json.dumps({"without_tpm": without_tpm == "1",
                                 "skip_uefi_ffc_pcie": skip_uefi_ffc_pcie == "1"}, indent=2) + "\n")
PYOPTIONS
python3 - "$artifacts_work/artifact-release.json" "$bsp" <<'PYRELEASE'
import json
from pathlib import Path
import sys
metadata = {"schema_version": 1, "bsp": sys.argv[2]}
if sys.argv[2] == "R39.2.1":
    metadata["source_fix"] = "r39-single-boot-order-v1"
Path(sys.argv[1]).write_text(json.dumps(metadata, indent=2) + "\n")
PYRELEASE
if ((skip_uefi_ffc_pcie)); then
    cp -- "$config_dir/uefi-pcie-skip-ffc.patch" "$artifacts_work/"
    python3 "$config_dir/uefi_pcie_filter.py" describe > "$artifacts_work/source-patches.json"
fi
if [[ "$bsp" == R39.2.1 ]]; then
    cp -- "$profile_dir/uefi-single-boot-order.patch" "$artifacts_work/"
    python3 "$profile_dir/uefi_single_boot_order.py" describe > "$artifacts_work/source-patches.json"
fi
(cd -- "$artifacts_work" && sha256sum uefi_jaj_nvme_RELEASE.bin ark_fast_boot.dtbo artifact-release.json > SHA256SUMS)
# Validate and restore before publishing checksums. A changed source must not
# leave an apparently stageable new artifact with audited-patch provenance.
if ((boot_order_patch_applied)); then
    python3 "$profile_dir/uefi_single_boot_order.py" restore "$build_dir/src/edk2-nvidia"
    boot_order_patch_applied=0
fi
if ((patch_applied)); then
    python3 "$config_dir/uefi_pcie_filter.py" restore "$build_dir/src/edk2-nvidia"
    patch_applied=0
fi
while read -r source_repo revision source_url; do
    [[ -z "$source_repo" || "$source_repo" == \#* ]] && continue
    source_dir="$build_dir/src/$source_repo"
    if [[ $(git -C "$source_dir" rev-parse HEAD) != "$revision" ]] ||
       ! git -C "$source_dir" diff --quiet || ! git -C "$source_dir" diff --cached --quiet; then
        printf 'Source changed during firmware build: %s; artifacts not published\n' "$source_dir" >&2
        exit 1
    fi
done < "$profile_dir/uefi-sources.lock"

# Both directories are on the same filesystem. Linux RENAME_EXCHANGE replaces
# an existing complete set atomically and preserves it at the private work path.
# If the filesystem lacks this operation, fail without touching the old set.
python3 - "$artifacts_work" "$build_dir/artifacts" <<'PYPUBLISH'
import ctypes
import os
from pathlib import Path
import stat
import sys
work, destination = map(Path, sys.argv[1:])
if destination.exists() or destination.is_symlink():
    if not stat.S_ISDIR(destination.lstat().st_mode):
        raise SystemExit("Refusing a non-directory artifact destination")
    libc = ctypes.CDLL(None, use_errno=True)
    exchange = libc.renameat2
    exchange.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    exchange.restype = ctypes.c_int
    if exchange(-100, os.fsencode(work), -100, os.fsencode(destination), 2):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    print(f"Previous artifacts retained: {work}")
else:
    work.rename(destination)
PYPUBLISH
printf '\nBuilt: %s\n' "$build_dir/artifacts/uefi_jaj_nvme_RELEASE.bin"
