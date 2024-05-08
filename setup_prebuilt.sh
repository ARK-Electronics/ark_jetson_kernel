#!/bin/bash
START_TIME=$(date +%s)
SUDO_PASSWORD=

# sudo password for later
IFS= read -rsp "[sudo] password for $USER: " SUDO_PASSWORD
echo ""

BSP_URL="https://developer.nvidia.com/downloads/embedded/l4t/r36_release_v3.0/release/jetson_linux_r36.3.0_aarch64.tbz2"
ROOT_FS_URL="https://developer.nvidia.com/downloads/embedded/l4t/r36_release_v3.0/release/tegra_linux_sample-root-filesystem_r36.3.0_aarch64.tbz2"
# Used for both NX and Nano
# Orin NX 16GB  : tegra234-p3768-0000+p3767-0000
# Orin NX 8GB   : tegra234-p3768-0000+p3767-0001
# Orin Nano 8GB : tegra234-p3768-0000+p3767-0003
# Orin Nano 4GB : tegra234-p3768-0000+p3767-0004
export L4T_RELEASE_PACKAGE=$(basename $BSP_URL)
export SAMPLE_FS_PACKAGE=$(basename $ROOT_FS_URL)
export BOARD="jetson-orin-nano-devkit"

pushd . > /dev/null
mkdir -p prebuilt && cd prebuilt

# https://developer.nvidia.com/embedded/jetson-linux-archive
echo "Downloading prebuilt BSP and root filesystem"
wget -nc $BSP_URL
wget -nc $ROOT_FS_URL

# https://docs.nvidia.com/jetson/archives/r36.3/DeveloperGuide/IN/QuickStart.html#to-flash-the-jetson-developer-kit-operating-software
if [ ! -d "Linux_for_Tegra/rootfs/" ]; then
	echo "Untarring files, this may take some time"
	tar xf $L4T_RELEASE_PACKAGE
	sudo -S tar xpf $SAMPLE_FS_PACKAGE -C Linux_for_Tegra/rootfs/ <<< "$SUDO_PASSWORD"
fi

cd Linux_for_Tegra/
echo "Satisfying prerequisites"
sudo -S ./tools/l4t_flash_prerequisites.sh <<< "$SUDO_PASSWORD"
echo "Applying binaries"
sudo -S ./apply_binaries.sh --debug <<< "$SUDO_PASSWORD"
cd ..

# Check if compiled devide tree repo is already downloaded
repo_downloaded=$(ls | grep "ark_jetson_compiled_device_tree_files")
if [ -z $repo_downloaded ]; then
	echo "Downloading device tree files"
	git clone -b ark_36.3.0 git@github.com:ARK-Electronics/ark_jetson_compiled_device_tree_files.git
fi

echo "Copying device tree files"
sudo -S cp -r ark_jetson_compiled_device_tree_files/Linux_for_Tegra/* Linux_for_Tegra/ <<< "$SUDO_PASSWORD"

popd > /dev/null

echo "Setting up login credentials for the Jetson"
sudo ./configure_user.sh

END_TIME=$(date +%s)
TOTAL_TIME=$((${END_TIME}-${START_TIME}))
echo "Finished -- $(date -d@${TOTAL_TIME} -u +%H:%M:%S)"
echo "You can now flash the device"

