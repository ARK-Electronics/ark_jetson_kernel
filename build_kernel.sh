#!/bin/bash

START_TIME=$(date +%s)

sudo -v
export ARK_JETSON_KERNEL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export CROSS_COMPILE=$HOME/l4t-gcc/aarch64--glibc--stable-2022.08-1/bin/aarch64-buildroot-linux-gnu-
export KERNEL_HEADERS=$ARK_JETSON_KERNEL_DIR/source_build/Linux_for_Tegra/source/kernel/kernel-jammy-src
export INSTALL_MOD_PATH=$ARK_JETSON_KERNEL_DIR/prebuilt/Linux_for_Tegra/rootfs/

pushd .

# Interactive selection for target platform
echo "Please select the target platform:"
echo "1) PAB"
echo "2) JAJ"
echo "3) PAB_V3"
read -p "Enter your choice (1, 2, or 3): " choice

case $choice in
    1)
        export TARGET="PAB"
        DT_SOURCE="ark_pab"
        ;;
    2)
        export TARGET="JAJ"
        DT_SOURCE="ark_jaj"
        ;;
    3)
        export TARGET="PAB_V3"
        DT_SOURCE="ark_pab_v3"
        ;;
    *)
        echo "Invalid choice. Exiting."
        exit 1
        ;;
esac

# Create a file to track the last built target
echo "$TARGET" > $ARK_JETSON_KERNEL_DIR/source_build/LAST_BUILT_TARGET

# Copy ARK device tree overlay into source build
cd $ARK_JETSON_KERNEL_DIR/source_build/
echo "Copying ARK device tree files for $TARGET"
cp -r $ARK_JETSON_KERNEL_DIR/device_tree/$DT_SOURCE/Linux_for_Tegra/* Linux_for_Tegra/

cd Linux_for_Tegra/source

# Apply Jetvariety patch
PATCH_FILE="$ARK_JETSON_KERNEL_DIR/JetsonOrinNX_OrinNano_JetPack6.2_L4T36.4.3_Jetvariety.patch"
echo "Checking if jetvariety patch has already been applied..."
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

echo "Building the kernel for $TARGET platform"
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

echo "Fixing kernel module symlinks in rootfs"
# The kernel version for JP 6.2
JETSON_KERNEL_VERSION="5.15.148-tegra"
ROOTFS_PATH="$INSTALL_MOD_PATH"
MODULES_PATH="$ROOTFS_PATH/lib/modules/$JETSON_KERNEL_VERSION"
HEADERS_TARGET="/usr/src/linux-headers-5.15.148-tegra-ubuntu22.04_aarch64/3rdparty/canonical/linux-jammy/kernel-source"

if [ -d "$MODULES_PATH" ]; then
    echo "Updating symlinks in $MODULES_PATH"
    # Remove old symlinks (they point to build machine paths)
    sudo rm -f "$MODULES_PATH/build"
    sudo rm -f "$MODULES_PATH/source"

    # Create new symlinks that will be valid on the Jetson
    sudo ln -sfn "$HEADERS_TARGET" "$MODULES_PATH/build"
    sudo ln -sfn "$HEADERS_TARGET" "$MODULES_PATH/source"

    echo "Symlinks updated successfully"
    ls -la "$MODULES_PATH" | grep -E "build|source"
else
    echo "WARNING: Module path $MODULES_PATH not found!"
fi

echo "Copying kernel Image to prebuilt"
cp kernel/kernel-jammy-src/arch/arm64/boot/Image ../../../prebuilt/Linux_for_Tegra/kernel/
$ARK_JETSON_KERNEL_DIR/copy_dtbs_to_prebuilt.sh

# NOTE: the camera params file is camera specific. This override file is for the IMX219
# $ARK_JETSON_KERNEL_DIR/copy_camera_params_to_prebuilt.sh

popd

echo "Copying arducam_csi2.ko to prebuilt"
sudo cp source_build/Linux_for_Tegra/source/nvidia-oot/drivers/media/i2c/arducam_csi2.ko \
    prebuilt/Linux_for_Tegra/rootfs/usr/lib/modules/5.15.148-tegra/updates/drivers/media/i2c/

END_TIME=$(date +%s)
TOTAL_TIME=$((${END_TIME}-${START_TIME}))
echo "Build complete in $(date -d@${TOTAL_TIME} -u +%H:%M:%S)"
echo "Target platform: $TARGET"
echo "You can now flash the device with ./flash.sh"
