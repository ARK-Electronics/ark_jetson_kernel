#!/bin/bash

START_TIME=$(date +%s)

sudo -v
export CROSS_COMPILE=$HOME/l4t-gcc/aarch64--glibc--stable-2022.08-1/bin/aarch64-buildroot-linux-gnu-
export KERNEL_HEADERS=$ARK_JETSON_KERNEL_DIR/source_build/Linux_for_Tegra/source/kernel/kernel-jammy-src
export INSTALL_MOD_PATH=$ARK_JETSON_KERNEL_DIR/prebuilt/Linux_for_Tegra/rootfs/

pushd .

cd $ARK_JETSON_KERNEL_DIR/source_build/
echo "Copying ARK device tree files"
cp -r ark_jetson_orin_nano_nx_device_tree/Linux_for_Tegra/* Linux_for_Tegra/

cd Linux_for_Tegra/source
make -C kernel && make modules && make dtbs
sudo -E make install -C kernel
cp kernel/kernel-jammy-src/arch/arm64/boot/Image ../../../prebuilt/Linux_for_Tegra/kernel/
$ARK_JETSON_KERNEL_DIR/copy_dtbs_to_prebuilt.sh

END_TIME=$(date +%s)
TOTAL_TIME=$((${END_TIME}-${START_TIME}))
echo "Build complete in $(date -d@${TOTAL_TIME} -u +%H:%M:%S)"
echo "You can now flash the device with ./flash.sh"

popd
