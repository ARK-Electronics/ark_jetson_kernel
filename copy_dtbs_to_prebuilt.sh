#!/bin/bash
DTBS_SOURCE_PATH="$ARK_JETSON_KERNEL_DIR/source_build/Linux_for_Tegra/source/kernel-devicetree/generic-dts/dtbs/"

PREBUILT_PATH="$ARK_JETSON_KERNEL_DIR/prebuilt/Linux_for_Tegra"
ARK_COMPILED_DEVICE_TREE_PATH="$ARK_JETSON_KERNEL_DIR/prebuilt/ark_jetson_compiled_device_tree_files/Linux_for_Tegra"

echo "Installing DTBs into prebuilt directory"
# Copy kernel device tree to bootloader path
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0000-nv.dtb $PREBUILT_PATH/rootfs/boot/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0001-nv.dtb $PREBUILT_PATH/rootfs/boot/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0003-nv.dtb $PREBUILT_PATH/rootfs/boot/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0004-nv.dtb $PREBUILT_PATH/rootfs/boot/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0000-dynamic.dtbo $PREBUILT_PATH/rootfs/boot/

# Copy kernel device tree to kernel path
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0000-nv.dtb $PREBUILT_PATH/kernel/dtb/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0001-nv.dtb $PREBUILT_PATH/kernel/dtb/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0003-nv.dtb $PREBUILT_PATH/kernel/dtb/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0004-nv.dtb $PREBUILT_PATH/kernel/dtb/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0000-dynamic.dtbo $PREBUILT_PATH/kernel/dtb/

# Copy camera overlays to kernel and bootloader paths
# IMX477 Single
sudo cp $DTBS_SOURCE_PATH/tegra234-p3767-camera-p3768-ark-imx477-single.dtbo $PREBUILT_PATH/rootfs/boot/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3767-camera-p3768-ark-imx477-single.dtbo $PREBUILT_PATH/kernel/dtb/
# IMX219 Quad
sudo cp $DTBS_SOURCE_PATH/tegra234-p3767-camera-p3768-ark-imx219-quad.dtbo $PREBUILT_PATH/rootfs/boot/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3767-camera-p3768-ark-imx219-quad.dtbo $PREBUILT_PATH/kernel/dtb/
# IMX219 Single
sudo cp $DTBS_SOURCE_PATH/tegra234-p3767-camera-p3768-ark-imx219-single.dtbo $PREBUILT_PATH/rootfs/boot/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3767-camera-p3768-ark-imx219-single.dtbo $PREBUILT_PATH/kernel/dtb/

echo "Removing non-supported overlays from prebuilt directory"
# Remove the overlays that don't work with ARK Carrier
file_names=(
	# TODO: remove overlays
)

for file in "${file_names[@]}"
do
    filepath="$ARK_JETSON_KERNEL_DIR/prebuilt/Linux_for_Tegra/rootfs/boot/$file"
    if [ -e "$filepath" ]; then
        echo "Removing $file..."
        sudo rm $filepath
    fi

    filepath="$ARK_JETSON_KERNEL_DIR/prebuilt/Linux_for_Tegra/kernel/dtb/$file"
    if [ -e "$filepath" ]; then
        echo "Removing $file..."
        sudo rm $filepath
    fi
done

echo "Installing DTBs into ark_jetson_compiled_device_tree_files directory"
# Copy kernel device tree to bootloader path
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0000-nv.dtb $ARK_COMPILED_DEVICE_TREE_PATH/rootfs/boot/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0001-nv.dtb $ARK_COMPILED_DEVICE_TREE_PATH/rootfs/boot/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0003-nv.dtb $ARK_COMPILED_DEVICE_TREE_PATH/rootfs/boot/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0004-nv.dtb $ARK_COMPILED_DEVICE_TREE_PATH/rootfs/boot/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0000-dynamic.dtbo $ARK_COMPILED_DEVICE_TREE_PATH/rootfs/boot/

# Copy kernel device tree to kernel path
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0000-nv.dtb $ARK_COMPILED_DEVICE_TREE_PATH/kernel/dtb/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0001-nv.dtb $ARK_COMPILED_DEVICE_TREE_PATH/kernel/dtb/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0003-nv.dtb $ARK_COMPILED_DEVICE_TREE_PATH/kernel/dtb/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0004-nv.dtb $ARK_COMPILED_DEVICE_TREE_PATH/kernel/dtb/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0000-dynamic.dtbo $ARK_COMPILED_DEVICE_TREE_PATH/kernel/dtb/

# Copy camera overlays to kernel and bootloader paths
# IMX477 Single
sudo cp $DTBS_SOURCE_PATH/tegra234-p3767-camera-p3768-ark-imx477-single.dtbo $ARK_COMPILED_DEVICE_TREE_PATH/rootfs/boot/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3767-camera-p3768-ark-imx477-single.dtbo $ARK_COMPILED_DEVICE_TREE_PATH/kernel/dtb/
# IMX219 Quad
sudo cp $DTBS_SOURCE_PATH/tegra234-p3767-camera-p3768-ark-imx219-quad.dtbo $ARK_COMPILED_DEVICE_TREE_PATH/rootfs/boot/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3767-camera-p3768-ark-imx219-quad.dtbo $ARK_COMPILED_DEVICE_TREE_PATH/kernel/dtb/
# IMX219 Single
sudo cp $DTBS_SOURCE_PATH/tegra234-p3767-camera-p3768-ark-imx219-single.dtbo $ARK_COMPILED_DEVICE_TREE_PATH/rootfs/boot/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3767-camera-p3768-ark-imx219-single.dtbo $ARK_COMPILED_DEVICE_TREE_PATH/kernel/dtb/
