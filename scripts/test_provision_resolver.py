#!/usr/bin/env python3
"""Exercise resolver replacement and EXIT cleanup with real temporary files."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

HELPER = Path(__file__).with_name("provision_resolver.sh")


class ProvisionResolverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "etc").mkdir()
        self.resolver = self.root / "etc/resolv.conf"
        self.backup = self.root / "etc/resolv.conf.ark-provision-backup"
        self.host_dns = self.root / "host-resolv.conf"
        self.host_dns.write_text("nameserver 192.0.2.53\n")

    def provision(self, exit_code=0, source=None):
        return subprocess.run([
            "bash", "-ec", '''
                source "$1"
                sudo() { "$@"; }
                trap restore_provision_resolver EXIT
                prepare_provision_resolver "$2" "$3"
                test ! -L "$2/etc/resolv.conf"
                cmp "$3" "$2/etc/resolv.conf"
                exit "$4"
            ''', "resolver-test", str(HELPER), str(self.root),
            str(source or self.host_dns), str(exit_code)
        ], text=True, capture_output=True)

    def test_noble_dangling_symlink_is_restored_on_success_and_failure(self):
        target = "../run/systemd/resolve/stub-resolv.conf"
        for exit_code in (0, 23):
            with self.subTest(exit_code=exit_code):
                self.resolver.symlink_to(target)
                result = self.provision(exit_code)
                self.assertEqual(result.returncode, exit_code, result.stderr)
                self.assertTrue(self.resolver.is_symlink())
                self.assertEqual(os.readlink(self.resolver), target)
                self.assertFalse(os.path.lexists(self.backup))
                self.resolver.unlink()

    def test_regular_file_content_and_mode_are_preserved(self):
        self.resolver.write_text("original resolver\n")
        self.resolver.chmod(0o640)
        result = self.provision(23)
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(self.resolver.read_text(), "original resolver\n")
        self.assertEqual(self.resolver.stat().st_mode & 0o777, 0o640)
        self.assertFalse(os.path.lexists(self.backup))

    def test_absent_original_leaves_no_host_dns_after_failure(self):
        result = self.provision(23)
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertFalse(os.path.lexists(self.resolver))
        self.assertFalse(os.path.lexists(self.backup))

    def test_copy_failure_restores_original(self):
        self.resolver.symlink_to("../run/systemd/resolve/stub-resolv.conf")
        result = self.provision(source=self.root / "missing-host-resolver")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(os.readlink(self.resolver), "../run/systemd/resolve/stub-resolv.conf")
        self.assertFalse(os.path.lexists(self.backup))

    def test_stale_backup_is_rejected_without_changing_either_file(self):
        self.resolver.write_text("original resolver\n")
        self.backup.symlink_to("missing-original-resolver")
        result = self.provision()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("stale provisioning resolver backup", result.stderr)
        self.assertEqual(self.resolver.read_text(), "original resolver\n")
        self.assertEqual(os.readlink(self.backup), "missing-original-resolver")


if __name__ == "__main__":
    unittest.main()
