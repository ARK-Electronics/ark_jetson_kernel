#!/bin/bash

START_TIME=$(date +%s)

sudo -v
export ARK_JETSON_KERNEL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export CROSS_COMPILE=$HOME/l4t-gcc/aarch64--glibc--stable-2022.08-1/bin/aarch64-buildroot-linux-gnu-
export KERNEL_HEADERS=$ARK_JETSON_KERNEL_DIR/source_build/Linux_for_Tegra/source/kernel/kernel-jammy-src
export INSTALL_MOD_PATH=$ARK_JETSON_KERNEL_DIR/prebuilt/Linux_for_Tegra/rootfs/

pushd .

cd $ARK_JETSON_KERNEL_DIR/source_build/
echo "Copying ARK device tree files"
cp -r ark_jetson_orin_nano_nx_device_tree/Linux_for_Tegra/* Linux_for_Tegra/

cd Linux_for_Tegra/source

# Apply Jetvariety patch
PATCH_FILE="$ARK_JETSON_KERNEL_DIR/JetsonOrinNX_OrinNano_JetPack6.2_L4T36.4.3_Jetvariety.patch"
echo "Checking if patch has already been applied..."
if patch -p1 -R --dry-run --force < "$PATCH_FILE" &>/dev/null; then
    echo "Patch is already applied."
    # NOTE: use this command to unapply the patch
    # patch -p1 -R < /home/jake/code/ark/ark_jetson_kernel/JetsonOrinNX_OrinNano_JetPack6.2_L4T36.4.3_Jetvariety.patch
else
    echo "Patch not yet applied. Applying now..."

    # Check if the patch can be applied cleanly
    if patch -p1 --dry-run --force < "$PATCH_FILE" &>/dev/null; then
        # Actually apply the patch
        if patch -p1 --force < "$PATCH_FILE"; then
            echo "Patch applied successfully."
        else
            echo "Error: Failed to apply patch."
            exit 1
        fi
    else
        echo "Error: Patch cannot be applied cleanly."
        exit 1
    fi
fi

echo "Building the kernel"
make -C kernel && make modules && make dtbs
if [ $? -ne 0 ]; then
    echo "Kernel build failed. Exiting."
    exit 1
fi
echo "Kernel build successful. Installing in-tree modules and dtbs..."
sudo -E make install -C kernel
if [ $? -ne 0 ]; then
    echo "Failed to install in-tree modules and dtbs. Exiting."
    exit 1
fi
echo "In-tree modules and dtbs installation successful. Installing out-of-tree modules..."
sudo -E make modules_install
if [ $? -ne 0 ]; then
    echo "Failed to install out-of-tree modules. Exiting."
    exit 1
fi

echo "Copying kernel Image to prebuilt"
cp kernel/kernel-jammy-src/arch/arm64/boot/Image ../../../prebuilt/Linux_for_Tegra/kernel/
$ARK_JETSON_KERNEL_DIR/copy_dtbs_to_prebuilt.sh
$ARK_JETSON_KERNEL_DIR/copy_camera_params_to_prebuilt.sh

popd

echo "Copying arducam_csi2.ko to prebuilt"
sudo cp source_build/Linux_for_Tegra/source/nvidia-oot/drivers/media/i2c/arducam_csi2.ko \
	prebuilt/Linux_for_Tegra/rootfs/usr/lib/modules/5.15.148-tegra/updates/drivers/media/i2c/

END_TIME=$(date +%s)
TOTAL_TIME=$((${END_TIME}-${START_TIME}))
echo "Build complete in $(date -d@${TOTAL_TIME} -u +%H:%M:%S)"
echo "You can now flash the device with ./flash.sh"
