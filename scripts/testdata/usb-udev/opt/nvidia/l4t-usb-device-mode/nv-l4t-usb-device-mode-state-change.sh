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
. "${script_dir}/nv-l4t-usb-device-mode-config.sh"

# Is USB device mode configured?
# If not, don't do anything
if [ ! -d /sys/kernel/config/usb_gadget/l4t ]; then
    exit 0
fi

if [ -f /sys/class/usb_role/usb2-0-role-switch/role ]; then
    role="$(cat /sys/class/usb_role/usb2-0-role-switch/role)"
    if [ "${role}" = "device" ]; then
        is_device=1
    else
        is_device=0
    fi
else
    state="$(cat /sys/devices/virtual/android_usb/android0/state)"
    if [ "${state}" = "CONFIGURED" ]; then
        is_device=1
    else
        is_device=0
    fi
fi

if [ ${is_device} -eq 1 ]; then
    # Ideally, we would just run nv-l4t-usb-device-mode-runtime-start.sh right
    # here. However, we're running from a udev rule, which disallows raw
    # network packet access, so we can't launch dhcpd directly from this
    # process or a child of it.
    service nv-l4t-usb-device-mode-runtime start
else
    service nv-l4t-usb-device-mode-runtime stop
fi

exit 0
