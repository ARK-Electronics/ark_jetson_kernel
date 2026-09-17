# Build Host Environment

## Required host

NVIDIA's documented flash/build host for L4T R39.2.1 / JetPack 7.2.1 is **Ubuntu 22.04** (or 20.04). The Jetson sample rootfs itself is **Ubuntu 24.04**. This repo's `setup.sh` and `build.sh` pin the build tools to 22.04:

- On Ubuntu 22.04 they run natively only when release-matched kmod tools are available. R39 needs kmod 31; otherwise they use the build container.
- On any other host they re-exec themselves inside a 22.04 docker container (`ark-jetson-builder:22.04`, built from `docker/Dockerfile` on first use).
- If docker is missing on a non-22.04 host, the scripts auto-install it via `apt-get install -y docker.io` (requires sudo). On non-apt distros they hard-fail with a pointer to this document.
- If the current user isn't in the `docker` group, the wrapper falls back to `sudo docker` so the build works without forcing a logout/relogin to pick up the new group. Add yourself to the `docker` group later if you'd rather avoid the per-invocation sudo: `sudo usermod -aG docker $USER` then re-login.

The repo bind-mounts itself into the container at `/workspace` and the Crosstool-NG cross-toolchain at `/root/l4t-gcc`, so build artifacts (`staging/`, `downloads/`) and the toolchain persist on the host across container runs.

The container runs as root, so everything it writes through the bind mounts (`staging/`, `downloads/`, `~/l4t-gcc`) ends up `root`-owned on the host. The same is partially true of a native-22.04 build (`apply_binaries.sh` and friends `sudo`-write a lot of the staging rootfs), so the practical impact is the same: use `sudo rm -rf` if you want to wipe a staging directory by hand.

`flash.sh` always runs on the host — it transfers already-built artifacts to the device over USB and gains nothing from containerization.

## Initramfs and firmware tools

Every kernel build refreshes NVIDIA's production initramfs after installing the
final Image and modules. NVIDIA's updater requires `qemu-user-static` and working
AArch64 binfmt registration to run `nv-update-initrd` inside the staged ARM
rootfs, including `--fast` builds that skip the BSP prerequisite installer. The
22.04 builder includes `qemu-user-static` and `binfmt-support`. It also includes
`device-tree-compiler` (`dtc`, `fdtget`, and `fdtput`) for the optional JAJ firmware
profile. On a native Ubuntu 22.04 host, install these prerequisites:

```sh
sudo apt-get install qemu-user-static binfmt-support device-tree-compiler
sudo update-binfmts --enable qemu-aarch64
```

The container wrapper rebuilds its image when the Dockerfile content hash
changes. Rerun `build.sh` through that wrapper after updating this checkout;
a manually launched container with an older image tag does not receive the new
packages automatically. If NVIDIA's updater reports that `qemu-user-static` is
not installed, rebuild the image or install the native prerequisites before
rerunning the build. Package installation alone is insufficient when the host
kernel has no working AArch64 binfmt handler; the updater's chroot must also
execute successfully. Do not omit initrd refresh to bypass that failure.

R39 requires **kmod 31** for installed module indexes and precomputed initrd
indexes. The container builds upstream kmod 31 from a SHA-256-pinned archive into
`/opt/ark-kmod31`; it retains Jammy's kmod 29 for R36 profiles. `build.sh` selects
the release-matched tools, and the optimizer rejects a mismatched version.

## Why pin the host to 22.04

Matching NVIDIA's documented host keeps flash tools and host-side packaging consistent across developer machines and CI. Historically (JetPack 6 / L4T R36) a kmod binary-index format mismatch between Ubuntu 24.04 hosts (kmod 31) and the Jammy rootfs (kmod 29) broke `modprobe` for built-in modules on first boot — which is why the container wrapper was introduced. JetPack 7's Noble rootfs uses kmod 31, so the build explicitly selects that version while keeping the supported Ubuntu 22.04 host environment. See [NVIDIA's R39.2.1 Quick Start](https://docs.nvidia.com/jetson/archives/r39.2.1/DeveloperGuide/IN/QuickStart.html).

## CI

GitHub Actions uses an `ubuntu-22.04` runner and the same release/tool checks. A standard runner uses the build container for R39. See `.github/workflows/build.yml`. The Crosstool-NG toolchain and the multi-GB L4T BSP/rootfs/sources tarballs are both cached across runs — the toolchain keyed on its pinned filename, the tarballs on the BSP version (e.g. `R39.2.1`) — so each is re-downloaded from NVIDIA only when its pin in `versions.env` changes.

## Verifying a healthy build

After flashing, on the Jetson:

```bash
sudo modprobe -v loop; echo "exit=$?"        # expect: no output, exit=0
ls /dev/loop0                                  # expect: /dev/loop0
systemctl is-active nv-l4t-usb-device-mode    # expect: active
```

From the host PC, after plugging the USB-C cable in:

```bash
ip a | grep -A1 enx                           # expect: USB-RNDIS interface up
ssh jetson@jetson.local                       # expect: connects
```
