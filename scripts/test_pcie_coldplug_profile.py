#!/usr/bin/env python3
"""Exercise exact native fnmatch exclusion and reversible offline coldplug setup."""
import contextlib
import ctypes
import io
import json
import os
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest import mock

import configure_fast_boot as profile


class ColdplugTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="test-jaj-coldplug-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.system = self.root / profile.SYSTEM
        self.system.mkdir(parents=True)
        (self.system / "multi-user.target.wants").mkdir()
        self.default = self.system / "default.target"
        self.default.symlink_to("/lib/systemd/system/graphical.target")
        self.stamp = self.root / "etc/ark_jetson_kernel"
        self.stamp.write_text("target=JAJ\nprecomputed_initrd=1\n")
        vendor = self.root / "usr/lib/systemd/system"
        vendor.mkdir(parents=True)
        (self.root / "lib").symlink_to("usr/lib")
        self.vendor = vendor / profile.UDEV_TRIGGER
        self.vendor.write_text("# Audited vendor fixture\n" + "\n".join(profile.UDEV_TRIGGER_DIRECTIVES) + "\n")
        self.vendor.chmod(0o640)
        for name in ("nv-l4t-bootloader-config", "ssh", "jtop", "system-manager"):
            (self.system / "multi-user.target.wants" / (name + ".service")).symlink_to(
                "/lib/systemd/system/" + name + ".service")
        boot = self.root / "boot"
        boot.mkdir()
        (boot / "Image").write_bytes(b"unchanged kernel")
        (boot / "carrier.dtb").write_bytes(b"unchanged PCIe configuration")
        self.original = self.files()

    def files(self):
        return {str(path.relative_to(self.root)): profile.snapshot(path)
                for path in self.root.rglob("*") if path.is_file() or path.is_symlink()}

    def apply(self, enabled=True, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            profile.apply(self.root, False, False, parallel_pcie=enabled, **kwargs)

    def restore(self):
        with contextlib.redirect_stdout(io.StringIO()):
            profile.restore(self.root)

    def assert_rejected_without_changes(self, pattern):
        before = self.files()
        with self.assertRaisesRegex(ValueError, pattern):
            self.apply()
        self.assertEqual(self.files(), before)

    def test_globs_exclude_only_exact_sysname_using_native_fnmatch(self):
        # Use libc's fnmatch(flags=0), the same matcher used by systemd249.
        fnmatch = ctypes.CDLL(None).fnmatch
        fnmatch.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int)
        fnmatch.restype = ctypes.c_int
        target = profile.JAJ_PCIE_SYSNAME
        patterns = profile.sysname_complement(target)
        names = {target, "14100000.pcie", "14160000.pcie", "140a0000.pcie",
                 "0007:00:00.0", "0007:01:00.0", "nvme0n1", "eth0", "usb1",
                 "platform:bpmp--platform:" + target, "x" + target, target + "x",
                 "141E0000.pcie", "141e0000.pcie-extra", "pci", "1", ".device"}
        for index in range(len(target)):
            for char in "abcdefghijklmnopqrstuvwxyz0123456789_.:-[]*?":
                names.add(target[:index] + char + target[index + 1:])
                names.add(target[:index] + char + target[index:])
            if index:
                names.add(target[:index])
        for name in names:
            with self.subTest(name=name):
                matches = any(fnmatch(pattern.encode(), name.encode(), 0) == 0 for pattern in patterns)
                self.assertEqual(matches, name != target)

    def test_generated_unit_keeps_native_commands_and_replay_out_of_critical_order(self):
        self.apply()
        text = (self.root / profile.PCIE_DROPIN).read_text()
        commands = [line.removeprefix("ExecStart=") for line in text.splitlines()
                    if line.startswith("ExecStart=")]
        self.assertEqual(commands[:2], ["", "-udevadm trigger --type=subsystems --action=add"])
        arguments = shlex.split(commands[2])
        self.assertEqual(arguments[:4], ["-udevadm", "trigger", "--type=devices", "--action=add"])
        self.assertEqual(arguments[4:], ["--sysname-match=" + pattern for pattern in
                                         profile.sysname_complement(profile.JAJ_PCIE_SYSNAME)])
        self.assertIn("Wants=" + profile.PCIE_REPLAY, text)
        self.assertNotIn("After=", text)
        self.assertNotIn("Before=", text)
        replay = (self.system / profile.PCIE_REPLAY).read_text()
        self.assertIn("DefaultDependencies=no", replay)
        self.assertNotIn("[Install]", replay)
        for line in replay.splitlines():
            if line.startswith(("Before=", "After=")):
                self.assertTrue(set(line.split("=", 1)[1].split()) <= {
                    "shutdown.target", "systemd-udevd-kernel.socket", "systemd-udevd-control.socket"})
        replay_command = next(line.split("=", 1)[1] for line in replay.splitlines()
                              if line.startswith("ExecStart="))
        self.assertEqual(shlex.split(replay_command), ["udevadm", "trigger", "--type=devices",
                         "--action=add", "--subsystem-match=platform", "--sysname-match=141e0000.pcie"])
        self.assertNotIn("--settle", replay)

    def test_roundtrip_and_reapply_preserve_peripherals_services_and_vendor(self):
        self.apply()
        for relative, state in self.original.items():
            if relative != profile.SYSTEM + "/default.target":
                self.assertEqual(profile.snapshot(self.root / relative), state)
        self.assertTrue(profile.read_manifest(self.root)["parallel_jaj_pcie_coldplug"])
        applied = self.files()
        self.apply()
        self.assertEqual(self.files(), applied)
        self.restore()
        self.assertEqual(self.files(), self.original)

    def test_disabled_option_installs_no_replay(self):
        self.apply(enabled=False)
        self.assertFalse((self.root / profile.PCIE_DROPIN).exists())
        self.assertFalse((self.system / profile.PCIE_REPLAY).exists())
        with self.assertRaisesRegex(ValueError, "restore before"):
            self.apply()
        self.restore()
        self.assertEqual(self.files(), self.original)

    def test_requires_jaj_build_stamp(self):
        for target in ("PAB", "PAB_V3", "UNKNOWN", None):
            with self.subTest(target=target):
                if target is None:
                    self.stamp.unlink()
                else:
                    self.stamp.write_text("target=" + target + "\n")
                self.assert_rejected_without_changes("target=JAJ")

    def test_unknown_missing_or_symlink_vendor_unit_is_rejected(self):
        original = self.vendor.read_text()
        for text in (original.replace("--action=add", "--action=change"),
                     original + "ExecStartPost=/bin/true\n", None):
            with self.subTest(text=text):
                if text is None:
                    self.vendor.unlink()
                else:
                    self.vendor.write_text(text)
                self.assert_rejected_without_changes("known.*vendor")
                self.vendor.write_text(original)
        self.vendor.unlink()
        self.vendor.symlink_to("other.service")
        self.assert_rejected_without_changes("known regular vendor")

    def test_existing_overrides_masks_and_replay_units_are_rejected(self):
        for name in (profile.UDEV_TRIGGER, profile.PCIE_REPLAY):
            for mode in ("file", "mask", "alias"):
                with self.subTest(name=name, mode=mode):
                    path = self.system / name
                    if mode == "file":
                        path.write_text("[Service]\nExecStart=/bin/true\n")
                    else:
                        path.symlink_to("/dev/null" if mode == "mask" else "other.service")
                    self.assert_rejected_without_changes("existing unit override or mask")
                    path.unlink()

    def test_custom_dropins_and_dependency_directories_are_rejected(self):
        for relative in (
            "etc/systemd/system/systemd-udev-trigger.service.d",
            "usr/lib/systemd/system/systemd-udev-.service.d",
            "etc/systemd/system/systemd-.service.d",
            "usr/lib/systemd/system/service.d",
            "etc/systemd/system.control/systemd-udev-trigger.service.d",
            "run/systemd/generator/systemd-udev-trigger.service.requires",
            "etc/systemd/system/ark-jaj-pcie-coldplug.service.d",
            "etc/systemd/system/ark-jaj-.service.d",
            "etc/systemd/system/ark-jaj-pcie-coldplug.service.wants",
        ):
            with self.subTest(relative=relative):
                path = self.root / relative
                path.mkdir(parents=True)
                self.assert_rejected_without_changes("custom drop-ins or dependencies")
                path.rmdir()

    def test_reapply_rechecks_vendor_and_added_custom_dropin(self):
        self.apply()
        original_vendor = self.vendor.read_text()
        self.vendor.write_text(original_vendor + "ExecStartPost=/bin/true\n")
        self.assert_rejected_without_changes("known regular vendor")
        self.vendor.write_text(original_vendor)
        custom = self.system / (profile.UDEV_TRIGGER + ".d") / "99-custom.conf"
        custom.write_text("[Service]\nTimeoutStartSec=10\n")
        self.assert_rejected_without_changes("custom drop-ins or dependencies")
        self.restore()
        self.assertEqual(custom.read_text(), "[Service]\nTimeoutStartSec=10\n")
        self.assertFalse((self.system / profile.PCIE_REPLAY).exists())

    def test_status_cli_reports_flag_and_rechecks_stamp(self):
        output = io.StringIO()
        with mock.patch("sys.argv", ["configure_fast_boot.py", "apply", str(self.root),
                                     "--parallel-jaj-pcie-coldplug"]):
            with contextlib.redirect_stdout(output):
                self.assertEqual(profile.main(), 0)
        output = io.StringIO()
        with mock.patch("sys.argv", ["configure_fast_boot.py", "status", str(self.root)]):
            with contextlib.redirect_stdout(output):
                self.assertEqual(profile.main(), 0)
        self.assertTrue(json.loads(output.getvalue())["parallel_jaj_pcie_coldplug"])
        self.stamp.write_text("target=PAB\n")
        with mock.patch("sys.argv", ["configure_fast_boot.py", "status", str(self.root)]):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(profile.main(), 1)


if __name__ == "__main__":
    unittest.main()
