#!/bin/bash
# I2S connector GPIO loopback test for ARK Jetson carriers.
#
# Drives HDR40 pin 40 (I2S0_DOUT, PI.00) and reads it back on
# HDR40 pin 38 (I2S0_DIN, PI.01). Connect pin 40 to pin 38 with
# a jumper wire before running.
#
# Requires the ark_i2s_gpio overlay to be applied:
#   sudo /opt/nvidia/jetson-io/config-by-hardware.py -n "ARK I2S to GPIO"
#   sudo reboot
#
# Usage:
#   sudo ./test_i2s_gpio_loopback.sh             # full loopback test
#   sudo ./test_i2s_gpio_loopback.sh --read-only # just poll DIN (no jumper)

set -u

DOUT_NAME=PI.00   # HDR40 pin 40, I2S0_DOUT, soc_gpio42_pi0
DIN_NAME=PI.01    # HDR40 pin 38, I2S0_DIN,  soc_gpio43_pi1

resolve() {
	# gpiofind prints "<chip> <offset>"; fail loudly if the line isn't there.
	local out
	if ! out=$(gpiofind "$1"); then
		echo "ERROR: line $1 not found on any gpiochip." >&2
		echo "Apply the overlay and reboot:" >&2
		echo "  sudo /opt/nvidia/jetson-io/config-by-hardware.py -n 'ARK I2S to GPIO'" >&2
		echo "  sudo reboot" >&2
		exit 1
	fi
	echo "$out"
}

DOUT=$(resolve "$DOUT_NAME")   # e.g. "gpiochip0 42"
DIN=$(resolve "$DIN_NAME")     # e.g. "gpiochip0 43"

echo "Resolved $DOUT_NAME -> $DOUT"
echo "Resolved $DIN_NAME  -> $DIN"
echo

# Flip DIN to input first so the BCT-default output-low doesn't fight DOUT
# through the jumper when DOUT is driven high. The direction sticks until
# something else claims the line.
gpioget $DIN >/dev/null

read_only_loop() {
	echo "Polling $DIN_NAME every 0.5s. Pull HDR40 pin 38 to GND or 3V3"
	echo "via a 1.5k resistor to verify input reads change. Ctrl-C to stop."
	echo
	while true; do
		printf '%s  %s=%s\n' "$(date +%H:%M:%S)" "$DIN_NAME" "$(gpioget $DIN)"
		sleep 0.5
	done
}

drive_and_read() {
	local level=$1 label=$2

	gpioset --mode=signal $DOUT=$level &
	local pid=$!
	sleep 0.2

	local got
	got=$(gpioget $DIN)

	kill "$pid" 2>/dev/null || true
	wait "$pid" 2>/dev/null || true

	printf 'DOUT=%s (%-4s)  DIN=%s  ' "$level" "$label" "$got"
	if [[ "$got" == "$level" ]]; then
		echo PASS
		return 0
	else
		echo "FAIL (expected $level)"
		return 1
	fi
}

if [[ "${1:-}" == "--read-only" ]]; then
	read_only_loop
	exit 0
fi

echo "Loopback test: HDR40 pin 40 ($DOUT_NAME) -> HDR40 pin 38 ($DIN_NAME)"
echo "Verify pin 40 and pin 38 are connected with a jumper wire."
echo

fail=0
drive_and_read 0 LOW  || fail=1
drive_and_read 1 HIGH || fail=1
drive_and_read 0 LOW  || fail=1
drive_and_read 1 HIGH || fail=1

echo
if [[ $fail -eq 0 ]]; then
	echo "All loopback transitions PASS."
else
	echo "One or more transitions FAILED. Check the jumper between pin 40 and pin 38."
	exit 1
fi
