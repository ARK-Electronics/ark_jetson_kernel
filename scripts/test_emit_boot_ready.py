#!/usr/bin/env python3
"""Exercise the actual HTTP probe without target hardware or stored response bodies."""
import contextlib
import copy
import http.server
import json
from pathlib import Path
import subprocess
import sys
import threading
import unittest

import emit_boot_ready as probe


READY = {
    "device_type": "jetson",
    "hardware": {"type": "jetson", "model": "NVIDIA Jetson Orin NX",
                 "module": "NVIDIA Jetson Orin NX 16GB", "l4t": "36.5.0",
                 "serial_number": "MUST-NOT-APPEAR-IN-OUTPUT"},
}
FALLBACK = {"device_type": "jetson", "hardware": {
    "type": None, "model": "Not available", "module": "Not available", "l4t": "Not available"}}


@contextlib.contextmanager
def serve(responses):
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            status, body = responses[min(len(requests) - 1, len(responses) - 1)]
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            if status == 302:
                self.send_header("Location", "/redirected")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def run_probe(url, *flags):
    return subprocess.run([sys.executable, str(Path(probe.__file__)), "--url", url,
                           "--timeout", "0.4", *flags], text=True, capture_output=True, timeout=3)


class ReadinessTests(unittest.TestCase):
    def test_real_collector_metadata_is_ready_without_optional_libraries_or_serial(self):
        payload = copy.deepcopy(READY)
        payload["hardware"].pop("serial_number")
        self.assertTrue(probe.ark_os_ready(payload))

    def test_fallback_and_malformed_shapes_are_not_ready(self):
        for payload in (None, True, [], {}, FALLBACK, {"device_type": "jetson", "hardware": []}):
            with self.subTest(payload=payload):
                self.assertFalse(probe.ark_os_ready(payload))
        for container, key, values in (
            (None, "device_type", (None, "modalix", "pi")),
            ("hardware", "type", (None, "pi", "")),
            ("hardware", "model", (None, 42, {}, "", " Unknown ")),
            ("hardware", "module", (None, "Not available", "n/a")),
            ("hardware", "l4t", (None, "", "not AVAILABLE", "null")),
        ):
            for value in values:
                payload = copy.deepcopy(READY)
                (payload[container] if container else payload)[key] = value
                with self.subTest(key=key, value=value):
                    self.assertFalse(probe.ark_os_ready(payload))

    def test_generic_mode_keeps_accepting_other_valid_json(self):
        for payload in (FALLBACK, ["customer-specific"], None):
            with serve([(200, json.dumps(payload).encode())]) as (url, _):
                result = run_probe(url)
            self.assertEqual(result.returncode, 0, result.stderr)
            record = json.loads(result.stdout.split(" ", 1)[1])
            self.assertNotIn("criterion", record)

    def test_transient_errors_and_fallback_wait_for_real_metadata(self):
        sequence = [(503, b"unavailable"), (200, b"not JSON"),
                    (200, json.dumps(FALLBACK).encode()), (200, json.dumps(READY).encode())]
        with serve(sequence) as (url, requests):
            result = run_probe(url, "--ark-os-ready")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreaterEqual(len(requests), 4)
        self.assertTrue(result.stdout.startswith("JAJ_API_READY "))
        record = json.loads(result.stdout.split(" ", 1)[1])
        self.assertEqual(record["criterion"], "ark_os_metadata")
        self.assertEqual(set(record), {"criterion", "kernel_uptime_s", "probe_elapsed_s"})
        self.assertNotIn("MUST-NOT-APPEAR", result.stdout + result.stderr)
        self.assertNotIn("serial_number", result.stdout + result.stderr)

    def test_fallback_times_out_without_a_ready_marker(self):
        with serve([(200, json.dumps(FALLBACK).encode())]) as (url, _):
            result = run_probe(url, "--ark-os-ready")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("Jetson metadata", result.stderr)

    def test_http_errors_redirects_non_json_constants_and_oversize_still_fail(self):
        for status, body in ((500, json.dumps(READY).encode()), (302, json.dumps(READY).encode()),
                             (200, b'NaN'), (200, b'"' + b'x' * probe.MAX_BODY + b'"')):
            with self.subTest(status=status, length=len(body)):
                with serve([(status, body)]) as (url, requests):
                    result = run_probe(url, "--ark-os-ready")
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertTrue(all(path == "/" for path in requests))


if __name__ == "__main__":
    unittest.main()
