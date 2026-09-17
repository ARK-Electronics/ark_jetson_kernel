#!/usr/bin/env python3
"""Guard and exact-file lifecycle coverage for the Noble nginx address fix."""
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

import patch_ark_nginx_upstreams as helper

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'scripts/testdata/ark-nginx/ark-ui.nginx'


class ArkNginxUpstreamTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.original = FIXTURE.read_bytes()
        self.write('etc/nv_tegra_release', b'# R39 (release), REVISION: 2.1, fixture\n')
        self.write('usr/lib/os-release', b'ID=ubuntu\nVERSION_ID="24.04"\nVERSION_CODENAME=noble\n')
        self.write('etc/ark_jetson_kernel', b'target=JAJ\n')
        self.source = self.write(helper.SOURCE, self.original)
        self.source.chmod(0o640)

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_real_package_fixture_all_products_exact_roundtrip(self):
        self.assertEqual(helper.digest(self.original), helper.ORIGINAL)
        info = self.source.stat()
        for product in helper.PRODUCTS:
            with self.subTest(product=product):
                self.write('etc/ark_jetson_kernel', f'target={product}\n'.encode())
                self.assertEqual(helper.update(self.root, product), 'patched')
                installed = self.source.read_bytes()
                self.assertEqual(helper.digest(installed), helper.PATCHED)
                changed = [(old, new) for old, new in zip(
                    self.original.splitlines(), installed.splitlines()) if old != new]
                self.assertEqual(len(changed), 2)
                for port in (3000, 5006):
                    self.assertIn((f'        proxy_pass http://localhost:{port};'.encode(),
                                   f'        proxy_pass http://127.0.0.1:{port};'.encode()), changed)
                self.assertIn(b'proxy_pass http://127.0.0.1:1984/;', installed)
                self.assertEqual(helper.update(self.root, product), 'patched')
                self.assertEqual(helper.update(self.root, product, 'check'), 'patched')
                self.assertEqual(helper.update(self.root, product, 'restore'), 'original')
                self.assertEqual(helper.update(self.root, product, 'restore'), 'original')
                self.assertEqual(self.source.read_bytes(), self.original)
        after = self.source.stat()
        self.assertEqual((info.st_mode, info.st_uid, info.st_gid),
                         (after.st_mode, after.st_uid, after.st_gid))

    def test_read_only_check_does_not_change_original(self):
        self.assertEqual(helper.update(self.root, 'JAJ', 'check'), 'original')
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_wrong_release_and_os_are_rejected_without_writes(self):
        for release in (b'# R36 (release), REVISION: 5.0, fixture\n',
                        b'# R39 (release), REVISION: 3.0, fixture\n'):
            self.write('etc/nv_tegra_release', release)
            with self.assertRaisesRegex(ValueError, 'R39.2.1'):
                helper.update(self.root, 'JAJ')
        self.write('etc/nv_tegra_release', b'# R39 (release), REVISION: 2.1, fixture\n')
        self.write('usr/lib/os-release', b'ID=ubuntu\nVERSION_ID="22.04"\nVERSION_CODENAME=jammy\n')
        with self.assertRaisesRegex(ValueError, 'Noble'):
            helper.update(self.root, 'JAJ')
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_wrong_product_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'product stamp'):
            helper.update(self.root, 'PAB')
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_customer_configuration_is_never_overwritten(self):
        changed = self.original.replace(b'client_max_body_size 500M;', b'client_max_body_size 20M;')
        self.source.write_bytes(changed)
        for mode in ('apply', 'check', 'restore'):
            with self.assertRaisesRegex(ValueError, 'audited'):
                helper.update(self.root, 'JAJ', mode)
        self.assertEqual(self.source.read_bytes(), changed)

    def test_file_and_parent_symlinks_are_rejected(self):
        external = self.write('external.conf', self.original)
        self.source.unlink()
        self.source.symlink_to(external)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            helper.update(self.root, 'JAJ')
        self.source.unlink()
        self.source.write_bytes(self.original)
        directory = self.source.parent
        moved = directory.with_name('moved')
        directory.rename(moved)
        directory.symlink_to(moved)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            helper.update(self.root, 'JAJ')
        self.assertEqual(external.read_bytes(), self.original)
        self.assertEqual((moved / self.source.name).read_bytes(), self.original)

    def test_bad_result_hash_fails_before_replacement(self):
        with patch.object(helper, 'PATCHED', '0' * 64):
            with self.assertRaisesRegex(ValueError, 'output checksum'):
                helper.update(self.root, 'JAJ')
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_failed_publish_preserves_original_and_cleans_temp(self):
        with patch.object(Path, 'replace', side_effect=OSError('fixture failure')):
            with self.assertRaisesRegex(OSError, 'fixture failure'):
                helper.update(self.root, 'JAJ')
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertFalse(list(self.source.parent.glob('.ark-nginx-*')))

    def test_concurrent_customer_edit_is_preserved(self):
        original_regular = helper.regular
        reads = []
        changed = self.original + b'# concurrent edit\n'
        def edit_on_recheck(root, relative):
            if relative == helper.SOURCE:
                reads.append(relative)
                if len(reads) == 2:
                    self.source.write_bytes(changed)
            return original_regular(root, relative)
        with patch.object(helper, 'regular', side_effect=edit_on_recheck):
            with self.assertRaisesRegex(ValueError, 'changed during'):
                helper.update(self.root, 'JAJ')
        self.assertEqual(self.source.read_bytes(), changed)
        self.assertFalse(list(self.source.parent.glob('.ark-nginx-*')))

    def test_provision_hook_runs_after_package_install_for_r39_only(self):
        source = (ROOT / 'provision.sh').read_text()
        hook = re.search(r'if \[ "\$EXPECTED_BSP_RELEASE" = R39 \]; then\n(?:(?!\nfi).)*patch_ark_nginx_upstreams.py(?:(?!\nfi).)*\nfi', source, re.S)
        self.assertIsNotNone(hook)
        self.assertIn('--rootfs "$ROOTFS_DIR" --product "$TARGET"', hook.group(0))
        self.assertIn('chroot "$ROOTFS_DIR" nginx -t', hook.group(0))
        self.assertLess(source.index('apt-get install -y "/tmp/$ARK_OS_DEB"'), hook.start())


if __name__ == '__main__':
    unittest.main()
