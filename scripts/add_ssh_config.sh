#!/bin/bash
set -euo pipefail

# Add a convenience SSH host entry for the Jetson to your ~/.ssh/config.
#
# After running this you can connect with `ssh jetson`, and reflashing the
# Jetson no longer trips the "REMOTE HOST IDENTIFICATION HAS CHANGED" warning
# -- the entry skips host-key pinning, which is fine for a lab device on a
# trusted LAN (don't use it for production / over-the-internet hosts).
#
# This edits the current user's config on your dev machine (not the Jetson);
# no sudo required. Safe to re-run -- it won't add the block twice.

SSH_DIR="$HOME/.ssh"
CONFIG="$SSH_DIR/config"
MARKER_BEGIN="# >>> ark jetson >>>"
MARKER_END="# <<< ark jetson <<<"

mkdir -p "$SSH_DIR"
chmod 700 "$SSH_DIR"
touch "$CONFIG"
chmod 600 "$CONFIG"

if grep -qF "$MARKER_BEGIN" "$CONFIG"; then
    echo "Jetson entry already present in $CONFIG -- nothing to do."
    exit 0
fi

if grep -qiE '^[[:space:]]*Host[[:space:]].*\<jetson\>' "$CONFIG"; then
    echo "A 'Host jetson' entry already exists in $CONFIG; leaving it untouched."
    exit 0
fi

cat >> "$CONFIG" <<EOF

$MARKER_BEGIN
Host jetson jetson.local
    HostName jetson.local
    User jetson
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
    LogLevel ERROR
$MARKER_END
EOF

echo "Added Jetson host to $CONFIG. Connect with: ssh jetson"
