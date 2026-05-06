#!/bin/bash

# Usage: ./setup.sh [--force | -y]
#   --force, -y   Skip the confirmation prompt before deleting an existing
#                 prebuilt/ or source_build/ (intended for CI / scripted runs).

# Log output to file while keeping terminal output
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec > >(tee "$SCRIPT_DIR/setup.log.txt") 2>&1

# Pulls in EXPECTED_BSP_*, BSP_URL, ROOT_FS_URL, PUBLIC_SOURCES_URL,
# TOOLCHAIN_URL, and the detect_bsp_version / require_bsp helpers.
# bsp_version.env is the single source of truth — edit it to bump versions.
source "$SCRIPT_DIR/scripts/check_bsp.sh"

FORCE=0
for arg in "$@"; do
    case "$arg" in
        --force|-y)
            FORCE=1
            ;;
        *)
            echo "Unknown option: $arg"
            echo "Usage: ./setup.sh [--force | -y]"
            exit 1
            ;;
    esac
done

# Confirm before destroying an existing setup. Done before any sudo prompt so
# an aborting user never has to authenticate. Users may have hand-edited
# kernel sources under source_build/ and we don't want to silently nuke them.
if [ -d "$SCRIPT_DIR/prebuilt" ] || [ -d "$SCRIPT_DIR/source_build" ]; then
    detect_bsp_version "$SCRIPT_DIR"
    case $? in
        0)
            echo "Existing setup detected: BSP ${DETECTED_BSP_RELEASE}.${DETECTED_BSP_REVISION} (matches expected)."
            ;;
        2)
            echo "Existing setup detected: BSP ${DETECTED_BSP_RELEASE}.${DETECTED_BSP_REVISION}"
            echo "  This repo now requires:  BSP ${EXPECTED_BSP_RELEASE}.${EXPECTED_BSP_REVISION}"
            ;;
        1)
            echo "Existing prebuilt/ or source_build/ detected (incomplete or unrecognized BSP version)."
            ;;
    esac
    echo ""
    echo "Re-running setup will DELETE prebuilt/ and source_build/ and re-download ~5GB."
    echo "Any local edits in those directories will be lost."
    if [ $FORCE -eq 0 ]; then
        read -p "Continue? (y/N): " confirm
        if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
            echo "Setup aborted."
            exit 0
        fi
    else
        echo "(--force specified, proceeding without confirmation)"
    fi
fi

function cleanup() {
	kill -9 $SUDO_PID
	exit 0
}

function sudo_refresh_loop() {
	while true; do
		sudo -v
		sleep 60
	done
}

function download_with_retry() {
	local url=$1
	local retries=3
	local count=0
	local success=0

	while [ $count -lt $retries ]; do
		wget $url
		if [ $? -eq 0 ]; then
			success=1
			break
		else
			echo "Download failed. Retrying... ($((count+1))/$retries)"
			count=$((count+1))
			sleep 5
		fi
	done

	if [ $success -eq 0 ]; then
		echo "Failed to download $url after $retries attempts."
		exit 1
	fi
}

trap cleanup SIGINT SIGTERM

# keep sudo credentials alive in the background
sudo -v
sudo_refresh_loop &
SUDO_PID=$!
START_TIME=$(date +%s)

export ARK_JETSON_KERNEL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export L4T_RELEASE_PACKAGE=$(basename $BSP_URL)
export SAMPLE_FS_PACKAGE=$(basename $ROOT_FS_URL)
export BOARD="jetson-orin-nano-devkit-super"

# If a previous setup is present, summarize it and confirm before deletion —
# users may have hand-edited kernel sources under source_build/.
if [ -d "$SCRIPT_DIR/prebuilt" ] || [ -d "$SCRIPT_DIR/source_build" ]; then
    detect_bsp_version "$SCRIPT_DIR"
    case $? in
        0)
            echo "Existing setup detected: BSP ${DETECTED_BSP_RELEASE}.${DETECTED_BSP_REVISION} (matches expected)."
            ;;
        2)
            echo "Existing setup detected: BSP ${DETECTED_BSP_RELEASE}.${DETECTED_BSP_REVISION}"
            echo "  This repo now requires:  BSP ${EXPECTED_BSP_RELEASE}.${EXPECTED_BSP_REVISION}"
            ;;
        1)
            echo "Existing prebuilt/ or source_build/ detected (incomplete or unrecognized BSP version)."
            ;;
    esac
    echo ""
    echo "Re-running setup will DELETE prebuilt/ and source_build/ and re-download ~5GB."
    echo "Any local edits in those directories will be lost."
    if [ $FORCE -eq 0 ]; then
        read -p "Continue? (y/N): " confirm
        if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
            echo "Setup aborted."
            exit 0
        fi
    else
        echo "(--force specified, proceeding without confirmation)"
    fi
fi

# remove previous
sudo rm -rf source_build
sudo rm -rf prebuilt

pushd .
mkdir -p prebuilt
cd prebuilt

# https://developer.nvidia.com/embedded/jetson-linux-archive
echo "Downloading prebuilt BSP and root filesystem"
download_with_retry $BSP_URL
download_with_retry $ROOT_FS_URL

# https://docs.nvidia.com/jetson/archives/r36.3/DeveloperGuide/IN/QuickStart.html#to-flash-the-jetson-developer-kit-operating-software
echo "Untarring files, this may take some time"
tar xf $L4T_RELEASE_PACKAGE
sudo tar xpf $SAMPLE_FS_PACKAGE -C Linux_for_Tegra/rootfs/

echo "Satisfying prerequisites"
sudo Linux_for_Tegra/tools/l4t_flash_prerequisites.sh
# Fresh systems could be missing these packages
sudo apt-get install make build-essential flex bison libssl-dev -y

echo "Applying binaries"
sudo Linux_for_Tegra/apply_binaries.sh --debug

echo "Setting up login credentials for the Jetson"
sudo -E $ARK_JETSON_KERNEL_DIR/scripts/configure_user.sh

popd

##### Setup source build
pushd .
mkdir -p source_build
cd source_build

echo "Downloading Jetson sources"

download_with_retry "${PUBLIC_SOURCES_URL}"
echo "Extracting Jetson sources"
tar -xjf public_sources.tbz2
pushd .
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
echo "CONFIG_RTL8XXXU=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_RTL8XXXU_UNTESTED=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_RTL8192CE=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_RTL8192SE=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_RTL8192DE=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_RTL8723AE=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_RTL8192CU=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_RTL8188EE=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_RTL8192EE=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_RTL8723BE=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_RTL8180=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_RTL8187=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_RTW88=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_RTW89=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig

# MediaTek MT76x2U (MT7612U USB adapter)
echo "CONFIG_MT76x2U=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_MT76x2_COMMON=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_MT76x02_LIB=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_MT76x02_USB=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_MT76_USB=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_MT76_CORE=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_MT76_LEDS=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig

# Bluetooth
echo "CONFIG_BT_INTEL=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_BT_BCM=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig
echo "CONFIG_BT_RTL=y" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig

# KSZ8795 SPI ethernet switch (PAB_V3)
echo "CONFIG_MICREL_KS8995MA=m" >> kernel/kernel-jammy-src/arch/arm64/configs/defconfig

popd

pushd .
mkdir -p $HOME/l4t-gcc
cd $HOME/l4t-gcc

TOOLCHAIN_FILENAME=$(basename "$TOOLCHAIN_URL")
TOOLCHAIN_DIRNAME=${FILENAME%.bz2}

if [ ! -f "$TOOLCHAIN_FILENAME" ]; then
	echo "Downloading Jetson bootlin toolchain"

	download_with_retry $TOOLCHAIN_URL
else
	echo "Jetson bootlin toolchain archive already exists, skipping download"
fi

if [ ! -d "$TOOLCHAIN_DIRNAME" ]; then
	echo "Extracting Jetson bootlin toolchain"
	tar xf $TOOLCHAIN_FILENAME
else
	echo "Jetson bootlin toolchain already extracted, skipping extraction"
fi
popd

popd

END_TIME=$(date +%s)
TOTAL_TIME=$((${END_TIME}-${START_TIME}))
echo "Setup complete in $(date -d@${TOTAL_TIME} -u +%H:%M:%S)"
echo "You can now build the kernel with ./build_kernel.sh"

cleanup
