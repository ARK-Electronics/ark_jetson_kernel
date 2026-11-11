#!/bin/bash
# Start the Hadron demo on a Jetson from this PC and open its page in the browser.
# usage: demo.sh [user@host [server options]]
#   user@host       default jetson@192.168.55.1, the Jetson over its USB-C cable
#   server options  passed to hadron_demo.py, e.g. --imu-bus 7 on a carrier whose IMU bus is not i2c-1
host=${1:-jetson@192.168.55.1}
[ $# -gt 0 ] && shift
addr=${host#*@}
port=8642
dir=$(dirname "$(readlink -f "$0")")

# One connection for both steps, so a password is asked for once.
ctl=$(mktemp -u /tmp/hadron-demo-ssh.XXXXXX)
on_jetson() { ssh -o ControlMaster=auto -o ControlPath="$ctl" -o ControlPersist=20 "$@"; }
trap 'ssh -O exit -o ControlPath="$ctl" "$host" 2>/dev/null' EXIT

tar -C "$dir" --exclude=__pycache__ -c demo view/boson.py view/icm45686.py view/syncline.py |
	on_jetson "$host" 'mkdir -p hadron-demo && tar -x -m -C hadron-demo' || exit 1

# Open the page once the server answers.
(
	for _ in $(seq 60); do
		if (exec 3<>"/dev/tcp/$addr/$port") 2>/dev/null; then
			echo "Hadron demo: http://$addr:$port/  (Ctrl-C here stops it)"
			(xdg-open "http://$addr:$port/" || open "http://$addr:$port/") >/dev/null 2>&1
			exit 0
		fi
		sleep 0.5
	done
) &
opener=$!

# -t gives the server a terminal, so Ctrl-C here reaches it and it lets go of the cameras.
on_jetson -t "$host" "exec python3 hadron-demo/demo/hadron_demo.py $* >/dev/null"
kill "$opener" 2>/dev/null
