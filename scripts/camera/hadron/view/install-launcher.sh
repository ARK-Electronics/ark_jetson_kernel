#!/bin/sh
# Put a "Hadron Live View" launcher on the desktop that starts the view from this directory.
dir=$(dirname "$(readlink -f "$0")")
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/$(id -u)/bus}"
launcher="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")/hadron-view.desktop"
cat >"$launcher" <<LAUNCHER || exit 1
[Desktop Entry]
Type=Application
Name=Hadron Live View
Comment=FLIR Hadron EO and IR cameras with the ICM-45686 frame timing (q, Esc or right click to quit)
Exec="$dir/start.sh"
Icon=camera-web
Terminal=false
Categories=AudioVideo;Video;
StartupNotify=false
LAUNCHER
chmod +x "$launcher"
# GNOME only runs a desktop launcher that is marked trusted.
gio set "$launcher" metadata::trusted true
