#!/bin/bash
BASHRC="$HOME/.bashrc"
START_TIME=$(date +%s)
SUDO_PASSWORD=

# sudo password for later
IFS= read -rsp "[sudo] password for $USER: " SUDO_PASSWORD
echo ""

echo "export ARK_JETSON_KERNEL_DIR=$PWD"
# Add to bashrc if necessary
exists=$(cat $BASHRC | grep ARK_JETSON_KERNEL_DIR)
if [ -z "$exists" ]; then
	echo "export ARK_JETSON_KERNEL_DIR=$PWD" >> $BASHRC
fi

pushd . > /dev/null
sudo -S rm -rf source_build <<< "$SUDO_PASSWORD"
mkdir -p source_build && cd source_build

echo "Downloading Jetson sources"
wget https://developer.nvidia.com/downloads/embedded/l4t/r36_release_v3.0/sources/public_sources.tbz2
echo "Extracting Jetson sources"
tar -xjf public_sources.tbz2
pushd . > /dev/null
cd Linux_for_Tegra/source
echo "Extracting kernel source"
tar xf kernel_src.tbz2
tar xf kernel_oot_modules_src.tbz2
tar xf nvidia_kernel_display_driver_source.tbz2

# Edit the defconfig file
echo "Editing kernel defconfig"
# Add the following to the end of the file
echo "CONFIG_USB_WDM=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_USB_NET_DRIVERS=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_USB_NET_QMI_WWAN=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_USB_SERIAL_QUALCOMM=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_WLAN=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_WLAN_VENDOR_INTEL=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_IWLWIFI=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_IWLWIFI_LEDS=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_IWLDVM=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_IWLMVM=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_IWLWIFI_OPMODE_MODULAR=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_ATH_COMMON=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_WLAN_VENDOR_ATH=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_NET_VENDOR_ATHEROS=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_ATH10K_CE=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_ATH10K_USB=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_CRYPTO_MICHAEL_MIC=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_ATH11K=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_ATH11K_PCI=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig

popd

pushd . > /dev/null
echo "Downloading and installing Jetson bootlin toolchain"
mkdir $HOME/l4t-gcc
cd $HOME/l4t-gcc
# https://docs.nvidia.com/jetson/archives/r36.3/DeveloperGuide/AT/JetsonLinuxToolchain.html#at-jetsonlinuxtoolchain
wget https://developer.nvidia.com/downloads/embedded/l4t/r36_release_v3.0/toolchain/aarch64--glibc--stable-2022.08-1.tar.bz2
tar xf aarch64--glibc--stable-2022.08-1.tar.bz2
popd > /dev/null

# Clone ARK device tree
echo "Downloading ARK device tree"
rm -rf ark_jetson_orin_nano_nx_device_tree
git clone -b ark_36.3.0 git@github.com:ARK-Electronics/ark_jetson_orin_nano_nx_device_tree.git
echo "Copying ARK device tree files"
cp -r ark_jetson_orin_nano_nx_device_tree/* Linux_for_Tegra/source/hardware/nvidia/t23x/nv-public/
popd

echo "Finished"
