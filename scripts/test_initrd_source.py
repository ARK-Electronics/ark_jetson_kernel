#!/usr/bin/env python3
"""Check that repeat builds cannot silently reuse an optimized init script."""

import gzip
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

from check_initrd_source import check_source
from optimize_initrd import CHECKSUM_FILE, MARKER, STOCK_DEPMOD, encode_record


def image(init=b"#!/bin/bash\n" + STOCK_DEPMOD, checksum=False):
    fields = [1, stat.S_IFREG | 0o755, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0]
    data = encode_record("init", fields, init)
    if checksum:
        data += encode_record(CHECKSUM_FILE, fields, b"old module hashes\n")
    data += encode_record("TRAILER!!!", fields, b"")
    return gzip.compress(data, mtime=0)


class InitrdSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="test-initrd-source-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "initrd"

    def test_stock_image_can_be_refreshed(self):
        self.path.write_bytes(image())
        before = self.path.read_bytes()
        check_source(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_optimizer_marker_is_rejected(self):
        self.path.write_bytes(image(init=b"#!/bin/bash\n# " + MARKER.encode()))
        with self.assertRaisesRegex(ValueError, "Already optimized"):
            check_source(self.path)

    def test_orphaned_optimizer_checksum_is_rejected(self):
        self.path.write_bytes(image(checksum=True))
        with self.assertRaisesRegex(ValueError, "Already optimized"):
            check_source(self.path)

    def test_unreadable_archive_is_rejected(self):
        self.path.write_bytes(gzip.compress(b"not a newc archive"))
        with self.assertRaisesRegex(ValueError, "newc archive"):
            check_source(self.path)

    def test_partial_promotion_checks_both_copies_without_changing_them(self):
        self.path.write_bytes(image())
        other = self.path.with_name("l4t_initrd.img")
        other.write_bytes(image(checksum=True))
        before = [self.path.read_bytes(), other.read_bytes()]
        tool = Path(__file__).with_name("check_initrd_source.py")
        result = subprocess.run([sys.executable, str(tool), "--target", "JAJ",
                                 str(self.path), str(other)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("restore BOTH", result.stderr)
        self.assertEqual([self.path.read_bytes(), other.read_bytes()], before)


if __name__ == "__main__":
    unittest.main()
