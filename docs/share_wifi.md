# Sharing WiFi over USB

`scripts/share_wifi.sh` shares your host PC's WiFi connection with the Jetson over the USB network link, so the Jetson gets internet access without a network connection of its own.

The Jetson's side is already set up. L4T's USB device mode gives the Jetson 192.168.55.1, hands the host 192.168.55.100 over DHCP, and adds a low-priority default route back through the host. The script sets up the host side: it enables IPv4 forwarding and uses NAT to masquerade the Jetson's traffic out through the WiFi interface.

## Start sharing

Boot the Jetson, connect it over USB, and run this on the host:

```
./scripts/share_wifi.sh
```

The script finds the USB link by itself, as the interface with an address in 192.168.55.0/24. It takes the WiFi interface to be the one whose name starts with `wl`. If it can't find the USB link, make sure the Jetson has finished booting and shows up in `ip addr`.

Then check the connection from the Jetson:

```
ping 8.8.8.8
```

The USB link does not carry DNS, so if `ping 8.8.8.8` works but hostnames don't resolve, point the Jetson at a nameserver:

```
sudo resolvectl dns l4tbr0 8.8.8.8
```

## Stop sharing

Run the stop script on the host. It flushes the iptables rules and turns IP forwarding off.

```
./scripts/stop_share_wifi.sh
```

## Caveats

- Both scripts edit the host's live iptables state. `share_wifi.sh` flushes the `FORWARD` chain, and `stop_share_wifi.sh` flushes all tables, so existing firewall rules such as those of ufw and docker are cleared. Add those rules back or restart those services afterwards if you rely on them.
- Nothing survives a host reboot, so run `share_wifi.sh` again after one.
- If the Jetson can join a WiFi network itself, that is simpler. See [Add WiFi](../README.md#3-add-wifi-optional) in the README.
