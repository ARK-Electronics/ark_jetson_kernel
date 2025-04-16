#!/bin/bash
export ARK_JETSON_KERNEL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_BUILD_PATH="$ARK_JETSON_KERNEL_DIR/source_build/Linux_for_Tegra/"
DTBS_SOURCE_PATH="$SOURCE_BUILD_PATH/source/kernel-devicetree/generic-dts/dtbs/"
PREBUILT_PATH="$ARK_JETSON_KERNEL_DIR/prebuilt/Linux_for_Tegra"

echo "Installing bootloader DTSI files into prebuilt directory"
echo "(previously from ark_jetson_compiled_device_tree_files)"
sudo cp $SOURCE_BUILD_PATH/bootloader/tegra234-mb1-bct-gpio-p3767-dp-a03.dtsi $PREBUILT_PATH/bootloader/
sudo cp $SOURCE_BUILD_PATH/bootloader/generic/BCT/tegra234-mb1-bct-padvoltage-p3767-dp-a03.dtsi $PREBUILT_PATH/bootloader/generic/BCT/
sudo cp $SOURCE_BUILD_PATH/bootloader/generic/BCT/tegra234-mb1-bct-pinmux-p3767-dp-a03.dtsi $PREBUILT_PATH/bootloader/generic/BCT/
sudo cp $SOURCE_BUILD_PATH/bootloader/generic/BCT/tegra234-mb2-bct-misc-p3767-0000.dts $PREBUILT_PATH/bootloader/generic/BCT/

echo "Installing DTBs into prebuilt directory"
# Copy kernel device tree to bootloader path
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0000-nv.dtb $PREBUILT_PATH/rootfs/boot/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0001-nv.dtb $PREBUILT_PATH/rootfs/boot/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0003-nv.dtb $PREBUILT_PATH/rootfs/boot/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0004-nv.dtb $PREBUILT_PATH/rootfs/boot/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0005-nv.dtb $PREBUILT_PATH/rootfs/boot/

sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0000-dynamic.dtbo $PREBUILT_PATH/rootfs/boot/

sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0000-nv-super.dtb $PREBUILT_PATH/rootfs/boot/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0001-nv-super.dtb $PREBUILT_PATH/rootfs/boot/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0003-nv-super.dtb $PREBUILT_PATH/rootfs/boot/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0004-nv-super.dtb $PREBUILT_PATH/rootfs/boot/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0005-nv-super.dtb $PREBUILT_PATH/rootfs/boot/

# Copy kernel device tree to kernel path
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0000-nv.dtb $PREBUILT_PATH/kernel/dtb/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0001-nv.dtb $PREBUILT_PATH/kernel/dtb/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0003-nv.dtb $PREBUILT_PATH/kernel/dtb/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0004-nv.dtb $PREBUILT_PATH/kernel/dtb/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0005-nv.dtb $PREBUILT_PATH/kernel/dtb/

sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0000-dynamic.dtbo $PREBUILT_PATH/kernel/dtb/

sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0000-nv-super.dtb $PREBUILT_PATH/kernel/dtb/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0001-nv-super.dtb $PREBUILT_PATH/kernel/dtb/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0003-nv-super.dtb $PREBUILT_PATH/kernel/dtb/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0004-nv-super.dtb $PREBUILT_PATH/kernel/dtb/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0005-nv-super.dtb $PREBUILT_PATH/kernel/dtb/

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
# AR0234 Single
sudo cp $DTBS_SOURCE_PATH/tegra234-p3767-camera-p3768-ark-arducam-single.dtbo $PREBUILT_PATH/rootfs/boot/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3767-camera-p3768-ark-arducam-single.dtbo $PREBUILT_PATH/kernel/dtb/

# IMX477 DUAL ARK Adaptor
sudo cp $DTBS_SOURCE_PATH/tegra234-p3767-camera-p3768-imx477-dual-4lane.dtbo $PREBUILT_PATH/rootfs/boot/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3767-camera-p3768-imx477-dual-4lane.dtbo $PREBUILT_PATH/kernel/dtb/


echo "Removing non-supported overlays from prebuilt directory"
# Remove the overlays that don't work with ARK Carrier
file_names=(
    "tegra234-p3767-camera-p3768-imx219-A.dtbo"
    "tegra234-p3767-camera-p3768-imx219-ark-quad.dtbo"
    "tegra234-p3767-camera-p3768-imx219-C.dtbo"
    "tegra234-p3767-camera-p3768-imx219-dual.dtbo"
    "tegra234-p3767-camera-p3768-imx219-imx477.dtbo"
    "tegra234-p3767-camera-p3768-imx477-A.dtbo"
    "tegra234-p3767-camera-p3768-imx477-C.dtbo"
    "tegra234-p3767-camera-p3768-imx477-dual.dtbo"
    "tegra234-p3767-camera-p3768-imx477-imx219.dtbo"
    "tegra234-p3767-camera-p3768-ov5647-single.dtbo"
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
