#!/usr/bin/env python3
"""Offline guard tests and executable cold-start ordering regression tests."""
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest
from unittest.mock import patch

import patch_gpu_power_order as helper


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.original = ('#!/bin/bash\nset -e\nvariant=nvgpu-l4t\n' + helper.BEFORE
                         + 'modprobe nvidia_drm\n').encode()
        self.updated = self.original.replace(helper.BEFORE.encode(), helper.AFTER.encode())
        for name, value in (("ORIGINAL", helper.digest(self.original)),
                            ("PATCHED", helper.digest(self.updated))):
            mock = patch.object(helper, name, value)
            mock.start()
            self.addCleanup(mock.stop)
        self.write('etc/nv_tegra_release', b'# R39 (release), REVISION: 2.1, fixture\n')
        self.write('etc/ark_jetson_kernel', b'target=JAJ\n')
        self.source = self.write(helper.SOURCE, self.original)
        self.source.chmod(0o751)
        companions = {}
        for relative in helper.COMPANIONS:
            payload = ('Audited fixture: ' + relative).encode()
            self.write(relative, payload)
            companions[relative] = helper.digest(payload)
        mock = patch.object(helper, 'COMPANIONS', companions)
        mock.start()
        self.addCleanup(mock.stop)

    def write(self, relative, payload):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def test_roundtrip_idempotency_and_metadata(self):
        info = self.source.stat()
        self.assertEqual(helper.update(self.root, 'JAJ'), 'patched')
        self.assertEqual(self.source.read_bytes(), self.updated)
        self.assertEqual(helper.update(self.root, 'JAJ'), 'patched')
        self.assertEqual(helper.update(self.root, 'JAJ', 'check'), 'patched')
        self.assertEqual(helper.update(self.root, 'JAJ', 'restore'), 'original')
        self.assertEqual(helper.update(self.root, 'JAJ', 'restore'), 'original')
        self.assertEqual(self.source.read_bytes(), self.original)
        after = self.source.stat()
        self.assertEqual((info.st_mode, info.st_uid, info.st_gid),
                         (after.st_mode, after.st_uid, after.st_gid))

    def test_all_three_products(self):
        for product in helper.PRODUCTS:
            self.write('etc/ark_jetson_kernel', f'target={product}\n'.encode())
            self.assertEqual(helper.update(self.root, product), 'patched')
        (self.root / 'etc/ark_jetson_kernel').unlink()
        self.assertEqual(helper.update(self.root, 'PAB'), 'patched')

    def test_owned_ordering_survives_real_early_api_profile(self):
        import configure_fast_boot as profile
        for unit, content in profile.EARLY_API_UNITS.items():
            self.write('usr/lib/systemd/system/' + unit, content.encode())
        helper.update(self.root, 'JAJ')
        dropin = self.root / helper.DROPIN
        self.assertEqual(dropin.read_bytes(), helper.DROPIN_DATA)
        self.assertEqual(dropin.stat().st_mode & 0o777, 0o644)
        changes = profile.early_api_changes(self.root)
        for relative, state in changes.items():
            profile.write_state(self.root / relative, state)
        self.assertTrue(profile.early_api_changes(self.root, applied=True))
        self.assertEqual(dropin.read_bytes(), helper.DROPIN_DATA)
        self.assertNotIn(b'After=', (self.root / 'etc/systemd/system/system-manager.service').read_bytes())
        self.assertNotIn(b'Requires=', dropin.read_bytes())
        helper.update(self.root, 'JAJ', 'restore')
        self.assertFalse(dropin.exists())

    def test_modified_owned_ordering_is_never_removed(self):
        helper.update(self.root, 'JAJ')
        path = self.root / helper.DROPIN
        changed = path.read_bytes() + b'After=custom.service\n'
        path.write_bytes(changed)
        for mode in ('check', 'apply', 'restore'):
            with self.assertRaises(ValueError):
                helper.update(self.root, 'JAJ', mode)
        self.assertEqual(path.read_bytes(), changed)
        self.assertEqual(self.source.read_bytes(), self.updated)

    def test_interrupted_known_pair_is_detected_and_reconciled(self):
        helper.update(self.root, 'JAJ')
        (self.root / helper.DROPIN).unlink()
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            helper.update(self.root, 'JAJ', 'check')
        self.assertEqual(helper.update(self.root, 'JAJ'), 'patched')
        self.source.write_bytes(self.original)
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            helper.update(self.root, 'JAJ', 'check')
        self.assertEqual(helper.update(self.root, 'JAJ', 'restore'), 'original')
        self.assertFalse((self.root / helper.DROPIN).exists())

    def test_native_publish_failure_rolls_back_new_ordering(self):
        real_replace = helper.replace_file
        def fail_native(path, payload, info):
            if path == self.source:
                raise OSError('fixture native publication failure')
            return real_replace(path, payload, info)
        with patch.object(helper, 'replace_file', side_effect=fail_native):
            with self.assertRaises(OSError):
                helper.update(self.root, 'JAJ')
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertFalse((self.root / helper.DROPIN).exists())

    def test_wrong_release_product_and_existing_stamp_fail(self):
        for product in ('PAB', 'unsupported'):
            with self.assertRaises(ValueError):
                helper.update(self.root, product)
        for release in ('# R36 (release), REVISION: 5.0,\n',
                        '# R39 (release), REVISION: 2.0,\n'):
            self.write('etc/nv_tegra_release', release.encode())
            with self.assertRaisesRegex(ValueError, 'R39.2.1'):
                helper.update(self.root, 'JAJ')
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_customer_modified_script_and_helpers_are_preserved(self):
        for relative in (helper.SOURCE, *helper.COMPANIONS):
            path = self.root / relative
            original = path.read_bytes()
            changed = original + b'\n# customer customization\n'
            path.write_bytes(changed)
            for mode in ('apply', 'restore', 'check'):
                with self.assertRaises(ValueError):
                    helper.update(self.root, 'JAJ', mode)
            self.assertEqual(path.read_bytes(), changed)
            path.write_bytes(original)

    def test_symlinks_rejected_without_touching_destination(self):
        for relative in (helper.SOURCE, *helper.COMPANIONS, 'etc/nv_tegra_release'):
            path = self.root / relative
            original = path.read_bytes()
            destination = self.root / 'outside'
            destination.write_bytes(original)
            path.unlink()
            path.symlink_to(destination)
            with self.assertRaisesRegex(ValueError, 'symlink'):
                helper.update(self.root, 'JAJ')
            self.assertEqual(destination.read_bytes(), original)
            path.unlink()
            path.write_bytes(original)

    def test_custom_unit_dropin_fails_before_mutation(self):
        for name in ('nvpower.service.d', 'nv-load-.service.d', 'service.d'):
            with self.subTest(name=name):
                path = self.write('etc/systemd/system/' + name + '/custom.conf',
                                  b'[Unit]\nAfter=custom.service\n')
                with self.assertRaisesRegex(ValueError, 'drop-in'):
                    helper.update(self.root, 'JAJ')
                self.assertEqual(self.source.read_bytes(), self.original)
                self.assertTrue(path.exists())
                path.unlink()

    def test_bad_generated_hash_fails_before_mutation(self):
        with patch.object(helper, 'PATCHED', '0' * 64):
            with self.assertRaisesRegex(ValueError, 'output checksum'):
                helper.update(self.root, 'JAJ')
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_atomic_publication_failure_keeps_native_script(self):
        with patch.object(Path, 'replace', side_effect=OSError('fixture rename failure')):
            with self.assertRaises(OSError):
                helper.update(self.root, 'JAJ')
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertFalse(list(self.source.parent.glob('.ark-gpu-power-*')))


class StartupTests(unittest.TestCase):
    """Model the actual bug: immediate DRM makes a later mask change impossible.

    Only executable boundaries are mocked. The complete patched load sequence,
    shell error propagation, query parsing and original regression run in bash.
    """
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.gpu = self.root / 'gpu'
        self.gpu.mkdir()
        (self.gpu / 'driver').symlink_to(self.bin)
        real_timeout = shutil.which('timeout')
        self.assertIsNotNone(real_timeout)
        self.stub('timeout', f"""[ "$1" = --foreground ] || exit 21
printf '%s:%s\\n' "$2" "$(basename "$3")" >> "$CASE/timeouts"
limit=$2
shift 2
[ "${{SHORT_TIMEOUT:-0}}" = 0 ] || limit=0.2s
exec {real_timeout} --foreground "$limit" "$@"
""")
        self.stub('modprobe', '''echo "module:$1" >> "$CASE/log"
if [ "$1" = nvidia_drm ] && [ "$VARIANT" = nvgpu-l4t ] && [ ! -f "$CASE/configured" ]; then
    echo 'fixture: golden context created before static masks' >&2
    exit 27
fi
''')
        self.stub('udevadm', '''echo "udev:$*" >> "$CASE/log"
[ "${UDEV_FAIL:-0}" = 0 ] || exit 8
[ "$*" = "trigger --action=change --settle --subsystem-match=platform --sysname-match=17000000.gpu $CASE/gpu" ] || exit 12
touch "$CASE/udev-completed"
''')
        self.model = self.stub('nvpmodel.sh', '''echo "apply:$MODE" >> "$CASE/log"
[ "${HANG_APPLY:-0}" = 0 ] || exec sleep 20
[ -f "$CASE/udev-completed" ] || exit 17
[ "${APPLY_FAIL:-0}" = 0 ] || exit 9
if read -r unused; then exit 18; fi
printf '%s\\n' "$MODE" > "$CASE/configured"
''')
        self.query = self.stub('nvpmodel', '''echo "query:$*" >> "$CASE/log"
[ "${HANG_QUERY:-0}" = 0 ] || exec sleep 20
[ "$1" = -q ] || exit 20
case "${QUERY_MODE:-valid}" in
  valid) printf 'NV Power Mode: Custom fixture\\n%s\\n' "$MODE";;
  unset) echo 'NVPM WARN: power mode is not set!';;
  missing_id) echo 'NV Power Mode: Custom fixture';;
  missing_name) echo "$MODE";;
  failure) exit 7;;
esac
''')

    def stub(self, name, body):
        path = self.bin / name
        path.write_text('#!/bin/bash\nset -e\n' + body)
        path.chmod(0o755)
        return path

    def run_startup(self, patched=True, **overrides):
        sequence = helper.AFTER if patched else helper.BEFORE
        sequence = sequence.replace('/sys/devices/platform/bus@0/17000000.gpu', str(self.gpu))
        sequence = sequence.replace('/etc/systemd/nvpmodel.sh', str(self.model))
        sequence = sequence.replace('/usr/sbin/nvpmodel', str(self.query))
        script = '#!/bin/bash\nset -e\nvariant=$VARIANT\n' + sequence + 'modprobe nvidia_drm\n'
        path = self.root / 'startup.sh'
        path.write_text(script)
        env = dict(os.environ, CASE=str(self.root), MODE='4', VARIANT='nvgpu-l4t',
                   PATH=str(self.bin) + os.pathsep + os.environ['PATH'])
        env.update(overrides)
        result = subprocess.run(['bash', str(path)], env=env, input='not a reboot\n',
                                capture_output=True, text=True, timeout=5)
        log = (self.root / 'log').read_text().splitlines()
        return result, log

    def test_original_sequence_reproduces_regression(self):
        result, log = self.run_startup(patched=False)
        self.assertEqual(result.returncode, 27)
        self.assertEqual(log, ['module:nvgpu', 'module:nvidia_drm'])

    def test_configured_policy_finishes_before_drm_without_forcing_mode(self):
        for mode in ('0', '1', '4', '27'):
            with self.subTest(mode=mode):
                result, log = self.run_startup(MODE=mode)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(log[-5:], ['module:nvgpu',
                    f'udev:trigger --action=change --settle --subsystem-match=platform --sysname-match=17000000.gpu {self.gpu}',
                    f'apply:{mode}', 'query:-q', 'module:nvidia_drm'])
                self.assertEqual((self.root / 'configured').read_text().strip(), mode)

    def test_handler_application_and_query_failures_never_load_drm(self):
        for env in ({'UDEV_FAIL': '1'}, {'APPLY_FAIL': '1'},
                    {'QUERY_MODE': 'unset'}, {'QUERY_MODE': 'missing_id'},
                    {'QUERY_MODE': 'missing_name'}, {'QUERY_MODE': 'failure'}):
            with self.subTest(env=env):
                log_path = self.root / 'log'
                log_path.unlink(missing_ok=True)
                result, log = self.run_startup(**env)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('module:nvidia_drm', log)
                self.assertIn('ERROR:', result.stderr)

    def test_individual_deadlines_are_30s_apply_and_10s_query(self):
        result, _ = self.run_startup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'timeouts').read_text().splitlines(),
                         ['30s:udevadm', '30s:nvpmodel.sh', '10s:nvpmodel'])

    def test_real_timeouts_abort_before_drm_for_hung_apply_and_query(self):
        for env in ({'HANG_APPLY': '1'}, {'HANG_QUERY': '1'}):
            with self.subTest(env=env):
                (self.root / 'log').unlink(missing_ok=True)
                result, log = self.run_startup(SHORT_TIMEOUT='1', **env)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('module:nvidia_drm', log)
                self.assertIn('ERROR:', result.stderr)

    def test_missing_driver_fails_without_replaying_policy(self):
        (self.gpu / 'driver').unlink()
        result, log = self.run_startup()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(log, ['module:nvgpu'])

    def test_other_native_variant_is_unchanged(self):
        result, log = self.run_startup(VARIANT='openrm-l4t')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(log, ['module:nvidia_drm'])


if __name__ == '__main__':
    unittest.main()
