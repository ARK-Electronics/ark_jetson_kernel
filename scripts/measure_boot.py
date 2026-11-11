#!/usr/bin/env python3
r"""Capture a Jetson boot using one host monotonic clock.

UART capture requires pyserial and an explicitly selected host debug adapter.
This script never transmits UART bytes. DTR/RTS are configured false before open;
USB serial drivers can still briefly toggle their lines when opening a port.

Capture only (does not establish the power-on time):
  measure_boot.py --serial /dev/serial/by-id/EXPLICIT_ADAPTER --duration 90 \
      --output-dir /tmp/jaj-capture

Power on an already OFF supply output, preserving voltage/current settings:
  measure_boot.py --serial /dev/serial/by-id/EXPLICIT_ADAPTER \
      --rigol /dev/usbtmc0 --expected-serial YOUR_SUPPLY_SERIAL --channel 1 \
      --power-on --host 192.168.55.1 \
      --output-dir /tmp/jaj-boot

--power-cycle explicitly permits cutting CH1 power. Shut the target down cleanly
before running it. No automatic shutdown, voltage/current changes, or power-off
at capture completion occur. Confirm the supply powers the intended board and
that alternate power sources cannot keep it alive. TCP success means a port
accepted a connection. --probe ssh instead requires an SSH identification banner;
neither probe verifies SSH authentication or application readiness.
The power reference is the host SCPI command-send time, NOT an electrical edge.
Supply identity and protection settings/alarms are checked before output changes.
Latched faults are never cleared. A final output/alarm check distinguishes supply
faults from software readiness timeouts; it does not continuously monitor power.
"""

import argparse
import base64
import codecs
import datetime as dt
import fcntl
import http.client
import json
import math
import os
from pathlib import Path
import re
import socket
import struct
import sys
import threading
import time
from urllib.parse import urlsplit


class Capture:
    def __init__(self, directory, marker=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.events = (self.directory / 'timeline.jsonl').open('w')
        self.raw = (self.directory / 'uart.raw').open('wb')
        self.lock = threading.RLock()
        self.start_ns = time.monotonic_ns()
        self.reference_ns = None
        self.phase = 'setup'
        self.marker = re.compile(marker) if marker else None
        self.decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        self.marker_buffer = ''
        self.ready = {}
        self.errors = []
        self.uart_bytes = 0
        self.reference_label = None
        self.event('capture_started')

    def event(self, kind, timestamp_ns=None, **details):
        with self.lock:
            now = time.monotonic_ns() if timestamp_ns is None else timestamp_ns
            record = {'event': kind, 'monotonic_ns': now,
                      'capture_elapsed_s': (now - self.start_ns) / 1e9,
                      'reference_elapsed_s': None if self.reference_ns is None else
                      (now - self.reference_ns) / 1e9, 'phase': self.phase, **details}
            self.events.write(json.dumps(record) + '\n')
            self.events.flush()
            return record

    def arm(self, label, timestamp_ns=None):
        with self.lock:
            self.reference_ns = time.monotonic_ns() if timestamp_ns is None else timestamp_ns
            self.reference_label = label
            self.marker_buffer = ''
            self.decoder.reset()
            self.phase = 'boot' if label == 'scpi_power_on_command_send' else 'capture'
            self.event('reference_started', self.reference_ns, label=label,
                       electrical_edge_measured=False)

    def mark_ready(self, kind, timestamp_ns=None):
        with self.lock:
            if self.reference_ns is None or kind in self.ready:
                return
            now = time.monotonic_ns() if timestamp_ns is None else timestamp_ns
            if now < self.reference_ns:
                return
            self.ready[kind] = {'monotonic_ns': now,
                                'reference_elapsed_s': (now - self.reference_ns) / 1e9}
            self.event(kind + '_ready', now)

    def uart(self, data, timestamp_ns=None):
        with self.lock:
            now = time.monotonic_ns() if timestamp_ns is None else timestamp_ns
            offset = self.uart_bytes
            self.raw.write(data)
            self.raw.flush()
            self.uart_bytes += len(data)
            self.event('uart_received', now, raw_offset=offset, length=len(data),
                       data_base64=base64.b64encode(data).decode('ascii'),
                       text=data.decode('utf-8', errors='replace'))
            # Keep full text until the marker matches, including split UTF-8/chunks.
            if self.marker and self.reference_ns is not None and now >= self.reference_ns and 'uart' not in self.ready:
                self.marker_buffer += self.decoder.decode(data)
                if self.marker.search(self.marker_buffer):
                    self.mark_ready('uart', now)
                    self.marker_buffer = ''

    def fail(self, source, error):
        with self.lock:
            detail = {'source': source, 'message': str(error), 'phase': self.phase}
            self.errors.append(detail)
            self.event('error', **detail)

    def close(self):
        self.raw.close()
        self.events.close()


class Rigol:
    # Linux _IOW(91, 10, __u32), from linux/usb/tmc.h; host timeout only.
    SET_TIMEOUT = (1 << 30) | (4 << 16) | (91 << 8) | 10

    def __init__(self, path, capture):
        self.fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
        self.capture = capture
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.ioctl(self.fd, self.SET_TIMEOUT, struct.pack('I', 2000))
        except BaseException:
            os.close(self.fd)
            raise

    def send(self, command, power_reference=False):
        # Hold the recorder lock so the reference is published before readers
        # can record any observation, with timestamp immediately before write.
        with self.capture.lock:
            now = time.monotonic_ns()
            data = (command + '\n').encode('ascii')
            if os.write(self.fd, data) != len(data):
                raise OSError('Incomplete SCPI write')
            if power_reference:
                self.capture.arm('scpi_power_on_command_send', now)
            self.capture.event('scpi_command_sent', now, command=command)

    def query(self, command):
        if not command.endswith('?') and '? ' not in command:
            raise ValueError('Expected an SCPI query')
        self.send(command)
        value = os.read(self.fd, 4096).decode('ascii', errors='replace').strip()
        if not value:
            raise RuntimeError('Empty instrument response to ' + command)
        self.capture.event('scpi_response', command=command, response=value)
        return value

    def close(self):
        os.close(self.fd)


def parse_identity(value, expected_serial):
    fields = [part.strip() for part in value.split(',')]
    if len(fields) != 4 or 'RIGOL' not in fields[0].upper() or fields[1] != 'DP811':
        raise ValueError('Expected a Rigol DP811, got: ' + value)
    if fields[2] != expected_serial:
        raise ValueError('Instrument serial does not match --expected-serial: ' + value)
    return dict(zip(('manufacturer', 'model', 'serial', 'firmware'), fields))


class SupplyFault(RuntimeError):
    """The verified supply has a protection latch or lost its enabled output."""


def supply_snapshot(instrument, include_settings=False):
    """Read DP811 CH1 status only; never clear alarms or change protection.

    OUTP:{OCP,OVP}:ALAR? returns YES/NO; their STAT? queries return ON/OFF.
    These non-clearing queries are documented in the DP800 Programming Guide.
    Each USBTMC read/write retains Rigol's bounded two-second host timeout.
    """
    def state(command, allowed):
        value = instrument.query(command).upper()
        if value not in allowed:
            raise RuntimeError(f"Unrecognized Rigol response to {command}: {value}")
        return value

    def limit(command):
        raw = instrument.query(command)
        value = float(raw)
        if not math.isfinite(value) or value <= 0:
            raise RuntimeError(f"Invalid Rigol protection limit for {command}: {raw}")
        return value

    result = {
        'output': state('OUTP? CH1', ('ON', 'OFF')),
        'ocp_tripped': state('OUTP:OCP:ALAR? CH1', ('YES', 'NO')) == 'YES',
        'ovp_tripped': state('OUTP:OVP:ALAR? CH1', ('YES', 'NO')) == 'YES',
    }
    if include_settings:
        result.update({
            'settings': instrument.query('APPL? CH1'),
            'ocp_enabled': state('OUTP:OCP:STAT? CH1', ('ON', 'OFF')) == 'ON',
            'ocp_limit_a': limit('OUTP:OCP:VAL? CH1'),
            'ovp_enabled': state('OUTP:OVP:STAT? CH1', ('ON', 'OFF')) == 'ON',
            'ovp_limit_v': limit('OUTP:OVP:VAL? CH1'),
        })
    return result


def check_supply(snapshot, require_on=False):
    faults = [label for key, label in (('ocp_tripped', 'OCP alarm'),
                                      ('ovp_tripped', 'OVP alarm')) if snapshot[key]]
    if require_on and snapshot['output'] != 'ON':
        faults.append('CH1 output is OFF after power-on was requested')
    if faults:
        raise SupplyFault('; '.join(faults) + '. No protection setting or latched fault was changed.')


def endpoint_ready(address, timeout, probe='tcp'):
    """Bound connection plus optional SSH identification to one deadline."""
    deadline = time.monotonic() + timeout
    try:
        with socket.socket(address[0], socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(address[1])
            if probe == 'tcp':
                return True
            received = b''
            # SSH may send informational lines before its identification line.
            # Bound both total bytes and time; never transmit protocol bytes.
            while len(received) < 8192:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                sock.settimeout(remaining)
                data = sock.recv(min(1024, 8192 - len(received)))
                if not data:
                    return False
                received += data
                for line in received.split(b'\n')[:-1]:
                    if re.fullmatch(rb'SSH-(?:2\.0|1\.99)-[^\r\n ]+(?: [^\r\n]*)?\r?', line):
                        return True
            return False
    except OSError:
        return False


def tcp_ready(address, timeout):
    return endpoint_ready(address, timeout, 'tcp')


def http_ready(url, timeout, require_json):
    """Require HTTP 200 and optionally parse JSON; never retain response bodies."""
    parsed = urlsplit(url)
    connection_type = http.client.HTTPSConnection if parsed.scheme == 'https' else http.client.HTTPConnection
    connection = connection_type(parsed.hostname, parsed.port, timeout=timeout)
    deadline = time.monotonic() + timeout
    timer = None
    try:
        connection.connect()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        active_socket = connection.sock
        active_socket.settimeout(remaining)
        # A deadline shutdown also bounds a server that slowly drips headers or
        # JSON bytes; ordinary socket timeouts only bound individual reads.
        def expire():
            try:
                active_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        timer = threading.Timer(remaining, expire)
        timer.daemon = True
        timer.start()
        target = parsed.path or '/'
        if parsed.query:
            target += '?' + parsed.query
        connection.request('GET', target, headers={'Accept': 'application/json' if require_json else '*/*',
                                                   'Connection': 'close'})
        with connection.getresponse() as response:
            if response.status != 200:
                return False
            if require_json:
                # Bound response memory as well as time. JSON scalars are valid;
                # this checks syntax, not a customer's application schema.
                body = response.read(1024 * 1024 + 1)
                if len(body) > 1024 * 1024:
                    return False
                def reject_constant(value):
                    raise ValueError('Non-JSON constant: ' + value)
                json.loads(body, parse_constant=reject_constant)
            return time.monotonic() <= deadline
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        if timer is not None:
            timer.cancel()
        connection.close()


def http_worker(url, timeout, require_json, interval, capture, stop):
    while not stop.is_set():
        if capture.reference_ns is not None and 'http' not in capture.ready:
            if http_ready(url, timeout, require_json) and not stop.is_set():
                capture.mark_ready('http')
                return
        stop.wait(interval)


def serial_worker(port, capture, stop):
    try:
        while not stop.is_set():
            data = port.read(max(1, min(port.in_waiting, 4096)))
            if data:
                capture.uart(data, time.monotonic_ns())
    except Exception as error:
        capture.fail('uart_capture', error)
        stop.set()


def tcp_worker(address, timeout, interval, probe, capture, stop):
    kind = 'ssh_banner' if probe == 'ssh' else 'tcp'
    while not stop.is_set():
        if capture.reference_ns is not None and kind not in capture.ready:
            if endpoint_ready(address, timeout, probe):
                capture.mark_ready(kind)
                return
        stop.wait(interval)


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output-dir', required=True, help='New directory; refuses to overwrite an existing capture')
    parser.add_argument('--serial', help='Explicit host debug UART path; omit for no UART capture')
    parser.add_argument('--baud', type=int, default=115200)
    parser.add_argument('--uart-marker', help='UTF-8 regular expression for application readiness; requires --serial')
    parser.add_argument('--host', help='Host for TCP readiness polling; optional')
    parser.add_argument('--port', type=int, default=22)
    parser.add_argument('--probe', choices=('tcp', 'ssh'), default='tcp',
                        help='TCP accept (default) or SSH identification banner; neither verifies authentication/application readiness')
    parser.add_argument('--tcp-timeout', type=float, default=0.25)
    parser.add_argument('--http-url', help='Additional HTTP(S) GET endpoint; readiness requires status 200, with no redirects followed')
    parser.add_argument('--http-json', action='store_true', help='Also require a valid JSON response (up to 1 MiB); bodies are never saved')
    parser.add_argument('--http-timeout', type=float, default=0.5, help='Timeout per HTTP probe in seconds')
    parser.add_argument('--poll-interval', type=float, default=0.1)
    parser.add_argument('--duration', type=float, default=90, help='Capture seconds after the reference, even if readiness occurs early')
    parser.add_argument('--rigol', help='Explicit /dev/usbtmcN for the DP811')
    parser.add_argument('--expected-serial', help='Required with --rigol: exact serial from the intended supply identity')
    parser.add_argument('--channel', type=int, choices=(1,), help='Required with --rigol; only verified DP811 CH1 is supported')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--power-on', action='store_true', help='Power on only if output is already OFF')
    action.add_argument('--power-cycle', action='store_true', help='Explicitly cut power first; shut down the target cleanly beforehand')
    parser.add_argument('--off-seconds', type=float, default=5, help='Output-OFF dwell for --power-cycle (default: 5)')
    args = parser.parse_args(argv)
    if args.http_json and not args.http_url:
        parser.error('--http-json requires --http-url')
    if args.http_url:
        try:
            parsed = urlsplit(args.http_url)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
                raise ValueError('Expected http(s)://host/path without credentials or fragment')
            if parsed.port is not None and not 1 <= parsed.port <= 65535:
                raise ValueError('Invalid HTTP port')
        except ValueError as error:
            parser.error('Invalid --http-url: ' + str(error))
    if args.probe == 'ssh' and not args.host:
        parser.error('--probe ssh requires --host')
    if args.uart_marker and not args.serial:
        parser.error('--uart-marker requires --serial')
    if args.uart_marker:
        try:
            re.compile(args.uart_marker)
        except re.error as error:
            parser.error('Invalid --uart-marker: ' + str(error))
    if bool(args.rigol) != bool(args.power_on or args.power_cycle):
        parser.error('--rigol requires exactly one of --power-on or --power-cycle, and vice versa')
    if bool(args.rigol) != bool(args.expected_serial):
        parser.error('--rigol and an explicit --expected-serial must be supplied together')
    if bool(args.rigol) != bool(args.channel):
        parser.error('--rigol and --channel 1 must be supplied together')
    if not args.serial and not args.host and not args.http_url:
        parser.error('Provide --serial, --host, and/or --http-url to observe readiness or capture UART')
    for option in ('duration', 'tcp_timeout', 'http_timeout', 'poll_interval', 'off_seconds'):
        value = getattr(args, option)
        if not 0 < value < float('inf'):
            parser.error('--' + option.replace('_', '-') + ' must be finite and positive')
    if not 1 <= args.port <= 65535 or args.baud <= 0:
        parser.error('Invalid TCP port or UART baud rate')
    return args


def run(args):
    capture = Capture(args.output_dir, args.uart_marker)
    stop = threading.Event()
    workers = []
    port = instrument = None
    instrument_verified = False
    power_on_attempted = False
    metadata = {'schema_version': 1, 'started_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
                'configuration': vars(args), 'clock': 'host time.monotonic_ns',
                'power_timing_note': 'SCPI command-send reference; electrical edge is not measured',
                'uart_timing_note': 'Host receive timestamps for chunks, not individual wire bytes',
                'readiness_note': 'TCP accept, SSH banner, HTTP 200 (optionally valid JSON), and UART regex are separate observations. TCP/SSH do not verify authentication or application readiness; HTTP does not validate the application response schema.',
                'http_readiness_requirement': ('HTTP 200 with valid JSON' if args.http_json else 'HTTP 200') if args.http_url else None,
                'http_response_body_saved': False,
                'network_readiness_kind': ('ssh_banner' if args.probe == 'ssh' else 'tcp') if args.host else None,
                'shutdown_note': 'No graceful shutdown is issued or timed; power-cycle requires prior manual shutdown',
                'status': 'error'}
    address = None
    try:
        if args.host:
            # Resolve before the timed boot; DNS delay must not contaminate it.
            resolved = socket.getaddrinfo(args.host, args.port, type=socket.SOCK_STREAM)[0]
            address = (resolved[0], resolved[4])
            metadata['tcp_address'] = resolved[4]
        if args.rigol:
            instrument = Rigol(args.rigol, capture)
            metadata['instrument'] = parse_identity(instrument.query('*IDN?'), args.expected_serial)
            instrument_verified = True
            initial = supply_snapshot(instrument, include_settings=True)
            metadata['initial_supply'] = initial
            state = metadata['initial_output'] = initial['output']
            metadata['initial_settings'] = initial['settings']
            capture.event('supply_status', stage='initial', **initial)
            check_supply(initial)
            if args.power_on and state != 'OFF':
                raise RuntimeError('--power-on requires CH1 already OFF; no power change was made')
        if args.serial:
            import serial
            port = serial.Serial(port=None, baudrate=args.baud, timeout=0.05,
                                 rtscts=False, dsrdtr=False, xonxoff=False, exclusive=True)
            port.dtr = False
            port.rts = False
            port.port = args.serial
            port.open()
            capture.event('uart_opened', path=args.serial, baud=args.baud,
                          dtr=False, rts=False, transmitted_bytes=0)
            worker = threading.Thread(target=serial_worker, args=(port, capture, stop), daemon=True)
            worker.start()
            workers.append(worker)
        if address:
            worker = threading.Thread(target=tcp_worker, args=(address, args.tcp_timeout,
                                      args.poll_interval, args.probe, capture, stop), daemon=True)
            worker.start()
            workers.append(worker)
        if args.http_url:
            worker = threading.Thread(target=http_worker, args=(args.http_url, args.http_timeout,
                                      args.http_json, args.poll_interval, capture, stop), daemon=True)
            worker.start()
            workers.append(worker)
        if instrument:
            if args.power_cycle:
                capture.phase = 'power_off'
                instrument.send('OUTP CH1,OFF')
                if instrument.query('OUTP? CH1').upper() != 'OFF':
                    raise RuntimeError('Output did not report OFF; power-on was not issued')
                capture.phase = 'off_dwell'
                capture.event('off_dwell_started', seconds=args.off_seconds)
                if stop.wait(args.off_seconds):
                    raise RuntimeError('Capture failed before power-on; output remains OFF')
            capture.phase = 'pre_power_on'
            if address and tcp_ready(address, args.tcp_timeout):
                raise RuntimeError('TCP endpoint responds while output is OFF; cannot attribute a cold boot to this supply')
            if stop.is_set():
                raise RuntimeError('Capture failed before power-on')
            power_on_attempted = True
            instrument.send('OUTP CH1,ON', power_reference=True)
            if instrument.query('OUTP? CH1').upper() != 'ON':
                raise RuntimeError('Output did not report ON')
        else:
            capture.arm('capture_start_boot_edge_unknown')
        deadline = capture.reference_ns / 1e9 + args.duration
        while not stop.is_set() and time.monotonic() < deadline:
            stop.wait(min(0.1, max(0, deadline - time.monotonic())))
        network_kind = 'ssh_banner' if args.probe == 'ssh' else 'tcp'
        wanted = [kind for kind, enabled in ((network_kind, args.host), ('http', args.http_url), ('uart', args.uart_marker)) if enabled]
        missing = [kind for kind in wanted if kind not in capture.ready]
        metadata['missing_readiness'] = missing
        if capture.errors:
            metadata['status'] = 'error'
        elif missing:
            metadata['status'] = 'readiness_timeout'
        else:
            metadata['status'] = 'ready' if wanted else 'capture_complete'
    except SupplyFault as error:
        capture.fail('power_supply', error)
        metadata['status'] = 'supply_fault'
        print(str(error), file=sys.stderr)
    except KeyboardInterrupt:
        metadata['status'] = 'interrupted'
        capture.event('interrupted')
    except Exception as error:
        capture.fail('measurement', error)
        metadata['status'] = 'error'
        print(str(error), file=sys.stderr)
    finally:
        stop.set()
        for worker in workers:
            worker.join(timeout=max(2, args.tcp_timeout + 1, args.http_timeout + 1))
        if port is not None:
            port.close()
        if instrument is not None:
            # Check after readers stop, so SCPI latency cannot extend readiness
            # polling or shift its reference. Never query an unverified device.
            if instrument_verified:
                try:
                    final = supply_snapshot(instrument)
                    metadata['final_supply'] = final
                    capture.event('supply_status', stage='final', **final)
                    check_supply(final, require_on=power_on_attempted)
                except SupplyFault as error:
                    capture.fail('power_supply', error)
                    metadata['status'] = 'supply_fault'
                    print(str(error), file=sys.stderr)
                except Exception as error:
                    capture.fail('power_supply_monitor', error)
                    if metadata['status'] != 'supply_fault':
                        metadata['status'] = 'error'
                    print(str(error), file=sys.stderr)
            instrument.close()
        metadata.update({'capture_start_monotonic_ns': capture.start_ns,
                         'reference_monotonic_ns': capture.reference_ns,
                         'reference_label': capture.reference_label,
                         'end_monotonic_ns': time.monotonic_ns(),
                         'final_phase': capture.phase, 'readiness': capture.ready,
                         'uart_bytes': capture.uart_bytes, 'errors': capture.errors})
        if metadata['status'] == 'readiness_timeout':
            capture.event('readiness_timeout', missing=metadata['missing_readiness'], duration_s=args.duration)
        capture.event('capture_finished', status=metadata['status'])
        (capture.directory / 'metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
        capture.close()
    print(json.dumps({'status': metadata['status'], 'output_dir': str(capture.directory),
                      'readiness': capture.ready}, indent=2))
    return {'ready': 0, 'capture_complete': 0, 'readiness_timeout': 2,
            'interrupted': 130, 'error': 1, 'supply_fault': 1}[metadata['status']]


if __name__ == '__main__':
    sys.exit(run(arguments()))
