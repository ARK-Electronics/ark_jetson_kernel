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

# Pinned versions — bump manually when releasing. MAVSDK_VERSION must match the
# pin in ARK-OS packaging/build.sh.
ARK_OS_VERSION="1.0.0"
MAVSDK_VERSION="3.17.1"
JETSON_STATS_VERSION="4.3.2"   # jtop daemon + client, installed system-wide via pip

ARK_OS_DEB="ark-os-jetson_${ARK_OS_VERSION}_arm64.deb"
# Upstream publishes no ubuntu22.04_arm64 build; debian12_arm64 is what ARK-OS has
# historically used on the Jammy rootfs (glibc-compatible in practice).
MAVSDK_DEB="libmavsdk-dev_${MAVSDK_VERSION}_debian12_arm64.deb"

ARK_OS_URL="https://github.com/ARK-Electronics/ARK-OS/releases/download/v${ARK_OS_VERSION}/${ARK_OS_DEB}"
MAVSDK_URL="https://github.com/mavlink/MAVSDK/releases/download/v${MAVSDK_VERSION}/${MAVSDK_DEB}"

echo "Downloading MAVSDK and ARK-OS debs..."
sudo wget -q -O "$ROOTFS_DIR/tmp/$MAVSDK_DEB" "$MAVSDK_URL"
sudo wget -q -O "$ROOTFS_DIR/tmp/$ARK_OS_DEB" "$ARK_OS_URL"

# Block all service (re)starts inside the chroot. There is no running init here,
# so a daemon start attempted by a dependency's maintainer script would fail or
# hang. policy-rc.d returning 101 makes "no service actions in the chroot" a hard
# guarantee, independent of each package's script hygiene (ARK-OS's own postinst
# self-gates on /run/systemd/system, but its dependencies — nginx, avahi, bluez,
# network-manager … — do not). The trap removes the shim on any exit so the
# flashed image boots normally.
printf '#!/bin/sh\nexit 101\n' | sudo tee "$ROOTFS_DIR/usr/sbin/policy-rc.d" >/dev/null
sudo chmod 0755 "$ROOTFS_DIR/usr/sbin/policy-rc.d"
trap 'sudo rm -f "$ROOTFS_DIR/usr/sbin/policy-rc.d"' EXIT

# ARK-OS ships no conffiles, so chroot/non-interactive installs never stall on
# conffile prompts. MAVSDK installs first because ark-os Depends: libmavsdk-dev,
# and apt-get install -f can't resolve it from any repo (it's not in one).
echo "Installing MAVSDK (ark-os depends on libmavsdk-dev)..."
sudo chroot "$ROOTFS_DIR" apt-get update
sudo chroot "$ROOTFS_DIR" dpkg -i "/tmp/$MAVSDK_DEB" || true
sudo chroot "$ROOTFS_DIR" apt-get install -f -y

echo "Installing ark-os-jetson..."
sudo chroot "$ROOTFS_DIR" dpkg -i "/tmp/$ARK_OS_DEB" || true
sudo chroot "$ROOTFS_DIR" apt-get install -f -y

# Fail loudly if either package is not fully configured. The `dpkg -i ... || true`
# above swallows dpkg's exit code (intended path: unmet deps, then resolved by
# `apt-get install -f`), so without this check a genuinely failed install — or an
# `apt-get -f` that "fixed" breakage by *removing* ark-os — would let the image
# build report success and ship with no ARK-OS.
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

echo "ARK-OS provisioning complete."
