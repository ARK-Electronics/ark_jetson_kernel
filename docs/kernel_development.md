# Kernel & Device Tree Development

`build.sh` automates everything below. These notes are for manual builds, for adding out-of-tree modules, and for understanding how the device tree is assembled.

## Manual kernel build

The steps below build and install the kernel, modules, and DTBs by hand. Adjust the `PAB` path for your target.

Set up the cross-compile environment:
```
export CROSS_COMPILE=$HOME/l4t-gcc/aarch64--glibc--stable-2022.08-1/bin/aarch64-buildroot-linux-gnu-
export KERNEL_HEADERS=$PWD/staging/PAB/Linux_for_Tegra/source/kernel/kernel-jammy-src
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
cp kernel/kernel-jammy-src/arch/arm64/boot/Image ../kernel/
```

`build.sh` does two more things a from-scratch flash needs, which the steps above leave out: it copies the per-module DTBs and camera `.dtbo` overlays from `kernel-devicetree/generic-dts/dtbs/` into both `rootfs/boot/` and `kernel/dtb/`, and it repoints the `lib/modules/<release>/{build,source}` symlinks at the on-target headers package (so DKMS and on-target module builds resolve their headers). Replicate those by hand, or just run `./build.sh <TARGET>`.

## Modifying the device tree

To change the device tree you must build from source. The device tree sources for each product live in `products/{TARGET}/device_tree/`. `build.sh` overlays these onto the NVIDIA kernel source on every build, so edit the files there and re-run `./build.sh <TARGET>`.

The base DTB is selected by module and RAM ([NVIDIA porting guide](https://docs.nvidia.com/jetson/archives/r36.3/DeveloperGuide/HR/JetsonModuleAdaptationAndBringUp/JetsonOrinNxNanoSeries.html#porting-the-linux-kernel-device-tree)):

| Module | DTB |
| --- | --- |
| Orin NX 16GB | `tegra234-p3768-0000+p3767-0000-nv.dtb` |
| Orin NX 8GB | `tegra234-p3768-0000+p3767-0001-nv.dtb` |
| Orin Nano 8GB | `tegra234-p3768-0000+p3767-0003-nv.dtb` |
| Orin Nano 4GB | `tegra234-p3768-0000+p3767-0004-nv.dtb` |

Changing the base DTB requires a reflash. **Overlays do not** — a rebuilt `.dtbo` can be copied to the target's `/boot` and selected with `jetson-io`, no reflash needed. ARK overlay sources live under `products/{TARGET}/overlay/`; `build.sh` layers them onto the BSP's stock overlay tree and builds exactly the set listed in that dir's `dtbo.list`, compiling to `kernel-devicetree/generic-dts/dtbs/*.dtbo`. See [cameras.md](cameras.md#installing-a-camera-overlay) for the build → copy → `jetson-io` loop.

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
