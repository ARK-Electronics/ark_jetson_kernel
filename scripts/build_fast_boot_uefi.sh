#!/usr/bin/env bash
# Build the pinned R36.5 JAJ NVMe firmware; never flash a device.
set -euo pipefail

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
config_dir="$repo_dir/products/JAJ/fastboot"
build_dir=${JAJ_UEFI_BUILD_DIR:-}
without_tpm=0
docker_bin=${DOCKER:-docker}
image=${JAJ_UEFI_BUILD_IMAGE:-ark-jaj-uefi-builder:r36.5}

usage() {
    cat <<'EOF'
Usage: scripts/build_fast_boot_uefi.sh [--build-dir DIRECTORY] [--without-tpm]

Builds a RELEASE UEFI for R36.5 Just a Jetson with NVMe/ext4.
Requires git, python3, and Docker. Downloads source and builds a container.
Output: DIRECTORY/artifacts/uefi_jaj_nvme_RELEASE.bin (default directory:
/tmp/jaj-uefi-build). Does not copy into a BSP or access/flash any device.
--without-tpm builds an experimental profile without UEFI TPM measurements,
using /tmp/jaj-uefi-no-tpm by default. Secure Boot, OP-TEE variables and ESRT
remain enabled. Only use this for devices that do not require TPM measured boot.
DOCKER may name a Docker executable; JAJ_UEFI_BUILD_IMAGE selects the image tag.
EOF
}
while (($#)); do
    case "$1" in
        --build-dir) build_dir=${2:?--build-dir requires a directory}; shift 2 ;;
        --without-tpm) without_tpm=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done
if [[ -z "$build_dir" ]]; then
    if ((without_tpm)); then
        build_dir=/tmp/jaj-uefi-no-tpm
    else
        build_dir=/tmp/jaj-uefi-build
    fi
fi
mkdir -p -- "$build_dir"
build_dir=$(cd -- "$build_dir" && pwd)
mkdir -p -- "$build_dir/src" "$build_dir/artifacts" "$build_dir/generated-config"
# Keep the base profile unchanged and record the actual requested configuration.
python3 - "$config_dir/jaj_nvme.defconfig" "$build_dir/generated-config/jaj_nvme.defconfig" "$without_tpm" <<'PYCONFIG'
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
done < "$config_dir/uefi-sources.lock"
git -C "$build_dir/src/edk2" submodule update --init --depth 1 --jobs 4

"$docker_bin" build -t "$image" -f "$config_dir/Dockerfile" "$config_dir"
"$docker_bin" run --rm --user "$(id -u):$(id -g)" \
    -v "$build_dir:/build" -v "$config_dir:/config:ro" -w /build/src \
    -e UEFI_SKIP_UPDATE=1 -e UEFI_SKIP_VENV=1 -e UEFI_RELEASE_ONLY=1 \
    -e FIRMWARE_VERSION_BASE=36.5.0 -e GIT_SYNC_REVISION=79ad0c17-jaj \
    "$image" bash -euc '
        # These EDK2 Python tools still import pkg_resources, removed from newer
        # setuptools. Pin that dependency rather than silently modifying EDK2.
        test -x venv/bin/python || python3 -m venv venv
        venv/bin/pip install --disable-pip-version-check \
            -r edk2/pip-requirements.txt setuptools==75.8.2 kconfiglib==14.1.0
        . venv/bin/activate
        # NVIDIA merges an existing .config after the requested defconfig.
        # Clear only our generated profile so changed inputs actually apply.
        rm -f nvidia-config/jaj_nvme/.config
        bash edk2-nvidia/Platform/NVIDIA/Tegra/build.sh \
            --init-defconfig /build/generated-config/jaj_nvme.defconfig
        dtc -@ -I dts -O dtb -o /build/artifacts/ark_fast_boot.dtbo /config/ark_fast_boot.dts
        python -m pip freeze > /build/artifacts/python-packages.txt
        aarch64-linux-gnu-gcc --version > /build/artifacts/compiler.txt
    ' 2>&1 | tee "$build_dir/uefi-build.log"

artifact="$build_dir/src/images/uefi_jaj_nvme_RELEASE.bin"
[[ -s "$artifact" ]] || { printf 'Missing firmware: %s\n' "$artifact" >&2; exit 1; }
# The standard T234 QSPI A/B cpu-bootloader partitions are 3.5 MiB.
(( $(stat -c %s "$artifact") <= 3670016 )) || {
    printf 'Firmware exceeds the standard T234 UEFI partition size\n' >&2; exit 1;
}
cp -- "$artifact" "$build_dir/artifacts/"
cp -- "$build_dir/generated-config/jaj_nvme.defconfig" "$config_dir/uefi-sources.lock" "$config_dir/ark_fast_boot.dts" "$build_dir/artifacts/"
cp -- "$build_dir/src/nvidia-config/jaj_nvme/.config" "$build_dir/artifacts/resolved.config"
"$docker_bin" image inspect --format '{{.Id}}' "$image" > "$build_dir/artifacts/container-image.txt"
(cd -- "$build_dir/artifacts" && sha256sum uefi_jaj_nvme_RELEASE.bin ark_fast_boot.dtbo > SHA256SUMS)
printf '\nBuilt: %s\n' "$build_dir/artifacts/uefi_jaj_nvme_RELEASE.bin"
