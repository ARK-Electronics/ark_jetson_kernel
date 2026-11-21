#!/bin/bash

# SPDX-FileCopyrightText: Copyright (c) 2019-2020 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice, this
# list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
# this list of conditions and the following disclaimer in the documentation
# and/or other materials provided with the distribution.
#
# 3. Neither the name of the copyright holder nor the names of its
# contributors may be used to endorse or promote products derived from
# this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

script_dir="$(cd "$(dirname "$0")" && pwd)"

service serial-getty@ttyGS0 stop

cd /sys/kernel/config/usb_gadget
echo "" > l4t/UDC
rmdir l4t/configs/c.1/strings/0x409
rm -f l4t/configs/c.1/ncm.usb0
rmdir l4t/functions/ncm.usb0/
rm -f l4t/configs/c.1/ecm.usb0
rmdir l4t/functions/ecm.usb0/
rm -f l4t/configs/c.1/mass_storage.0
rmdir l4t/functions/mass_storage.0/
rm -f l4t/configs/c.1/acm.GS0
rmdir l4t/functions/acm.GS0/
rm -f l4t/configs/c.1/rndis.usb0
rmdir l4t/functions/rndis.usb0/
rm -f l4t/os_desc/c.1
rmdir l4t/configs/c.1/
rmdir l4t/strings/0x409
rmdir l4t
/sbin/ifconfig l4tbr0 down
/sbin/brctl delbr l4tbr0

# Stop this once the USB configuration is gone; this guarantees that
# -state-change.sh can't race with this and restart the service.
service nv-l4t-usb-device-mode-runtime stop

fs_img="${script_dir}/filesystem.img"
loop_devs=$(losetup | grep "${fs_img}" | awk '{print $1}')
if [ -n "${loop_devs}" ]; then
    for loop_dev in "${loop_devs}"; do
        /sbin/losetup -d "${loop_dev}"
    done
fi

exit 0
