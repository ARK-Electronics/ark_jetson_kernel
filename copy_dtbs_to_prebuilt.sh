#!/bin/bash

sudo cp $ARK_JETSON_KERNEL_DIR/source_build/Linux_for_Tegra/source/kernel_out/nvidia-oot/device-tree/platform/generic-dts/dtbs/tegra234-p3768-0000+p3767-0000-nv.dtb $ARK_JETSON_KERNEL_DIR/prebuilt/Linux_for_Tegra/rootfs/boot/
sudo cp $ARK_JETSON_KERNEL_DIR/source_build/Linux_for_Tegra/source/kernel_out/nvidia-oot/device-tree/platform/generic-dts/dtbs/tegra234-p3768-0000+p3767-0001-nv.dtb $ARK_JETSON_KERNEL_DIR/prebuilt/Linux_for_Tegra/rootfs/boot/
sudo cp $ARK_JETSON_KERNEL_DIR/source_build/Linux_for_Tegra/source/kernel_out/nvidia-oot/device-tree/platform/generic-dts/dtbs/tegra234-p3768-0000+p3767-0003-nv.dtb $ARK_JETSON_KERNEL_DIR/prebuilt/Linux_for_Tegra/rootfs/boot/
sudo cp $ARK_JETSON_KERNEL_DIR/source_build/Linux_for_Tegra/source/kernel_out/nvidia-oot/device-tree/platform/generic-dts/dtbs/tegra234-p3768-0000+p3767-0004-nv.dtb $ARK_JETSON_KERNEL_DIR/prebuilt/Linux_for_Tegra/rootfs/boot/
sudo cp $ARK_JETSON_KERNEL_DIR/source_build/Linux_for_Tegra/source/kernel_out/nvidia-oot/device-tree/platform/generic-dts/dtbs/tegra234-p3767-camera-p3768-imx219-ark-quad.dtbo $ARK_JETSON_KERNEL_DIR/prebuilt/Linux_for_Tegra/rootfs/boot/
sudo cp $ARK_JETSON_KERNEL_DIR/source_build/Linux_for_Tegra/source/kernel_out/nvidia-oot/device-tree/platform/generic-dts/dtbs/tegra234-p3768-0000+p3767-0000-dynamic.dtbo $ARK_JETSON_KERNEL_DIR/prebuilt/Linux_for_Tegra/rootfs/boot/

sudo cp $ARK_JETSON_KERNEL_DIR/source_build/Linux_for_Tegra/source/kernel_out/nvidia-oot/device-tree/platform/generic-dts/dtbs/tegra234-p3768-0000+p3767-0000-nv.dtb $ARK_JETSON_KERNEL_DIR/prebuilt/Linux_for_Tegra/kernel/dtb/
sudo cp $ARK_JETSON_KERNEL_DIR/source_build/Linux_for_Tegra/source/kernel_out/nvidia-oot/device-tree/platform/generic-dts/dtbs/tegra234-p3768-0000+p3767-0001-nv.dtb $ARK_JETSON_KERNEL_DIR/prebuilt/Linux_for_Tegra/kernel/dtb/
sudo cp $ARK_JETSON_KERNEL_DIR/source_build/Linux_for_Tegra/source/kernel_out/nvidia-oot/device-tree/platform/generic-dts/dtbs/tegra234-p3768-0000+p3767-0003-nv.dtb $ARK_JETSON_KERNEL_DIR/prebuilt/Linux_for_Tegra/kernel/dtb/
sudo cp $ARK_JETSON_KERNEL_DIR/source_build/Linux_for_Tegra/source/kernel_out/nvidia-oot/device-tree/platform/generic-dts/dtbs/tegra234-p3768-0000+p3767-0004-nv.dtb $ARK_JETSON_KERNEL_DIR/prebuilt/Linux_for_Tegra/kernel/dtb/
sudo cp $ARK_JETSON_KERNEL_DIR/source_build/Linux_for_Tegra/source/kernel_out/nvidia-oot/device-tree/platform/generic-dts/dtbs/tegra234-p3767-camera-p3768-imx219-ark-quad.dtbo $ARK_JETSON_KERNEL_DIR/prebuilt/Linux_for_Tegra/kernel/dtb/
sudo cp $ARK_JETSON_KERNEL_DIR/source_build/Linux_for_Tegra/source/kernel_out/nvidia-oot/device-tree/platform/generic-dts/dtbs/tegra234-p3768-0000+p3767-0000-dynamic.dtbo $ARK_JETSON_KERNEL_DIR/prebuilt/Linux_for_Tegra/kernel/dtb/