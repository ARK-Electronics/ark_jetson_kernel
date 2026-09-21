#!/bin/bash
# Install the two UART boot markers on a running JAJ over SSH.
# Usage: bench/install_markers.sh [host]   (default 192.168.55.1; needs ssh-copy-id done)
set -e -o pipefail
HOST="${1:-192.168.55.1}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
scp -q "$DIR"/units/ark-boot-*.service "jetson@$HOST:/tmp/"
ssh "jetson@$HOST" 'sudo install -m 0644 /tmp/ark-boot-*.service /etc/systemd/system/ &&
    sudo systemctl daemon-reload &&
    sudo systemctl enable ark-boot-started.service ark-boot-reached.service &&
    rm -f /tmp/ark-boot-*.service'
echo "Markers installed on $HOST; they fire on the next boot."
