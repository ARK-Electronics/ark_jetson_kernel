#!/usr/bin/env python3
"""No-hardware regression checks for interrupted firmware staging."""

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import stage_fast_boot_firmware as firmware


class FirmwareStageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='test-jaj-firmware-')
        self.addCleanup(self.temporary.cleanup)
        self.checkout = Path(self.temporary.name) / 'checkout'
        self.l4t = self.checkout / 'staging/JAJ/Linux_for_Tegra'
        (self.l4t / 'rootfs/etc').mkdir(parents=True)
        (self.l4t / 'rootfs/etc/ark_jetson_kernel').write_text('target=JAJ\n')
        (self.l4t / 'rootfs/etc/nv_tegra_release').write_text('# R36 (release), REVISION: 5.0, GCID: fixture\n')
        (self.l4t / 'bootloader').mkdir()
        (self.l4t / 'bootloader/uefi_jetson.bin').write_bytes(b'stock firmware')
        (self.l4t / 'kernel').mkdir()
        (self.l4t / 'kernel/Image').write_bytes(b'fixture kernel')
        self.artifacts = Path(self.temporary.name) / 'artifacts'
        self.artifacts.mkdir()
        self.make_artifacts(b'candidate firmware')

    def make_artifacts(self, image):
        contents = {'uefi_jaj_nvme_RELEASE.bin': image, 'ark_fast_boot.dtbo': b'fixture overlay'}
        hashes = []
        for name, data in contents.items():
            (self.artifacts / name).write_bytes(data)
            hashes.append(hashlib.sha256(data).hexdigest() + '  ' + name + '\n')
        (self.artifacts / 'SHA256SUMS').write_text(''.join(hashes))

    def interrupt_after_first_replacement(self):
        replace = firmware.replace_file
        calls = 0
        def interrupted(source, destination):
            nonlocal calls
            replace(source, destination)
            calls += 1
            if calls == 1:
                raise RuntimeError('simulated interruption after UEFI replacement')
        with mock.patch.object(firmware, 'replace_file', side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, 'simulated interruption'):
                firmware.stage(self.l4t, self.artifacts, False)

    def test_complete_stage_clears_guard_after_validating_pair(self):
        firmware.stage(self.l4t, self.artifacts, False)
        self.assertFalse((self.l4t / firmware.IN_PROGRESS).exists())
        manifest = firmware.load_verified_manifest(self.l4t)
        self.assertEqual(manifest['files']['bootloader/uefi_jetson.bin'],
                         firmware.digest(self.artifacts / 'uefi_jaj_nvme_RELEASE.bin'))
        self.assertEqual((self.l4t / firmware.BACKUPS / 'bootloader/uefi_jetson.bin').read_bytes(),
                         b'stock firmware')

    def test_shared_profile_binds_manifest_to_each_product(self):
        stamp = self.l4t / 'rootfs/etc/ark_jetson_kernel'
        for product in firmware.PRODUCTS:
            with self.subTest(product=product):
                # A fresh manifest for the next independent product fixture.
                (self.l4t / firmware.MARKER).unlink(missing_ok=True)
                stamp.write_text(f'target={product}\n')
                manifest = firmware.stage(self.l4t, self.artifacts, False, product=product)
                self.assertEqual(manifest['target'], product)
                firmware.load_verified_manifest(self.l4t, product=product)
                other = 'PAB' if product == 'JAJ' else 'JAJ'
                with self.assertRaisesRegex(ValueError, 'product mismatch'):
                    firmware.load_verified_manifest(self.l4t, product=other)
                stamp.write_text(f'target={other}\n')
                with self.assertRaisesRegex(ValueError, 'manifest mismatch: target'):
                    firmware.load_verified_manifest(self.l4t)

    def test_unsupported_product_and_storage_rejected_before_mutation(self):
        stamp = self.l4t / 'rootfs/etc/ark_jetson_kernel'
        stamp.write_text('target=UNKNOWN\n')
        with self.assertRaisesRegex(ValueError, 'completed JAJ, PAB or PAB_V3'):
            firmware.stage(self.l4t, self.artifacts, False)
        self.assertEqual((self.l4t / 'bootloader/uefi_jetson.bin').read_bytes(), b'stock firmware')
        self.assertFalse((self.l4t / firmware.IN_PROGRESS).exists())
        stamp.write_text('target=PAB_V3\n')
        firmware.stage(self.l4t, self.artifacts, False)
        with self.assertRaisesRegex(ValueError, 'nvme0n1p1'):
            firmware.load_verified_manifest(self.l4t, storage='sda')

    def test_interrupted_first_stage_rejects_verification_and_flash(self):
        self.interrupt_after_first_replacement()
        self.assertEqual((self.l4t / 'bootloader/uefi_jetson.bin').read_bytes(), b'candidate firmware')
        self.assertTrue((self.l4t / firmware.IN_PROGRESS).is_file())
        self.assertFalse((self.l4t / firmware.MARKER).exists())
        with self.assertRaisesRegex(ValueError, 'interrupted'):
            firmware.load_verified_manifest(self.l4t)
        with self.assertRaisesRegex(ValueError, 'interrupted'):
            firmware.stage(self.l4t, self.artifacts, False)
        # Run the real flash script against an isolated fake staging tree. Only
        # sudo credential priming / tee are stubbed; it must reject before its
        # confirmation prompt or any recovery/device loop.
        shutil.copy2(Path(__file__).resolve().parents[1] / 'flash.sh', self.checkout / 'flash.sh')
        (self.checkout / 'scripts').mkdir()
        (self.checkout / 'scripts/check_bsp.sh').write_text('require_bsp_staging() { :; }\n')
        binaries = self.checkout / 'bin'
        binaries.mkdir()
        sudo = binaries / 'sudo'
        sudo.write_text('#!/bin/sh\nif [ "$1" = "-v" ]; then exit 0; fi\nexec "$@"\n')
        sudo.chmod(0o755)
        result = subprocess.run(['bash', str(self.checkout / 'flash.sh'), 'JAJ'],
                                input='', text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                env={**os.environ, 'PATH': str(binaries) + os.pathsep + os.environ['PATH']},
                                timeout=5)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('staging is active or was interrupted', result.stdout)
        self.assertNotIn('Waiting for device', result.stdout)

    def test_interrupted_restage_guard_overrides_previous_valid_manifest(self):
        firmware.stage(self.l4t, self.artifacts, False)
        self.make_artifacts(b'new candidate firmware')
        self.interrupt_after_first_replacement()
        self.assertTrue((self.l4t / firmware.MARKER).is_file())
        with self.assertRaisesRegex(ValueError, 'interrupted'):
            firmware.load_verified_manifest(self.l4t)
        self.assertEqual((self.l4t / firmware.BACKUPS / 'bootloader/uefi_jetson.bin').read_bytes(),
                         b'stock firmware')

    def test_stale_old_temporary_symlink_is_not_followed(self):
        outside = Path(self.temporary.name) / 'untouched'
        outside.write_bytes(b'outside contents')
        (self.l4t / 'bootloader/uefi_jetson.bin.ark-fastboot-new').symlink_to(outside)
        firmware.stage(self.l4t, self.artifacts, False)
        self.assertEqual(outside.read_bytes(), b'outside contents')
        firmware.load_verified_manifest(self.l4t)


if __name__ == '__main__':
    unittest.main()
