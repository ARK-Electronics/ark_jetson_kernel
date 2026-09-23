#!/bin/bash
#
# Rootfs provisioning — runs during staging on a full build (skipped by build.sh --no-provision).
# Env: ROOTFS_DIR (the staged rootfs), TARGET (PAB|JAJ|PAB_V3).
#
# /proc, /sys, /dev are bind-mounted and DNS is set up; /run is not, so ARK-OS's
# postinst sees no running systemd and defers its runtime steps to first boot. The
# default 'jetson' user already exists, which ark-os's postinst relies on.

set -e

# Wall-clock start, reported at the end.
PROVISION_START=$(date +%s)

# Version pins (ARK_OS_VERSION, JETSON_STATS_VERSION) live in versions.env; keep
# JETSON_STATS in sync with ARK-OS packaging/versions.env.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=versions.env
source "$SCRIPT_DIR/versions.env"

# Select the artifact by the actual rootfs ABI, then validate its Debian metadata.
# A Jammy venv cannot be installed into Noble by renaming the package.
source "$SCRIPT_DIR/scripts/provision_packages.sh"
select_provision_packages "$ROOTFS_DIR"
DOWNLOADS_DIR="${DOWNLOADS_DIR:-$SCRIPT_DIR/downloads}"

NV_CAMERA_PKGS=(nvidia-l4t-gstreamer nvidia-l4t-camera nvidia-l4t-multimedia nvidia-l4t-multimedia-utils)
nv_camera_deb() { echo "${1}_${NV_CAMERA_STACK_VERSION}_arm64.deb"; }
nv_camera_url() {
    local pool="$NV_CAMERA_POOL"
    [ "$1" = nvidia-l4t-gstreamer ] && pool=common
    echo "https://repo.download.nvidia.com/jetson/${pool}/pool/main/n/${1}/$(nv_camera_deb "$1")"
}

# Stage a deb into the rootfs /tmp, caching under downloads/ so rebuilds reuse it
fetch_deb() {
    local deb="$1" url="$2"
    if [ ! -f "$DOWNLOADS_DIR/$deb" ]; then
        echo "Downloading $deb from $url"
        if ! sudo wget -nv -o /dev/stderr -O "$DOWNLOADS_DIR/$deb.partial" "$url"; then
            sudo rm -f "$DOWNLOADS_DIR/$deb.partial"
            echo "ERROR: could not fetch $deb (not cached in $DOWNLOADS_DIR, and the" >&2
            echo "       download from $url failed — the release may not exist yet)." >&2
            echo "       Fix: drop the deb into $DOWNLOADS_DIR and set the matching" >&2
            echo "       *_VERSION in versions.env so the filename lines up." >&2
            exit 1
        fi
        sudo mv "$DOWNLOADS_DIR/$deb.partial" "$DOWNLOADS_DIR/$deb"
    else
        echo "Using cached $deb from $DOWNLOADS_DIR"
    fi
    sudo cp "$DOWNLOADS_DIR/$deb" "$ROOTFS_DIR/tmp/$deb"
}

echo "Fetching the ABI-matched ARK-OS deb..."
if [ -n "${ARK_OS_DEB_PATH:-}" ]; then
    validate_ark_os_deb "$ARK_OS_DEB_PATH"
    sudo cp "$ARK_OS_DEB_PATH" "$ROOTFS_DIR/tmp/$ARK_OS_DEB"
else
    # No fallback to a different release or OS: absence is actionable, not an
    # excuse to silently ship an incompatible or unpinned application.
    if [ ! -f "$DOWNLOADS_DIR/$ARK_OS_DEB" ] && ! curl -sfIL -o /dev/null "$ARK_OS_URL"; then
        echo "ERROR: pinned artifact $ARK_OS_DEB is not cached or published." >&2
        echo "       Build the matching package using docs/jetpack7_provisioning.md," >&2
        echo "       place it in downloads/, or set ARK_OS_DEB_PATH to its local path." >&2
        exit 1
    fi
    fetch_deb "$ARK_OS_DEB" "$ARK_OS_URL"
    validate_ark_os_deb "$ROOTFS_DIR/tmp/$ARK_OS_DEB"
fi

# Block service (re)starts in the chroot: there's no init, so a dependency's
# maintainer script trying to start a daemon would fail or hang. policy-rc.d → 101
# guarantees this regardless of whether each package's scripts self-gate; the trap
# removes the shim on exit.
printf '#!/bin/sh\nexit 101\n' | sudo tee "$ROOTFS_DIR/usr/sbin/policy-rc.d" >/dev/null
sudo chmod 0755 "$ROOTFS_DIR/usr/sbin/policy-rc.d"

# NVIDIA's apt source has a templated <SOC> entry resolved only on-device; in the
# chroot it 404s and fails apt-get update under set -e. ARK-OS needs no NVIDIA repos,
# so move it aside during provisioning and restore on exit — the shipped image is
# left untouched so first boot resolves <SOC> as usual.
NV_APT_SRC="$ROOTFS_DIR/etc/apt/sources.list.d/nvidia-l4t-apt-source.list"
[ -f "$NV_APT_SRC" ] && sudo mv "$NV_APT_SRC" "$NV_APT_SRC.provision-disabled"

# Undo both on any exit so the flashed image boots and updates normally.
cleanup_provision() {
    sudo rm -f "$ROOTFS_DIR/usr/sbin/policy-rc.d"
    [ -f "$NV_APT_SRC.provision-disabled" ] && \
        sudo mv "$NV_APT_SRC.provision-disabled" "$NV_APT_SRC"
}
trap cleanup_provision EXIT

### Install ARK-OS
echo "Installing ${ARK_OS_PKG}..."
sudo chroot "$ROOTFS_DIR" apt-get update
sudo chroot "$ROOTFS_DIR" apt-get install -y "/tmp/$ARK_OS_DEB"

### Confirm the package is fully configured
echo "Verifying ARK-OS is installed..."
status=$(sudo chroot "$ROOTFS_DIR" dpkg-query -W -f='${Status}' "$ARK_OS_PKG" 2>/dev/null || true)
if [ "$status" != "install ok installed" ]; then
    echo "ERROR: $ARK_OS_PKG is not installed (dpkg status: '${status:-not present}')." >&2
    echo "       Aborting provisioning to avoid shipping an image without ARK-OS." >&2
    exit 1
fi
# The services load the MAVSDK bundled inside the deb; assert it shipped.
sudo chroot "$ROOTFS_DIR" sh -c 'ls /usr/lib/ark-os/mavsdk/lib/libmavsdk.so.* >/dev/null 2>&1' \
    || { echo "ERROR: installed ark-os ships no bundled MAVSDK under /usr/lib/ark-os/mavsdk." >&2; exit 1; }

# ARK-OS binds its gateway and flight review to IPv4. Noble may resolve
# localhost to ::1 in nginx, producing intermittent 502s despite healthy services.
# Patch only the pinned package configuration; unknown customizations fail closed.
if [ "$EXPECTED_BSP_RELEASE" = R39 ]; then
    sudo python3 "$SCRIPT_DIR/scripts/patch_ark_nginx_upstreams.py" \
        --rootfs "$ROOTFS_DIR" --product "$TARGET"
    sudo chroot "$ROOTFS_DIR" nginx -t
fi

### Install the camera userspace stack (Argus + GStreamer plugins)
# R39 uses its native BSP versions and dependencies unchanged. In particular,
# preserve the nvgpu/openrm backend selected by apply_binaries. Only R36 uses
# the previously validated Argus regression workaround and package holds.
echo "Installing camera userspace (${NV_CAMERA_INSTALLED_VERSION}, $NV_CAMERA_POLICY)..."
NV_CAMERA_TMP_DEBS=()
for pkg in "${NV_CAMERA_PKGS[@]}"; do
    deb=$(nv_camera_deb "$pkg")
    fetch_deb "$deb" "$(nv_camera_url "$pkg")"
    if [ "$NV_CAMERA_POLICY" = repack-r36 ]; then
        work=$(mktemp -d)
        relax_l4t_deps "$DOWNLOADS_DIR/$deb" "$work/ark1_$deb"
        sudo mv "$work/ark1_$deb" "$ROOTFS_DIR/tmp/ark1_$deb"
        rmdir "$work"
        sudo rm -f "$ROOTFS_DIR/tmp/$deb"
        NV_CAMERA_TMP_DEBS+=("/tmp/ark1_$deb")
    else
        NV_CAMERA_TMP_DEBS+=("/tmp/$deb")
    fi
done
# One transaction preserves the quartet's exact-version dependencies.
sudo chroot "$ROOTFS_DIR" apt-get install -y --allow-downgrades --allow-change-held-packages \
    "${NV_CAMERA_TMP_DEBS[@]}"
if [ "$NV_CAMERA_POLICY" = repack-r36 ]; then
    sudo chroot "$ROOTFS_DIR" apt-mark hold "${NV_CAMERA_PKGS[@]}"
fi
for pkg in "${NV_CAMERA_PKGS[@]}"; do
    v=$(sudo chroot "$ROOTFS_DIR" dpkg-query -W -f='${Version}' "$pkg")
    [ "$v" = "$NV_CAMERA_INSTALLED_VERSION" ] || {
        echo "ERROR: $pkg is '$v', expected $NV_CAMERA_INSTALLED_VERSION." >&2; exit 1; }
done
sudo chroot "$ROOTFS_DIR" apt-get check
# Assert the plugin actually loads and registers nvarguscamerasrc — file existence
# alone misses unresolvable libraries. Inspect the plugin *file*, not the element:
# element instantiation (e.g. --exists) dials nvargus-daemon/EGL, absent in a chroot.
# The registry cache is pointed at /tmp so scan state doesn't ship in the image.
GPU_LIBRARY_PATH=$(provision_gpu_library_path "$ROOTFS_DIR" "$TARGET")
GST_CHECK_ENV=(GST_REGISTRY=/tmp/provision-gst-registry.bin)
# R39's GPU libraries become globally visible through its first-boot service.
# Supply only this check's loader path; leave that service and its state intact.
[ -z "$GPU_LIBRARY_PATH" ] || GST_CHECK_ENV+=("LD_LIBRARY_PATH=$GPU_LIBRARY_PATH")
sudo chroot "$ROOTFS_DIR" env "${GST_CHECK_ENV[@]}" \
    gst-inspect-1.0 /usr/lib/aarch64-linux-gnu/gstreamer-1.0/libgstnvarguscamerasrc.so \
    | grep -qw nvarguscamerasrc \
    || { echo "ERROR: nvarguscamerasrc missing or failed to load after installing the camera stack." >&2; exit 1; }
sudo rm -f "$ROOTFS_DIR/tmp/provision-gst-registry.bin"
for deb in "${NV_CAMERA_TMP_DEBS[@]}"; do sudo rm -f "$ROOTFS_DIR$deb"; done

### Hold the boot chain we build ourselves
# /boot/Image and /boot/*.dtb* are ordinary package files owned by nvidia-l4t-kernel
# and nvidia-l4t-kernel-dtbs, not conffiles, and the image ships NVIDIA's apt source
# live — so one `apt upgrade` swaps the ARK defconfig and DTBs for stock. The DTB half
# is the silent one: the board keeps running the ARK tree from the kernel-dtb
# partition, so only jetson-io notices, dying on the resulting model mismatch.
# nvidia-l4t-bootloader is in the set because its postinst rewrites the QSPI that
# carries our MB1 BCT pinmux. The kernel packages hold as one group — vermagic ties
# the OOT modules to the kernel, so a partial upgrade is worse than either.
NV_BOOT_CHAIN_PKGS=(
    nvidia-l4t-kernel
    nvidia-l4t-kernel-dtbs
    nvidia-l4t-kernel-headers
    nvidia-l4t-kernel-oot-headers
    nvidia-l4t-kernel-oot-modules
    nvidia-l4t-display-kernel
    nvidia-l4t-bootloader
)
# R39 splits additional kernel backends, partition images, and initrd into
# independent packages. Updating any one would break the matched custom set.
if [ "$EXPECTED_BSP_RELEASE" = R39 ]; then
    NV_BOOT_CHAIN_PKGS+=(
        nvidia-l4t-kernel-module-configs
        nvidia-l4t-kernel-nvgpu
        nvidia-l4t-kernel-openrm
        nvidia-l4t-kernel-partitions
        nvidia-l4t-initrd
        nvidia-l4t-bootloader-utils
    )
fi
for pkg in "${NV_BOOT_CHAIN_PKGS[@]}"; do
    status=$(sudo chroot "$ROOTFS_DIR" dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null || true)
    [ "$status" = 'install ok installed' ] || {
        echo "ERROR: required boot-chain package $pkg is not installed." >&2; exit 1; }
done
echo "Holding the ARK-built boot chain against NVIDIA's apt repo..."
sudo chroot "$ROOTFS_DIR" apt-mark hold "${NV_BOOT_CHAIN_PKGS[@]}"
# apt-mark hold is a no-op on a package that is not installed, so assert the result
# rather than the command.
held=$(sudo chroot "$ROOTFS_DIR" apt-mark showhold)
REQUIRED_HELD_PKGS=("${NV_BOOT_CHAIN_PKGS[@]}")
[ "$NV_CAMERA_POLICY" != repack-r36 ] || REQUIRED_HELD_PKGS+=("${NV_CAMERA_PKGS[@]}")
for pkg in "${REQUIRED_HELD_PKGS[@]}"; do
    printf '%s\n' "$held" | grep -qx "$pkg" || {
        echo "ERROR: $pkg is not held; an apt upgrade would replace it." >&2; exit 1; }
done

### Install pip
sudo chroot "$ROOTFS_DIR" apt-get install -y python3-pip

### Break System Packages on 24.04
PIP_FLAGS=()
if sudo chroot "$ROOTFS_DIR" sh -c 'ls /usr/lib/python3*/EXTERNALLY-MANAGED >/dev/null 2>&1'; then
    PIP_FLAGS=(--break-system-packages)
fi

### Install jtop
# jetson-stats stays a *system* install (not a venv, unlike ARK-OS app code)
echo "Installing jetson-stats (jtop) system-wide..."
sudo chroot "$ROOTFS_DIR" pip3 install "${PIP_FLAGS[@]}" "jetson-stats==${JETSON_STATS_VERSION}"
sudo chroot "$ROOTFS_DIR" python3 -c "import jtop"
sudo chroot "$ROOTFS_DIR" test -f /etc/systemd/system/jtop.service
# Enable jtop.service for first boot, since systemctl can't reach a manager in the chroot.
sudo chroot "$ROOTFS_DIR" mkdir -p /etc/systemd/system/multi-user.target.wants
sudo chroot "$ROOTFS_DIR" ln -sf /etc/systemd/system/jtop.service /etc/systemd/system/multi-user.target.wants/jtop.service

### Install bench test tooling
echo "Installing bench-test tools"

# python3-spidev via apt, not pip: it has no aarch64 wheel, so pip would compile from sdist (pulls in a toolchain)
sudo chroot "$ROOTFS_DIR" apt-get install -y python3-spidev

sudo chroot "$ROOTFS_DIR" apt-get install -y \
    gpiod i2c-tools usbutils pciutils v4l-utils \
    x11-xserver-utils xdotool inxi uhubctl

sudo chroot "$ROOTFS_DIR" pip3 install "${PIP_FLAGS[@]}" pyserial dronecan smbus2 Jetson.GPIO

# Sanity check: importable system-wide. RPi.GPIO (Jetson.GPIO) is left out since it reads /proc/device-tree to detect the Jetson model at import
sudo chroot "$ROOTFS_DIR" python3 -c "import serial, dronecan, smbus2, spidev"

# ── Your packages ───────────────────────────────────────────────────────────
# Preinstall anything else you want baked into the image here.
# For example:
#   sudo chroot "$ROOTFS_DIR" apt-get install -y vim tmux
#   sudo chroot "$ROOTFS_DIR" pip3 install "${PIP_FLAGS[@]}" some-package

# Set the boot clock to build time: an RTC-less fixture with no NTP otherwise boots at a
# stale epoch, and a clock behind the jetson password date breaks gdm-autologin (no X on :0).
sudo mkdir -p "$ROOTFS_DIR/var/lib/systemd/timesync"
sudo touch "$ROOTFS_DIR/var/lib/systemd/timesync/clock"
sudo chroot "$ROOTFS_DIR" chown systemd-timesync:systemd-timesync /var/lib/systemd/timesync/clock
# Pin the password date to the past so clock skew can't trip autologin regardless.
sudo chroot "$ROOTFS_DIR" chage -d 2020-01-01 jetson

sudo rm "$ROOTFS_DIR/tmp/$ARK_OS_DEB"

PROVISION_ELAPSED=$(( $(date +%s) - PROVISION_START ))
printf 'ARK-OS provisioning complete after %dm %02ds.\n' \
    $((PROVISION_ELAPSED / 60)) $((PROVISION_ELAPSED % 60))
