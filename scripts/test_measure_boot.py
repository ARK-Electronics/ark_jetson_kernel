#!/usr/bin/env python3
"""Hardware-free checks for capture boundaries and meaningful readiness results."""

import contextlib
import importlib.util
import io
import http.server
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('measure_boot', Path(__file__).with_name('measure_boot.py'))
boot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(boot)


class ParsingTests(unittest.TestCase):
    def test_power_changes_require_explicit_instrument_and_channel(self):
        invalid = (
            ['--power-on'],
            ['--rigol', '/dev/usbtmc0'],
            ['--rigol', '/dev/usbtmc0', '--power-cycle'],
            ['--channel', '1'],
            ['--rigol', '/dev/usbtmc0', '--channel', '1', '--power-on', '--power-cycle'],
        )
        for extra in invalid:
            with self.subTest(extra=extra), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    boot.arguments(['--output-dir', '/tmp/unused', '--host', '127.0.0.1', *extra])
        args = boot.arguments(['--output-dir', '/tmp/unused', '--host', '127.0.0.1'])
        self.assertFalse(args.power_on or args.power_cycle)
        self.assertIsNone(args.rigol)
        self.assertIsNone(args.serial)

    def test_invalid_regex_and_nonfinite_duration_rejected_before_access(self):
        for extra in (['--uart-marker', '['], ['--duration', 'nan'], ['--duration', 'inf']):
            with self.subTest(extra=extra), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    boot.arguments(['--output-dir', '/tmp/unused', '--serial', '/not/opened', *extra])

    def test_supply_requires_explicit_expected_serial(self):
        argv = ['--output-dir', '/tmp/unused', '--host', '127.0.0.1',
                '--rigol', '/dev/not-opened', '--channel', '1', '--power-on']
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            boot.arguments(argv)
        self.assertEqual(boot.arguments([*argv, '--expected-serial', 'SERIAL_TEST']).expected_serial,
                         'SERIAL_TEST')

    def test_instrument_identity_must_match_model_and_serial(self):
        self.assertEqual(boot.parse_identity('RIGOL TECHNOLOGIES,DP811,SERIAL_TEST,00.01.14',
                                           'SERIAL_TEST')['model'], 'DP811')
        for reply in ('OTHER,DP811,SERIAL_TEST,1', 'RIGOL,DP832,SERIAL_TEST,1',
                      'RIGOL,DP811,WRONG,1', 'truncated'):
            with self.subTest(reply=reply), self.assertRaises(ValueError):
                boot.parse_identity(reply, 'SERIAL_TEST')


class CaptureTests(unittest.TestCase):
    def test_marker_spans_chunks_and_utf8_but_never_crosses_boot_boundary(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'capture'
            capture = boot.Capture(path, r'READY café')
            origin = capture.start_ns + 1_000_000_000
            capture.uart(b'READY caf\xc3', origin - 100)
            capture.arm('scpi_power_on_command_send', origin)
            capture.uart(b'\xa9\n', origin + 100)
            self.assertNotIn('uart', capture.ready)
            capture.uart(b'READY caf\xc3', origin + 200)
            capture.uart(b'\xa9\n', origin + 300)
            self.assertEqual(capture.ready['uart']['monotonic_ns'], origin + 300)
            capture.uart(b'READY caf\xc3\xa9\n', origin + 400)
            self.assertEqual(capture.ready['uart']['monotonic_ns'], origin + 300)
            capture.close()
            events = [json.loads(line) for line in (path / 'timeline.jsonl').read_text().splitlines()]
            chunks = [event for event in events if event['event'] == 'uart_received']
            self.assertEqual(len([e for e in events if e['event'] == 'uart_ready']), 1)
            self.assertEqual(sum(e['length'] for e in chunks), len((path / 'uart.raw').read_bytes()))
            self.assertEqual(chunks[-1]['raw_offset'] + chunks[-1]['length'], len((path / 'uart.raw').read_bytes()))

    def test_pre_reference_readiness_is_not_boot_readiness(self):
        with tempfile.TemporaryDirectory() as root:
            capture = boot.Capture(Path(root) / 'capture')
            capture.mark_ready('tcp')
            self.assertEqual(capture.ready, {})
            reference = capture.start_ns + 1_000_000
            capture.arm('scpi_power_on_command_send', reference)
            capture.mark_ready('tcp', reference - 1)
            self.assertEqual(capture.ready, {})
            capture.mark_ready('tcp', reference + 25_000_000)
            self.assertEqual(capture.ready['tcp']['reference_elapsed_s'], 0.025)
            capture.close()


class TcpIntegrationTests(unittest.TestCase):
    def run_capture(self, listener, expected_status, probe='tcp'):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'capture'
            args = boot.arguments(['--output-dir', str(path), '--host', '127.0.0.1',
                                   '--port', str(listener.getsockname()[1]), '--duration', '0.08',
                                   '--poll-interval', '0.01', '--tcp-timeout', '0.02', '--probe', probe])
            with contextlib.redirect_stdout(io.StringIO()):
                code = boot.run(args)
            result = json.loads((path / 'metadata.json').read_text())
            self.assertEqual(result['status'], expected_status)
            self.assertEqual(result['reference_label'], 'capture_start_boot_edge_unknown')
            self.assertEqual(result['uart_bytes'], 0)
            self.assertEqual(result['errors'], [])
            return code, result

    def test_real_listening_port_yields_ready_without_claiming_power_edge(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            code, result = self.run_capture(listener, 'ready')
            self.assertEqual(code, 0)
            self.assertGreaterEqual(result['readiness']['tcp']['reference_elapsed_s'], 0)

    def test_ssh_banner_is_distinct_readiness(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            def respond():
                connection, _ = listener.accept()
                with connection:
                    connection.sendall(b'Fixture informational line\r\nSSH-2.0-OpenSSH_test\r\n')
            server = threading.Thread(target=respond, daemon=True)
            server.start()
            code, result = self.run_capture(listener, 'ready', probe='ssh')
            server.join(timeout=1)
            self.assertFalse(server.is_alive())
            self.assertEqual(code, 0)
            self.assertEqual(set(result['readiness']), {'ssh_banner'})
            self.assertEqual(result['network_readiness_kind'], 'ssh_banner')

    def test_accepting_non_ssh_endpoint_does_not_satisfy_ssh_probe(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            def respond():
                connection, _ = listener.accept()
                with connection:
                    connection.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n')
            server = threading.Thread(target=respond, daemon=True)
            server.start()
            code, result = self.run_capture(listener, 'readiness_timeout', probe='ssh')
            server.join(timeout=1)
            self.assertFalse(server.is_alive())
            self.assertEqual(code, 2)
            self.assertEqual(result['readiness'], {})
            self.assertEqual(result['missing_readiness'], ['ssh_banner'])
            # The same non-SSH listening port still satisfies plain TCP mode.
            self.assertTrue(boot.endpoint_ready((socket.AF_INET, listener.getsockname()), 0.02, 'tcp'))

    def test_bound_nonlistening_port_yields_readiness_timeout(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            code, result = self.run_capture(listener, 'readiness_timeout')
            self.assertEqual(code, 2)
            self.assertEqual(result['readiness'], {})
            self.assertEqual(result['missing_readiness'], ['tcp'])
            self.assertEqual(result['final_phase'], 'capture')


class SupplyIntegrationTests(unittest.TestCase):
    def run_supply(self, *, identity='RIGOL,DP811,SERIAL_TEST,1.0', initial_fault=None,
                   final_fault=None, final_off=False, final_error=False,
                   ready=False, cycle=False):
        instances = []
        class FakeRigol:
            def __init__(self, path, capture):
                self.capture = capture
                self.calls = []
                self.output = 'OFF'
                self.power_requested = False
                self.power_output_reads = 0
                self.closed = False
                self.reference_ns = None
                instances.append(self)

            def query(self, command):
                self.calls.append(command)
                if command == '*IDN?':
                    return identity
                if command == 'OUTP? CH1':
                    if self.power_requested:
                        self.power_output_reads += 1
                        if self.power_output_reads >= 2:
                            if final_error:
                                raise TimeoutError('Fixture supply status read timed out')
                            if final_off or final_fault:
                                self.output = 'OFF'
                    return self.output
                values = {'APPL? CH1': 'CH1:25.000,5.300',
                          'OUTP:OCP:STAT? CH1': 'ON', 'OUTP:OCP:VAL? CH1': '4.900',
                          'OUTP:OVP:STAT? CH1': 'ON', 'OUTP:OVP:VAL? CH1': '27.000'}
                for alarm in ('OCP', 'OVP'):
                    if command == f'OUTP:{alarm}:ALAR? CH1':
                        fault = final_fault if self.power_requested and self.power_output_reads >= 2 else initial_fault
                        return 'YES' if fault == alarm else 'NO'
                return values[command]

            def send(self, command, power_reference=False):
                self.calls.append(command)
                if command == 'OUTP CH1,ON':
                    self.output = 'ON'
                    self.power_requested = True
                    self.reference_ns = boot.time.monotonic_ns()
                    if power_reference:
                        self.capture.arm('scpi_power_on_command_send', self.reference_ns)
                elif command == 'OUTP CH1,OFF':
                    self.output = 'OFF'
                else:
                    raise AssertionError('Unexpected mutating SCPI command: ' + command)

            def close(self):
                self.closed = True

        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'capture'
            args = boot.arguments(['--output-dir', str(path), '--host', '127.0.0.1',
                                   '--rigol', '/dev/not-opened', '--channel', '1',
                                   '--expected-serial', 'SERIAL_TEST',
                                   '--power-cycle' if cycle else '--power-on',
                                   '--duration', '0.03', '--poll-interval', '0.001',
                                   '--off-seconds', '0.001'])
            with patch.object(boot, 'Rigol', FakeRigol), \
                    patch.object(boot, 'endpoint_ready', side_effect=lambda *args: ready and instances[0].power_requested), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = boot.run(args)
            result = json.loads((path / 'metadata.json').read_text())
            events = [json.loads(line) for line in (path / 'timeline.jsonl').read_text().splitlines()]
        instrument = instances[0]
        self.assertTrue(instrument.closed)
        # Output ON/OFF are the only permitted writes: no clears or limit changes.
        self.assertTrue(all('?' in call or call in ('OUTP CH1,ON', 'OUTP CH1,OFF')
                            for call in instrument.calls))
        return code, result, events, instrument

    def test_unverified_identity_prevents_all_other_supply_access(self):
        code, result, _, instrument = self.run_supply(identity='RIGOL,DP811,OTHER,1.0')
        self.assertEqual(code, 1)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(instrument.calls, ['*IDN?'])
        self.assertIsNone(result['reference_monotonic_ns'])

    def test_initial_protection_latch_prevents_on_and_cycle_without_clearing(self):
        for alarm in ('OCP', 'OVP'):
            for cycle in (False, True):
                with self.subTest(alarm=alarm, cycle=cycle):
                    code, result, _, instrument = self.run_supply(initial_fault=alarm, cycle=cycle)
                    self.assertEqual((code, result['status']), (1, 'supply_fault'))
                    self.assertFalse(any(call.startswith('OUTP CH1,') for call in instrument.calls))
                    self.assertIsNone(result['reference_monotonic_ns'])
                    self.assertTrue(result['initial_supply'][alarm.lower() + '_tripped'])
                    self.assertEqual(result['initial_supply']['ocp_limit_a'], 4.9)
                    self.assertEqual(result['initial_supply']['ovp_limit_v'], 27.0)
                    self.assertTrue(result['initial_supply']['ocp_enabled'])

    def test_final_protection_trip_is_supply_fault_even_if_readiness_was_observed(self):
        for alarm in ('OCP', 'OVP'):
            for ready in (False, True):
                with self.subTest(alarm=alarm, ready=ready):
                    code, result, events, instrument = self.run_supply(final_fault=alarm, ready=ready)
                    self.assertEqual((code, result['status']), (1, 'supply_fault'))
                    self.assertEqual(result['final_supply']['output'], 'OFF')
                    self.assertTrue(result['final_supply'][alarm.lower() + '_tripped'])
                    self.assertTrue(any(error['source'] == 'power_supply' for error in result['errors']))
                    self.assertFalse(any(event['event'] == 'readiness_timeout' for event in events))
                    self.assertEqual([call for call in instrument.calls if '?' not in call], ['OUTP CH1,ON'])
                    if ready:
                        self.assertIn('tcp', result['readiness'])

    def test_unexpected_output_off_is_supply_fault_without_protection_alarm(self):
        code, result, _, _ = self.run_supply(final_off=True)
        self.assertEqual((code, result['status']), (1, 'supply_fault'))
        self.assertFalse(result['final_supply']['ocp_tripped'])
        self.assertFalse(result['final_supply']['ovp_tripped'])
        self.assertIn('output is OFF', result['errors'][-1]['message'])

    def test_healthy_supply_can_distinguish_ready_from_software_timeout(self):
        for ready in (False, True):
            with self.subTest(ready=ready):
                code, result, events, instrument = self.run_supply(ready=ready, cycle=True)
                self.assertEqual((code, result['status']), (0, 'ready') if ready else (2, 'readiness_timeout'))
                self.assertEqual(result['errors'], [])
                self.assertEqual(result['initial_output'], 'OFF')
                self.assertEqual(result['final_supply']['output'], 'ON')
                self.assertEqual(result['reference_monotonic_ns'], instrument.reference_ns)
                self.assertEqual([call for call in instrument.calls if '?' not in call],
                                 ['OUTP CH1,OFF', 'OUTP CH1,ON'])
                if not ready:
                    self.assertEqual(sum(event['event'] == 'readiness_timeout' for event in events), 1)

    def test_final_supply_read_failure_cannot_report_success_or_software_timeout(self):
        for ready in (False, True):
            with self.subTest(ready=ready):
                code, result, events, _ = self.run_supply(ready=ready, final_error=True)
                self.assertEqual((code, result['status']), (1, 'error'))
                self.assertEqual(result['errors'][-1]['source'], 'power_supply_monitor')
                self.assertFalse(any(event['event'] == 'readiness_timeout' for event in events))


class HttpIntegrationTests(unittest.TestCase):
    def start_server(self, responses):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                index = min(self.server.request_count, len(responses) - 1)
                status, body = responses[index]
                self.server.request_count += 1
                self.send_response(status)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.request_count = 0
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server, 'http://127.0.0.1:' + str(server.server_port) + '/api/system/info'

    def test_http_errors_and_invalid_json_do_not_count_as_ready(self):
        _, url = self.start_server([(503, b'{"starting":true}'),
                                    (200, b'<html>still starting</html>'),
                                    (200, b'{"ready":true}')])
        self.assertFalse(boot.http_ready(url, 0.25, True))
        self.assertFalse(boot.http_ready(url, 0.25, True))
        self.assertTrue(boot.http_ready(url, 0.25, True))

    def test_nonfinite_constants_are_not_valid_json(self):
        _, url = self.start_server([(200, value) for value in
                                    (b'NaN', b'Infinity', b'-Infinity',
                                     b'{"nested":[NaN]}', b'null')])
        for _ in range(4):
            self.assertFalse(boot.http_ready(url, 0.25, True))
        self.assertTrue(boot.http_ready(url, 0.25, True))

    def test_http_status_only_does_not_require_json(self):
        _, url = self.start_server([(200, b'<html>ready</html>')])
        self.assertTrue(boot.http_ready(url, 0.25, False))
        self.assertFalse(boot.http_ready(url, 0.25, True))

    def test_http_readiness_is_separate_and_body_is_never_saved(self):
        server, url = self.start_server([(503, b'{"starting":true}'),
                                         (200, b'not JSON'),
                                         (200, b'{"private":"DO_NOT_SAVE_THIS_BODY"}')])
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'capture'
            args = boot.arguments(['--output-dir', str(path), '--host', '127.0.0.1',
                                   '--port', str(server.server_port), '--http-url', url, '--http-json',
                                   '--duration', '0.15', '--poll-interval', '0.005', '--http-timeout', '0.05'])
            with contextlib.redirect_stdout(io.StringIO()):
                code = boot.run(args)
            result = json.loads((path / 'metadata.json').read_text())
            self.assertEqual(code, 0)
            self.assertEqual(set(result['readiness']), {'tcp', 'http'})
            self.assertEqual(result['http_readiness_requirement'], 'HTTP 200 with valid JSON')
            self.assertFalse(result['http_response_body_saved'])
            self.assertGreaterEqual(server.request_count, 3)
            for artifact in path.iterdir():
                self.assertNotIn(b'DO_NOT_SAVE_THIS_BODY', artifact.read_bytes())

    def test_http_only_is_allowed_but_json_requires_url(self):
        args = boot.arguments(['--output-dir', '/tmp/unused', '--http-url', 'http://127.0.0.1/'])
        self.assertIsNone(args.host)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            boot.arguments(['--output-dir', '/tmp/unused', '--host', '127.0.0.1', '--http-json'])


if __name__ == '__main__':
    unittest.main()
