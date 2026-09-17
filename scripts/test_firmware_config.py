#!/usr/bin/env python3
"""Catch the Noble compressed-firmware regression before any kernel compile."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from check_firmware_config import check_config

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "scripts/check_firmware_config.py"
TARGETS = ("JAJ", "PAB", "PAB_V3")
VALID = "CONFIG_FW_LOADER=y\nCONFIG_FW_LOADER_COMPRESS=y\nCONFIG_FW_LOADER_COMPRESS_ZSTD=y\n"
BROKEN = "CONFIG_FW_LOADER=y\n# CONFIG_FW_LOADER_COMPRESS is not set\nCONFIG_ZSTD_DECOMPRESS=y\n"


class FirmwareConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "config"

    def test_shared_fragment_supplies_all_products_without_firmware_changes(self):
        shared = (REPO / "defconfig.fragment").read_text()
        for target in TARGETS:
            with self.subTest(target=target):
                product = REPO / "products" / target / "defconfig.fragment"
                source = "CONFIG_FW_LOADER=y\n" + shared
                if product.exists():
                    source += "\n" + product.read_text()
                self.config.write_text(source)
                check_config(self.config)
                self.assertEqual(self.config.read_text(), source)

    def test_shipped_broken_config_and_missing_zstd_support_are_rejected_for_each_target(self):
        for target in TARGETS:
            for source in (BROKEN, VALID.replace("CONFIG_FW_LOADER_COMPRESS_ZSTD=y", "# CONFIG_FW_LOADER_COMPRESS_ZSTD is not set")):
                with self.subTest(target=target, source=source):
                    self.config.write_text(source)
                    result = subprocess.run(["python3", str(CHECK), "--target", target, str(self.config)], capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("CONFIG_FW_LOADER_COMPRESS_ZSTD=y", result.stderr)
                    self.assertEqual(self.config.read_text(), source)

    def test_real_build_preflight_stops_compile_on_missing_effective_options(self):
        build = (REPO / "build.sh").read_text()
        start = build.index("# R39 ships compressed firmware.")
        end = build.index('\nmake modules CC=', start)
        body = build[start:end]
        kernel = self.root / "kernel"
        kernel.mkdir()
        compiled = self.root / "compiled"
        shell = '''set -e
KERNEL_MAKE_ARGS=()
make() {
    if [ "$2" = kernel ]; then
        touch "$COMPILE_MARKER"
    else
        cp "$EFFECTIVE_FIXTURE" "$KERNEL_HEADERS/.config"
    fi
}
''' + body
        for target in TARGETS:
            for valid in (False, True):
                with self.subTest(target=target, valid=valid):
                    compiled.unlink(missing_ok=True)
                    self.config.write_text(VALID if valid else BROKEN)
                    env = dict(os.environ, EXPECTED_BSP_RELEASE="R39", TARGET=target,
                               SCRIPT_DIR=str(REPO), KERNEL_HEADERS=str(kernel),
                               COMPILE_MARKER=str(compiled), EFFECTIVE_FIXTURE=str(self.config))
                    result = subprocess.run(["bash", "-c", shell], env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode == 0, valid, result.stderr)
                    self.assertEqual(compiled.exists(), valid)


if __name__ == "__main__":
    unittest.main()
