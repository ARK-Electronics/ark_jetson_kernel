# Kernel & Device Tree Development

`build.sh` automates everything below. These notes are for manual builds and for understanding how the device tree is assembled.

## Manual kernel build

The steps below build the kernel, modules, and DTBs by hand. Adjust the `PAB` path for your target.

Set up the cross-compile environment:
```
export CROSS_COMPILE=$HOME/l4t-gcc/aarch64--glibc--stable-2022.08-1/bin/aarch64-buildroot-linux-gnu-
export KERNEL_HEADERS=$PWD/staging/PAB/Linux_for_Tegra/source/kernel/kernel-jammy-src
export INSTALL_MOD_PATH=$PWD/staging/PAB/Linux_for_Tegra/rootfs/
cd staging/PAB/Linux_for_Tegra/source
make -C kernel && make modules && make dtbs
```
Install the kernel into the staging rootfs:
```
sudo -E make install -C kernel
```
Copy the kernel image into place:
```
cp kernel/kernel-jammy-src/arch/arm64/boot/Image ../../kernel/
```

## Modifying the device tree

To change the device tree you must build the kernel from source. The resulting DTBs are installed into the staging directory and included in the next flash.

The device tree source for each product lives in `products/{TARGET}/device_tree/`. `build.sh` overlays these onto the NVIDIA kernel source on every build, so edit the files there and re-run `./build.sh <TARGET>`.

The correct DTB depends on the module and its RAM ([NVIDIA porting guide](https://docs.nvidia.com/jetson/archives/r36.3/DeveloperGuide/HR/JetsonModuleAdaptationAndBringUp/JetsonOrinNxNanoSeries.html#porting-the-linux-kernel-device-tree)):

| Module | DTB |
| --- | --- |
| Orin NX 16GB | `tegra234-p3768-0000+p3767-0000-nv.dtb` |
| Orin NX 8GB | `tegra234-p3768-0000+p3767-0001-nv.dtb` |
| Orin Nano 8GB | `tegra234-p3768-0000+p3767-0003-nv.dtb` |
| Orin Nano 4GB | `tegra234-p3768-0000+p3767-0004-nv.dtb` |
