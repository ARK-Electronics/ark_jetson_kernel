# Build Host Environment

## Required host

The build host for L4T R39.2.1 / JetPack 7.2.1 is **Ubuntu 24.04**, the release the Jetson rootfs is built on. NVIDIA supports 24.04 and 22.04 hosts. This repo's `setup.sh` and `build.sh` hold you to 24.04:

- On Ubuntu 24.04 they run natively.
- On any other host they run themselves again inside a 24.04 docker container. The image is `ark-jetson-builder:24.04`, built from `docker/Dockerfile` the first time it is needed.
- If docker is missing on such a host, the scripts install it with `apt-get install -y docker.io`, which needs sudo. On a distro without apt they stop with an error that points to this page.
- If you aren't in the `docker` group, the wrapper uses `sudo docker`, so the build works without logging out and in again to pick up the group. To avoid the sudo on every run, add yourself to the group with `sudo usermod -aG docker $USER` and log in again.

The repo is bind-mounted into the container at `/workspace`, and the cross-toolchain at `/root/l4t-gcc`. The build artifacts in `staging/` and `downloads/` and the toolchain therefore stay on the host from one container run to the next.

The container runs as root, so everything it writes through the bind mounts ends up owned by `root` on the host: `staging/`, `downloads/` and `~/l4t-gcc`. A native 24.04 build does much the same, because `apply_binaries.sh` and similar scripts write a lot of the staging rootfs with `sudo`. Either way, use `sudo rm -rf` to wipe a staging directory by hand.

`flash.sh` always runs on the host. It only sends built artifacts to the device over USB, so a container would gain it nothing.

## Why 24.04 specifically — kmod

`make modules_install` runs the **host's** `depmod` to fill `/lib/modules/$(uname -r)/` in the staged rootfs, and so does NVIDIA's `apply_binaries.sh`. `depmod` writes binary index files that `modprobe` on the device reads: `modules.builtin.bin`, `modules.builtin.alias.bin` and `modules.builtin.modinfo`. Their format differs between kmod 29 and kmod 31.

| Where | OS | `kmod` version |
| --- | --- | --- |
| Build host | Ubuntu 24.04 | 31 |
| Jetson device (L4T R39.2.1 sample rootfs) | Ubuntu 24.04 | 31 |
| An Ubuntu 22.04 host | Ubuntu 22.04 | 29 |

JetPack 6 hit the mismatch from the other side, when a kmod 31 host wrote the indexes for a kmod 29 rootfs. `modprobe` then reported built-in modules as not found, `nv-l4t-usb-device-mode` failed on `modprobe loop`, and the USB gadget never bound.

Matching the host to the rootfs fixes the cause. Patching the failing service, or running `depmod` again on first boot, would only cover it.

## CI

GitHub Actions has a native `ubuntu-24.04` runner, so CI builds natively and never uses the container. The workflow is `.github/workflows/build.yml`. It caches the cross-toolchain and the ~2.7 GB of L4T BSP, rootfs and sources tarballs between runs. The toolchain's cache key is its pinned filename, and the tarballs' key is the BSP version, for example `R39.2.1`. Each is downloaded from NVIDIA again only when its pin in `versions.env` changes.

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

If `modprobe -v loop` returns "Module loop not found in directory ..." while the text `modules.builtin` lists `loop.ko`, you are looking at the kmod format mismatch described above. The build was almost certainly made on a host other than 24.04 without the container wrapper.
