#!/usr/bin/env python3
"""Synthetic guard fixtures and executable Bluetooth module-order checks."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import patch_bluetooth_preference as helper


class BluetoothPreferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.original = ('# Synthetic fixture; not a vendor configuration copy.\n'
                         + helper.BEFORE + '\ninstall fixture_net /bin/true\n').encode()
        self.updated = self.original.replace(helper.BEFORE.encode(), helper.AFTER.encode())
        for field, data in (("ORIGINAL", self.original), ("PATCHED", self.updated)):
            item = patch.object(helper, field, helper.digest(data))
            item.start()
            self.addCleanup(item.stop)
        self.write('etc/nv_tegra_release', b'# R39 (release), REVISION: 2.1, fixture\n')
        self.write('etc/ark_jetson_kernel', b'target=JAJ\n')
        self.source = self.write(helper.SOURCE, self.original)
        self.source.chmod(0o640)

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_all_products_roundtrip_and_metadata(self):
        info = self.source.stat()
        for product in helper.PRODUCTS:
            with self.subTest(product=product):
                self.write('etc/ark_jetson_kernel', f'target={product}\n'.encode())
                self.assertEqual(helper.update(self.root, product), 'patched')
                self.assertEqual(self.source.read_bytes(), self.updated)
                self.assertEqual(helper.update(self.root, product), 'patched')
                self.assertEqual(helper.update(self.root, product, 'check'), 'patched')
                self.assertEqual(helper.update(self.root, product, 'restore'), 'original')
                self.assertEqual(helper.update(self.root, product, 'restore'), 'original')
                self.assertEqual(self.source.read_bytes(), self.original)
        after = self.source.stat()
        self.assertEqual((info.st_mode, info.st_uid, info.st_gid),
                         (after.st_mode, after.st_uid, after.st_gid))

    def test_wrong_release_product_and_changed_vendor_fail_without_writes(self):
        self.write('etc/nv_tegra_release', b'# R36 (release), REVISION: 5.0, fixture\n')
        with self.assertRaisesRegex(ValueError, 'R39.2.1'):
            helper.update(self.root, 'JAJ')
        self.write('etc/nv_tegra_release', b'# R39 (release), REVISION: 2.1, fixture\n')
        with self.assertRaisesRegex(ValueError, 'product stamp'):
            helper.update(self.root, 'PAB')
        self.assertEqual(self.source.read_bytes(), self.original)
        self.source.write_bytes(self.original + b'# customer change\n')
        for mode in ('apply', 'check', 'restore'):
            with self.assertRaisesRegex(ValueError, 'audited'):
                helper.update(self.root, 'JAJ', mode)
        self.assertTrue(self.source.read_bytes().endswith(b'# customer change\n'))

    def test_symlink_is_rejected(self):
        external = self.write('external.conf', self.original)
        self.source.unlink()
        self.source.symlink_to(external)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            helper.update(self.root, 'JAJ')
        self.assertEqual(external.read_bytes(), self.original)

    def test_failed_publish_leaves_original_and_removes_temp(self):
        with patch.object(Path, 'replace', side_effect=OSError('fixture failure')):
            with self.assertRaisesRegex(OSError, 'fixture failure'):
                helper.update(self.root, 'JAJ')
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertFalse(list(self.source.parent.glob('.ark-btusb-*')))

    def run_rule(self, present=True, rtk_status=0, generic_status=0):
        helper.update(self.root, 'JAJ')
        # Parse the installed rule's continuation syntax, then run its shell
        # command with fake modinfo/modprobe executables, never host modules.
        logical = self.source.read_text().replace('\\\n', '')
        command = next(line.removeprefix('install btusb ')
                       for line in logical.splitlines() if line.startswith('install btusb '))
        modinfo = self.write('fake-modinfo', b'#!/bin/sh\nexit "$PRESENT_STATUS"\n')
        modprobe = self.write('fake-modprobe', b'''#!/bin/sh
printf '%s\\n' "$*" >> "$CALL_LOG"
if [ "$1" = rtk_btusb ]; then exit "$RTK_STATUS"; fi
exit "$GENERIC_STATUS"
''')
        modinfo.chmod(0o755)
        modprobe.chmod(0o755)
        command = command.replace('/sbin/modinfo', str(modinfo)).replace('/sbin/modprobe', str(modprobe))
        env = {**os.environ, 'PRESENT_STATUS': '0' if present else '1',
               'RTK_STATUS': str(rtk_status), 'GENERIC_STATUS': str(generic_status),
               'CMDLINE_OPTS': 'reset=0 enable_autosuspend=0',
               'CALL_LOG': str(self.root / 'calls')}
        result = subprocess.run(['sh', '-c', command], env=env, capture_output=True, text=True)
        log = self.root / 'calls'
        return result.returncode, log.read_text().splitlines() if log.exists() else []

    def test_realtek_first_then_generic_with_options(self):
        status, calls = self.run_rule()
        self.assertEqual(status, 0)
        self.assertEqual(calls, ['rtk_btusb', '--ignore-install btusb reset=0 enable_autosuspend=0'])

    def test_missing_realtek_still_loads_generic(self):
        status, calls = self.run_rule(present=False)
        self.assertEqual(status, 0)
        self.assertEqual(calls, ['--ignore-install btusb reset=0 enable_autosuspend=0'])

    def test_realtek_load_failure_is_visible_without_reversing_preference(self):
        status, calls = self.run_rule(rtk_status=17)
        self.assertEqual(status, 17)
        self.assertEqual(calls, ['rtk_btusb'])

    def test_generic_failure_propagates(self):
        status, calls = self.run_rule(generic_status=19)
        self.assertEqual(status, 19)
        self.assertEqual(calls[0], 'rtk_btusb')

    def test_build_hook_is_r39_only(self):
        source = (Path(__file__).resolve().parents[1] / 'build.sh').read_text()
        hook = re.search(r'if \[ "\$EXPECTED_BSP_RELEASE" = R39 \]; then\n(?:(?!\nfi).)*patch_bluetooth_preference.py(?:(?!\nfi).)*\nfi', source, re.S)
        self.assertIsNotNone(hook)
        self.assertIn('--rootfs "$L4T_DIR/rootfs" --product "$TARGET"', hook.group(0))


if __name__ == '__main__':
    unittest.main()
