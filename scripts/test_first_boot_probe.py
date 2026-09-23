#!/usr/bin/env python3
import socket
import subprocess
import threading
import unittest
from pathlib import Path


class FirstBootProbeTests(unittest.TestCase):
    def probe(self, payload):
        root = Path(__file__).resolve().parents[1]
        functions = []
        for script in (root / "flash.sh", root / "packaging/flash_from_package.sh"):
            source = script.read_text()
            start = source.index("ssh_banner_ready() {")
            functions.append(source[start:source.index("\n}\n", start) + 3])
        self.assertEqual(*functions)
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            server.settimeout(5)

            def respond():
                with server.accept()[0] as client:
                    client.sendall(payload)

            worker = threading.Thread(target=respond)
            worker.start()
            try:
                result = subprocess.run(
                    ["bash", "-c", functions[0] + '\nssh_banner_ready "$1" "$2"',
                     "--", "127.0.0.1", str(server.getsockname()[1])], timeout=5)
            finally:
                worker.join(timeout=6)
        return result.returncode

    def test_accepts_ssh_identification(self):
        self.assertEqual(self.probe(b"SSH-2.0-OpenSSH_9.6\r\n"), 0)

    def test_tcp_only_is_not_ready(self):
        self.assertNotEqual(self.probe(b""), 0)

    def test_unrelated_service_is_not_ready(self):
        self.assertNotEqual(self.probe(b"HTTP/1.1 200 OK\r\n"), 0)


if __name__ == "__main__":
    unittest.main()
