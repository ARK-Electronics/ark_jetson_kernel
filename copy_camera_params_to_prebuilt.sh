#!/bin/bash

DOWNLOAD_URL="https://files.waveshare.com/upload/e/eb/Camera_overrides.tar.gz"
TAR_FILE="Camera_overrides.tar.gz"
ISP_FILE="camera_overrides.isp"
DEST_DIR="$ARK_JETSON_KERNEL_DIR/prebuilt/Linux_for_Tegra/rootfs/var/nvidia/nvcam/settings"

if [ -f "$DEST_DIR/$ISP_FILE" ]; then
    echo "Camera overrides file already exists at $DEST_DIR/$ISP_FILE"
    exit 0
fi

echo "Camera overrides file not found in $DEST_DIR. Downloading and installing..."

echo "Downloading $TAR_FILE..."
wget "$DOWNLOAD_URL"

echo "Extracting $TAR_FILE..."
tar zxvf "$TAR_FILE"

sudo chmod 664 $ISP_FILE
sudo chown root:root $ISP_FILE

echo "Copying $ISP_FILE to $DEST_DIR..."
sudo mv "$ISP_FILE" "$DEST_DIR/"

echo "Installation completed successfully!"

rm $TAR_FILE
