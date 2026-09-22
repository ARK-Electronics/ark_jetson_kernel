#!/usr/bin/env python3
"""Bench helper for a Jetson carrier in the loop: Rigol DP712 power, debug UART, USB and SSH.

    jetson_hil.py ports                                  # what is attached: serial devices, Jetson USB state, gadget NICs
    jetson_hil.py ports --identify                       # also ask each FTDI cable *IDN? to find the DP712
    jetson_hil.py psu PORT status                        # identity, setpoints, measured V/A, output state
    jetson_hil.py psu PORT set --volts 16 --amps 3       # setpoints only; the output state is left alone
    jetson_hil.py psu PORT on|off
    jetson_hil.py console PORT --seconds 60 [--until REGEX] [--log FILE] [--cycle-psu PSU_PORT]
    jetson_hil.py wait-ssh [--host 192.168.55.1] [--timeout 180]

--cycle-psu turns the output off, waits --off-seconds, turns it on and captures from that instant, so
the timestamps are time since OUTP ON. Needs pyserial.
"""
import argparse
import glob
import os
import re
import subprocess
import sys
import time

import serial

# Recovery-mode (APX) product IDs by module; 0x7020 is the booted L4T USB gadget.
NVIDIA_VID = "0955"
USB_STATES = {"7323": "recovery: Orin NX 16GB", "7423": "recovery: Orin NX 8GB", "7523": "recovery: Orin Nano 8GB",
              "7623": "recovery: Orin Nano 4GB", "7020": "booted: L4T USB gadget"}
FTDI_VID = "0403"

# The DP712 drops the first query after its port opens (DTR resets the FT232) and garbles commands sent
# back to back, so every exchange waits a beat and every query is re-asked.
PSU_BAUD = 9600
PSU_OPEN_SETTLE = 0.2
PSU_COMMAND_GAP = 0.1
PSU_QUERY_ATTEMPTS = 3

SSH = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
       "-o", "ConnectTimeout=3", "-o", "LogLevel=ERROR"]


def sysfs_usb_attr(device_dir, name):
    try:
        with open(os.path.join(device_dir, name)) as f:
            return f.read().strip()
    except OSError:
        return ""


def usb_device_of(path):
    """Walk up from a sysfs node to the USB device that owns it."""
    path = os.path.realpath(path)
    while path != "/":
        if os.path.exists(os.path.join(path, "idVendor")):
            return path
        path = os.path.dirname(path)
    return None


def serial_ports():
    ports = []
    for link in sorted(glob.glob("/dev/serial/by-id/*")):
        tty = os.path.realpath(link)
        device = usb_device_of(f"/sys/class/tty/{os.path.basename(tty)}/device")
        vid = sysfs_usb_attr(device, "idVendor") if device else ""
        pid = sysfs_usb_attr(device, "idProduct") if device else ""
        ports.append((link, tty, vid, pid))
    return ports


def jetson_usb_devices():
    found = []
    for device in glob.glob("/sys/bus/usb/devices/*"):
        if sysfs_usb_attr(device, "idVendor") == NVIDIA_VID:
            pid = sysfs_usb_attr(device, "idProduct")
            found.append((os.path.basename(device), pid, USB_STATES.get(pid, "unknown NVIDIA device")))
    return found


def gadget_nics():
    nics = []
    for net in glob.glob("/sys/class/net/*"):
        device = usb_device_of(os.path.join(net, "device")) if os.path.exists(os.path.join(net, "device")) else None
        if device and sysfs_usb_attr(device, "idVendor") == NVIDIA_VID:
            name = os.path.basename(net)
            out = subprocess.run(["ip", "-4", "-br", "addr", "show", name], capture_output=True, text=True).stdout.split()
            nics.append((name, " ".join(out[2:]) or "no IPv4"))
    return nics


class Psu:
    def __init__(self, port):
        self.dev = serial.Serial(port, PSU_BAUD, timeout=1)
        time.sleep(PSU_OPEN_SETTLE)
        self.dev.reset_input_buffer()

    def write(self, command):
        self.dev.write((command + "\n").encode())
        self.dev.flush()
        time.sleep(PSU_COMMAND_GAP)

    def query(self, command):
        for _ in range(PSU_QUERY_ATTEMPTS):
            self.dev.reset_input_buffer()
            self.write(command)
            reply = self.dev.readline().decode(errors="replace").strip()
            if reply:
                return reply
        raise RuntimeError(f"no reply to {command!r}")

    def output(self, on):
        want = "ON" if on else "OFF"
        for _ in range(PSU_QUERY_ATTEMPTS):
            self.write(f":OUTP CH1,{want}")
            if self.query(":OUTP? CH1") in (want, "1" if on else "0"):
                return
        raise RuntimeError(f"output did not read back {want}")

    def close(self):
        self.dev.close()


def cmd_ports(args):
    print("serial devices:")
    for link, tty, vid, pid in serial_ports():
        line = f"  {tty:14s} {vid}:{pid}  {os.path.basename(link)}"
        if args.identify and vid == FTDI_VID:
            try:
                psu = Psu(tty)
                line += f"  -> {psu.query('*IDN?')}"
                psu.close()
            except (RuntimeError, serial.SerialException) as error:
                line += f"  -> no *IDN? reply ({error}); likely a console"
        print(line)
    print("NVIDIA USB devices:")
    for bus_id, pid, state in jetson_usb_devices() or [("", "", "none")]:
        print(f"  {bus_id} {NVIDIA_VID}:{pid} {state}" if pid else "  none")
    print("gadget NICs:")
    for name, addr in gadget_nics() or [("none", "")]:
        print(f"  {name} {addr}")


def cmd_psu(args):
    psu = Psu(args.port)
    try:
        if args.action == "set":
            psu.write(f":SOUR:VOLT {args.volts:.3f}")
            psu.write(f":SOUR:CURR {args.amps:.3f}")
        elif args.action in ("on", "off"):
            psu.output(args.action == "on")
        print(f"identity {psu.query('*IDN?')}")
        print(f"setpoint {psu.query(':SOUR:VOLT?')} V  {psu.query(':SOUR:CURR?')} A")
        print(f"measured {psu.query(':MEAS:VOLT? CH1')} V  {psu.query(':MEAS:CURR? CH1')} A")
        print(f"output   {psu.query(':OUTP? CH1')}")
    finally:
        psu.close()


def cmd_console(args):
    until = re.compile(args.until) if args.until else None
    uart = serial.Serial(args.port, args.baud, timeout=0.2)
    log = open(args.log, "a") if args.log else None
    if args.cycle_psu:
        psu = Psu(args.cycle_psu)
        psu.output(False)
        time.sleep(args.off_seconds)
        uart.reset_input_buffer()
        psu.output(True)
        psu.close()
    t0 = time.monotonic()
    if args.send:
        uart.write((args.send + "\n").encode())
    matched = False
    buffer = b""
    while time.monotonic() - t0 < args.seconds:
        buffer += uart.read(4096)
        while b"\n" in buffer:
            raw, buffer = buffer.split(b"\n", 1)
            line = f"[{time.monotonic() - t0:8.3f}] {raw.decode(errors='replace').rstrip()}"
            print(line, flush=True)
            if log:
                log.write(line + "\n")
            if until and until.search(line):
                matched = True
                break
        if matched:
            break
    uart.close()
    if log:
        log.close()
    if until and not matched:
        print(f"timeout: {args.until!r} not seen in {args.seconds} s", file=sys.stderr)
        return 1
    return 0


def cmd_wait_ssh(args):
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        if subprocess.run(SSH + [f"{args.user}@{args.host}", "true"], capture_output=True).returncode == 0:
            print(f"ssh {args.user}@{args.host} up after {args.timeout - (deadline - time.monotonic()):.0f} s")
            return 0
        time.sleep(2)
    print(f"ssh {args.user}@{args.host} not reachable in {args.timeout} s", file=sys.stderr)
    return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    ports = sub.add_parser("ports")
    ports.add_argument("--identify", action="store_true", help="send *IDN? to each FTDI cable (types into a console)")
    ports.set_defaults(func=cmd_ports)

    psu = sub.add_parser("psu")
    psu.add_argument("port")
    psu.add_argument("action", choices=["status", "set", "on", "off"])
    psu.add_argument("--volts", type=float)
    psu.add_argument("--amps", type=float)
    psu.set_defaults(func=cmd_psu)

    console = sub.add_parser("console")
    console.add_argument("port")
    console.add_argument("--baud", type=int, default=115200)
    console.add_argument("--seconds", type=float, default=60)
    console.add_argument("--until", help="stop at the first line matching this regex; exit 1 if never seen")
    console.add_argument("--log")
    console.add_argument("--send", help="one line to type after capture starts, e.g. a login name")
    console.add_argument("--cycle-psu", metavar="PSU_PORT")
    console.add_argument("--off-seconds", type=float, default=5)
    console.set_defaults(func=cmd_console)

    wait = sub.add_parser("wait-ssh")
    wait.add_argument("--host", default="192.168.55.1")
    wait.add_argument("--user", default="jetson")
    wait.add_argument("--timeout", type=float, default=180)
    wait.set_defaults(func=cmd_wait_ssh)

    args = parser.parse_args()
    if getattr(args, "action", None) == "set" and (args.volts is None or args.amps is None):
        parser.error("set needs both --volts and --amps")
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
