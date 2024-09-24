These notes provide instructions on how to share the wifi connection from your host PC to the jetson via RNDIS over micro USB.
You may need to change the interface names (enxf69cefb27da8 and wlo1) and subnet (192.168.55.0/24).

#### Desktop
```
./share_wifi.sh
```

Reset the state on the desktop
```
./stop_share_wifi.sh
```

#### Jetson
Test internet connection
```
ping 8.8.8.8
```

Connect to your WiFi network if available
```
SSID="<your_ssid>"
PASSWORD="<your_password>"
sudo nmcli con add type wifi ifname '*' con-name "$SSID" autoconnect yes ssid "$SSID" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PASSWORD"
sudo nmcli con up $SSID
```
