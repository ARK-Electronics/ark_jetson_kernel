# Build Host Environment

## Required host

NVIDIA's documented flash/build host for L4T R39.2 / JetPack 7.2 is **Ubuntu 22.04** (or 20.04). The Jetson sample rootfs itself is **Ubuntu 24.04**. This repo's `setup.sh` and `build.sh` pin the build tools to 22.04:

- On Ubuntu 22.04 they run natively.
- On any other host they re-exec themselves inside a 22.04 docker container (`ark-jetson-builder:22.04`, built from `docker/Dockerfile` on first use).
- If docker is missing on a non-22.04 host, the scripts auto-install it via `apt-get install -y docker.io` (requires sudo). On non-apt distros they hard-fail with a pointer to this document.
- If the current user isn't in the `docker` group, the wrapper falls back to `sudo docker` so the build works without forcing a logout/relogin to pick up the new group. Add yourself to the `docker` group later if you'd rather avoid the per-invocation sudo: `sudo usermod -aG docker $USER` then re-login.

The repo bind-mounts itself into the container at `/workspace` and the Crosstool-NG cross-toolchain at `/root/l4t-gcc`, so build artifacts (`staging/`, `downloads/`) and the toolchain persist on the host across container runs.

The container runs as root, so everything it writes through the bind mounts (`staging/`, `downloads/`, `~/l4t-gcc`) ends up `root`-owned on the host. The same is partially true of a native-22.04 build (`apply_binaries.sh` and friends `sudo`-write a lot of the staging rootfs), so the practical impact is the same: use `sudo rm -rf` if you want to wipe a staging directory by hand.

`flash.sh` always runs on the host — it transfers already-built artifacts to the device over USB and gains nothing from containerization.

## Why pin the host to 22.04

Matching NVIDIA's documented host keeps flash tools and host-side packaging consistent across developer machines and CI. Historically (JetPack 6 / L4T R36) a kmod binary-index format mismatch between Ubuntu 24.04 hosts (kmod 31) and the Jammy rootfs (kmod 29) broke `modprobe` for built-in modules on first boot — which is why the container wrapper was introduced. JetPack 7's rootfs is Noble (kmod 31), so that particular mismatch is less sharp, but we still pin to NVIDIA's 22.04 host recommendation for reproducibility.

## CI

GitHub Actions has a native `ubuntu-22.04` runner, so CI runs the build natively without involving the container at all. See `.github/workflows/build.yml`. The Crosstool-NG toolchain and the multi-GB L4T BSP/rootfs/sources tarballs are both cached across runs — the toolchain keyed on its pinned filename, the tarballs on the BSP version (e.g. `R39.2.0`) — so each is re-downloaded from NVIDIA only when its pin in `versions.env` changes.

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
