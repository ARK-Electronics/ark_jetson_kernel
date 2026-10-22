#!/usr/bin/env python3
"""Audit the real sample-rootfs logging topology and transactional opt-in."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

import configure_fast_boot as profile


FIXTURES = Path(__file__).parent / "testdata/logging"


class KernelLoggingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="test-kernel-logging-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(FIXTURES, self.root, dirs_exist_ok=True)
        # Exercise merged-/usr and Ubuntu's usual os-release symlink.
        (self.root / "usr").mkdir()
        (self.root / "lib").rename(self.root / "usr/lib")
        (self.root / "lib").symlink_to("usr/lib")
        (self.root / "usr/lib/os-release").write_text('ID=ubuntu\nVERSION_ID="22.04"\n')
        (self.root / "etc/os-release").symlink_to("../usr/lib/os-release")
        self.system = self.root / profile.SYSTEM
        (self.system / "multi-user.target.wants").mkdir(parents=True)
        (self.system / "default.target").symlink_to("/lib/systemd/system/graphical.target")
        for relative in ("syslog.service", "multi-user.target.wants/rsyslog.service"):
            (self.system / relative).symlink_to("/lib/systemd/system/rsyslog.service")
        for name in ("ssh", "nv-l4t-bootloader-config", "jtop", "system-manager"):
            (self.system / "multi-user.target.wants" / (name + ".service")).symlink_to(
                "/lib/systemd/system/" + name + ".service")
        self.fstab = self.root / "etc/fstab"
        self.fstab.write_text('/dev/root / ext4 defaults 0 1\nUUID=TEST /boot/efi vfat defaults 0 1\n')
        (self.root / "var/log").mkdir(parents=True)
        for relative in ("usr/sbin/rsyslogd", "usr/lib/aarch64-linux-gnu/rsyslog/imklog.so",
                         "usr/lib/aarch64-linux-gnu/rsyslog/imuxsock.so"):
            binary = self.root / relative
            binary.parent.mkdir(parents=True, exist_ok=True)
            # These tests check installed-file guards, not ARM execution.
            binary.write_bytes(b"\x7fELF" + b"test fixture; never executed")
            binary.chmod(0o755)
        self.original = self.files()

    def files(self):
        return {str(p.relative_to(self.root)): profile.snapshot(p)
                for p in self.root.rglob("*") if p.is_file() or p.is_symlink()}

    def apply(self, enabled=True):
        with contextlib.redirect_stdout(io.StringIO()):
            profile.apply(self.root, False, False, rsyslog_kernel=enabled)

    def restore(self):
        with contextlib.redirect_stdout(io.StringIO()):
            profile.restore(self.root)

    def reject_without_mutation(self, pattern):
        before = self.files()
        with self.assertRaisesRegex(ValueError, pattern):
            self.apply()
        self.assertEqual(self.files(), before)

    def test_default_keeps_kernel_journal_unchanged(self):
        self.apply(enabled=False)
        self.assertFalse((self.root / profile.KERNEL_LOG_DROPIN).exists())
        self.assertFalse(profile.read_manifest(self.root)["rsyslog_kernel_logging"])
        self.restore()
        self.assertEqual(self.files(), self.original)

    def test_exact_stock_configs_opt_in_restore_and_idempotence(self):
        self.apply()
        manifest = profile.read_manifest(self.root)
        self.assertTrue(manifest["rsyslog_kernel_logging"])
        self.assertEqual(set(manifest["changes"]),
                         {profile.KERNEL_LOG_DROPIN, profile.SYSTEM + "/default.target"})
        self.assertEqual((self.root / profile.KERNEL_LOG_DROPIN).read_bytes(), profile.KERNEL_LOG_CONTENT)
        for relative, state in self.original.items():
            if relative != profile.SYSTEM + "/default.target":
                self.assertEqual(profile.snapshot(self.root / relative), state)
        applied = self.files()
        self.apply()
        self.assertEqual(self.files(), applied)
        self.restore()
        self.restore()
        self.assertEqual(self.files(), self.original)

    def test_unknown_distro_or_config_hash_rejected(self):
        files = ["usr/lib/os-release", *profile.KERNEL_LOG_CONFIG_HASHES]
        for relative in files:
            with self.subTest(relative=relative):
                p = profile.resolve_offline(self.root, relative)
                original = p.read_bytes()
                p.write_bytes(original.replace(b'22.04', b'24.04') if relative.endswith('os-release')
                              else original + b"# Unknown package/customer configuration\n")
                self.reject_without_mutation("distribution|configuration hash")
                p.write_bytes(original)

    def test_missing_or_duplicate_kernel_input_and_output_rejected(self):
        p = self.root / "etc/rsyslog.conf"
        original = p.read_text()
        for change in (original.replace('module(load="imklog"', '#module(load="imklog"'),
                       original + '\nmodule(load="imklog")\n',
                       original + '\nmodule(load="imjournal")\n'):
            p.write_text(change)
            self.reject_without_mutation("configuration hash")
        p.write_text(original)
        p = self.root / "etc/rsyslog.d/50-default.conf"
        p.write_text(p.read_text().replace('/var/log/kern.log', '/dev/null'))
        self.reject_without_mutation("configuration hash")

    def test_additional_include_cannot_discard_or_duplicate_kernel_records(self):
        p = self.root / "etc/rsyslog.d/00-custom.conf"
        for text in ("kern.* stop\n", 'module(load="imklog")\n', 'include(file="/tmp/custom")\n'):
            p.write_text(text)
            self.reject_without_mutation("include directory")

    def test_known_units_and_all_relevant_dropins_are_audited(self):
        for name in profile.KERNEL_LOG_UNIT_HASHES:
            with self.subTest(name=name):
                p = self.root / "usr/lib/systemd/system" / name
                original = p.read_bytes()
                p.write_bytes(original + b"# Unknown revision\n")
                self.reject_without_mutation("native unit hash")
                p.write_bytes(original)
        for relative in ("etc/systemd/system/rsyslog.service.d/override.conf",
                         "usr/lib/systemd/system/systemd-.service.d/override.conf",
                         "etc/systemd/system/syslog.service.d/override.conf",
                         "usr/local/lib/systemd/system/socket.d/override.conf"):
            with self.subTest(relative=relative):
                p = self.root / relative
                p.parent.mkdir(parents=True)
                p.write_text("[Service]\nExecStart=/bin/false\n")
                self.reject_without_mutation("drop-ins/dependencies")
                p.unlink()
                p.parent.rmdir()

    def test_disabled_or_redirected_rsyslog_alias_rejected(self):
        for relative in ("syslog.service", "multi-user.target.wants/rsyslog.service"):
            p = self.system / relative
            original = os.readlink(p)
            for target in (None, "/dev/null", "/lib/systemd/system/custom.service"):
                with self.subTest(relative=relative, target=target):
                    p.unlink()
                    if target is not None:
                        p.symlink_to(target)
                    self.reject_without_mutation("enabled rsyslog")
                    if p.is_symlink():
                        p.unlink()
                    p.symlink_to(original)
        p = self.system / "rsyslog.service"
        p.symlink_to("/dev/null")
        self.reject_without_mutation("native unit hash")

    def test_missing_input_binary_or_nonexecutable_logger_rejected(self):
        p = self.root / "usr/lib/aarch64-linux-gnu/rsyslog/imklog.so"
        original = p.read_bytes()
        p.unlink()
        self.reject_without_mutation("regular file")
        p.write_bytes(b"not an ELF")
        self.reject_without_mutation("installed logger/input ELF")
        p.write_bytes(original)
        (self.root / "usr/sbin/rsyslogd").chmod(0o644)
        self.reject_without_mutation("executable rsyslogd")

    def test_competing_daemon_and_custom_journal_configuration_rejected(self):
        for relative in ("etc/systemd/system/klogd.service",
                         "etc/systemd/system/syslog-ng.service",
                         "usr/lib/systemd/journald.conf",
                         "etc/systemd/journald.conf.d/50-custom.conf"):
            with self.subTest(relative=relative):
                p = self.root / relative
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("# custom topology\n")
                self.reject_without_mutation("competing|custom overrides|custom drop-ins")
                p.unlink()
                if p.parent.name == "journald.conf.d":
                    p.parent.rmdir()

    def test_persistence_requires_known_ext4_root_and_no_log_mounts(self):
        original = self.fstab.read_text()
        for text in ("", original.replace("ext4", "tmpfs"), original.replace("defaults 0 1", "ro 0 1", 1),
                     original + "tmpfs /var/log tmpfs defaults 0 0\n",
                     original + "UUID=OTHER /var ext4 defaults 0 1\n",
                     original + "tmpfs /var\\057log tmpfs defaults 0 0\n"):
            self.fstab.write_text(text)
            self.reject_without_mutation("ext4 root|custom mounts")
        self.fstab.write_text(original)
        p = self.system / "var-log.mount"
        p.write_text("[Mount]\nWhat=tmpfs\nWhere=/var/log\nType=tmpfs\n")
        self.reject_without_mutation("custom mount units")

    def test_fstab_mount_aliases_cannot_hide_volatile_kernel_logs(self):
        original = self.fstab.read_text()
        for mountpoint in ("/var/log/", "/var//log", "/var/log/.", "//var/log",
                           "/var/lib/../log", "/var/", "/var/log/kern.log/"):
            with self.subTest(mountpoint=mountpoint):
                self.fstab.write_text(original + f"tmpfs {mountpoint} tmpfs defaults 0 0\n")
                self.reject_without_mutation("canonical absolute fstab mountpoints")
        # Ordinary swap entries do not refer to a filesystem mountpoint.
        self.fstab.write_text(original + "/swapfile none swap sw 0 0\n")
        self.apply()

    def test_symlinked_configuration_or_log_output_rejected(self):
        p = self.root / "var/log/kern.log"
        p.symlink_to("/dev/null")
        self.reject_without_mutation("ordinary persistent kern.log")
        p.unlink()
        p = self.root / "etc/rsyslog.conf"
        original = p.read_bytes()
        p.unlink()
        (self.root / "etc/custom.conf").write_bytes(original)
        p.symlink_to("custom.conf")
        self.reject_without_mutation("regular file")

    def test_status_and_reapply_detect_later_logging_drift_but_restore_is_possible(self):
        self.apply()
        p = self.root / "etc/rsyslog.d/50-default.conf"
        p.write_text(p.read_text().replace("/var/log/kern.log", "/dev/null"))
        with self.assertRaisesRegex(ValueError, "configuration hash"):
            self.apply()
        with mock.patch("sys.argv", ["configure_fast_boot.py", "status", str(self.root)]):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(profile.main(), 1)
        # Undo only our managed drop-in, preserving the user's changed config.
        self.restore()
        self.assertFalse((self.root / profile.KERNEL_LOG_DROPIN).exists())
        self.assertIn("/dev/null", p.read_text())

    def test_interrupted_apply_restores_without_changing_logger(self):
        write = profile.write_state
        def fail_dropin(path, state):
            if path == self.root / profile.KERNEL_LOG_DROPIN:
                raise OSError("simulated interrupted write")
            return write(path, state)
        with mock.patch.object(profile, "write_state", side_effect=fail_dropin):
            with self.assertRaisesRegex(OSError, "simulated"):
                self.apply()
        self.restore()
        self.assertEqual(self.files(), self.original)

    def test_cli_exposes_explicit_choice_and_status(self):
        with mock.patch("sys.argv", ["configure_fast_boot.py", "apply", str(self.root),
                                     "--rsyslog-kernel-logging"]):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(profile.main(), 0)
        output = io.StringIO()
        with mock.patch("sys.argv", ["configure_fast_boot.py", "status", str(self.root)]):
            with contextlib.redirect_stdout(output):
                self.assertEqual(profile.main(), 0)
        self.assertTrue(json.loads(output.getvalue())["rsyslog_kernel_logging"])


if __name__ == "__main__":
    unittest.main()
