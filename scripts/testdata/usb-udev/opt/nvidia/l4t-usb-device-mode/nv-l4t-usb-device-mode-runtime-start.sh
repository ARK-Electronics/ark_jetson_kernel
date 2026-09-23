#!/bin/bash

# SPDX-FileCopyrightText: Copyright (c) 2017-2020 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

/sbin/ifconfig l4tbr0 up
# The interface might lose the address when down. Add it here to make sure
# it's configured.
/sbin/ifconfig l4tbr0 ${net_ip} netmask ${net_mask}
/sbin/ifconfig l4tbr0 add ${net_ipv6}

# Start a DHCP server so that connected systems automatically receive an IP
# address. This avoids users having to manually configure the connection, and
# also prevents Network Manager on Linux from destroying any manually applied
# configuration.
#
# The DHCP server must be started here, because it won't start if it's told to
# run on an interface that's down.
if [ -n "${net_dhcp_start}" ]; then
    dhcpd_conf="${script_dir}/dhcpd.conf"
    dhcpd_leases="/run/l4t-usb-devmode-dhcpd.leases"
    dhcpd_pid="/run/l4t-usb-devmode-dhcpd.pid"
    cat > "${dhcpd_conf}" <<ENDOFHERE
max-lease-time ${net_dhcp_lease_time};
default-lease-time ${net_dhcp_lease_time};

subnet ${net_net} netmask ${net_mask} {
    range ${net_dhcp_start} ${net_dhcp_end};
}
ENDOFHERE
    rm -f "${dhcpd_leases}"
    touch "${dhcpd_leases}"
    /usr/sbin/dhcpd \
        -cf "${dhcpd_conf}" \
        -pf "${dhcpd_pid}" \
        -lf "${dhcpd_leases}" \
        l4tbr0
fi

# When an interface is set to "down", all routes through it are lost, so we
# must restore any routes when setting the interface to "up".
if [ -n "${net_ipv4_defroute_router}" ]; then
    route add default gw ${net_ipv4_defroute_router} dev l4tbr0 \
        ${net_ipv4_defroute_metric:+metric ${net_ipv4_defroute_metric}}
fi

exit 0
