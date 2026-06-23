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

# Package name carries the rootfs codename; the Jetson rootfs is Ubuntu 22.04 = jammy.
ARK_OS_PKG="ark-os-jetson-jammy"
ARK_OS_DEB="${ARK_OS_PKG}_${ARK_OS_VERSION}_arm64.deb"

ARK_OS_URL="https://github.com/ARK-Electronics/ARK-OS/releases/download/v${ARK_OS_VERSION}/${ARK_OS_DEB}"

# Prefer a deb already in downloads/ over downloading, so a locally-supplied deb
# (CI artifact or unreleased build) can be exercised: drop it in downloads/ and set
# the matching *_VERSION so the filename lines up. Override the dir via DOWNLOADS_DIR.
DOWNLOADS_DIR="${DOWNLOADS_DIR:-$SCRIPT_DIR/downloads}"

# Use the pinned ARK_OS_VERSION when its deb is cached locally or published on
# GitHub; otherwise fall back loudly to the newest published (pre)release so an
# unreleased pin (e.g. the 0.0.0 placeholder) doesn't block the build. True GitHub
# drafts are invisible to both paths.
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

# Stage a deb into the rootfs /tmp, caching under downloads/ first (atomic
# .partial → final) so rebuilds reuse it. A failed download aborts rather than
# shipping no ARK-OS. wget -nv still surfaces HTTP errors; -o /dev/stderr stops it
# dropping a wget-log file when it has no TTY (as in the build container).
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

# apt-get install ./file.deb installs the local deb and pulls its deps in one step,
# and (unlike dpkg -i) fails loud on error. MAVSDK is not installed separately:
# ark-os bundles its own (ARK-OS#75). A pre-bundling ark-os deb fails here with an
# unresolvable libmavsdk-dev Depends — fix by bumping ARK_OS_VERSION.
echo "Installing ${ARK_OS_PKG}..."
sudo chroot "$ROOTFS_DIR" apt-get update
sudo chroot "$ROOTFS_DIR" apt-get install -y "/tmp/$ARK_OS_DEB"

# Confirm the package is fully configured — catches a half-configured package
# and documents the post-condition the image relies on.
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

# jetson-stats stays a *system* install (not a venv, unlike ARK-OS app code): jtop's
# daemon and its client library must be one version, and ARK-OS's venv sees the client
# via --system-site-packages. setup.py installs jtop.service when run as root; we
# enable it for first boot below since systemctl can't reach a manager in the chroot.
echo "Installing jetson-stats (jtop) system-wide..."
sudo chroot "$ROOTFS_DIR" apt-get install -y python3-pip
# Ubuntu >= 24.04 (JetPack 7) marks system Python externally-managed (PEP 668), so a
# system pip install needs --break-system-packages. Gate on the marker the rootfs
# ships: present (noble+, new pip) → flag; absent (jammy, pip 22 without it) → plain.
PIP_FLAGS=()
if sudo chroot "$ROOTFS_DIR" sh -c 'ls /usr/lib/python3*/EXTERNALLY-MANAGED >/dev/null 2>&1'; then
    PIP_FLAGS=(--break-system-packages)
fi
sudo chroot "$ROOTFS_DIR" pip3 install "${PIP_FLAGS[@]}" "jetson-stats==${JETSON_STATS_VERSION}"
sudo chroot "$ROOTFS_DIR" python3 -c "import jtop"                  # sanity: client installed
sudo chroot "$ROOTFS_DIR" test -f /etc/systemd/system/jtop.service  # sanity: setup.py placed the unit
# Enable jtop.service for first boot (offline `systemctl enable`; it's
# WantedBy=multi-user.target), since systemctl can't reach a manager in the chroot.
sudo chroot "$ROOTFS_DIR" mkdir -p /etc/systemd/system/multi-user.target.wants
sudo chroot "$ROOTFS_DIR" ln -sf /etc/systemd/system/jtop.service \
    /etc/systemd/system/multi-user.target.wants/jtop.service

# spidev is needed by the manufacturing IMU test (ark_scripts JAJ bundle), which
# runs under the system python. The test fixture gives the Jetson no internet, so
# the module must ship in the image; the apt package is a prebuilt binary (PyPI
# only has the sdist), keeping compilers out of the rootfs.
echo "Installing python3-spidev (manufacturing IMU test)..."
sudo chroot "$ROOTFS_DIR" apt-get install -y python3-spidev
sudo chroot "$ROOTFS_DIR" python3 -c "import spidev"  # sanity: importable system-wide

# ── Your packages ───────────────────────────────────────────────────────────
# Preinstall anything else you want baked into the image here; it lands in the
# rootfs via the chroot. apt's lists are already updated and PIP_FLAGS is set for
# the rootfs's pip. For example:
#   sudo chroot "$ROOTFS_DIR" apt-get install -y vim tmux
#   sudo chroot "$ROOTFS_DIR" pip3 install "${PIP_FLAGS[@]}" some-package

sudo rm "$ROOTFS_DIR/tmp/$ARK_OS_DEB"

PROVISION_ELAPSED=$(( $(date +%s) - PROVISION_START ))
printf 'ARK-OS provisioning complete after %dm %02ds.\n' \
    $((PROVISION_ELAPSED / 60)) $((PROVISION_ELAPSED % 60))
