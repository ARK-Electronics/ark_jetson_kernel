#!/bin/bash

sudo -v

if [ "$#" -ne 2 ]; then
    echo "Usage: $0 <SSID> <Password>"
    exit 1
fi

SSID="$1"
PASSWORD="$2"
TEMPLATE="ConnectionTemplate.nmconnection"
DEST_DIR="prebuilt/Linux_for_Tegra/rootfs/etc/NetworkManager/system-connections"
OUTPUT_FILE="${DEST_DIR}/${SSID}.nmconnection"

# Ensure the destination directory exists
sudo mkdir -p "$DEST_DIR"
sudo cp $TEMPLATE $OUTPUT_FILE

# TODO: replace all occurences of YourNetworkSSID with $SSID and YourNetworkPassword with $PASSWORD
sudo sed -i "s/YourNetworkSSID/$SSID/g; s/YourNetworkPassword/$PASSWORD/g" "$OUTPUT_FILE"

# Set the correct ownership and permissions for the new network connection file
sudo chown root:root "$OUTPUT_FILE"
sudo chmod 600 "$OUTPUT_FILE"

echo "Network configuration for SSID '$SSID' has been created at $OUTPUT_FILE."
