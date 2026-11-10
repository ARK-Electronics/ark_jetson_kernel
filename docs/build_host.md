# Build Host Environment

## Required host

The build host for L4T R39.2.1 / JetPack 7.2.1 is **Ubuntu 24.04**, the release the Jetson rootfs is built on. NVIDIA supports 24.04 and 22.04 hosts. This repo's `setup.sh` and `build.sh` enforce 24.04:

- On Ubuntu 24.04 they run natively.
- On any other host they re-exec themselves inside a 24.04 docker container (`ark-jetson-builder:24.04`, built from `docker/Dockerfile` on first use).
- If docker is missing on a non-24.04 host, the scripts auto-install it via `apt-get install -y docker.io` (requires sudo). On non-apt distros they hard-fail with a pointer to this document.
- If the current user isn't in the `docker` group, the wrapper falls back to `sudo docker` so the build works without forcing a logout/relogin to pick up the new group. Add yourself to the `docker` group later if you'd rather avoid the per-invocation sudo: `sudo usermod -aG docker $USER` then re-login.

The repo bind-mounts itself into the container at `/workspace` and the cross-toolchain at `/root/l4t-gcc`, so build artifacts (`staging/`, `downloads/`) and the toolchain persist on the host across container runs.

The container runs as root, so everything it writes through the bind mounts ends up owned by `root` on the host: `staging/`, `downloads/` and `~/l4t-gcc`. A native 24.04 build does much the same, because `apply_binaries.sh` and similar scripts write a lot of the staging rootfs with `sudo`. Either way, use `sudo rm -rf` to wipe a staging directory by hand.

`flash.sh` always runs on the host. It only sends built artifacts to the device over USB, so a container would gain it nothing.

## Why 24.04 specifically — kmod

`make modules_install` (and NVIDIA's `apply_binaries.sh`) runs the **host's** `depmod` to populate `/lib/modules/$(uname -r)/` in the staged rootfs. `depmod` writes binary index files (`modules.builtin.bin`, `modules.builtin.alias.bin`, `modules.builtin.modinfo`) that `modprobe` on the device reads, and their format differs between kmod 29 and kmod 31.

| Where | OS | `kmod` version |
| --- | --- | --- |
| Build host | Ubuntu 24.04 | 31 |
| Jetson device (L4T R39.2.1 sample rootfs) | Ubuntu 24.04 | 31 |
| An Ubuntu 22.04 host | Ubuntu 22.04 | 29 |

JetPack 6 hit the mismatch from the other side, a kmod 31 host writing indexes for a kmod 29 rootfs. `modprobe` then reported built-in modules as not found, `nv-l4t-usb-device-mode` failed on `modprobe loop`, and the USB gadget never bound.

Matching the host to the rootfs fixes the cause, where patching the failing service or re-running `depmod` on first boot would only cover it.

## CI

GitHub Actions has a native `ubuntu-24.04` runner, so CI runs the build natively without involving the container at all. See `.github/workflows/build.yml`. The cross-toolchain and the ~2.7 GB of L4T BSP/rootfs/sources tarballs are both cached across runs — the toolchain keyed on its pinned filename, the tarballs on the BSP version (e.g. `R36.5.0`) — so each is re-downloaded from NVIDIA only when its pin in `versions.env` changes.

## Verifying a healthy build

After you flash, run these on the Jetson:

```bash
sudo modprobe -v loop; echo "exit=$?"        # expect: no output, exit=0
ls /dev/loop0                                  # expect: /dev/loop0
systemctl is-active nv-l4t-usb-device-mode    # expect: active
```

Then plug the USB-C cable in and run these on the host PC:

```bash
ip a | grep -A1 enx                           # expect: USB-RNDIS interface up
ssh jetson@jetson.local                       # expect: connects
```

If `modprobe -v loop` returns "Module loop not found in directory ..." while the text `modules.builtin` lists `loop.ko`, the indexes were written by a mismatched kmod: the build ran on a non-24.04 host without the container wrapper.
