# Build Host Environment

## Required host

NVIDIA's documented build host for L4T R36.4.4 / JetPack 6.2.1 is **Ubuntu 22.04** (or 20.04). This repo's `setup.sh` and `build.sh` enforce that:

- On Ubuntu 22.04 they run natively.
- On any other host they re-exec themselves inside a 22.04 docker container (`ark-jetson-builder:22.04`, built from `docker/Dockerfile` on first use).
- If docker is missing on a non-22.04 host, the scripts auto-install it via `apt-get install -y docker.io` (requires sudo). On non-apt distros they hard-fail with a pointer to this document.
- If the current user isn't in the `docker` group, the wrapper falls back to `sudo docker` so the build works without forcing a logout/relogin to pick up the new group. Add yourself to the `docker` group later if you'd rather avoid the per-invocation sudo: `sudo usermod -aG docker $USER` then re-login.

The repo bind-mounts itself into the container at `/workspace` and the bootlin cross-toolchain at `/root/l4t-gcc`, so build artifacts (`staging/`, `downloads/`) and the toolchain persist on the host across container runs.

The container runs as root, so everything it writes through the bind mounts (`staging/`, `downloads/`, `~/l4t-gcc`) ends up `root`-owned on the host. The same is partially true of a native-22.04 build (`apply_binaries.sh` and friends `sudo`-write a lot of the staging rootfs), so the practical impact is the same: use `sudo rm -rf` if you want to wipe a staging directory by hand.

`flash.sh` always runs on the host — it transfers already-built artifacts to the device over USB and gains nothing from containerization.

## Why 22.04 specifically — the kmod incompatibility

The proximate reason is a binary-format incompatibility between `kmod` versions on the host and on the device.

| Where | OS | `kmod` version |
| --- | --- | --- |
| Build host (NVIDIA's documented config) | Ubuntu 22.04 | 29 |
| Build host (current Ubuntu LTS) | Ubuntu 24.04 | 31 |
| Jetson device (L4T R36.4.4 sample rootfs) | Ubuntu 22.04 | 29 |

`make modules_install` (and NVIDIA's `apply_binaries.sh`) runs the **host's** `depmod` to populate `/lib/modules/$(uname -r)/` in the staged rootfs. `depmod` writes both a text `modules.builtin` file and a set of binary index files (`modules.builtin.bin`, `modules.builtin.alias.bin`, `modules.builtin.modinfo`) that `modprobe` consults at runtime.

Between kmod 29 and kmod 31 the binary-index format changed. When a kmod-31 host writes those indexes, the kmod-29 `modprobe` on the booted Jetson cannot parse them and falls through to "module not found" — even for modules that are correctly listed in the text `modules.builtin` and built into the running kernel.

### Symptom

The first symptom we hit was the `nv-l4t-usb-device-mode` service failing to bind the USB gadget on first boot, killing USB-RNDIS reachability on the PAB and PAB_V3:

```
$ sudo modprobe -v loop
modprobe: FATAL: Module loop not found in directory /lib/modules/5.15.148-tegra
$ echo $?
1
```

Despite:

- `CONFIG_BLK_DEV_LOOP=y` in the running kernel (`zcat /proc/config.gz | grep BLK_DEV_LOOP`).
- `/dev/loop0` existing and `/proc/devices` listing `7 loop`.
- `/lib/modules/5.15.148-tegra/modules.builtin` containing the line `kernel/drivers/block/loop.ko`.
- All four binary index files (`modules.builtin{,.bin,.alias.bin,.modinfo}`) present with sizes matching the host-staged copy.

The exit-1 from `modprobe` aborts `nv-l4t-usb-device-mode-start.sh` at line 172 (it runs under `set -e`), so the composite gadget never binds and the host PC sees no USB device when the cable is plugged in. The same failure mode silently affects every other service that probes a built-in module on the device.

### Why containerize instead of patching around it

Three alternatives we considered:

1. **Patch `nv-l4t-usb-device-mode-start.sh` to `modprobe loop || true`.** Masks this one symptom; every other built-in `modprobe` call on the device still fails silently.
2. **Run `depmod -a` on the device on first boot.** Rewrites the indexes in the device's native kmod-29 format. Works, but adds boot latency and on-device startup ordering complexity for what is fundamentally a build-host bug.
3. **Strip the binary indexes after `make modules_install`** so `modprobe` falls back to text. Effective, but a workaround masking that we're building on an unsupported host.

Containerization addresses the root cause and matches NVIDIA's documented support. It also keeps the build reproducible across developer machines and CI without each contributor having to maintain a 22.04 host or VM.

## CI

GitHub Actions has a native `ubuntu-22.04` runner, so CI runs the build natively without involving the container at all. See `.github/workflows/build.yml`. The bootlin toolchain is cached across runs keyed on `versions.env`'s hash; the BSP/rootfs/sources tarballs are re-downloaded each run (caching ~5GB across runs is not worth the Actions cache churn).

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

If `modprobe -v loop` returns "Module loop not found in directory ..." while the text `modules.builtin` lists `loop.ko`, you are looking at the kmod-format mismatch described above and the build was almost certainly produced on a non-22.04 host without the container wrapper.
