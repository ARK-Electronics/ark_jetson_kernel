#!/usr/bin/env python3
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import stage_kernel_headers as helper


def elf(machine):
    data = bytearray(64)
    data[:6] = b'\x7fELF\x02\x01'
    data[18:20] = machine.to_bytes(2, 'little')
    return bytes(data)


class PreparedHeaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.headers = self.root / 'kernel', self.root / 'headers'
        contents = {
            '.config': 'CONFIG_ARM64=y\nCONFIG_MODULES=y\nCONFIG_TEGRA_BPMP=m\n',
            'include/config/auto.conf': 'CONFIG_ARM64=y\nCONFIG_MODULES=y\nCONFIG_TEGRA_BPMP=m\n',
            'include/config/kernel.release': '6.8.12-1021-tegra\n',
            'include/generated/autoconf.h': '#define CONFIG_ARM64 1\n#define CONFIG_MODULES 1\n#define CONFIG_TEGRA_BPMP_MODULE 1\n',
            'include/generated/utsrelease.h': '#define UTS_RELEASE "6.8.12-1021-tegra"\n',
            'include/soc/tegra/bpmp.h': '/* current BPMP API */\n',
            'include/soc/tegra/mc.h': '/* current MC API */\n',
            'arch/arm64/include/asm/example.h': '/* current arm64 API */\n',
            'Module.symvers': '0x12345678\ttegra_bpmp_transfer\tvmlinux\tEXPORT_SYMBOL_GPL\n',
            'scripts/module.lds': 'SECTIONS {}\n',
            'Makefile': 'VERSION = 6\nPATCHLEVEL = 8\nSUBLEVEL = 12\nEXTRAVERSION =\n',
        }
        for name, text in contents.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        (self.source / 'include/current.h').symlink_to('soc/tegra/mc.h')
        for name in helper.TOOLS:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(elf(62))
        shutil.copytree(self.source, self.headers, symlinks=True)
        for name in helper.TOOLS:
            (self.headers / name).write_bytes(elf(183))
        (self.headers / 'Makefile').write_text(contents['Makefile'].replace('EXTRAVERSION =', 'EXTRAVERSION = -1021-tegra'))
        (self.headers / 'include/soc/tegra/mc.h').write_text('/* stale vendor API */\n')
        (self.headers / 'Module.symvers').write_text('0xffffffff\ttegra_bpmp_transfer\tvmlinux\tEXPORT_SYMBOL_GPL\n')

    def test_sync_updates_transitive_api_crc_and_config_but_preserves_arm_tools(self):
        old_makefile = (self.headers / 'Makefile').read_bytes()
        (self.headers / 'include/config/STALE_OPTION').touch()
        result = helper.synchronize(self.source, self.headers)
        self.assertTrue(result['changed'])
        self.assertEqual(helper.source_snapshot(self.source), helper.source_snapshot(self.headers))
        self.assertEqual((self.headers / 'Makefile').read_bytes(), old_makefile)
        for name in helper.TOOLS:
            self.assertEqual((self.headers / name).read_bytes(), elf(183))
            self.assertEqual((self.source / name).read_bytes(), elf(62))
        self.assertFalse((self.headers / 'include/config/STALE_OPTION').exists())
        self.assertTrue((self.headers / 'include/current.h').is_symlink())
        self.assertFalse(helper.synchronize(self.source, self.headers, verify=True)['changed'])
        self.assertFalse(helper.synchronize(self.source, self.headers)['changed'])

    def test_mismatched_release_or_generated_configuration_fails_before_mutation(self):
        old = (self.headers / 'Module.symvers').read_bytes()
        (self.headers / 'include/config/kernel.release').write_text('6.8.12-tegra\n')
        with self.assertRaisesRegex(ValueError, 'kernel release'):
            helper.synchronize(self.source, self.headers)
        self.assertEqual((self.headers / 'Module.symvers').read_bytes(), old)
        (self.headers / 'include/config/kernel.release').write_text('6.8.12-1021-tegra\n')
        (self.source / 'include/generated/autoconf.h').write_text('#define CONFIG_ARM64 1\n')
        with self.assertRaisesRegex(ValueError, 'autoconf.h disagree'):
            helper.synchronize(self.source, self.headers)
        self.assertEqual((self.headers / 'Module.symvers').read_bytes(), old)

    def test_kconfig_strings_are_unquoted_only_in_make_metadata(self):
        with (self.source / '.config').open('a') as stream:
            stream.write('CONFIG_LOCALVERSION=""\nCONFIG_CC_VERSION_TEXT="gcc 13.2.0"\n')
        with (self.source / 'include/config/auto.conf').open('a') as stream:
            stream.write('CONFIG_LOCALVERSION=\nCONFIG_CC_VERSION_TEXT=gcc 13.2.0\n')
        with (self.source / 'include/generated/autoconf.h').open('a') as stream:
            stream.write('#define CONFIG_LOCALVERSION ""\n#define CONFIG_CC_VERSION_TEXT "gcc 13.2.0"\n')
        self.assertTrue(helper.synchronize(self.source, self.headers)['changed'])

    def test_cross_host_tools_or_elf_in_header_tree_are_rejected(self):
        (self.headers / helper.TOOLS[0]).write_bytes(elf(62))
        with self.assertRaisesRegex(ValueError, 'native AArch64 ELF'):
            helper.synchronize(self.source, self.headers)
        (self.headers / helper.TOOLS[0]).write_bytes(elf(183))
        (self.source / 'include/generated/host-tool').write_bytes(elf(62))
        with self.assertRaisesRegex(ValueError, 'ELF executable'):
            helper.synchronize(self.source, self.headers)

    def test_symlink_escape_is_rejected(self):
        outside = self.root / 'outside.h'
        outside.write_text('external header')
        (self.source / 'include/escape.h').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'symlink escapes'):
            helper.synchronize(self.source, self.headers)

    def test_incomplete_copy_or_publish_failure_keeps_original_tree(self):
        original = helper.source_snapshot(self.headers)
        with mock.patch.object(helper, 'exchange_directories', side_effect=OSError('fixture failure')):
            with self.assertRaisesRegex(OSError, 'fixture failure'):
                helper.synchronize(self.source, self.headers)
        self.assertEqual(helper.source_snapshot(self.headers), original)
        self.assertFalse(list(self.root.glob('.ark-headers-*')))

    def test_verify_rejects_old_symbol_versions_without_writing(self):
        original = helper.source_snapshot(self.headers)
        with self.assertRaisesRegex(ValueError, 'differ from the completed'):
            helper.synchronize(self.source, self.headers, verify=True)
        self.assertEqual(helper.source_snapshot(self.headers), original)

    def test_wrong_vendor_release_suffix_is_not_preserved(self):
        path = self.headers / 'Makefile'
        path.write_text(path.read_text().replace('-1021-tegra', '-tegra'))
        with self.assertRaisesRegex(ValueError, 'suffix does not reproduce'):
            helper.synchronize(self.source, self.headers)

    def test_concurrent_destination_change_is_preserved(self):
        original = shutil.copytree
        def copy_with_edit(source, destination, *args, **kwargs):
            result = original(source, destination, *args, **kwargs)
            if Path(source) == self.source / 'include':
                (self.headers / 'include/soc/tegra/mc.h').write_text('external edit')
            return result
        with mock.patch.object(helper.shutil, 'copytree', side_effect=copy_with_edit):
            with self.assertRaisesRegex(ValueError, 'external changes preserved'):
                helper.synchronize(self.source, self.headers)
        self.assertEqual((self.headers / 'include/soc/tegra/mc.h').read_text(), 'external edit')
        self.assertIn('0xffffffff', (self.headers / 'Module.symvers').read_text())

    def test_unrelated_vendor_makefile_changes_are_not_silently_kept(self):
        with (self.headers / 'Makefile').open('a') as stream:
            stream.write('unexpected-target:\n')
        with self.assertRaisesRegex(ValueError, 'differs beyond'):
            helper.synchronize(self.source, self.headers)


if __name__ == '__main__':
    unittest.main()
