# Build Host Environment

## Required host

NVIDIA's documented build host for L4T R36.5.0 / JetPack 6.2.2 is **Ubuntu 22.04**, or 20.04. This repo's `setup.sh` and `build.sh` hold you to that:

- On Ubuntu 22.04 they run natively.
- On any other host they run themselves again inside a 22.04 docker container. The image is `ark-jetson-builder:22.04`, built from `docker/Dockerfile` the first time it is needed.
- If docker is missing on such a host, the scripts install it with `apt-get install -y docker.io`, which needs sudo. On a distro without apt they stop with an error that points to this page.
- If you aren't in the `docker` group, the wrapper uses `sudo docker`, so the build works without logging out and in again to pick up the group. To avoid the sudo on every run, add yourself to the group with `sudo usermod -aG docker $USER` and log in again.

The repo is bind-mounted into the container at `/workspace`, and the bootlin cross-toolchain at `/root/l4t-gcc`. The build artifacts in `staging/` and `downloads/` and the toolchain therefore stay on the host from one container run to the next.

The container runs as root, so everything it writes through the bind mounts ends up owned by `root` on the host: `staging/`, `downloads/` and `~/l4t-gcc`. A native 22.04 build does much the same, because `apply_binaries.sh` and similar scripts write a lot of the staging rootfs with `sudo`. Either way, use `sudo rm -rf` to wipe a staging directory by hand.

`flash.sh` always runs on the host. It only sends built artifacts to the device over USB, so a container would gain it nothing.

## Why 22.04 specifically — the kmod incompatibility

The immediate reason is that the `kmod` version on the host and the one on the device write incompatible binary formats.

| Where | OS | `kmod` version |
| --- | --- | --- |
| Build host (NVIDIA's documented config) | Ubuntu 22.04 | 29 |
| Build host (current Ubuntu LTS) | Ubuntu 24.04 | 31 |
| Jetson device (L4T R36.5.0 sample rootfs) | Ubuntu 22.04 | 29 |

`make modules_install` runs the **host's** `depmod` to fill `/lib/modules/$(uname -r)/` in the staged rootfs, and so does NVIDIA's `apply_binaries.sh`. `depmod` writes a text `modules.builtin` file and a set of binary index files: `modules.builtin.bin`, `modules.builtin.alias.bin` and `modules.builtin.modinfo`. `modprobe` reads those indexes at runtime.

The binary index format changed between kmod 29 and kmod 31. When a kmod-31 host writes the indexes, the kmod-29 `modprobe` on the booted Jetson cannot parse them and reports "module not found". It does so even for modules that the text `modules.builtin` lists correctly and that are built into the running kernel.

### Symptom

The first symptom was the `nv-l4t-usb-device-mode` service failing to bind the USB gadget on first boot, which took away the USB-RNDIS link on the PAB and PAB_V3:

```
$ sudo modprobe -v loop
modprobe: FATAL: Module loop not found in directory /lib/modules/5.15.148-tegra
$ echo $?
1
```

It failed even though:

- The running kernel had `CONFIG_BLK_DEV_LOOP=y`, as `zcat /proc/config.gz | grep BLK_DEV_LOOP` shows.
- `/dev/loop0` existed and `/proc/devices` listed `7 loop`.
- `/lib/modules/5.15.148-tegra/modules.builtin` contained the line `kernel/drivers/block/loop.ko`.
- All four index files, `modules.builtin{,.bin,.alias.bin,.modinfo}`, were present, with the same sizes as the copies staged on the host.

`modprobe` exits with 1, which aborts `nv-l4t-usb-device-mode-start.sh` at line 172, because the script runs under `set -e`. The composite gadget then never binds, and the host PC sees no USB device when you plug the cable in. Every other service on the device that probes a built-in module fails the same way, without a message.

### Why containerize instead of patching around it

We considered three alternatives:

1. **Patch `nv-l4t-usb-device-mode-start.sh` to `modprobe loop || true`.** This hides the one symptom, and every other `modprobe` of a built-in module on the device still fails silently.
2. **Run `depmod -a` on the device on first boot.** This rewrites the indexes in the device's own kmod-29 format. It works, but it slows the first boot and adds ordering problems at startup, all for a bug that belongs to the build host.
3. **Strip the binary indexes after `make modules_install`**, so that `modprobe` falls back to the text file. This works too, but it hides the fact that we are building on a host NVIDIA does not support.

The container fixes the root cause and matches the host NVIDIA documents. It also gives every developer machine and CI the same build, without each contributor keeping a 22.04 host or VM.

## CI

GitHub Actions has a native `ubuntu-22.04` runner, so CI builds natively and never uses the container. The workflow is `.github/workflows/build.yml`. It caches the bootlin toolchain and the ~2.7 GB of L4T BSP, rootfs and sources tarballs between runs. The toolchain's cache key is its pinned filename, and the tarballs' key is the BSP version, for example `R36.5.0`. Each is downloaded from NVIDIA again only when its pin in `versions.env` changes.

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

If `modprobe -v loop` returns "Module loop not found in directory ..." while the text `modules.builtin` lists `loop.ko`, you are looking at the kmod format mismatch described above. The build was almost certainly made on a host other than 22.04 without the container wrapper.
