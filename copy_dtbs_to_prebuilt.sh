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

sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0000-nv-super.dtb $PREBUILT_PATH/kernel/dtb/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0001-nv-super.dtb $PREBUILT_PATH/kernel/dtb/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0003-nv-super.dtb $PREBUILT_PATH/kernel/dtb/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0004-nv-super.dtb $PREBUILT_PATH/kernel/dtb/
sudo cp $DTBS_SOURCE_PATH/tegra234-p3768-0000+p3767-0005-nv-super.dtb $PREBUILT_PATH/kernel/dtb/

echo "Installing overlay dtbo files from build"
# Build list of source dtbo filenames
source_dtbos=()
for dtbo in $DTBS_SOURCE_PATH/*.dtbo; do
    filename=$(basename "$dtbo")
    source_dtbos+=("$filename")
    echo "  $filename"
    sudo cp "$dtbo" $PREBUILT_PATH/rootfs/boot/
    sudo cp "$dtbo" $PREBUILT_PATH/kernel/dtb/
done

# Remove stale kernel-built dtbo files from prebuilt that aren't in the source build
# Only check tegra*/ark_* files to preserve UEFI dtbos (AcpiBoot, BootOrder*, etc.)
for dir in $PREBUILT_PATH/rootfs/boot $PREBUILT_PATH/kernel/dtb; do
    for f in "$dir"/tegra*.dtbo "$dir"/ark_*.dtbo; do
        [ -e "$f" ] || continue
        filename=$(basename "$f")
        found=false
        for src in "${source_dtbos[@]}"; do
            if [ "$filename" = "$src" ]; then
                found=true
                break
            fi
        done
        if [ "$found" = false ]; then
            echo "  Removing stale: $filename"
            sudo rm -f "$f"
        fi
    done
done
