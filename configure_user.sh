#!/bin/bash

USER="jetson"
PASSWORD="jetson"
HOST="jetson"

cd prebuilt/Linux_for_Tegra/
sudo tools/l4t_create_default_user.sh -u $USER -p $PASSWORD -n $HOST -a --accept-license