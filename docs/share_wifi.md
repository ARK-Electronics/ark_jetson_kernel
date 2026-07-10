# Sharing WiFi over USB

`scripts/share_wifi.sh` shares your host PC's WiFi connection with the Jetson over the micro-USB (RNDIS) link, so the Jetson gets internet access without a network connection of its own.

The Jetson's side is already set up out of the box: L4T's USB device mode assigns the Jetson 192.168.55.1, hands the host 192.168.55.100 over DHCP, and adds a low-priority default route back through the host. The script fills in the host side by enabling IPv4 forwarding and NAT-masquerading the Jetson's traffic out through the WiFi interface.

## Start sharing

With the Jetson booted and connected over USB, run on the host:

```
./scripts/share_wifi.sh
```

The script auto-detects the USB link (the interface with an address in 192.168.55.0/24) and the WiFi interface (name starting with `wl`). If it can't find the USB link, make sure the Jetson has finished booting and shows up in `ip addr`.

Verify from the Jetson:

```
ping 8.8.8.8
```

The USB link does not carry DNS, so if `ping 8.8.8.8` works but hostnames don't resolve, point the Jetson at a nameserver:

```
sudo resolvectl dns l4tbr0 8.8.8.8
```

## Stop sharing

```
./scripts/stop_share_wifi.sh
```

This flushes the iptables rules and disables IP forwarding.

## Caveats

- Both scripts edit the host's live iptables state: `share_wifi.sh` flushes the `FORWARD` chain and `stop_share_wifi.sh` flushes all tables, so existing firewall rules (ufw, docker) are cleared. Re-add or restart those services afterwards if you rely on them.
- Nothing persists across a host reboot — rerun `share_wifi.sh` as needed.
- If the Jetson can join a WiFi network directly, that's simpler: see [Add WiFi](../README.md#3-add-wifi-optional) in the README.
