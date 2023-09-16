#!/bin/bash
BASHRC="$HOME/.bashrc"
START_TIME=$(date +%s)
SUDO_PASSWORD=

# sudo password for later
IFS= read -rsp "[sudo] password for $USER: " SUDO_PASSWORD
echo ""

# Add to bashrc if necessary
exists=$(cat $BASHRC | grep ARK_JETSON_CORE_DIR)
if [ -z "$exists" ]; then
	echo "export ARK_JETSON_CORE_DIR=$PWD" >> $BASHRC
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
	# https://developer.nvidia.com/embedded/jetson-linux-archive
	wget https://developer.nvidia.com/downloads/embedded/l4t/r35_release_v3.1/sources/public_sources.tbz2/
	echo "Extracting Jetson sources"
	tar -xjf public_sources.tbz2
	cd Linux_for_Tegra/source/public
	echo "Extracting kernel source"
	tar -xjf kernel_src.tbz2
fi

# Check if toolchain is installed
toolchain_installed=$(ls $HOME | grep "l4t-gcc")
if [ -z "$toolchain_installed" ]; then
	pushd .
	echo "Downloading and installing Jetson bootlin toolchain"
	mkdir $HOME/l4t-gcc
	cd $HOME/l4t-gcc
	wget https://developer.nvidia.com/embedded/jetson-linux/bootlin-toolchain-gcc-93
	tar xf aarch64--glibc--stable-final.tar.gz
	echo "Adding environment variables to bashrc"
	echo "export CROSS_COMPILE_AARCH64_PATH=$HOME/l4t-gcc" >> $BASHRC
	echo "export CROSS_COMPILE_AARCH64=$HOME/l4t-gcc/bin/aarch64-buildroot-linux-gnu-" >> $BASHRC
	popd
fi

echo "Finished"
