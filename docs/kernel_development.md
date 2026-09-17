# Kernel & Device Tree Development

`build.sh` automates everything below. These notes are for manual builds, for adding out-of-tree modules, and for understanding how the device tree is assembled.

## Manual kernel build

The steps below build and install the kernel, modules, and DTBs by hand. Adjust the `PAB` path for your target.

Set up the cross-compile environment (paths match JetPack 7.2.1 / L4T R39.2.1):
```
export CROSS_COMPILE=$HOME/l4t-gcc/x-tools/aarch64-none-linux-gnu/bin/aarch64-none-linux-gnu-
export KERNEL_HEADERS=$PWD/staging/PAB/Linux_for_Tegra/source/kernel/kernel-noble
export kernel_name=noble
export INSTALL_MOD_PATH=$PWD/staging/PAB/Linux_for_Tegra/rootfs/
cd staging/PAB/Linux_for_Tegra/source
make -C kernel && make modules && make dtbs
```
Install the kernel with its in-tree modules and DTBs, then the out-of-tree modules (the NVIDIA display driver and other OOT drivers that `make modules` builds):
```
sudo -E make install -C kernel
sudo -E make modules_install
```
Copy the kernel image into `../kernel/`, where `flash.sh` reads it:
```
cp kernel/kernel-noble/arch/arm64/boot/Image ../kernel/
```

`build.sh` also refreshes both production and flashing initrds after installing the final Image/modules. The commands above do not perform that refresh or the optional initrd optimization; use the build script for a flashable image.

`build.sh` does two more things a from-scratch flash needs, which the steps above leave out: it copies the per-module DTBs and camera `.dtbo` overlays from `build/nvidia-public/devicetree/generic-dtbs/` into both `rootfs/boot/` and `kernel/dtb/`, and it repoints the `lib/modules/<release>/{build,source}` symlinks at the on-target headers package (so DKMS and on-target module builds resolve their headers). Replicate those by hand, or just run `./build.sh <TARGET>`.

## Prepared headers in the flashed image

After the final kernel and NVIDIA module builds, `build.sh` runs `scripts/stage_kernel_headers.py`. It synchronizes the public and generated headers, `.config`, `Module.symvers`, and module linker script with the completed custom kernel. This includes the Tegra BPMP and memory-controller headers: matching the release string alone does not establish matching exported-symbol CRCs.

The helper checks that `.config`, `auto.conf`, `autoconf.h`, and the generated release agree. It preserves NVIDIA's release-suffixed Makefile and native AArch64 `fixdep`, `modpost`, and `genksyms` executables; cross-build host executables are never copied into the image. A verified temporary tree replaces the installed headers atomically. Its `.ark-prepared-headers.json` records the synchronized-file digest and retained tool hashes. Passing `--verify` checks the installed tree against the completed source build without changing it.

After flashing, validate the complete on-device toolchain with a small external module built using `make -C /lib/modules/$(uname -r)/build M="$PWD" modules`. Check its `modinfo` vermagic, load and unload it, and check the kernel log for symbol-version errors. This hardware check passed on the R39 JAJ: a native external module built against the shipped headers, resolved the custom BPMP export CRCs, and loaded/unloaded successfully. GCC 13.3 reported the expected version difference from the GCC 13.2 cross compiler. The unsigned test module was removed; the subsequent cold boots use the production modules. See [the R39 validation record](jetpack7_validation.md) for scope and artifact identity.

## Modifying the device tree

To change the device tree you must build from source. ARK carries only its **delta** under `products/{TARGET}/device_tree/` — the BCT pinmux/gpio files and a single `ark-{TARGET}-overrides.dtsi` fragment — which `build.sh` layers onto the stock NVIDIA tree every build; model strings live in `products/{TARGET}/dtb_models.env`. Edit those and re-run `./build.sh <TARGET>`. See [docs/device-tree.md](device-tree.md) for the layout and how to add an override.

The base DTB is selected by module and RAM ([NVIDIA porting guide](https://docs.nvidia.com/jetson/archives/r39.2.1/DeveloperGuide/HR/JetsonModuleAdaptationAndBringUp/JetsonOrinNxNanoSeries.html#porting-the-linux-kernel-device-tree)):

| Module | DTB |
| --- | --- |
| Orin NX 16GB | `tegra234-p3768-0000+p3767-0000-nv.dtb` |
| Orin NX 8GB | `tegra234-p3768-0000+p3767-0001-nv.dtb` |
| Orin Nano 8GB | `tegra234-p3768-0000+p3767-0003-nv.dtb` |
| Orin Nano 4GB | `tegra234-p3768-0000+p3767-0004-nv.dtb` |

Changing the base DTB requires a reflash. **Overlays do not** — a rebuilt `.dtbo` can be copied to the target's `/boot` and selected with `jetson-io`, no reflash needed. ARK overlay sources live under `products/{TARGET}/overlay/`; `build.sh` layers them onto the BSP's stock overlay tree and builds exactly the set listed in that dir's `dtbo.list`, compiling to `build/nvidia-public/devicetree/generic-dtbs/*.dtbo`. See [camera_overlays.md](camera_overlays.md) for the build → copy → `jetson-io` loop and for authoring custom camera overlays.


## Out-of-tree kernel modules

The OOT modules that ship with the BSP are installed by the `make modules` / `make modules_install` steps above. To build your *own* external module against the staged kernel, reuse the same `CROSS_COMPILE` and `KERNEL_HEADERS` exports from the manual build, then point kbuild at your module directory:
```
make -C "$KERNEL_HEADERS" M=$PWD ARCH=arm64 modules
```
To load the result on a running Jetson without reflashing, copy the `.ko` over, refresh the module index, and load it:
```
scp my_module.ko jetson@jetson.local:~
ssh jetson@jetson.local
sudo cp my_module.ko /lib/modules/$(uname -r)/extra/
sudo depmod -a
sudo modprobe my_module
```
The flashed image already carries the matching kernel headers — `build.sh` repoints `/lib/modules/<release>/build` at them — so modules can also be built on-target or rebuilt automatically via DKMS.
