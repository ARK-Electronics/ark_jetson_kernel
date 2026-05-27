#!/bin/bash

# Configure the default user in a staged rootfs.
# Usage: configure_user.sh <path-to-Linux_for_Tegra>

L4T_DIR="${1:?Usage: configure_user.sh <path-to-Linux_for_Tegra>}"

USER="jetson"
PASSWORD="jetson"
HOST="jetson"

sudo "$L4T_DIR/tools/l4t_create_default_user.sh" -u $USER -p $PASSWORD -n $HOST -a --accept-license
