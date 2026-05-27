#!/bin/bash

sudo -v

if [ "$#" -lt 3 ]; then
    echo "Usage: $0 <TARGET> <SSID> <Password>"
    echo "  e.g. $0 PAB MyNetwork MyPassword"
    exit 1
fi

TARGET="$1"
SSID="$2"
PASSWORD="$3"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
TEMPLATE="$SCRIPT_DIR/ConnectionTemplate.nmconnection"
DEST_DIR="$ROOT_DIR/staging/$TARGET/Linux_for_Tegra/rootfs/etc/NetworkManager/system-connections"

if [ ! -d "$ROOT_DIR/staging/$TARGET/Linux_for_Tegra/rootfs" ]; then
    echo "ERROR: staging/$TARGET/ not found. Run ./build.sh $TARGET first."
    exit 1
fi

OUTPUT_FILE="${DEST_DIR}/${SSID}.nmconnection"

sudo mkdir -p "${DEST_DIR}"
sudo cp $TEMPLATE "${OUTPUT_FILE}"

sudo sed -i "s/YourNetworkSSID/${SSID}/g; s/YourNetworkPassword/${PASSWORD}/g" "${OUTPUT_FILE}"

sudo chown root:root "${OUTPUT_FILE}"
sudo chmod 600 "${OUTPUT_FILE}"

echo "Network configuration for SSID '${SSID}' has been created at ${OUTPUT_FILE}."
