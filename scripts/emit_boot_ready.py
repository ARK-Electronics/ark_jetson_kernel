#!/usr/bin/env python3
"""Temporary HTTP readiness probe; never stores response bodies or device serials."""
import argparse
import http.client
import json
import math
import os
import select
import signal
import stat
import sys
import time
from urllib.parse import urlsplit


MAX_BODY = 1024 * 1024


def expired(_signum, _frame):
    raise TimeoutError("HTTP probe deadline expired")


def invalid_constant(_value):
    raise ValueError("Non-JSON numeric constant")


def ark_os_ready(payload):
    """Require the existing Jetson collector's metadata, not its fallback values."""
    if not isinstance(payload, dict) or payload.get("device_type") != "jetson":
        return False
    hardware = payload.get("hardware")
    if not isinstance(hardware, dict) or hardware.get("type") != "jetson":
        return False
    placeholders = {"", "unknown", "not available", "unavailable", "n/a", "none", "null"}
    return all(isinstance(hardware.get(key), str)
               and hardware[key].strip().casefold() not in placeholders
               for key in ("model", "module", "l4t"))


def http_ready(url, timeout, require_ark_os=False):
    kind = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
    connection = kind(url.hostname, url.port, timeout=timeout)
    # Bound slow headers/body trickles as well as individual socket operations.
    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        target = (url.path or "/") + ("?" + url.query if url.query else "")
        connection.request("GET", target, headers={"Accept": "application/json", "Connection": "close"})
        with connection.getresponse() as response:
            if response.status != 200:  # http.client never follows redirects.
                return False
            body = response.read(MAX_BODY + 1)
            if len(body) > MAX_BODY:
                return False
            payload = json.loads(body, parse_constant=invalid_constant)
            return ark_os_ready(payload) if require_ark_os else True
    except (OSError, http.client.HTTPException, ValueError, RecursionError):
        return False
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        connection.close()


def emit(line, serial_output):
    if serial_output is None:
        print(line, flush=True)
        return
    fd = os.open(serial_output, os.O_WRONLY | os.O_NONBLOCK | os.O_NOCTTY)
    try:
        if not stat.S_ISCHR(os.fstat(fd).st_mode):
            raise ValueError("--serial-output must name a character device")
        pending = ("\r\n" + line + "\r\n").encode("ascii")
        deadline = time.monotonic() + 1
        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([], [fd], [], remaining)[1]:
                raise TimeoutError("Serial marker write timed out")
            try:
                count = os.write(fd, pending)
            except BlockingIOError:
                continue
            if count == 0:
                raise OSError("Serial marker write made no progress")
            pending = pending[count:]
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1/api/system/info")
    parser.add_argument("--timeout", type=float, default=60, help="Overall deadline in seconds")
    parser.add_argument("--serial-output", help="Explicit target serial device, e.g. /dev/ttyTCU0; default stdout")
    parser.add_argument("--ark-os-ready", action="store_true",
                        help="Require Jetson type/model/module/L4T metadata; does not validate all ARK features. "
                             "Response bodies and device serial values are never saved or printed")
    args = parser.parse_args()
    url = urlsplit(args.url)
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.fragment:
        parser.error("--url must be an HTTP(S) URL without credentials or a fragment")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be positive and finite")
    signal.signal(signal.SIGALRM, expired)
    started = time.monotonic()
    while (remaining := args.timeout - (time.monotonic() - started)) > 0:
        if http_ready(url, min(0.25, remaining), args.ark_os_ready):
            record = {"kernel_uptime_s": round(time.clock_gettime(time.CLOCK_BOOTTIME), 6),
                      "probe_elapsed_s": round(time.monotonic() - started, 6)}
            if args.ark_os_ready:
                record["criterion"] = "ark_os_metadata"
            emit("JAJ_API_READY " + json.dumps(record, separators=(",", ":")), args.serial_output)
            return 0
        time.sleep(min(0.05, max(0, args.timeout - (time.monotonic() - started))))
    requirement = "HTTP 200 with valid JSON and Jetson metadata" if args.ark_os_ready else "HTTP 200 with valid JSON"
    print("JAJ_API_TIMEOUT: no " + requirement + " before deadline", file=sys.stderr)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError) as error:
        print("Readiness probe failed: " + str(error), file=sys.stderr)
        sys.exit(2)
