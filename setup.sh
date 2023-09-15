#!/bin/bash
BASHRC="$HOME/.bashrc"
START_TIME=$(date +%s)
SUDO_PASSWORD=

# sudo password for later
IFS= read -rsp "[sudo] password for $USER: " SUDO_PASSWORD
echo ""

L4T_RELEASE_PACKAGE="jetson_linux_r35.3.1_aarch64.tbz2"
SAMPLE_FS_PACKAGE="tegra_linux_sample-root-filesystem_r35.3.1_aarch64.tbz2"
BOARD="jetson-orin-nano-devkit"

# Checks if setup is already performed
already_performed=$(ls | grep "Linux_for_Tegra")
if [ "$already_performed" ]; then
	echo "Setup already complete. To start from scratch remove the Linux_for_Tegra directory"
	echo "eg	sudo rm -rf Linux_for_Tegra/"
	exit
fi

# Add to bashrc if necessary
exists=$(cat $BASHRC | grep L4T_RELEASE_PACKAGE)
if [ -z "$exists" ]; then
	echo "Adding environment variables to bashrc"
	echo "export L4T_RELEASE_PACKAGE=$L4T_RELEASE_PACKAGE" >> $BASHRC
	echo "export SAMPLE_FS_PACKAGE=$SAMPLE_FS_PACKAGE" >> $BASHRC
	echo "export BOARD=$BOARD" >> $BASHRC
fi

# Check if release source files need to be downloaded
release_downloaded=$(ls | grep $L4T_RELEASE_PACKAGE)
if [ -z $release_downloaded ]; then
	echo "Downloading sources"
	# https://developer.nvidia.com/embedded/jetson-linux-archive
	wget https://developer.nvidia.com/downloads/embedded/l4t/r35_release_v3.1/release/jetson_linux_r35.3.1_aarch64.tbz2
	wget https://developer.nvidia.com/downloads/embedded/l4t/r35_release_v3.1/release/tegra_linux_sample-root-filesystem_r35.3.1_aarch64.tbz2
fi

# Perform setup
# https://docs.nvidia.com/jetson/archives/r35.3.1/DeveloperGuide/text/IN/QuickStart.html#jetson-modules-and-configurations
echo "Untarring files, this may take some time"
tar xf ${L4T_RELEASE_PACKAGE}
sudo -S tar xpf ${SAMPLE_FS_PACKAGE} -C Linux_for_Tegra/rootfs/ <<< "$SUDO_PASSWORD"
cd Linux_for_Tegra/
echo "Applying binaries"
sudo -S ./apply_binaries.sh --debug <<< "$SUDO_PASSWORD"
echo "Satisfying prerequisites"
sudo -S ./tools/l4t_flash_prerequisites.sh <<< "$SUDO_PASSWORD"
cd ..

# From the “ark_jetson_compiled_device_tree_file” github repository you can find the modified files with the compiled device
# tree binary. Copy these files to the corresponding locations in the “Linux_for_Tegra” folder you downloaded and extracted from NVIDIA.
repo_downloaded=$(ls | grep "ark_jetson_compiled_device_tree_files")
if [ -z $repo_downloaded ]; then
	echo "Downloading device tree files"
	git clone git@github.com:ARK-Electronics/ark_jetson_compiled_device_tree_files.git
fi
echo "Copying device tree files"
sudo -S cp -r ark_jetson_compiled_device_tree_files/Linux_for_Tegra/* Linux_for_Tegra/ <<< "$SUDO_PASSWORD"

END_TIME=$(date +%s)
TOTAL_TIME=$((${END_TIME}-${START_TIME}))
echo "Finished -- $(date -d@${TOTAL_TIME} -u +%H:%M:%S)"
echo "You can now flash the device"