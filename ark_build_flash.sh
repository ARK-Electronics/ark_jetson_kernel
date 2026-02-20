#!/bin/bash
############################################################################
#
# Copyright (c) 2026 Applied Aeronautics, Inc. All rights reserved.
#
# Author: Ryan Johnston <ryan@appliedaeronautics.com>
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in
#    the documentation and/or other materials provided with the
#    distribution.
# 3. Neither the name of Applied Aeronautics nor the names of its
#    contributors may be used to endorse or promote products derived
#    from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS
# OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED
# AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#
############################################################################
#
# ARK Jetson Orin Build & Flash Script
#
# Usage: Place this script in the ark_jetson_kernel repo root and run it.
#        It replaces running build_kernel.sh + flash.sh separately.
#
############################################################################

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$SCRIPT_DIR/ark_build_flash.log.txt"
touch "$LOG_FILE" 2>/dev/null || LOG_FILE="/tmp/ark_build_flash.log.txt"
exec > >(tee "$LOG_FILE") 2>&1

echo "============================================="
echo "  ARK Jetson Orin Build & Flash"
echo "============================================="
echo ""

# ── Storage target ────────────────────────────────────────────────────────────
echo "Select storage target:"
echo "  1) SD card  (mmcblk0p1) "
echo "  2) NVMe     (nvme0n1p1)"
echo ""
read -p "Enter choice (1/2): " storage_choice

case $storage_choice in
    1)
        STORAGE_DEV="mmcblk0p1"
        STORAGE_LABEL="SD card"
        USE_INITRD=false
        ;;
    2)
        STORAGE_DEV="nvme0n1p1"
        STORAGE_LABEL="NVMe"
        USE_INITRD=true
        ;;
    *)
        echo "Invalid choice. Exiting."
        exit 1
        ;;
esac

# ── Module variant ────────────────────────────────────────────────────────────
echo ""
echo "Select Orin module variant:"
echo "  1) Orin Nano / NX Super  (p3767-0005 / -super)"
echo "  2) Orin Nano / NX        (p3767-0000 to 0004)"
echo ""
read -p "Enter choice (1/2): " module_choice

case $module_choice in
    1)
        FLASH_TARGET="jetson-orin-nano-devkit-super"
        ;;
    2)
        FLASH_TARGET="jetson-orin-nano-devkit"
        ;;
    *)
        echo "Invalid choice. Exiting."
        exit 1
        ;;
esac

# ── WiFi (optional) ───────────────────────────────────────────────────────────
echo ""
echo "Configure WiFi? (credentials will be baked into the image)"
read -p "Enter choice (y/N): " wifi_choice

WIFI_SSID=""
WIFI_PASSWORD=""
if [ "$wifi_choice" = "y" ] || [ "$wifi_choice" = "Y" ]; then
    read -p "WiFi network name (SSID): " WIFI_SSID
    read -s -p "WiFi password: " WIFI_PASSWORD
    echo ""
fi

echo ""
echo "============================================="
echo "  Storage : $STORAGE_LABEL ($STORAGE_DEV)"
echo "  Target  : $FLASH_TARGET"
if [ -n "$WIFI_SSID" ]; then
    echo "  WiFi    : $WIFI_SSID"
fi
echo "============================================="
echo ""
read -p "Confirm and continue? (y/N): " confirm
if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
    echo "Aborted."
    exit 0
fi

sudo -v

# ── Step 1: Build ─────────────────────────────────────────────────────────────
echo ""
echo "============================================="
echo "  Step 1/3: Build kernel + DTBs"
echo "  (build_kernel.sh will ask you to select"
echo "   the platform: PAB / JAJ / PAB_V3)"
echo "============================================="
echo ""

"$SCRIPT_DIR/build_kernel.sh"
if [ $? -ne 0 ]; then
    echo ""
    echo "ERROR: build_kernel.sh failed. Exiting."
    exit 1
fi

# Read the target that was built so we know which BCT dir to use
LAST_TARGET_FILE="$SCRIPT_DIR/source_build/LAST_BUILT_TARGET"
if [ ! -f "$LAST_TARGET_FILE" ]; then
    echo "ERROR: Cannot determine built target (LAST_BUILT_TARGET not found)."
    exit 1
fi
BUILT_TARGET=$(cat "$LAST_TARGET_FILE")

case $BUILT_TARGET in
    PAB)     DT_SOURCE="ark_pab"     ;;
    JAJ)     DT_SOURCE="ark_jaj"     ;;
    PAB_V3)  DT_SOURCE="ark_pab_v3"  ;;
    *)
        echo "ERROR: Unknown built target '$BUILT_TARGET'."
        exit 1
        ;;
esac

# ── Step 2: Copy ARK BCT files ──────────────────────────────────
echo ""
echo "============================================="
echo "  Step 2/3: Copying ARK BCT files"
echo "  (fixes missing step in build_kernel.sh)"
echo "============================================="
echo ""

BCT_SRC="$SCRIPT_DIR/device_tree/$DT_SOURCE/Linux_for_Tegra/bootloader"
BCT_DST="$SCRIPT_DIR/prebuilt/Linux_for_Tegra/bootloader"

echo "Source : $BCT_SRC"
echo "Dest   : $BCT_DST"
echo ""

# GPIO BCT
echo "Copying tegra234-mb1-bct-gpio-p3767-dp-a03.dtsi ..."
sudo cp "$BCT_SRC/tegra234-mb1-bct-gpio-p3767-dp-a03.dtsi" \
        "$BCT_DST/tegra234-mb1-bct-gpio-p3767-dp-a03.dtsi"
if [ $? -ne 0 ]; then echo "ERROR: Failed to copy GPIO BCT file."; exit 1; fi

# Pinmux BCT
echo "Copying tegra234-mb1-bct-pinmux-p3767-dp-a03.dtsi ..."
sudo cp "$BCT_SRC/generic/BCT/tegra234-mb1-bct-pinmux-p3767-dp-a03.dtsi" \
        "$BCT_DST/generic/BCT/tegra234-mb1-bct-pinmux-p3767-dp-a03.dtsi"
if [ $? -ne 0 ]; then echo "ERROR: Failed to copy pinmux BCT file."; exit 1; fi

# Padvoltage BCT
echo "Copying tegra234-mb1-bct-padvoltage-p3767-dp-a03.dtsi ..."
sudo cp "$BCT_SRC/generic/BCT/tegra234-mb1-bct-padvoltage-p3767-dp-a03.dtsi" \
        "$BCT_DST/generic/BCT/tegra234-mb1-bct-padvoltage-p3767-dp-a03.dtsi"
if [ $? -ne 0 ]; then echo "ERROR: Failed to copy padvoltage BCT file."; exit 1; fi

# Verify
echo "Verifying..."
sudo diff "$BCT_SRC/tegra234-mb1-bct-gpio-p3767-dp-a03.dtsi" \
          "$BCT_DST/tegra234-mb1-bct-gpio-p3767-dp-a03.dtsi" > /dev/null || \
          { echo "ERROR: GPIO BCT verification failed!"; exit 1; }

sudo diff "$BCT_SRC/generic/BCT/tegra234-mb1-bct-pinmux-p3767-dp-a03.dtsi" \
          "$BCT_DST/generic/BCT/tegra234-mb1-bct-pinmux-p3767-dp-a03.dtsi" > /dev/null || \
          { echo "ERROR: Pinmux BCT verification failed!"; exit 1; }

sudo diff "$BCT_SRC/generic/BCT/tegra234-mb1-bct-padvoltage-p3767-dp-a03.dtsi" \
          "$BCT_DST/generic/BCT/tegra234-mb1-bct-padvoltage-p3767-dp-a03.dtsi" > /dev/null || \
          { echo "ERROR: Padvoltage BCT verification failed!"; exit 1; }

echo "BCT files OK. Checksums:"
sudo sha256sum \
    "$BCT_DST/tegra234-mb1-bct-gpio-p3767-dp-a03.dtsi" \
    "$BCT_DST/generic/BCT/tegra234-mb1-bct-pinmux-p3767-dp-a03.dtsi" \
    "$BCT_DST/generic/BCT/tegra234-mb1-bct-padvoltage-p3767-dp-a03.dtsi"

# ── Step 2.5: Configure WiFi (optional) ───────────────────────────────────────
if [ -n "$WIFI_SSID" ]; then
    echo ""
    echo "Configuring WiFi..."
    "$SCRIPT_DIR/add_wifi_network.sh" "$WIFI_SSID" "$WIFI_PASSWORD"
    if [ $? -ne 0 ]; then
        echo "WARNING: WiFi configuration failed. Continuing with flash..."
    else
        echo "WiFi configured OK."
    fi
fi

# ── Step 3: Flash ──────────────────────────────────────────────────────────────
echo ""
echo "============================================="
echo "  Step 3/3: Flash to $STORAGE_LABEL"
echo "============================================="
echo ""

# Check if board is already in recovery mode
check_recovery() {
    lsusb | grep -qi "0955:"
}

if check_recovery; then
    echo "Board already detected in recovery mode (NVIDIA APX)."
else
    echo "Board not detected in recovery mode."
    echo ""
    echo "Put the board into recovery mode:"
    echo "  - Short the recovery pins and press reset, OR"
    echo "  - Hold the Force Recovery button and press Reset"
    echo "  - Or via SSH: sudo reboot --force forced-recovery"
    echo ""
    echo "Waiting for board to enter recovery mode..."
    until check_recovery; do
        sleep 2
        printf "."
    done
    echo ""
    echo "Board detected in recovery mode."
fi

echo ""

if [ "$USE_INITRD" = true ]; then
    # NVMe: use initrd flash
    # Note: may have USB stability issues on Ubuntu 24.04 but did work.
    cd "$SCRIPT_DIR/prebuilt/Linux_for_Tegra"
    sudo ./tools/kernel_flash/l4t_initrd_flash.sh \
        --external-device nvme0n1p1 \
        -p "-c ./bootloader/generic/cfg/flash_t234_qspi.xml" \
        -c ./tools/kernel_flash/flash_l4t_t234_nvme.xml \
        --erase-all --showlogs --network usb0 \
        "$FLASH_TARGET" nvme0n1p1
    FLASH_RESULT=$?
else
    # SD card: use traditional flash (more reliable, no USB networking needed)
    cd "$SCRIPT_DIR/prebuilt/Linux_for_Tegra"
    sudo ./flash.sh "$FLASH_TARGET" "$STORAGE_DEV"
    FLASH_RESULT=$?
fi

cd "$SCRIPT_DIR"

echo ""
if [ $FLASH_RESULT -eq 0 ]; then
    echo "============================================="
    echo "  SUCCESS"
    echo "  Platform : $BUILT_TARGET"
    echo "  Module   : $FLASH_TARGET"
    echo "  Storage  : $STORAGE_LABEL ($STORAGE_DEV)"
    echo "  Log      : $LOG_FILE"
    echo "============================================="
else
    echo "============================================="
    echo "  ERROR: Flash failed."
    echo "  Check $LOG_FILE for details."
    echo "============================================="
    exit 1
fi