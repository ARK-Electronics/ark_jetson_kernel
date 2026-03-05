#!/bin/bash
sudo iptables -F
sudo iptables -t nat -F
sudo iptables -t mangle -F
sudo iptables -X
sudo sysctl net.ipv4.ip_forward=0
