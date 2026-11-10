# Kernel & Device Tree Development

`build.sh` automates everything below. These notes are for manual builds, for adding out-of-tree modules, and for understanding how the device tree is assembled.

## Manual kernel build

The steps below build and install the kernel, modules, and DTBs by hand. Adjust the `PAB` path for your target.

Set up the cross-compile environment and build. On an aarch64 host, skip the `CROSS_COMPILE` line.

```
source versions.env
export CROSS_COMPILE=$HOME/l4t-gcc/$TOOLCHAIN_CROSS_PREFIX
export KERNEL_HEADERS=$PWD/staging/PAB/Linux_for_Tegra/source/kernel/$KERNEL_SRC_DIR
export kernel_name=$KERNEL_NAME
export INSTALL_MOD_PATH=$PWD/staging/PAB/Linux_for_Tegra/rootfs/
cd staging/PAB/Linux_for_Tegra/source
make -C kernel && make modules && make dtbs
```

Install the kernel with its in-tree modules and DTBs, then the out-of-tree modules. Those are the NVIDIA display driver and the other OOT drivers that `make modules` builds.

```
sudo -E make install -C kernel
sudo -E make modules_install
```

Copy the kernel image into `../kernel/`, where `flash.sh` reads it:

```
cp kernel/$KERNEL_SRC_DIR/arch/arm64/boot/Image ../kernel/
```

`build.sh` does two more things that a flash from scratch needs and the steps above leave out. It copies the per-module DTBs and the camera `.dtbo` overlays from `build/nvidia-public/devicetree/generic-dtbs/` into both `rootfs/boot/` and `kernel/dtb/`. It also repoints the `lib/modules/<release>/{build,source}` symlinks at the on-target headers package, so that DKMS and module builds on the target find their headers. Do those two by hand, or run `./build.sh <TARGET>`.

## Modifying the device tree

To change the device tree you must build from source. ARK keeps only its **delta** under `products/{TARGET}/device_tree/`: the BCT pinmux and gpio files and a single `ark-{TARGET}-overrides.dtsi` fragment. `build.sh` layers them onto the stock NVIDIA tree on every build. The model strings live in `products/{TARGET}/dtb_models.env`. Edit those files and run `./build.sh <TARGET>` again. [docs/device-tree.md](device-tree.md) describes the layout and how to add an override.

The module and its RAM select the base DTB, as the [NVIDIA porting guide](https://docs.nvidia.com/jetson/archives/r36.3/DeveloperGuide/HR/JetsonModuleAdaptationAndBringUp/JetsonOrinNxNanoSeries.html#porting-the-linux-kernel-device-tree) describes:

| Module | DTB |
| --- | --- |
| Orin NX 16GB | `tegra234-p3768-0000+p3767-0000-nv.dtb` |
| Orin NX 8GB | `tegra234-p3768-0000+p3767-0001-nv.dtb` |
| Orin Nano 8GB | `tegra234-p3768-0000+p3767-0003-nv.dtb` |
| Orin Nano 4GB | `tegra234-p3768-0000+p3767-0004-nv.dtb` |

Changing the base DTB requires a reflash. **Overlays do not.** You can copy a rebuilt `.dtbo` to the target's `/boot` and select it with `jetson-io`. ARK's overlay sources live under `products/{TARGET}/overlay/`. `build.sh` layers them onto the BSP's stock overlay tree and builds exactly the set listed in that directory's `dtbo.list`, as `kernel-devicetree/generic-dts/dtbs/*.dtbo`. [camera_overlays.md](camera_overlays.md) covers the loop of building, copying and selecting with `jetson-io`, and writing your own camera overlays.

## Out-of-tree kernel modules

The `make modules` and `make modules_install` steps above install the OOT modules that ship with the BSP. To build your *own* external module against the staged kernel, reuse the `CROSS_COMPILE` and `KERNEL_HEADERS` exports from the manual build, then point kbuild at your module directory:

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

The flashed image already carries the matching kernel headers, and `build.sh` points `/lib/modules/<release>/build` at them. You can therefore also build modules on the target, or have DKMS rebuild them.
