#!/usr/bin/env python3
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from find_kernel_headers import find_headers, target_path


class KernelHeaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.release = "6.8.12-1021-tegra"
        self.package = self.root / "usr/src" / ("linux-headers-" + self.release + "-ubuntu24.04_aarch64")

    def prepare(self, tree, release=None):
        (tree / "include/config").mkdir(parents=True)
        (tree / "include/generated").mkdir()
        (tree / "include/config/kernel.release").write_text((release or self.release) + "\n")
        (tree / "include/generated/autoconf.h").touch()
        (tree / "Makefile").touch()

    def test_finds_nested_noble_and_legacy_kbuild_roots(self):
        for suffix in ("3rdparty/canonical/linux-noble", "3rdparty/canonical/kernel-source", ""):
            with self.subTest(suffix=suffix):
                tree = self.package / suffix
                self.prepare(tree)
                self.assertEqual(find_headers(self.root, self.release), tree)
                (tree / "include/config/kernel.release").unlink()

    def test_symlinked_staging_installs_device_header_path(self):
        tree = self.package / "3rdparty/canonical/linux-noble"
        self.prepare(tree)
        workspace = self.root / "workspace"
        workspace.mkdir()
        staged = workspace / "staging"
        staged.symlink_to(self.root, target_is_directory=True)
        self.assertEqual(find_headers(staged, self.release), tree)
        expected = "/" + str(tree.relative_to(self.root))
        self.assertEqual(str(target_path(staged, tree)), expected)
        # Run the actual build assignment, catching host-prefix substitutions
        # which work in a normal checkout but fail with CI's staging symlink.
        repo = Path(__file__).resolve().parents[1]
        assignment = re.search(r'^HEADERS_TARGET=(?:.*\\\n)*.*$',
                               (repo / "build.sh").read_text(), re.M).group(0)
        result = subprocess.run(
            ["bash", "-e", "-c", assignment + '\nprintf "%s" "$HEADERS_TARGET"'],
            env={**os.environ, "SCRIPT_DIR": str(repo),
                 "INSTALL_MOD_PATH": str(staged) + "/",
                 "JETSON_KERNEL_VERSION": self.release},
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, expected)
        self.assertTrue(result.stdout.startswith("/usr/src/"))

    def test_target_path_rejects_headers_outside_rootfs(self):
        outside = self.root / "outside"
        outside.mkdir()
        rootfs = self.root / "rootfs"
        rootfs.mkdir()
        with self.assertRaises(ValueError):
            target_path(rootfs, outside)

    def test_rejects_wrong_abi_and_missing_prepared_headers(self):
        tree = self.package / "3rdparty/canonical/linux-noble"
        self.prepare(tree, "6.8.12-tegra")
        with self.assertRaises(ValueError):
            find_headers(self.root, self.release)
        (tree / "include/config/kernel.release").write_text(self.release)
        (tree / "include/generated/autoconf.h").unlink()
        with self.assertRaises(ValueError):
            find_headers(self.root, self.release)

    def test_ambiguous_prepared_trees_fail(self):
        self.prepare(self.package / "one")
        self.prepare(self.package / "two")
        with self.assertRaises(ValueError):
            find_headers(self.root, self.release)


if __name__ == "__main__":
    unittest.main()
