#!/bin/bash
sudo true

ETH_IFACE=$(ip addr show | awk '/inet / && /192.168.55./ {print $NF; exit}')
WIFI_IFACE=$(ip link | awk -F: '/^ *[0-9]+: wl/ {print $2}' | tr -d ' ')
JETSON_RNDIS_IP=192.168.55.0/24

# If there are multiple ethernet interfaces, it will only take the first one with a matching IP range
if [[ -z "$ETH_IFACE" ]]; then
    echo "No Ethernet interface with IP in the 192.168.55.0/24 range found."
    exit 1
fi

sudo sysctl net.ipv4.ip_forward=1
sudo iptables -F FORWARD
sudo iptables -A FORWARD -i $ETH_IFACE -o $WIFI_IFACE -j ACCEPT
sudo iptables -A FORWARD -i $WIFI_IFACE -o $ETH_IFACE -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
sudo iptables -A FORWARD -j LOG --log-prefix "IPTables-Dropped: "
sudo iptables -t nat -A POSTROUTING -o $WIFI_IFACE -s $JETSON_RNDIS_IP -j MASQUERADE
