#!/usr/bin/env python3
"""Noble-specific guards using real vendor units and synthetic rule/profile bodies."""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest import mock

import configure_fast_boot as profile
import test_rsyslog_kernel_profile as logging_fixture
import test_scoped_usb_udev as usb_fixture


class NobleScopedUSBTests(unittest.TestCase):
    def setUp(self):
        self.fixture = usb_fixture.ScopedUSBTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        for relative in f.audit['rules']:
            (f.root / relative).unlink()
        source_root = Path(__file__).parent / 'testdata/usb-udev-r39'
        for source in source_root.rglob('*'):
            if source.is_file():
                target = f.root / source.relative_to(source_root)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        (f.root / 'etc/nv_tegra_release').write_text('# R39 (release), REVISION: 2.1, GCID: TEST\n')
        (f.root / 'etc/os-release').write_text('ID=ubuntu\nVERSION_ID="24.04"\n')
        f.status.write_text('Package: udev\nStatus: install ok installed\nVersion: 255.4-1ubuntu8.16\n\n')
        f.audit = json.loads(profile.USB_UDEV_R39_AUDIT_PATH.read_text())
        for relative in f.audit['rules']:
            p = f.root / relative
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('# synthetic Noble rule ' + relative + '\n')
            f.audit['rules'][relative] = hashlib.sha256(p.read_bytes()).hexdigest()
        f.audit_path.write_text(json.dumps(f.audit))
        patcher = mock.patch.object(profile, 'USB_UDEV_R39_AUDIT_PATH', f.audit_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        f.original = f.files()

    def test_exact_transform_direct_runtime_and_rollback(self):
        f = self.fixture
        before = f.start.read_text()
        f.apply(direct_usb=True)
        after = f.start.read_text()
        self.assertEqual(hashlib.sha256(after.encode()).hexdigest(), f.audit['scoped_start_sha256'])
        self.assertEqual(before.split('# Create a local disk device')[0],
                         after.split('# Create a local disk device')[0])
        self.assertIn('modprobe tegra_xudc', after)
        self.assertIn('/sbin/ip link add name l4tbr0 type bridge', after)
        self.assertIn('a808670000.usb', after)
        f.apply(direct_usb=True)
        f.restore()
        self.assertEqual(f.files(), f.original)

    def test_r39_ordering_is_always_paired_and_exactly_restored(self):
        f = self.fixture
        for direct in (False, True):
            with self.subTest(direct_usb=direct):
                f.apply(direct_usb=direct)
                order = f.root / profile.USB_RUNTIME_ORDER
                self.assertEqual(order.read_bytes(), profile.USB_RUNTIME_ORDER_CONTENT)
                self.assertIn(f"Requires={profile.USB_MAIN}\n", order.read_text())
                self.assertIn(f"After={profile.USB_MAIN}\n", order.read_text())
                handler = (f.root / profile.USB_HANDLER).read_bytes()
                self.assertEqual(hashlib.sha256(handler).hexdigest(), profile.USB_HANDLER_ORDERED_HASH)
                manifest = profile.read_manifest(f.root)
                self.assertEqual(set(manifest['changes']), {
                    profile.SYSTEM + '/default.target', profile.USB_START,
                    profile.USB_HANDLER, profile.USB_RUNTIME_ORDER})
                for unit in (profile.USB_MAIN, profile.USB_RUNTIME):
                    relative = f'{profile.USB_DIR}/{unit}'
                    self.assertEqual(profile.snapshot(f.root / relative), f.original[relative])
                f.apply(direct_usb=direct)
                f.restore()
                self.assertFalse(order.parent.exists())
                self.assertEqual(f.files(), f.original)

    def test_cable_events_queue_without_waiting_for_bridge_creation(self):
        f = self.fixture
        f.apply()
        scratch = Path(f.temp.name) / 'events'
        scratch.mkdir()
        (scratch / 'gadget').mkdir()
        (scratch / 'nv-l4t-usb-device-mode-config.sh').write_text('')
        role = scratch / 'role'
        log = scratch / 'jobs'
        systemctl = scratch / 'systemctl'
        # Queue jobs only: an early RUN event must return while l4tbr0 does
        # not yet exist, so udev can finish and the gadget service can start.
        systemctl.write_text('#!/bin/sh\n'
            '[ "$1" = --no-block ] || exit 91\n'
            'shift\n[ "$2" = nv-l4t-usb-device-mode-runtime.service ] || exit 92\n'
            'printf "%s\\n" "$1" >> "$JOB_LOG"\n')
        systemctl.chmod(0o755)
        script = scratch / 'handler.sh'
        text = (f.root / profile.USB_HANDLER).read_text()
        text = text.replace('/sys/kernel/config/usb_gadget/l4t', str(scratch / 'gadget'))
        text = text.replace('/sys/class/usb_role/usb2-0-role-switch/role', str(role))
        text = text.replace('/bin/systemctl', str(systemctl))
        script.write_text(text)
        for state in ('device', 'host', 'device'):
            role.write_text(state + '\n')
            result = subprocess.run(['bash', str(script)], capture_output=True, text=True,
                timeout=2, env={**os.environ, 'JOB_LOG': str(log)})
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(log.read_text().splitlines(), ['start', 'stop', 'start'])
        self.assertFalse((scratch / 'l4tbr0').exists())
        (scratch / 'gadget').rmdir()
        subprocess.run(['bash', str(script)], check=True, timeout=2,
                       env={**os.environ, 'JOB_LOG': str(log)})
        self.assertEqual(log.read_text().splitlines(), ['start', 'stop', 'start'])

    @unittest.skipUnless(shutil.which('systemd-analyze'), 'systemd-analyze is required')
    def test_actual_units_and_order_dropin_have_no_dependency_cycle(self):
        f = self.fixture
        f.apply()
        # systemd 249 (Ubuntu 22.04 CI) cannot verify with --root. Isolate its
        # unit search path instead, materializing the native units and mapping
        # only executable paths into this fixture. Keep every dependency and
        # the managed ordering drop-in unchanged; no target script is executed.
        for unit in (profile.USB_MAIN, profile.USB_RUNTIME):
            link = f.system / unit
            link.unlink()
            lines = (f.root / profile.USB_DIR / unit).read_text().splitlines(keepends=True)
            for index, line in enumerate(lines):
                if line.startswith(('ExecStart=/', 'ExecStopPost=/')):
                    directive, executable = line.rstrip('\n').split('=', 1)
                    self.assertTrue(executable.startswith('/' + profile.USB_DIR + '/'))
                    self.assertNotIn(' ', executable)
                    lines[index] = f'{directive}={f.root / executable.lstrip("/")}\n'
            link.write_text(''.join(lines))
        for script in (f.root / profile.USB_DIR).glob('*.sh'):
            script.chmod(0o755)
        for unit in ('sysinit.target', 'basic.target', 'shutdown.target',
                     'systemd-tmpfiles-setup-dev.service'):
            content = '[Unit]\nDescription=Synthetic dependency\nDefaultDependencies=no\n'
            if unit.endswith('.service'):
                content += f'[Service]\nExecStart={f.root / profile.USB_START}\n'
            (f.system / unit).write_text(content)
        def verify():
            return subprocess.run(['systemd-analyze', 'verify',
                                   str(f.system / profile.USB_RUNTIME),
                                   str(f.system / profile.USB_MAIN)],
                                  env={**os.environ, 'SYSTEMD_UNIT_PATH': str(f.system)},
                                  capture_output=True, text=True, timeout=10)
        result = verify()
        self.assertEqual(result.returncode, 0, result.stderr)
        # Prove verify loaded the managed drop-in: adding an opposite ordering
        # edge must fail, rather than silently validating just the vendor units.
        order = f.root / profile.USB_RUNTIME_ORDER
        order.write_bytes(order.read_bytes() + f'Before={profile.USB_MAIN}\n'.encode())
        result = verify()
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn('ordering cycle', result.stderr.lower())

    def test_unknown_or_edited_runtime_order_is_rejected_without_mutation(self):
        f = self.fixture
        order = f.root / profile.USB_RUNTIME_ORDER
        order.parent.mkdir()
        order.write_text('[Unit]\nAfter=unreviewed.service\n')
        f.reject_without_mutation('custom runtime service drop-ins')
        order.unlink()
        order.parent.rmdir()
        f.apply()
        good = order.read_bytes()
        order.write_bytes(good + b'Before=unreviewed.service\n')
        before = f.files()
        with self.assertRaisesRegex(ValueError, 'changed outside this profile'):
            f.apply()
        self.assertEqual(f.files(), before)
        order.write_bytes(good)
        f.restore()
        self.assertEqual(f.files(), f.original)

    def test_new_rule_wrong_udev_and_wrong_distro_fail_closed(self):
        f = self.fixture
        f.status.write_text(f.status.read_text().replace('255.', '249.'))
        f.reject_without_mutation('udev 255')
        f.status.write_bytes(base64.b64decode(f.original['var/lib/dpkg/status']['data']))
        p = f.root / 'etc/udev/rules.d/99-unknown.rules'
        p.write_text('RUN+="/custom/setup"\n')
        f.reject_without_mutation('rule inventory differs')
        p.unlink()
        (f.root / 'etc/os-release').write_text('ID=ubuntu\nVERSION_ID="22.04"\n')
        f.reject_without_mutation('Ubuntu 24.04')

    def test_new_vendor_start_script_change_is_rejected(self):
        f = self.fixture
        f.start.write_text(f.start.read_text().replace('modprobe tegra_xudc', '# disabled USB'))
        f.reject_without_mutation('vendor file hash')


class NobleLoggingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = logging_fixture.KernelLoggingTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        (f.root / 'usr/lib/os-release').write_text('ID=ubuntu\nVERSION_ID="24.04"\n')
        (f.root / 'etc/nv_tegra_release').write_text('# R39 (release), REVISION: 2.1, GCID: TEST\n')
        source_root = Path(__file__).parent / 'testdata/logging-noble'
        for source in source_root.rglob('*'):
            relative = source.relative_to(source_root)
            if source.is_file() and relative.parts[0] in ('etc', 'usr'):
                target = f.root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        self.audit = json.loads(profile.NOBLE_LOG_AUDIT_PATH.read_text())
        # Tests pin synthetic AppArmor include contents. The production audit
        # pins actual Ubuntu files without redistributing the whole policy tree.
        for relative in self.audit['apparmor']['files']:
            p = f.root / relative
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('# synthetic audited include ' + relative + '\n')
            self.audit['apparmor']['files'][relative] = hashlib.sha256(p.read_bytes()).hexdigest()
        self.audit_path = Path(f.temp.name) / 'noble-logging-audit.json'
        self.audit_path.write_text(json.dumps(self.audit))
        patcher = mock.patch.object(profile, 'NOBLE_LOG_AUDIT_PATH', self.audit_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ('nv-load-display-modules', 'nv-load-gpu-libs', 'nv-graphics'):
            (f.system / 'multi-user.target.wants' / (name + '.service')).symlink_to(
                '/etc/systemd/system/' + name + '.service')
        f.original = f.files()

    def test_noble_transaction_preserves_vendor_logging_confinement_and_gpu(self):
        f = self.fixture
        f.apply()
        for relative, before in f.original.items():
            if relative != profile.SYSTEM + '/default.target':
                self.assertEqual(profile.snapshot(f.root / relative), before)
        self.assertIn('ReadKMsg=no', (f.root / profile.KERNEL_LOG_DROPIN).read_text())
        f.apply()
        f.restore()
        self.assertEqual(f.files(), f.original)

    def test_custom_apparmor_policy_and_new_optional_include_are_rejected(self):
        f = self.fixture
        p = f.root / 'etc/apparmor.d/local/usr.sbin.rsyslogd'
        old = p.read_bytes()
        p.write_text('deny /proc/kmsg r,\n')
        f.reject_without_mutation('AppArmor include hash')
        p.write_bytes(old)
        p = f.root / self.audit['apparmor']['absent'][0]
        p.mkdir()
        f.reject_without_mutation('unexpected AppArmor optional include')

    def test_vendor_journal_and_confinement_helper_edits_rejected(self):
        f = self.fixture
        for relative in ('usr/lib/systemd/journald.conf.d/syslog.conf',
                         'usr/lib/rsyslog/reload-apparmor-profile',
                         'etc/rsyslog.d/21-cloudinit.conf'):
            with self.subTest(relative=relative):
                p = f.root / relative
                old = p.read_bytes()
                p.write_bytes(old + b'# unreviewed change\n')
                f.reject_without_mutation('configuration hash')
                p.write_bytes(old)
        p = f.root / 'usr/lib/systemd/system/systemd-journald.service.d/custom.conf'
        p.write_text('[Service]\nExecStart=/custom/logger\n')
        f.reject_without_mutation('custom drop-ins')

    def test_ark_os_persistent_journal_file_is_optional_and_preserved(self):
        f = self.fixture
        relative = "etc/systemd/journald.conf.d/10-ark-os.conf"
        path = f.root / relative
        before = profile.snapshot(path)
        f.apply()
        self.assertEqual(profile.snapshot(path), before)
        f.restore()
        self.assertEqual(profile.snapshot(path), before)
        path.unlink()
        f.apply()
        self.assertFalse(path.exists())
        f.restore()
        self.assertFalse(path.exists())

    def test_changed_ark_os_journal_file_and_other_dropins_rejected(self):
        f = self.fixture
        path = f.root / "etc/systemd/journald.conf.d/10-ark-os.conf"
        original = path.read_bytes()
        path.write_bytes(original + b"ForwardToKMsg=yes\n")
        f.reject_without_mutation("optional journal configuration hash")
        path.write_bytes(original)
        other = path.with_name("11-custom.conf")
        other.write_text("[Journal]\nForwardToKMsg=yes\n")
        f.reject_without_mutation("custom drop-ins")
        other.unlink()
        f.apply()
        path.write_bytes(original + b"ReadKMsg=yes\n")
        with self.assertRaisesRegex(ValueError, "optional journal configuration hash"):
            f.apply()
        self.assertEqual(path.read_bytes(), original + b"ReadKMsg=yes\n")
        path.write_bytes(original)
        f.restore()

    def test_noble_cannot_use_jammy_unit_audit(self):
        f = self.fixture
        p = f.root / 'usr/lib/systemd/system/rsyslog.service'
        p.write_bytes((logging_fixture.FIXTURES / 'lib/systemd/system/rsyslog.service').read_bytes())
        f.reject_without_mutation('native unit hash')


if __name__ == '__main__':
    unittest.main()
