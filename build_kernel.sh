#!/bin/bash

sudo -v
export CROSS_COMPILE=$HOME/l4t-gcc/aarch64--glibc--stable-2022.08-1/bin/aarch64-buildroot-linux-gnu-
export KERNEL_HEADERS=$ARK_JETSON_KERNEL_DIR/source_build/Linux_for_Tegra/source/kernel/kernel-jammy-src
export INSTALL_MOD_PATH=$ARK_JETSON_KERNEL_DIR/prebuilt/Linux_for_Tegra/rootfs/
cd $ARK_JETSON_KERNEL_DIR/source_build/Linux_for_Tegra/source
make -C kernel && make modules && make dtbs
sudo -E make install -C kernel
cp kernel/kernel-jammy-src/arch/arm64/boot/Image ../../../prebuilt/Linux_for_Tegra/kernel/
$ARK_JETSON_KERNEL_DIR/copy_dtbs_to_prebuilt.sh
