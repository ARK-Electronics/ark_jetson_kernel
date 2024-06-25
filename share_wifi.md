These notes provide instructions on how to share the wifi connection from your host PC to the jetson via RNDIS over micro USB.
You may need to change the interface names (enxf69cefb27da8 and wlo1) and subnet (192.168.55.0/24).

#### Desktop
```
sudo sysctl net.ipv4.ip_forward=1
sudo iptables -F FORWARD
sudo iptables -A FORWARD -i enxf69cefb27da8 -o wlo1 -j ACCEPT
sudo iptables -A FORWARD -i wlo1 -o enxf69cefb27da8 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
sudo iptables -A FORWARD -j LOG --log-prefix "IPTables-Dropped: "
sudo iptables -t nat -A POSTROUTING -o wlo1 -s 192.168.55.0/24 -j MASQUERADE
```

Reset the state on the desktop
```
sudo iptables -F
sudo iptables -t nat -F
sudo iptables -t mangle -F
sudo iptables -X
sudo sysctl net.ipv4.ip_forward=0
```

#### Jetson
```
echo "nameserver 8.8.8.8" | sudo tee /etc/resolv.conf > /dev/null
```

Test internet connection
```
ping 8.8.8.8
```

#### Setting up WiFi
Install the missing wifi module
```
sudo apt-get install -y backport-iwlwifi-dkms
```

Connect to your network
```
SSID="<your_ssid>"
PASSWORD="<your_password>"
sudo nmcli con add type wifi ifname '*' con-name "$SSID" autoconnect yes ssid "$SSID" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PASSWORD"
sudo nmcli con up $SSID
```