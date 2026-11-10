# Build Host Environment

## Required host

The build runs on **Ubuntu 22.04** with **kmod 31**. This repo's `setup.sh` and `build.sh` enforce that:

- On Ubuntu 22.04 with kmod 31 installed they run natively. Stock 22.04 ships kmod 29, so in practice they use the container.
- On any other host they re-exec themselves inside a 22.04 docker container (`ark-jetson-builder:22.04`, built from `docker/Dockerfile` on first use), which builds kmod 31 into `/opt/ark-kmod31`.
- If docker is missing on a non-22.04 host, the scripts auto-install it via `apt-get install -y docker.io` (requires sudo). On non-apt distros they hard-fail with a pointer to this document.
- If the current user isn't in the `docker` group, the wrapper falls back to `sudo docker` so the build works without forcing a logout/relogin to pick up the new group. Add yourself to the `docker` group later if you'd rather avoid the per-invocation sudo: `sudo usermod -aG docker $USER` then re-login.

The repo bind-mounts itself into the container at `/workspace` and the cross-toolchain at `/root/l4t-gcc`, so build artifacts (`staging/`, `downloads/`) and the toolchain persist on the host across container runs.

The container runs as root, so everything it writes through the bind mounts ends up owned by `root` on the host: `staging/`, `downloads/` and `~/l4t-gcc`. A native 22.04 build does much the same, because `apply_binaries.sh` and similar scripts write a lot of the staging rootfs with `sudo`. Either way, use `sudo rm -rf` to wipe a staging directory by hand.

`flash.sh` always runs on the host. It only sends built artifacts to the device over USB, so a container would gain it nothing.

## Why kmod 31

`make modules_install` runs the **host's** `depmod` to write the binary module indexes under `/lib/modules/<release>/` in the staged rootfs, and `modprobe` on the device reads them. The two must agree on the index format:

| Where | OS | `kmod` version |
| --- | --- | --- |
| Build container base | Ubuntu 22.04 | 29 |
| Build container, `/opt/ark-kmod31` | built from source | 31 |
| Jetson device (L4T R39.2.1 sample rootfs) | Ubuntu 24.04 | 31 |

On JetPack 6 the mismatch ran the other way (kmod 31 host, kmod 29 device) and broke `modprobe` for built-in modules: `nv-l4t-usb-device-mode` could not load `loop`, so the USB gadget never bound. `build.sh` refuses to run with any other `depmod`.

## CI

GitHub Actions runs on `ubuntu-22.04`, which has kmod 29, so CI builds through the container like any other host. See `.github/workflows/build.yml`. The cross-toolchain and the ~2.7 GB of L4T BSP/rootfs/sources tarballs are both cached across runs — the toolchain keyed on its pinned filename, the tarballs on the BSP version (e.g. `R36.5.0`) — so each is re-downloaded from NVIDIA only when its pin in `versions.env` changes.

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
