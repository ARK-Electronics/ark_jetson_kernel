#!/usr/bin/env python3
"""Exercise the guarded USB transform without a USB device or running udev."""
import base64
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import configure_fast_boot as profile


FIXTURES = Path(__file__).parent / "testdata/usb-udev"


class ScopedUSBTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="test-scoped-usb-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "rootfs"
        shutil.copytree(FIXTURES, self.root)
        (self.root / "usr/lib").mkdir(parents=True)
        (self.root / "lib").symlink_to("usr/lib")
        self.system = self.root / profile.SYSTEM
        self.system.mkdir(parents=True)
        (self.system / "default.target").symlink_to("/lib/systemd/system/graphical.target")
        for unit in (profile.USB_MAIN, profile.USB_RUNTIME):
            (self.system / unit).symlink_to(f"/{profile.USB_DIR}/{unit}")
        (self.root / "etc/nv_tegra_release").write_text("# R36 (release), REVISION: 5.0, GCID: TEST\n")
        self.status = self.root / "var/lib/dpkg/status"
        self.status.parent.mkdir(parents=True)
        self.status.write_text("Package: udev\nStatus: install ok installed\nVersion: 249.11-0ubuntu3.17\n\n")
        for relative in ("usr/bin/timeout", "bin/udevadm"):
            p = self.root / relative
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"\x7fELFfixture; never execute target binaries")
            p.chmod(0o755)
        # Keep real BSD-licensed vendor scripts and their real pinned hashes.
        # Rule inventories use synthetic content so tests need not redistribute
        # NVIDIA proprietary rules. Production hashes are checked separately
        # against each staged board without modifying those root filesystems.
        self.audit = json.loads(profile.USB_UDEV_AUDIT_PATH.read_text())
        for relative in self.audit["rules"]:
            p = self.root / relative
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# synthetic inventory fixture " + relative + "\n")
            self.audit["rules"][relative] = hashlib.sha256(p.read_bytes()).hexdigest()
        self.optional_rule = next(iter(self.audit["optional_rules"]))
        self.optional_content = b'# synthetic reviewed GPIO-only rule\nSUBSYSTEM=="gpio", MODE="0660"\n'
        self.audit["optional_rules"][self.optional_rule] = hashlib.sha256(self.optional_content).hexdigest()
        self.audit_path = Path(self.temp.name) / "audit.json"
        self.audit_path.write_text(json.dumps(self.audit))
        patcher = mock.patch.object(profile, "USB_UDEV_AUDIT_PATH", self.audit_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.start = self.root / profile.USB_START
        self.start.chmod(0o750)
        self.original = self.files()

    def files(self):
        return {str(p.relative_to(self.root)): profile.snapshot(p)
                for p in self.root.rglob("*") if p.is_file() or p.is_symlink()}

    def apply(self, enabled=True, direct_usb=False):
        with contextlib.redirect_stdout(io.StringIO()):
            profile.apply(self.root, False, False, scoped_usb=enabled, direct_usb=direct_usb)

    def restore(self):
        with contextlib.redirect_stdout(io.StringIO()):
            profile.restore(self.root)

    def reject_without_mutation(self, pattern):
        before = self.files()
        with self.assertRaisesRegex(ValueError, pattern):
            self.apply()
        self.assertEqual(self.files(), before)

    def test_default_leaves_usb_script_untouched(self):
        self.apply(enabled=False)
        self.assertEqual(profile.snapshot(self.start), self.original[profile.USB_START])
        self.assertFalse(profile.read_manifest(self.root)["scoped_usb_udev"])
        self.restore()
        self.assertEqual(self.files(), self.original)

    def test_only_scoped_wait_and_filters_change_with_exact_restore(self):
        before = self.start.read_text()
        self.apply()
        after = self.start.read_text()
        self.assertEqual(hashlib.sha256(after.encode()).hexdigest(), profile.USB_START_SCOPED_HASH)
        # Everything preceding local loop-device creation, including all gadget
        # functions, descriptors, bridges and image population, is untouched.
        boundary = '# Create a local disk device'
        self.assertEqual(before.split(boundary)[0], after.split(boundary)[0])
        self.assertNotIn('\nudevadm settle\n', after)
        self.assertNotIn('--property-match=', after)
        self.assertIn('--settle --action=change "${loop_dev}"', after)
        self.assertIn('--subsystem-match=android_usb', after)
        self.assertIn('--subsystem-match=usb_role', after)
        self.assertEqual(self.start.stat().st_mode & 0o777, 0o750)
        manifest = profile.read_manifest(self.root)
        self.assertTrue(manifest["scoped_usb_udev"])
        self.assertEqual(set(manifest["changes"]), {profile.USB_START, profile.SYSTEM + '/default.target'})
        subprocess.run(['bash', '-n', str(self.start)], check=True)
        self.apply()
        self.restore()
        self.assertEqual(self.files(), self.original)

    def test_combines_with_native_runtime_calls_and_preserves_every_unit(self):
        self.apply(direct_usb=True)
        handler = (self.root / profile.USB_HANDLER).read_text()
        self.assertIn('/bin/systemctl start ' + profile.USB_RUNTIME, handler)
        self.apply(direct_usb=True)
        for unit in (profile.USB_MAIN, profile.USB_RUNTIME):
            relative = f'{profile.USB_DIR}/{unit}'
            self.assertEqual(profile.snapshot(self.root / relative), self.original[relative])
        self.restore()
        self.assertEqual(self.files(), self.original)

    def test_vendor_drift_and_unknown_release_or_udev_are_rejected(self):
        for relative in self.audit['files']:
            with self.subTest(relative=relative):
                p = self.root / relative
                original = p.read_bytes()
                p.write_bytes(original + b'# custom change\n')
                self.reject_without_mutation('vendor file hash')
                p.write_bytes(original)
        self.status.write_text(self.status.read_text().replace('249.', '252.'))
        self.reject_without_mutation('udev 249')
        self.status.write_bytes(base64.b64decode(self.original['var/lib/dpkg/status']['data']))
        (self.root / 'etc/nv_tegra_release').write_text('# R36 (release), REVISION: 4.0,\n')
        self.reject_without_mutation('R36.5.0')

    def test_unknown_added_changed_removed_or_masked_rules_rejected(self):
        p = self.root / next(iter(self.audit['rules']))
        original = p.read_bytes()
        p.write_bytes(original + b'RUN+="/custom/dependency"\n')
        self.reject_without_mutation('rule inventory differs')
        p.unlink()
        self.reject_without_mutation('rule inventory differs')
        p.symlink_to('/dev/null')
        self.reject_without_mutation('rule alias/mask')
        p.unlink()
        p.write_bytes(original)
        for directory in ('etc/udev/rules.d', 'run/udev/rules.d', 'usr/local/lib/udev/rules.d'):
            added = self.root / directory / '00-custom.rules'
            added.parent.mkdir(parents=True, exist_ok=True)
            added.write_text('RUN+="/custom/wait"\n')
            self.reject_without_mutation('rule inventory differs')
            added.unlink()

    def test_reviewed_optional_ark_rule_only(self):
        p = self.root / self.optional_rule
        p.write_bytes(self.optional_content + b'# unknown\n')
        self.reject_without_mutation('unknown optional udev rule')
        p.write_bytes(self.optional_content)
        self.apply()
        self.assertEqual(p.read_bytes(), self.optional_content)

    def test_only_exact_reviewed_network_rule_variant_is_allowed(self):
        relative = 'usr/lib/udev/rules.d/75-net-description.rules'
        path = self.root / relative
        original = path.read_bytes()
        alternate = b'# synthetic reviewed net_id ordering variant\n'
        self.audit['rule_variants'][relative] = [hashlib.sha256(alternate).hexdigest()]
        self.audit_path.write_text(json.dumps(self.audit))
        path.write_bytes(alternate)
        self.apply()
        self.restore()
        self.assertEqual(path.read_bytes(), alternate)
        path.write_bytes(alternate + b'RUN+="/custom/dependency"\n')
        self.reject_without_mutation('rule inventory differs')
        path.unlink()
        self.reject_without_mutation('rule inventory differs')
        path.write_bytes(original)
        self.apply()
        self.restore()

    def test_optional_fwupd_requires_exact_pinned_contents_if_present(self):
        relative = 'usr/lib/udev/rules.d/90-fwupd-devices.rules'
        path = self.root / relative
        content = b'# synthetic reviewed fwupd rule\n'
        self.audit['optional_rules'][relative] = hashlib.sha256(content).hexdigest()
        self.audit_path.write_text(json.dumps(self.audit))
        self.assertFalse(path.exists())
        self.apply()
        self.restore()
        path.write_bytes(content)
        self.apply()
        self.restore()
        self.assertEqual(path.read_bytes(), content)
        path.write_bytes(content + b'RUN+="/custom/dependency"\n')
        self.reject_without_mutation('unknown optional udev rule')

    def test_production_rule_exceptions_are_explicitly_pinned(self):
        audit = json.loads((Path(__file__).resolve().parents[1] /
                           'products/JAJ/fastboot/usb-udev-audit.json').read_text())
        self.assertEqual(audit['rule_variants'], {
            'usr/lib/udev/rules.d/75-net-description.rules': [
                'b4ffc9178da4c87f9844bb7534ef0e1f7fa9fd7d3e7874195d9b2813d9b94d23']})
        self.assertEqual(audit['optional_rules'], {
            'etc/udev/rules.d/99-ark-gpio.rules':
                '627710371111e347a775930d46fb906760fbcce9a41809ab333623e07dfd7d4a',
            'usr/lib/udev/rules.d/90-fwupd-devices.rules':
                '473e4621935ba91b07321d76d7f9c896936a1dbaab6a29c0d0e3233cfe6ffdce'})

    def test_custom_unit_ordering_or_overrides_rejected(self):
        for relative in (f'{profile.SYSTEM}/{profile.USB_RUNTIME}.d/override.conf',
                         'usr/lib/systemd/system/nv-.service.d/custom.conf'):
            p = self.root / relative
            p.parent.mkdir(parents=True)
            p.write_text('[Unit]\nAfter=' + profile.USB_MAIN + '\n')
            self.reject_without_mutation('drop-ins')
            p.unlink()
            p.parent.rmdir()
        p = self.system / profile.USB_MAIN
        p.unlink()
        p.symlink_to('/dev/null')
        self.reject_without_mutation('override/mask')

    def test_missing_timeout_is_not_silently_unbounded(self):
        p = self.root / 'usr/bin/timeout'
        p.unlink()
        self.reject_without_mutation('installed executable')

    def test_status_and_reapply_reaudit_vendor_rules(self):
        self.apply()
        p = self.root / next(iter(self.audit['rules']))
        p.write_text('# changed after apply\n')
        with self.assertRaisesRegex(ValueError, 'rule inventory differs'):
            self.apply()
        with mock.patch('sys.argv', ['configure_fast_boot.py', 'status', str(self.root)]):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(profile.main(), 1)
        self.restore()
        self.assertEqual(profile.snapshot(self.start), self.original[profile.USB_START])
        self.assertEqual(p.read_text(), '# changed after apply\n')

    def test_scoped_loop_failure_stops_role_replay_and_success_runs_both(self):
        self.apply()
        content = self.start.read_text()
        tail = content[content.index('# Wait only for this loop device;'):]
        program = Path(self.temp.name) / 'tail.sh'
        program.write_text('set -e\nloop_dev=/dev/loop42\n' + tail)
        bindir = Path(self.temp.name) / 'bin'
        bindir.mkdir()
        fake = bindir / 'udevadm'
        fake.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALL_LOG"\n'
                        'case "$*" in *--settle*) exit "$LOOP_RESULT";; esac\n')
        fake.chmod(0o755)
        log = Path(self.temp.name) / 'calls'
        for result in ('0', '42'):
            if log.exists():
                log.unlink()
            run = subprocess.run(['bash', str(program)], env={**os.environ,
                'PATH': str(bindir) + os.pathsep + os.environ.get('PATH', ''),
                'CALL_LOG': str(log), 'LOOP_RESULT': result}, capture_output=True, text=True)
            self.assertEqual(run.returncode, int(result), run.stderr)
            commands = log.read_text().splitlines()
            self.assertEqual(commands[0], 'trigger --settle --action=change /dev/loop42')
            self.assertEqual(commands[1:], [] if result != '0' else [
                'trigger -v --action=change --subsystem-match=android_usb',
                'trigger -v --action=change --subsystem-match=usb_role'])

    def test_cli_reports_explicit_option(self):
        with mock.patch('sys.argv', ['configure_fast_boot.py', 'apply', str(self.root), '--scoped-usb-udev']):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(profile.main(), 0)
        self.assertTrue(profile.read_manifest(self.root)['scoped_usb_udev'])


if __name__ == '__main__':
    unittest.main()
