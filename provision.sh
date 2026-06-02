#!/bin/bash
#
# Rootfs provisioning script — runs during staging when --provision is passed.
#
# Available environment:
#   ROOTFS_DIR  — absolute path to the rootfs (staging/{TARGET}/Linux_for_Tegra/rootfs)
#   TARGET      — product name (PAB, JAJ, PAB_V3)
#
# /proc, /sys, /dev are bind-mounted into the rootfs and DNS is configured.
# /run is NOT bind-mounted, so [ -d /run/systemd/system ] is false inside the
# chroot — the ARK-OS postinst's runtime-only operations are skipped here and the
# device finishes provisioning on first boot. The default user (jetson) is created
# before this runs, which the ark-os-jetson package's postinst relies on.
#
# Use `sudo chroot "$ROOTFS_DIR" <command>` to run commands inside the rootfs.

set -e

# Wall-clock start; reported at the end as "provisioning complete after Xm Ys".
PROVISION_START=$(date +%s)

# Pinned versions — bump manually when releasing. MAVSDK_VERSION and
# JETSON_STATS_VERSION must match the canonical pins in ARK-OS
# packaging/versions.env. This repo has no ARK-OS checkout at build time, so the
# values are duplicated here rather than sourced — keep them in sync.
#
# ARK_OS_VERSION currently points at a CI-artifact build — untagged ARK-OS CI
# builds are versioned 0.0.0-<sha8> and the matching deb is cached in downloads/.
# Bump to the released version (e.g. 1.0.0) once ARK-OS PR #68 merges and a
# GitHub release exists.
ARK_OS_VERSION="0.0.0-4e1fc680"
MAVSDK_VERSION="3.17.1"
JETSON_STATS_VERSION="4.3.2"   # jtop daemon + client, installed system-wide via pip

ARK_OS_DEB="ark-os-jetson_${ARK_OS_VERSION}_arm64.deb"
# Upstream publishes no ubuntu22.04_arm64 build; debian12_arm64 is what ARK-OS has
# historically used on the Jammy rootfs (glibc-compatible in practice).
MAVSDK_DEB="libmavsdk-dev_${MAVSDK_VERSION}_debian12_arm64.deb"

ARK_OS_URL="https://github.com/ARK-Electronics/ARK-OS/releases/download/v${ARK_OS_VERSION}/${ARK_OS_DEB}"
MAVSDK_URL="https://github.com/mavlink/MAVSDK/releases/download/v${MAVSDK_VERSION}/${MAVSDK_DEB}"

# Prefer a deb already sitting in the repo's downloads/ cache, falling back to the
# release download. This lets a locally-supplied deb — a CI artifact or a
# pre-release build with no published GitHub release yet — be exercised through the
# full chroot install: drop the file in downloads/ and set the matching *_VERSION
# above so the filename lines up. The match is by exact filename (which embeds the
# version), so a version bump with nothing cached falls through to the release URL.
# provision.sh lives at the repo root, so derive downloads/ from this script's
# location (works under build.sh and when run standalone); DOWNLOADS_DIR may be set
# in the environment to override.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOWNLOADS_DIR="${DOWNLOADS_DIR:-$SCRIPT_DIR/downloads}"

# Place a deb into the rootfs's /tmp from the cache if present, else download it.
# A failed download (e.g. a release that doesn't exist yet and no cached file)
# aborts provisioning with an explicit error rather than silently shipping no
# ARK-OS. wget -nv keeps the log quiet but still surfaces HTTP errors (-q hid the
# 404 that previously made this fail silently).
fetch_deb() {
    local deb="$1" url="$2"
    if [ -f "$DOWNLOADS_DIR/$deb" ]; then
        echo "Using cached $deb from $DOWNLOADS_DIR"
        sudo cp "$DOWNLOADS_DIR/$deb" "$ROOTFS_DIR/tmp/$deb"
    else
        echo "Downloading $deb from $url"
        if ! sudo wget -nv -O "$ROOTFS_DIR/tmp/$deb" "$url"; then
            sudo rm -f "$ROOTFS_DIR/tmp/$deb"
            echo "ERROR: could not fetch $deb (not cached in $DOWNLOADS_DIR, and the" >&2
            echo "       download from $url failed — the release may not exist yet)." >&2
            echo "       Fix: drop the deb into $DOWNLOADS_DIR and set the matching" >&2
            echo "       *_VERSION at the top of provision.sh so the filename lines up." >&2
            exit 1
        fi
    fi
}

echo "Fetching MAVSDK and ARK-OS debs..."
fetch_deb "$MAVSDK_DEB" "$MAVSDK_URL"
fetch_deb "$ARK_OS_DEB" "$ARK_OS_URL"

# Block all service (re)starts inside the chroot. There is no running init here,
# so a daemon start attempted by a dependency's maintainer script would fail or
# hang. policy-rc.d returning 101 makes "no service actions in the chroot" a hard
# guarantee, independent of each package's script hygiene (ARK-OS's own postinst
# self-gates on /run/systemd/system, but its dependencies — nginx, avahi, bluez,
# network-manager … — do not). The trap removes the shim on any exit so the
# flashed image boots normally.
printf '#!/bin/sh\nexit 101\n' | sudo tee "$ROOTFS_DIR/usr/sbin/policy-rc.d" >/dev/null
sudo chmod 0755 "$ROOTFS_DIR/usr/sbin/policy-rc.d"

# NVIDIA's apt source ships a templated `<SOC>` repo entry that is resolved only
# on-device at first boot; in the chroot it stays literal and makes `apt-get
# update` exit non-zero ("does not have a Release file"), which under set -e would
# abort provisioning. ARK-OS needs none of the NVIDIA repos — its dependencies
# come from the Ubuntu ports archive plus the MAVSDK deb — so move the source
# aside while provisioning and restore it on exit, leaving the shipped image's
# apt config untouched so first boot resolves `<SOC>` as usual.
NV_APT_SRC="$ROOTFS_DIR/etc/apt/sources.list.d/nvidia-l4t-apt-source.list"
[ -f "$NV_APT_SRC" ] && sudo mv "$NV_APT_SRC" "$NV_APT_SRC.provision-disabled"

# Remove the policy-rc.d shim and restore the NVIDIA apt source on any exit
# (success or failure) so the flashed image boots and updates normally.
cleanup_provision() {
    sudo rm -f "$ROOTFS_DIR/usr/sbin/policy-rc.d"
    [ -f "$NV_APT_SRC.provision-disabled" ] && \
        sudo mv "$NV_APT_SRC.provision-disabled" "$NV_APT_SRC"
}
trap cleanup_provision EXIT

# ARK-OS ships no conffiles, so chroot/non-interactive installs never stall on
# conffile prompts. `apt-get install ./file.deb` installs the local deb and pulls
# its dependencies from the repos in one step — and, unlike `dpkg -i`, exits
# non-zero on a real failure instead of leaving the package unconfigured. MAVSDK
# installs first because ark-os Depends: libmavsdk-dev, which is in no repo, so it
# must already be installed before ark-os's dependencies can resolve.
echo "Installing MAVSDK (ark-os depends on libmavsdk-dev)..."
sudo chroot "$ROOTFS_DIR" apt-get update
sudo chroot "$ROOTFS_DIR" apt-get install -y "/tmp/$MAVSDK_DEB"

echo "Installing ark-os-jetson..."
sudo chroot "$ROOTFS_DIR" apt-get install -y "/tmp/$ARK_OS_DEB"

# Belt-and-suspenders: confirm both packages ended up fully configured. apt-get
# already aborts under set -e on a real failure, but this also catches a package
# left half-configured and documents the post-condition the image relies on.
echo "Verifying packages are installed..."
for pkg in libmavsdk-dev ark-os-jetson; do
    status=$(sudo chroot "$ROOTFS_DIR" dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null || true)
    if [ "$status" != "install ok installed" ]; then
        echo "ERROR: $pkg is not installed (dpkg status: '${status:-not present}')." >&2
        echo "       Aborting provisioning to avoid shipping an image without ARK-OS." >&2
        exit 1
    fi
done

# jetson-stats provides the jtop daemon (jtop.service) and its Python client,
# installed system-wide via pip. ARK-OS system-manager imports this same install
# through the bundled venv's system-site-packages, so the client and daemon are
# always the same version. Run as root, setup.py installs the unit to
# /etc/systemd/system/jtop.service; its own systemctl enable/start are no-ops
# while systemd isn't running in the chroot, so we enable it for first boot below.
echo "Installing jetson-stats (jtop) system-wide..."
sudo chroot "$ROOTFS_DIR" apt-get install -y python3-pip
sudo chroot "$ROOTFS_DIR" pip3 install "jetson-stats==${JETSON_STATS_VERSION}"
sudo chroot "$ROOTFS_DIR" python3 -c "import jtop"                  # sanity: client installed
sudo chroot "$ROOTFS_DIR" test -f /etc/systemd/system/jtop.service  # sanity: setup.py placed the unit
# Enable jtop.service for first boot — offline equivalent of `systemctl enable`
# (unit is WantedBy=multi-user.target), reliable in the chroot where systemctl
# cannot reach a running manager.
sudo chroot "$ROOTFS_DIR" mkdir -p /etc/systemd/system/multi-user.target.wants
sudo chroot "$ROOTFS_DIR" ln -sf /etc/systemd/system/jtop.service \
    /etc/systemd/system/multi-user.target.wants/jtop.service

sudo rm "$ROOTFS_DIR/tmp/$MAVSDK_DEB" "$ROOTFS_DIR/tmp/$ARK_OS_DEB"

PROVISION_ELAPSED=$(( $(date +%s) - PROVISION_START ))
printf 'ARK-OS provisioning complete after %dm %02ds.\n' \
    $((PROVISION_ELAPSED / 60)) $((PROVISION_ELAPSED % 60))
