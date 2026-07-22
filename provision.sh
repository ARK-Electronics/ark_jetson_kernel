#!/bin/bash
#
# Rootfs provisioning — runs during staging when build.sh is given --provision.
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

ARK_OS_PKG="ark-os-jetson-jammy"
ARK_OS_DEB="${ARK_OS_PKG}_${ARK_OS_VERSION}_arm64.deb"
ARK_OS_URL="https://github.com/ARK-Electronics/ARK-OS/releases/download/v${ARK_OS_VERSION}/${ARK_OS_DEB}"

# Camera userspace stack — pinned below the BSP; see versions.env and
# docs/argus_relaunch_regression.md. gstreamer ships from the `common` pool, the
# other three from the SoC pool.
NV_CAMERA_PKGS=(nvidia-l4t-gstreamer nvidia-l4t-camera nvidia-l4t-multimedia nvidia-l4t-multimedia-utils)
nv_camera_deb() { echo "${1}_${NV_CAMERA_STACK_VERSION}_arm64.deb"; }
nv_camera_url() {
    local pool="t234"
    [ "$1" = "nvidia-l4t-gstreamer" ] && pool="common"
    echo "https://repo.download.nvidia.com/jetson/${pool}/pool/main/n/${1}/$(nv_camera_deb "$1")"
}

# Prefer a deb already in downloads/ over downloading
DOWNLOADS_DIR="${DOWNLOADS_DIR:-$SCRIPT_DIR/downloads}"

# Use the pinned ARK_OS_VERSION when its deb is cached locally or published on
# GitHub; otherwise fall back loudly to the newest published (pre)release.
if [ -f "$DOWNLOADS_DIR/$ARK_OS_DEB" ]; then
    echo "Using local ARK-OS deb: $ARK_OS_DEB"
elif curl -sfIL -o /dev/null "$ARK_OS_URL"; then
    echo "Using pinned ARK-OS release: v${ARK_OS_VERSION}"
else
    echo "WARNING: pinned ARK-OS v${ARK_OS_VERSION} has no published release asset ($ARK_OS_DEB);" >&2
    echo "WARNING: falling back to the newest published ARK-OS (pre)release." >&2
    if [ -n "${GITHUB_ACTIONS:-}" ]; then
        echo "::warning::Pinned ARK-OS v${ARK_OS_VERSION} is not published; provisioning with the newest (pre)release instead."
    fi
    ARK_OS_URL=$(curl -sfL "https://api.github.com/repos/ARK-Electronics/ARK-OS/releases?per_page=100" \
        | python3 -c '
import sys, json
rels = json.load(sys.stdin)
for r in sorted(rels, key=lambda r: r.get("created_at", ""), reverse=True):
    for a in r.get("assets", []):
        n = a.get("name", "")
        if n.startswith("ark-os-jetson-jammy_") and n.endswith("_arm64.deb"):
            print(a["browser_download_url"]); sys.exit(0)
sys.exit(1)
') || { echo "ERROR: could not resolve a latest ARK-OS jetson deb." >&2; exit 1; }
    ARK_OS_DEB="$(basename "$ARK_OS_URL")"
    echo "Latest ARK-OS deb: $ARK_OS_DEB"
fi

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

echo "Fetching the ARK-OS deb..."
fetch_deb "$ARK_OS_DEB" "$ARK_OS_URL"

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

### Install the pinned camera userspace stack (Argus + GStreamer plugins)
# apply_binaries installs the BSP-stamp camera/multimedia debs; replace them (and add
# nvidia-l4t-gstreamer, which the BSP set lacks) with the pinned known-good set. The
# pinned debs' deps assume their own release, so repack each with the out-of-set bounds
# relaxed (nvidia-l4t-core upper cap, exact-stamp cuda/nvsci) and the in-set exact deps
# retargeted to the +ark1 version — a clean apt install instead of dpkg --force-depends,
# so on-device apt stays consistent. Hold the set so an on-device upgrade can't drag it
# back to the regressed BSP stamp.
relax_l4t_deps() {
    local in="$1" out="$2" work
    work=$(mktemp -d)
    dpkg-deb -R "$in" "$work"
    sed -i \
        -e "s/nvidia-l4t-core (<< [0-9.]*-0)/nvidia-l4t-core (<< 37.0-0)/" \
        -e "s/nvidia-l4t-cuda (= [^)]*)/nvidia-l4t-cuda/" \
        -e "s/nvidia-l4t-nvsci (= [^)]*)/nvidia-l4t-nvsci/" \
        -e "s/(= ${NV_CAMERA_STACK_VERSION})/(= ${NV_CAMERA_STACK_VERSION}+ark1)/g" \
        -e "s/^Version: .*/&+ark1/" \
        "$work/DEBIAN/control"
    dpkg-deb -b --root-owner-group "$work" "$out" >/dev/null
    rm -rf "$work"
}
echo "Installing the pinned camera userspace stack (${NV_CAMERA_STACK_VERSION}+ark1)..."
NV_CAMERA_TMP_DEBS=()
for pkg in "${NV_CAMERA_PKGS[@]}"; do
    deb=$(nv_camera_deb "$pkg")
    fetch_deb "$deb" "$(nv_camera_url "$pkg")"
    relax_l4t_deps "$DOWNLOADS_DIR/$deb" "/tmp/ark1_$deb"
    sudo mv "/tmp/ark1_$deb" "$ROOTFS_DIR/tmp/ark1_$deb"
    sudo rm -f "$ROOTFS_DIR/tmp/$deb"
    NV_CAMERA_TMP_DEBS+=("/tmp/ark1_$deb")
done
# One transaction: the set inter-depends by exact version.
sudo chroot "$ROOTFS_DIR" apt-get install -y --allow-downgrades --allow-change-held-packages \
    "${NV_CAMERA_TMP_DEBS[@]}"
sudo chroot "$ROOTFS_DIR" apt-mark hold "${NV_CAMERA_PKGS[@]}"
for pkg in "${NV_CAMERA_PKGS[@]}"; do
    v=$(sudo chroot "$ROOTFS_DIR" dpkg-query -W -f='${Version}' "$pkg")
    [ "$v" = "${NV_CAMERA_STACK_VERSION}+ark1" ] || {
        echo "ERROR: $pkg is '$v', expected ${NV_CAMERA_STACK_VERSION}+ark1." >&2; exit 1; }
done
# Assert the plugin actually loads and registers nvarguscamerasrc — file existence
# alone misses unresolvable libraries. Inspect the plugin *file*, not the element:
# element instantiation (e.g. --exists) dials nvargus-daemon/EGL, absent in a chroot.
# The registry cache is pointed at /tmp so scan state doesn't ship in the image.
sudo chroot "$ROOTFS_DIR" env GST_REGISTRY=/tmp/provision-gst-registry.bin \
    gst-inspect-1.0 /usr/lib/aarch64-linux-gnu/gstreamer-1.0/libgstnvarguscamerasrc.so \
    | grep -qw nvarguscamerasrc \
    || { echo "ERROR: nvarguscamerasrc missing or failed to load after installing the camera stack." >&2; exit 1; }
sudo rm -f "$ROOTFS_DIR/tmp/provision-gst-registry.bin"
for pkg in "${NV_CAMERA_PKGS[@]}"; do sudo rm -f "$ROOTFS_DIR/tmp/ark1_$(nv_camera_deb "$pkg")"; done

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
