#!/usr/bin/env python3
"""Compile actual patched NVIDIA registration/promotion/failure code on the host."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import test_uefi_pcie_filter as guard_tests

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / 'products/JAJ/fastboot/r39.2.1'
FIXTURE = ROOT / 'scripts/fixtures/uefi_boot_order'
SPEC = importlib.util.spec_from_file_location('uefi_single_boot_order', PROFILE / 'uefi_single_boot_order.py')
HELPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)


class SourceGuardTests(guard_tests.PatchGuardTests):
    def setUp(self):
        super().setUp()
        swap = patch.object(guard_tests, 'FILTER', HELPER)
        swap.start()
        self.addCleanup(swap.stop)


class BootOrderTests(unittest.TestCase):
    def test_real_patched_registration_promotion_and_diagnostics(self):
        fixture = json.loads((FIXTURE / 'vendor-sections.json').read_text())
        self.assertEqual(fixture['source_sha256'], HELPER.BEFORE)
        self.assertEqual(HELPER.digest(HELPER.PATCH), HELPER.PATCH_HASH)
        lines = ['\n'] * max(part['line'] + len(part['lines']) for part in fixture['sections'])
        for part in fixture['sections']:
            start = part['line'] - 1
            lines[start:start + len(part['lines'])] = part['lines']
        with tempfile.TemporaryDirectory(prefix='uefi-order-harness-') as temporary:
            root = Path(temporary)
            source = root / HELPER.SOURCE
            source.parent.mkdir(parents=True)
            original = ''.join(lines)
            source.write_text(original)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            subprocess.run(['git', '-C', str(root), 'apply', str(HELPER.PATCH)], check=True)
            patched = source.read_text()
            functions = []
            for name in ('PlatformPromoteBootOption', 'PlatformRegisterFvBootOption',
                         'SingleBootStatusCodeCallback'):
                location = patched.index(name + ' (')
                start = patched.rfind('\nSTATIC\n', 0, location) + 1
                brace = patched.index('{', location)
                depth = 1
                end = brace + 1
                while depth:
                    depth += (patched[end] == '{') - (patched[end] == '}')
                    end += 1
                functions.append(patched[start:end])
            self.assertIn('LoadOptionTypeBoot,\n      0\n', patched)
            self.assertEqual(patched.count('LoadOptionTypeBoot,\n          MAX_UINTN\n'), 2)
            harness = root / 'harness.c'
            harness.write_text((FIXTURE / 'harness.c.in').read_text().replace(
                '@@FUNCTIONS@@', '\n'.join(functions)))
            binary = root / 'harness'
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                            '-Wno-unused-parameter', str(harness), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)
            subprocess.run(['git', '-C', str(root), 'apply', '--reverse', str(HELPER.PATCH)], check=True)
            self.assertEqual(source.read_text(), original)


if __name__ == '__main__':
    unittest.main()
