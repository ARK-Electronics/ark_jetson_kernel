#!/bin/bash
BASHRC="$HOME/.bashrc"
START_TIME=$(date +%s)
SUDO_PASSWORD=

# sudo password for later
IFS= read -rsp "[sudo] password for $USER: " SUDO_PASSWORD
echo ""

L4T_RELEASE_PACKAGE="jetson_linux_r35.5.0_aarch64.tbz2"
SAMPLE_FS_PACKAGE="tegra_linux_sample-root-filesystem_r35.5.0_aarch64.tbz2"
BOARD="jetson-orin-nano-devkit"

pushd . > /dev/null
mkdir -p prebuilt && cd prebuilt

export L4T_RELEASE_PACKAGE=$L4T_RELEASE_PACKAGE
export SAMPLE_FS_PACKAGE=$SAMPLE_FS_PACKAGE
export BOARD=$BOARD

# Check if release binary files need to be downloaded
release_downloaded=$(ls | grep $L4T_RELEASE_PACKAGE)
if [ -z $release_downloaded ]; then
	echo "Downloading prebuilt binaries"
	# https://developer.nvidia.com/embedded/jetson-linux-archive
	wget https://developer.nvidia.com/downloads/embedded/l4t/r35_release_v5.0/release/jetson_linux_r35.5.0_aarch64.tbz2
	wget https://developer.nvidia.com/downloads/embedded/l4t/r35_release_v5.0/release/tegra_linux_sample-root-filesystem_r35.5.0_aarch64.tbz2
fi

# Checks if setup is already performed
already_performed=$(ls | grep "Linux_for_Tegra")
if [ -z "$already_performed" ]; then
	# https://docs.nvidia.com/jetson/archives/r35.5.0/DeveloperGuide/IN/QuickStart.html#jetson-modules-and-configurations
	echo "Untarring files, this may take some time"
	tar xf ${L4T_RELEASE_PACKAGE}
	sudo -S tar xpf ${SAMPLE_FS_PACKAGE} -C Linux_for_Tegra/rootfs/ <<< "$SUDO_PASSWORD"
	cd Linux_for_Tegra/
	echo "Applying binaries"
	sudo -S ./apply_binaries.sh --debug <<< "$SUDO_PASSWORD"
	echo "Satisfying prerequisites"
	sudo -S ./tools/l4t_flash_prerequisites.sh <<< "$SUDO_PASSWORD"
	cd ..
fi

# Check if compiled devide tree repo is already downloaded
repo_downloaded=$(ls | grep "ark_jetson_compiled_device_tree_files")
if [ -z $repo_downloaded ]; then
	echo "Downloading device tree files"
	git clone -b ark_35.5.0 git@github.com:ARK-Electronics/ark_jetson_compiled_device_tree_files.git
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

