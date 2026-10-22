#!/usr/bin/env python3
"""Cold-boot timing for the JAJ bench: DP712 power cycle, debug-UART markers, systemd-analyze.

Usage: bench/measure_boot.py --config baseline --psu /dev/ttyUSB0 --uart /dev/ttyUSB1 [--runs 5]

Time zero is the OUTP ON command. Each run is one JSON under bench/results/<config>/;
a summary line (min/median/max of every marker) is printed and appended to
bench/results/<config>/summary.txt. Needs pyserial and ssh keys on the target.
"""
import argparse
import json
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

import serial

MARKERS = {
    "uefi": re.compile(r"Jetson UEFI firmware|UEFI firmware \(version"),
    "kernel": re.compile(r"Booting Linux|\[\s+0\.0{6}\]"),
    "started": re.compile(r"ARK_BOOT_STARTED(?: ([0-9.]+))?"),
    "reached": re.compile(r"ARK_BOOT_REACHED(?: ([0-9.]+))?"),
}
ANALYZE = {
    "time": "systemd-analyze time",
    "critical_chain": "systemd-analyze critical-chain multi-user.target",
    "blame": "systemd-analyze blame | head -60",
}
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
       "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=3", "-o", "LogLevel=ERROR"]


def psu(port, baud, command):
    with serial.Serial(port, baud, timeout=1) as dev:
        dev.write((command + "\n").encode())
        dev.flush()


def ssh_wait(host, deadline):
    while time.monotonic() < deadline:
        if subprocess.run(SSH + [f"jetson@{host}", "true"], capture_output=True).returncode == 0:
            return True
        time.sleep(2)
    return False


def one_run(args, index):
    uart = serial.Serial(args.uart, 115200, timeout=0.5)
    psu(args.psu, args.psu_baud, ":OUTP CH1,OFF")
    time.sleep(args.off_time)
    uart.reset_input_buffer()
    t0 = time.monotonic()
    psu(args.psu, args.psu_baud, ":OUTP CH1,ON")
    found = {}
    lines = []
    first_line = None
    while time.monotonic() - t0 < args.timeout and "reached" not in found:
        raw = uart.readline()
        if not raw:
            continue
        now = time.monotonic() - t0
        line = raw.decode(errors="replace").rstrip("\r\n")
        lines.append((round(now, 3), line))
        if first_line is None:
            first_line = now
        for name, pattern in MARKERS.items():
            if name not in found and (match := pattern.search(line)):
                found[name] = {"host_s": round(now, 3)}
                if match.groups() and match.group(1):
                    found[name]["uptime_s"] = float(match.group(1))
    uart.close()
    result = {"config": args.config, "run": index, "first_uart_line_s": round(first_line, 3) if first_line else None,
              "markers": found, "timeout": "reached" not in found, "uart": lines}
    if ssh_wait(args.host, t0 + args.timeout + 60):
        result["analyze"] = {}
        for key, command in ANALYZE.items():
            proc = subprocess.run(SSH + [f"jetson@{args.host}", command], capture_output=True, text=True)
            result["analyze"][key] = proc.stdout
    else:
        result["analyze"] = None
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", required=True)
    parser.add_argument("--psu", required=True, help="DP712 serial device")
    parser.add_argument("--psu-baud", type=int, default=9600)
    parser.add_argument("--uart", required=True, help="JAJ debug UART device")
    parser.add_argument("--host", default="192.168.55.1")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--off-time", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--discard-first", action="store_true", help="one extra boot first, not recorded")
    args = parser.parse_args()

    out = Path(__file__).resolve().parent / "results" / args.config
    out.mkdir(parents=True, exist_ok=True)
    if args.discard_first:
        print("discard boot ...", flush=True)
        one_run(args, 0)
    runs = []
    for i in range(1, args.runs + 1):
        result = one_run(args, i)
        runs.append(result)
        (out / f"run{i:02d}.json").write_text(json.dumps(result, indent=1))
        marks = " ".join(f"{k}={v['host_s']:.2f}" for k, v in result["markers"].items())
        print(f"run {i}: {marks}{'  TIMEOUT' if result['timeout'] else ''}", flush=True)

    summary = [f"{args.config}: {len(runs)} runs"]
    for name in ("uefi", "kernel", "started", "reached"):
        values = [r["markers"][name]["host_s"] for r in runs if name in r["markers"]]
        if values:
            summary.append(f"  {name:8s} min {min(values):6.2f}  median {statistics.median(values):6.2f}  max {max(values):6.2f}  (n={len(values)})")
    text = "\n".join(summary)
    print(text)
    with (out / "summary.txt").open("a") as stream:
        stream.write(text + "\n")
    return 1 if any(r["timeout"] for r in runs) else 0


if __name__ == "__main__":
    sys.exit(main())
