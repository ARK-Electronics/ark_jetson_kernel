#!/bin/bash
BASHRC="$HOME/.bashrc"
START_TIME=$(date +%s)
SUDO_PASSWORD=

# sudo password for later
IFS= read -rsp "[sudo] password for $USER: " SUDO_PASSWORD
echo ""

# Add to bashrc if necessary
exists=$(cat $BASHRC | grep ARK_JETSON_KERNEL_DIR)
if [ -z "$exists" ]; then
	echo "export ARK_JETSON_KERNEL_DIR=$PWD" >> $BASHRC
fi

mkdir -p source_build && cd source_build

# Checks if setup is already performed
already_performed=$(ls | grep "Linux_for_Tegra")
if [ "$already_performed" ]; then
	echo "Setup already complete. To start from scratch remove the Linux_for_Tegra directory"
	echo "eg	sudo rm -rf source_build/Linux_for_Tegra/"
	exit
fi

# Check if release source files need to be downloaded
release_downloaded=$(ls | grep "public_sources.tbz2")
if [ -z $release_downloaded ]; then
	echo "Downloading Jetson sources"
	wget https://developer.nvidia.com/downloads/embedded/l4t/r36_release_v3.0/sources/public_sources.tbz2
	echo "Extracting Jetson sources"
	tar -xjf public_sources.tbz2
	cd Linux_for_Tegra/source
	echo "Extracting kernel source"
	tar xf kernel_src.tbz2
	tar xf kernel_oot_modules_src.tbz2
	tar xf nvidia_kernel_display_driver_source.tbz2
fi

# Check if toolchain is installed
toolchain_installed=$(ls $HOME | grep "l4t-gcc")
if [ -z "$toolchain_installed" ]; then
	pushd . > /dev/null
	echo "Downloading and installing Jetson bootlin toolchain"
	mkdir $HOME/l4t-gcc
	cd $HOME/l4t-gcc
	# https://docs.nvidia.com/jetson/archives/r36.3/DeveloperGuide/AT/JetsonLinuxToolchain.html#at-jetsonlinuxtoolchain
	wget https://developer.nvidia.com/downloads/embedded/l4t/r36_release_v3.0/toolchain/aarch64--glibc--stable-2022.08-1.tar.bz2
	tar xf aarch64--glibc--stable-2022.08-1.tar.bz2
	popd > /dev/null
fi

echo "Finished"
