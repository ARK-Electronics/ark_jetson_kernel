#!/usr/bin/env python3
"""Flash-time refresh retains vendor updates, recomputes, and fails closed."""
import gzip
import hashlib
import json
from pathlib import Path
import stat
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

import optimize_initrd as opt
import prepare_flash_initrd as hook


class FlashInitrdTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.l4t = Path(self.temp.name) / 'Linux_for_Tegra'
        for name in ('tools', 'bootloader', 'kernel', 'rootfs/boot', 'rootfs/etc'):
            (self.l4t / name).mkdir(parents=True, exist_ok=True)
        self.stock = (b'#!/bin/bash\n# preserve recovery and encryption branches\n' +
                      opt.STOCK_DEPMOD + opt.STOCK_MOUNT_LOOP + b'\n' + opt.STOCK_DEVICE_POLL)
        polled = self.stock.replace(opt.STOCK_MOUNT_LOOP, opt.FAST_MOUNT_LOOP).replace(
            opt.STOCK_DEVICE_POLL, opt.FAST_DEVICE_POLL)
        for constant, value in (('R39_STOCK_INIT_SHA256', self.stock),
                                ('R39_POLLING_INIT_SHA256', polled)):
            context = mock.patch.object(opt, constant, hashlib.sha256(value).hexdigest())
            context.start()
            self.addCleanup(context.stop)
        (self.l4t / 'rootfs/etc/nv_tegra_release').write_text('# R39 (release), REVISION: 2.1,\n')
        for name in ('kernel/Image', 'rootfs/boot/Image'):
            (self.l4t / name).write_bytes(b'Linux version 6.8.12-1021-tegra fixture')
        self.original = self.archive(b'old indexes', modules=1)
        self.refreshed = self.archive(b'full rootfs indexes', modules=2)
        self.optimized = self.archive(b'new subset indexes', modules=2)
        for name in hook.IMAGES:
            (self.l4t / name).write_bytes(self.original)
        (self.l4t / 'refreshed.img').write_bytes(self.refreshed)
        self.vendor = b'''#!/bin/sh
set -e
root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
printf '%s\\n' "$@" > "$root/vendor-args"
cp "$root/refreshed.img" "$root/bootloader/l4t_initrd.img"
[ ! -f "$root/fail-vendor" ] || exit 19
cp "$root/refreshed.img" "$root/rootfs/boot/initrd"
'''
        updater = self.l4t / hook.UPDATER
        updater.write_bytes(self.vendor)
        updater.chmod(0o755)
        context = mock.patch.dict(hook.UPDATER_HASHES, {'39.2.1': hook.digest(self.vendor)})
        context.start()
        self.addCleanup(context.stop)

    def archive(self, indexes, modules=1, init_extra=b''):
        polled = opt.optimize_root_polling(self.stock, '39.2.1')
        init = polled.replace(opt.STOCK_DEPMOD, hook.depmod_guard('6.8.12-1021-tegra', modules)) + init_extra
        files = {'init': init, 'usr/lib/modules/6.8.12-1021-tegra/modules.dep.bin': indexes,
                 'etc/untouched': b'vendor configuration'}
        for n in range(modules):
            files[f'usr/lib/modules/6.8.12-1021-tegra/kernel/module{n}.ko'] = f'module{n}'.encode()
        files[opt.CHECKSUM_FILE] = b''.join(
            hashlib.sha256(data).hexdigest().encode() + b'  ' + name.encode() + b'\n'
            for name, data in sorted(files.items()) if name.startswith('usr/lib/modules/'))
        fields = [1, stat.S_IFREG | 0o644, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0]
        payload = b''.join(opt.encode_record(name, fields, data) for name, data in files.items())
        payload += opt.encode_record('TRAILER!!!', fields, b'')
        return gzip.compress(payload, mtime=0)

    def assert_original_pair(self):
        for name in hook.IMAGES:
            self.assertEqual((self.l4t / name).read_bytes(), self.original)
        self.assertFalse((self.l4t / hook.DIRECTORY / 'in-progress.json').exists())

    def test_stock_reconstruction_preserves_new_modules_and_metadata(self):
        restored = hook.stock_archive(self.refreshed, '39.2.1')
        entries = {e['name']: e for e in opt.read_newc(gzip.decompress(restored))[0]}
        self.assertEqual(entries['init']['payload'], self.stock)
        self.assertNotIn(opt.CHECKSUM_FILE, entries)
        before = {e['name']: e for e in opt.read_newc(gzip.decompress(self.refreshed))[0]}
        for name in entries.keys() - {'init'}:
            self.assertEqual(entries[name]['record'], before[name]['record'])

    def test_customer_init_or_modified_guard_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unaudited /init'):
            hook.stock_archive(self.archive(b'index', init_extra=b'\n# customer customization'), '39.2.1')
        decoded = gzip.decompress(self.original)
        entries, trailer = opt.read_newc(decoded)
        payload = b''.join(opt.encode_record(e['archive_name'], e['fields'],
                          e['payload'].replace(b'depmod -a', b'depmod -b /unexpected'))
                           if e['name'] == 'init' else e['record'] for e in entries) + trailer
        with self.assertRaisesRegex(ValueError, 'Unreviewed ARK'):
            hook.stock_archive(gzip.compress(payload), '39.2.1')

    def test_installer_is_self_contained_idempotent_and_restorable(self):
        hook.install(self.l4t)
        hook.install(self.l4t)
        self.assertEqual((self.l4t / hook.UPDATER).read_bytes(), hook.WRAPPER)
        self.assertEqual((self.l4t / hook.VENDOR).read_bytes(), self.vendor)
        for name in hook.SCRIPTS:
            self.assertEqual((self.l4t / hook.DIRECTORY / name).read_bytes(),
                             (Path(hook.__file__).parent / name).read_bytes())
        hook.remove(self.l4t)
        hook.remove(self.l4t)
        self.assertEqual((self.l4t / hook.UPDATER).read_bytes(), self.vendor)
        self.assertFalse((self.l4t / hook.DIRECTORY).exists())
        self.assert_original_pair()

    def test_changed_vendor_rejected_before_install(self):
        (self.l4t / hook.UPDATER).write_bytes(self.vendor + b'# changed\n')
        with self.assertRaisesRegex(ValueError, 'Unaudited vendor'):
            hook.install(self.l4t)
        self.assertFalse((self.l4t / hook.DIRECTORY).exists())

    def test_installed_helper_tampering_is_rejected(self):
        hook.install(self.l4t)
        (self.l4t / hook.DIRECTORY / 'optimize_initrd.py').write_text('# changed\n')
        with self.assertRaisesRegex(ValueError, 'helper changed'):
            hook.refresh(self.l4t, [])
        self.assertFalse((self.l4t / 'vendor-args').exists())

    def test_vendor_updates_are_reindexed_before_publishing_both_copies(self):
        hook.install(self.l4t)
        observed = []
        def reindex(l4t, source, output, work):
            entries = {e['name']: e['payload'] for e in opt.read_newc(gzip.decompress(source.read_bytes()))[0]}
            observed.append(entries)
            self.assertEqual(entries['init'], self.stock)
            self.assertEqual(entries['usr/lib/modules/6.8.12-1021-tegra/modules.dep.bin'], b'full rootfs indexes')
            self.assertIn('usr/lib/modules/6.8.12-1021-tegra/kernel/module1.ko', entries)
            output.write_bytes(self.optimized)
        with mock.patch.object(hook, 'reoptimize', side_effect=reindex):
            hook.refresh(self.l4t, ['--list-files', 'modules_k6.8'])
        self.assertEqual(len(observed), 1)
        self.assertEqual((self.l4t / 'vendor-args').read_text(), '--list-files\nmodules_k6.8\n')
        for name in hook.IMAGES:
            self.assertEqual((self.l4t / name).read_bytes(), self.optimized)
        stamp = json.loads((self.l4t / hook.DIRECTORY / 'last-refresh.json').read_text())
        self.assertEqual(stamp['initrd_sha256'], hook.digest(self.optimized))
        self.assertFalse(list((self.l4t / hook.DIRECTORY).glob('recovery-*')))

    def test_partial_vendor_failure_restores_previous_pair_and_aborts(self):
        hook.install(self.l4t)
        (self.l4t / 'fail-vendor').touch()
        with self.assertRaises(subprocess.CalledProcessError):
            hook.refresh(self.l4t, [])
        self.assert_original_pair()

    def test_optimizer_failure_restores_pair(self):
        hook.install(self.l4t)
        with mock.patch.object(hook, 'reoptimize', side_effect=ValueError('dependency missing')):
            with self.assertRaisesRegex(ValueError, 'dependency missing'):
                hook.refresh(self.l4t, [])
        self.assert_original_pair()

    def test_vendor_custom_init_is_not_silently_removed(self):
        hook.install(self.l4t)
        (self.l4t / 'refreshed.img').write_bytes(self.archive(b'indexes', init_extra=b'\n# customization'))
        with self.assertRaisesRegex(ValueError, 'Unaudited /init'):
            hook.refresh(self.l4t, [])
        self.assert_original_pair()

    def test_interrupted_refresh_guard_blocks_new_run_and_removal(self):
        hook.install(self.l4t)
        (self.l4t / hook.DIRECTORY / 'in-progress.json').write_text('{}')
        for method in (lambda: hook.refresh(self.l4t, []), lambda: hook.remove(self.l4t)):
            with self.assertRaisesRegex(ValueError, 'Interrupted initrd refresh'):
                method()
        self.assertFalse((self.l4t / 'vendor-args').exists())

    def test_kernel_image_mismatch_blocks_before_vendor(self):
        hook.install(self.l4t)
        (self.l4t / 'kernel/Image').write_bytes(b'changed image')
        with self.assertRaisesRegex(ValueError, 'Kernel Image copies differ'):
            hook.refresh(self.l4t, [])
        self.assertFalse((self.l4t / 'vendor-args').exists())

    def test_r36_directory_cannot_redirect_vendor_to_another_bsp(self):
        self.assertEqual(hook.vendor_arguments(self.l4t, '36.5.0', []), ['-l', str(self.l4t)])
        self.assertEqual(hook.vendor_arguments(self.l4t, '36.5.0', ['-l', str(self.l4t)]),
                         ['-l', str(self.l4t)])
        for arguments in (['-l', '/different'], ['--ldk_dir=/different'], ['-l/different']):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                hook.vendor_arguments(self.l4t, '36.5.0', arguments)

    def test_refresh_preserves_modes_and_owner_on_success_and_rollback(self):
        hook.install(self.l4t)
        for name in hook.IMAGES:
            (self.l4t / name).chmod(0o640)
        before = [(p.stat().st_uid, p.stat().st_gid) for p in (self.l4t / n for n in hook.IMAGES)]
        def reindex(l4t, source, output, work):
            output.write_bytes(self.optimized)
        with mock.patch.object(hook, 'reoptimize', side_effect=reindex):
            hook.refresh(self.l4t, [])
        (self.l4t / 'fail-vendor').touch()
        with self.assertRaises(subprocess.CalledProcessError):
            hook.refresh(self.l4t, [])
        for name, owner in zip(hook.IMAGES, before):
            info = (self.l4t / name).stat()
            self.assertEqual(stat.S_IMODE(info.st_mode), 0o640)
            self.assertEqual((info.st_uid, info.st_gid), owner)
            self.assertEqual((self.l4t / name).read_bytes(), self.optimized)

    def test_remove_cannot_race_refresh_before_its_recovery_guard(self):
        hook.install(self.l4t)
        reached, release = threading.Event(), threading.Event()
        errors = []
        original = hook.stock_archive
        def pause_before_guard(*arguments):
            reached.set()
            if not release.wait(5):
                raise RuntimeError('fixture barrier timed out')
            return original(*arguments)
        def run():
            try:
                hook.refresh(self.l4t, [])
            except Exception as error:
                errors.append(error)
        def reindex(l4t, source, output, work):
            output.write_bytes(self.optimized)
        with mock.patch.object(hook, 'stock_archive', side_effect=pause_before_guard), \
                mock.patch.object(hook, 'reoptimize', side_effect=reindex):
            thread = threading.Thread(target=run)
            thread.start()
            try:
                self.assertTrue(reached.wait(5))
                self.assertFalse((self.l4t / hook.DIRECTORY / 'in-progress.json').exists())
                with self.assertRaises(BlockingIOError):
                    hook.remove(self.l4t)
                with self.assertRaises(BlockingIOError):
                    hook.install(self.l4t)
            finally:
                release.set()
                thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        hook.verify(self.l4t)


if __name__ == '__main__':
    unittest.main()
