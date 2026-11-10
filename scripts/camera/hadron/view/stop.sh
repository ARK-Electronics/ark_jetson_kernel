#!/bin/sh
# Stop a running Hadron live view.
cd "$(dirname "$(readlink -f "$0")")" || exit 1
[ -f view.pid ] || exit 0
pid=$(cat view.pid)
# A crash or power cut leaves the file behind, and the PID may be another process by now.
if ! grep -qs hadron_view.py "/proc/$pid/cmdline"; then
	rm -f view.pid
	exit 0
fi
kill "$pid" 2>/dev/null
for _ in 1 2 3 4 5 6 7 8 9 10; do
	kill -0 "$pid" 2>/dev/null || exit 0
	sleep 0.3
done
kill -9 "$pid" 2>/dev/null
rm -f view.pid
