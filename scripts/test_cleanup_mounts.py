#!/usr/bin/env python3
import tempfile
import unittest
from pathlib import Path

from check_cleanup_mounts import mounts_under


def record(path):
    return f"30 20 0:1 / {path} rw - tmpfs tmpfs rw\n"


class CleanupMountTests(unittest.TestCase):
    def test_nested_and_exact_mounts_block_but_sibling_does_not(self):
        data = record("/staging/JAJ") + record("/staging/JAJ/rootfs/dev") + record("/staging/JAJ2")
        self.assertEqual(mounts_under("/staging/JAJ/", data),
                         ["/staging/JAJ", "/staging/JAJ/rootfs/dev"])

    def test_escaped_space_and_symlink_are_resolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "build tree"
            tree.mkdir()
            link = Path(tmp) / "link"
            link.symlink_to(tree)
            data = record(str(tree / "rootfs/dev").replace(" ", r"\040"))
            self.assertEqual(mounts_under(link, data), [str(tree / "rootfs/dev")])

    def test_root_and_malformed_input_fail_closed(self):
        with self.assertRaises(ValueError):
            mounts_under("/", "")
        with self.assertRaises(ValueError):
            mounts_under("/staging", "bad input")


if __name__ == "__main__":
    unittest.main()
