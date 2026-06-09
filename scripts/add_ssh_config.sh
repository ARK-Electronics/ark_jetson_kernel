#!/bin/bash
set -euo pipefail

# Add a convenience SSH entry for the Jetson to ~/.ssh/config, so `ssh jetson` works
# and reflashing doesn't trip the host-key-changed warning. It disables host-key
# checking — fine for a lab device on a trusted LAN, not for production hosts.
# Edits your dev machine's config (no sudo); safe to re-run.

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
