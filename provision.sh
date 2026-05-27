#!/bin/bash
#
# Rootfs provisioning script — runs during staging when --provision is passed.
#
# Available environment:
#   ROOTFS_DIR  — absolute path to the rootfs (staging/{TARGET}/Linux_for_Tegra/rootfs)
#   TARGET      — product name (PAB, JAJ, PAB_V3)
#
# /proc, /sys, /dev are bind-mounted into the rootfs and DNS is configured.
# Use `sudo chroot "$ROOTFS_DIR" <command>` to run commands inside the rootfs.

set -e

# sudo chroot "$ROOTFS_DIR" apt-get update
# sudo chroot "$ROOTFS_DIR" apt-get install -y vim htop

# sudo cp some-package.deb "$ROOTFS_DIR/tmp/"
# sudo chroot "$ROOTFS_DIR" dpkg -i /tmp/some-package.deb
# sudo rm "$ROOTFS_DIR/tmp/some-package.deb"
