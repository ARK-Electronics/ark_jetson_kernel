#!/usr/bin/env python3
"""Version isolation and archive safety for the R36/R39 initrd profiles."""
import gzip
import hashlib
from pathlib import Path
import re
import stat
import tempfile
import unittest
from unittest.mock import patch

import check_initrd_source as source_check
import optimize_initrd as optimizer


class ReleasePolicyTests(unittest.TestCase):
    def test_exact_release_selects_matching_kmod_and_kernel(self):
        for release, stamp, kmod, valid, invalid in (
            ('36.5.0', '# R36 (release), REVISION: 5.0, GCID: test', 29,
             '5.15.185-tegra', '6.8.12-tegra'),
            ('39.2.1', '# R39 (release), REVISION: 2.1, GCID: test', 31,
             '6.8.12-1021-tegra', '6.8.12-1021-generic'),
        ):
            with self.subTest(release=release):
                actual, policy = optimizer.release_policy(stamp)
                self.assertEqual(actual, release)
                self.assertEqual(policy['kmod'], kmod)
                self.assertIsNotNone(re.fullmatch(policy['kernel'], valid))
                self.assertIsNone(re.fullmatch(policy['kernel'], invalid))
        _, policy = optimizer.release_policy('# R39 (release), REVISION: 2.1,')
        self.assertIsNotNone(re.fullmatch(policy['kernel'], '6.8.12-tegra'))

    def test_builtin_manifest_requires_explicit_safe_module_paths(self):
        self.assertEqual(optimizer.builtin_module_names(
            b"kernel/drivers/pci/controller/dwc/pcie-tegra194.ko\n"
            b"kernel/drivers/usb/gadget/udc/tegra-xudc.ko\n"),
            {"pcie-tegra194", "tegra-xudc"})
        self.assertEqual(optimizer.builtin_module_names(b""), set())
        for content in (b"/etc/custom.ko\n", b"kernel/../custom.ko\n",
                        b"kernel/unreviewed module.ko\n", b"kernel/custom.ko.zst\n"):
            with self.subTest(content=content), self.assertRaisesRegex(ValueError, "Invalid modules.builtin"):
                optimizer.builtin_module_names(content)

    def test_unknown_minor_release_is_not_assumed_compatible(self):
        for stamp in ('# R39 (release), REVISION: 2.2,', '# R36 (release), REVISION: 4.4,', ''):
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                optimizer.release_policy(stamp)

    def test_r39_polling_preserves_new_non_nvme_branches(self):
        untouched = b'# T264 UFS, LUKS UUID validation, NFS, vblk and recovery stay intact\n'
        original = untouched + optimizer.STOCK_MOUNT_LOOP + untouched + optimizer.STOCK_DEVICE_POLL
        expected = original.replace(optimizer.STOCK_MOUNT_LOOP, optimizer.FAST_MOUNT_LOOP).replace(
            optimizer.STOCK_DEVICE_POLL, optimizer.FAST_DEVICE_POLL)
        with patch.object(optimizer, 'R39_STOCK_INIT_SHA256', hashlib.sha256(original).hexdigest()), \
                patch.object(optimizer, 'R39_POLLING_INIT_SHA256', hashlib.sha256(expected).hexdigest()):
            self.assertEqual(optimizer.optimize_root_polling(original, '39.2.1'), expected)
            with self.assertRaisesRegex(ValueError, 'Unaudited /init'):
                optimizer.optimize_root_polling(original, '36.5.0')
            with self.assertRaisesRegex(ValueError, 'Unaudited /init'):
                optimizer.optimize_root_polling(original + b'# custom change', '39.2.1')
            with patch.object(optimizer, 'FAST_DEVICE_POLL', optimizer.FAST_DEVICE_POLL + b'# unreviewed'):
                with self.assertRaisesRegex(ValueError, 'output checksum'):
                    optimizer.optimize_root_polling(original, '39.2.1')


class ArchiveDirectoryTests(unittest.TestCase):
    def archive(self, duplicate_mode=stat.S_IFDIR | 0o755):
        directory = [1, stat.S_IFDIR | 0o755, 0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0]
        second = directory.copy()
        second[0], second[1], second[4] = 5, duplicate_mode, 3
        regular = [3, stat.S_IFREG | 0o755, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0]
        return (optimizer.encode_record('.', directory, b'') +
                optimizer.encode_record('init', regular, b'#!/bin/bash\n') +
                optimizer.encode_record('.', second, b'') +
                optimizer.encode_record('TRAILER!!!', regular, b''))

    def test_optimizer_still_rejects_duplicate_directories_by_default(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            optimizer.read_newc(self.archive())

    def test_audited_source_retains_directory_records_without_extracting(self):
        original = self.archive()
        records, trailer = optimizer.read_newc(original, allow_duplicate_directories=True)
        self.assertEqual(b''.join(r['record'] for r in records) + trailer, original)

    def test_file_directory_collision_rejected_even_when_exception_enabled(self):
        for mode in (stat.S_IFREG | 0o755, stat.S_IFLNK | 0o755, stat.S_IFDIR | 0o777):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'Duplicate'):
                optimizer.read_newc(self.archive(mode), allow_duplicate_directories=True)

    def test_source_exception_requires_the_complete_compressed_image_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'initrd'
            data = gzip.compress(self.archive(), mtime=0)
            path.write_bytes(data)
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                source_check.check_source(path)
            with patch.object(source_check, 'AUDITED_UNNORMALIZED_SOURCES', {hashlib.sha256(data).hexdigest()}):
                source_check.check_source(path)
                path.write_bytes(gzip.compress(self.archive(), mtime=1))
                with self.assertRaisesRegex(ValueError, 'Duplicate'):
                    source_check.check_source(path)


if __name__ == '__main__':
    unittest.main()
