#!/bin/bash

USER="jetson"
PASSWORD="jetson"
HOST="jetson"

sudo $ARK_JETSON_KERNEL_DIR/prebuilt/Linux_for_Tegra/tools/l4t_create_default_user.sh -u $USER -p $PASSWORD -n $HOST -a --accept-license
