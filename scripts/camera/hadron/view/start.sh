#!/bin/sh
# Start the Hadron live view on the local display; works from the desktop launcher or ssh.
cd "$(dirname "$(readlink -f "$0")")" || exit 1
uid=$(id -u)
export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-/run/user/$uid/gdm/Xauthority}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/$uid/bus}"

# An image built from this tree loads the OV64B40 driver at boot. Otherwise load it here:
# a locally built nv_ov64b40.ko placed next to this script, or the installed module.
if ! grep -qs ov64b40 /sys/class/video4linux/*/name; then
	if [ -e nv_ov64b40.ko ]; then
		pkexec /usr/sbin/insmod "$PWD/nv_ov64b40.ko" || exit 1
	else
		pkexec /usr/sbin/modprobe nv_ov64b40 || exit 1
	fi
	sleep 2
fi

./stop.sh
# Wake a dimmed screen, then keep the session from idling while the view runs.
xdotool mousemove_relative -- 1 0 >/dev/null 2>&1
xdotool mousemove_relative -- -1 0 >/dev/null 2>&1
exec gnome-session-inhibit --inhibit idle --reason "Hadron live view" \
	python3 -X faulthandler -W ignore::DeprecationWarning hadron_view.py "$@" >view.log 2>&1
