#!/usr/bin/env python3
"""Offline regression checks for reversible JAJ rootfs profile changes."""

import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest

import configure_fast_boot as profile


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="test-jaj-fastboot-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / profile.SYSTEM / "multi-user.target.wants").mkdir(parents=True)
        (self.root / "etc/os-release").write_text('ID=ubuntu\nVERSION_ID="22.04"\n')
        self.system = self.root / profile.SYSTEM
        (self.system / "default.target").symlink_to("/lib/systemd/system/graphical.target")
        for name in ("ssh", "NetworkManager", "nv-l4t-bootloader-config", "nvfancontrol",
                     "nvidia-l4t-usb-device-mode", "ark-ui-backend", "mavlink-router",
                     "nginx", "jtop", "ark-os-firstboot"):
            (self.system / "multi-user.target.wants" / (name + ".service")).symlink_to(
                "/lib/systemd/system/" + name + ".service")
        (self.system / "multi-user.target.wants/ark-os.target").symlink_to(
            "/lib/systemd/system/ark-os.target")
        (self.system / "ark-os.target.wants").mkdir()
        (self.system / "ark-os.target.wants/ark-ui-backend.service").symlink_to(
            "/lib/systemd/system/ark-ui-backend.service")
        self.utmp = self.system / "systemd-update-utmp.service.d/override.conf"
        self.utmp.parent.mkdir()
        self.utmp.write_text("# Keep this comment\n[Service]\nExecStartPre=/bin/sleep 2\n")
        self.utmp.chmod(0o640)
        self.original = self.files()

    def files(self):
        return {str(path.relative_to(self.root)): profile.snapshot(path)
                for path in self.root.rglob("*") if path.is_symlink() or path.is_file()}

    def apply(self, defer=False, skip_utmp=False, direct_usb=False, skip_lvm=False):
        with contextlib.redirect_stdout(io.StringIO()):
            profile.apply(self.root, defer, skip_utmp, direct_usb, skip_lvm)

    def add_usb_runtime(self):
        directory = self.root / profile.USB_DIR
        directory.mkdir(parents=True)
        (directory / profile.USB_RUNTIME).write_text(
            "# NVIDIA fixture\n" + "\n".join(profile.USB_RUNTIME_DIRECTIVES) + "\n")
        (self.system / profile.USB_RUNTIME).symlink_to(
            f"/{profile.USB_DIR}/{profile.USB_RUNTIME}")
        handler = self.root / profile.USB_HANDLER
        handler.write_text(
            "#!/bin/bash\n# Preserve cable state handling\n"
            "if [ -d /sys/kernel/config/usb_gadget/l4t ]; then\n"
            "    service nv-l4t-usb-device-mode-runtime start\n"
            "else\n"
            "    service nv-l4t-usb-device-mode-runtime stop\n"
            "fi\n")
        handler.chmod(0o750)
        return handler

    def add_lvm_monitor(self, dependency="sysinit.target.wants"):
        directory = self.system / dependency
        directory.mkdir(exist_ok=True)
        link = directory / profile.LVM_MONITOR
        link.symlink_to("/lib/systemd/system/lvm2-monitor.service")
        return link

    def restore(self):
        with contextlib.redirect_stdout(io.StringIO()):
            profile.restore(self.root)

    def test_headless_default_does_not_change_service_enablement(self):
        self.apply()
        self.assertEqual(os.readlink(self.system / "default.target"),
                         "/lib/systemd/system/multi-user.target")
        for relative, state in self.original.items():
            if relative != profile.SYSTEM + "/default.target":
                self.assertEqual(profile.snapshot(self.root / relative), state)
        self.assertFalse((self.system / profile.LAUNCHER).exists())
        self.restore()
        self.assertEqual(self.files(), self.original)

    def test_defer_preserves_essential_and_disabled_services(self):
        self.apply(defer=True)
        for name in ("ssh", "NetworkManager", "nv-l4t-bootloader-config", "nvfancontrol",
                     "nvidia-l4t-usb-device-mode"):
            self.assertTrue((self.system / "multi-user.target.wants" / (name + ".service")).is_symlink())
        for name in ("ark-ui-backend", "mavlink-router", "nginx", "jtop", "ark-os-firstboot"):
            self.assertFalse((self.system / "multi-user.target.wants" / (name + ".service")).is_symlink())
            self.assertTrue((self.system / (profile.BACKGROUND + ".wants") / (name + ".service")).is_symlink())
        self.assertTrue((self.system / "ark-os.target.wants/ark-ui-backend.service").is_symlink())
        self.assertFalse((self.system / (profile.BACKGROUND + ".wants/polaris.service")).is_symlink())
        self.restore()
        self.assertEqual(self.files(), self.original)

    def test_reapply_and_restore_are_idempotent(self):
        self.apply(defer=True, skip_utmp=True)
        applied = self.files()
        self.apply(defer=True, skip_utmp=True)
        self.assertEqual(self.files(), applied)
        self.restore()
        self.restore()
        self.assertEqual(self.files(), self.original)

    def test_utmp_removes_only_known_wait_and_restores_mode(self):
        self.utmp.write_text("# Keep this comment\n[Service]\nExecStartPre=/bin/true\nExecStartPre=/bin/sleep 2\n")
        self.original = self.files()
        self.apply(skip_utmp=True)
        self.assertEqual(self.utmp.read_text(), "# Keep this comment\n[Service]\nExecStartPre=/bin/true\n")
        self.assertEqual(self.utmp.stat().st_mode & 0o777, 0o640)
        self.restore()
        self.assertEqual(self.files(), self.original)

    def test_unknown_utmp_configuration_is_not_overwritten(self):
        self.utmp.write_text("[Service]\nExecStartPre=/bin/sleep 20\n")
        before = self.files()
        with self.assertRaisesRegex(ValueError, "known NVIDIA"):
            self.apply(skip_utmp=True)
        self.assertEqual(self.files(), before)

    def test_direct_usb_and_lvm_with_utmp_roundtrip(self):
        handler = self.add_usb_runtime()
        lvm = self.add_lvm_monitor()
        self.utmp.write_text("# Keep this comment\n[Service]\nExecStartPre=/bin/true\nExecStartPre=/bin/sleep 2\n")
        self.original = self.files()
        self.apply(direct_usb=True, skip_lvm=True, skip_utmp=True)
        expected = self.original[profile.USB_HANDLER]
        import base64
        expected_text = base64.b64decode(expected["data"]).decode().replace(
            "service nv-l4t-usb-device-mode-runtime start", f"/bin/systemctl start {profile.USB_RUNTIME}"
        ).replace("service nv-l4t-usb-device-mode-runtime stop", f"/bin/systemctl stop {profile.USB_RUNTIME}")
        self.assertEqual(handler.read_text(), expected_text)
        self.assertEqual(handler.stat().st_mode & 0o777, 0o750)
        self.assertFalse(lvm.is_symlink())
        self.assertIn("ExecStartPre=/bin/true", self.utmp.read_text())
        self.assertNotIn("sleep 2", self.utmp.read_text())
        self.assertTrue((self.system / "multi-user.target.wants/ark-ui-backend.service").is_symlink())
        applied = self.files()
        self.apply(direct_usb=True, skip_lvm=True, skip_utmp=True)
        self.assertEqual(self.files(), applied)
        self.restore()
        self.assertEqual(self.files(), self.original)

    def test_direct_usb_requires_known_calls(self):
        handler = self.add_usb_runtime()
        handler.write_text(handler.read_text().replace("runtime stop", "runtime restart"))
        before = self.files()
        with self.assertRaisesRegex(ValueError, "known NVIDIA USB runtime"):
            self.apply(direct_usb=True)
        self.assertEqual(self.files(), before)

    def test_direct_usb_rejects_associated_sockets(self):
        self.add_usb_runtime()
        for name, contents in (
            ("nv-l4t-usb-device-mode-runtime.socket", "[Socket]\nListenStream=1234\n"),
            ("custom.socket", f"[Socket]\nService = {profile.USB_RUNTIME}\nListenStream=1234\n"),
        ):
            with self.subTest(name=name):
                socket = self.system / name
                socket.write_text(contents)
                before = self.files()
                with self.assertRaisesRegex(ValueError, "associated socket"):
                    self.apply(direct_usb=True)
                self.assertEqual(self.files(), before)
                socket.unlink()

    def test_direct_usb_reapply_rechecks_socket_associations(self):
        self.add_usb_runtime()
        self.apply(direct_usb=True)
        (self.system / "custom.socket").write_text(f"[Socket]\nService={profile.USB_RUNTIME}\n")
        before = self.files()
        with self.assertRaisesRegex(ValueError, "associated socket"):
            self.apply(direct_usb=True)
        self.assertEqual(self.files(), before)

    def test_direct_usb_rejects_custom_unit_and_dropins(self):
        self.add_usb_runtime()
        unit = self.root / profile.USB_DIR / profile.USB_RUNTIME
        original_unit = unit.read_text()
        unit.write_text(original_unit + "TimeoutStartSec=10\n")
        with self.assertRaisesRegex(ValueError, "known NVIDIA runtime"):
            self.apply(direct_usb=True)
        unit.write_text(original_unit)
        dropin = self.system / (profile.USB_RUNTIME + ".d")
        dropin.mkdir()
        (dropin / "override.conf").write_text("[Unit]\nAfter=custom.target\n")
        before = self.files()
        with self.assertRaisesRegex(ValueError, "drop-ins"):
            self.apply(direct_usb=True)
        self.assertEqual(self.files(), before)

    def test_direct_usb_resolves_absolute_links_inside_rootfs(self):
        self.add_usb_runtime()
        directory = self.root / "usr/lib/systemd/system"
        directory.mkdir(parents=True)
        (self.root / "lib").symlink_to("usr/lib")
        (directory / "custom.socket").write_text(f"[Socket]\nService={profile.USB_RUNTIME}\n")
        before = self.files()
        with self.assertRaisesRegex(ValueError, "associated socket"):
            self.apply(direct_usb=True)
        self.assertEqual(self.files(), before)

    def test_lvm_disabled_stays_disabled(self):
        self.apply(skip_lvm=True)
        self.assertFalse((self.system / "sysinit.target.wants/lvm2-monitor.service").is_symlink())
        self.restore()
        self.assertEqual(self.files(), self.original)

    def test_lvm_required_dependency_is_not_removed(self):
        self.add_lvm_monitor("custom.target.requires")
        before = self.files()
        with self.assertRaisesRegex(ValueError, "required dependency"):
            self.apply(skip_lvm=True)
        self.assertEqual(self.files(), before)

    def test_new_option_changes_require_restore(self):
        self.apply()
        for options in ({"direct_usb": True}, {"skip_lvm": True}):
            with self.subTest(options=options):
                with self.assertRaisesRegex(ValueError, "restore before"):
                    self.apply(**options)

    def test_restore_refuses_conflicting_user_edit_before_any_changes(self):
        self.apply(defer=True)
        (self.system / profile.LAUNCHER).write_text("# edited by user\n")
        before = self.files()
        with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
            self.restore()
        self.assertEqual(self.files(), before)

    def test_interrupted_write_leftover_does_not_block_restore(self):
        self.apply(defer=True)
        leftover = self.system / ".ark-fastboot-interrupted"
        leftover.mkdir()
        (leftover / "entry").write_text("unfinished write")
        self.restore()
        self.assertEqual(os.readlink(self.system / "default.target"),
                         "/lib/systemd/system/graphical.target")
        self.assertEqual((leftover / "entry").read_text(), "unfinished write")

    @unittest.skipUnless(os.geteuid() == 0, "distinct-owner roundtrip requires root")
    def test_restore_preserves_distinct_file_and_symlink_owners(self):
        handler = self.add_usb_runtime()
        lvm = self.add_lvm_monitor()
        os.chown(handler, 1238, 1239)
        os.chown(lvm, 1240, 1241, follow_symlinks=False)
        os.chown(self.utmp, 1234, 1235)
        os.chown(self.system / "default.target", 1236, 1237, follow_symlinks=False)
        self.original = self.files()
        self.apply(defer=True, skip_utmp=True, direct_usb=True, skip_lvm=True)
        self.assertEqual((self.utmp.stat().st_uid, self.utmp.stat().st_gid), (1234, 1235))
        self.assertEqual((handler.stat().st_uid, handler.stat().st_gid), (1238, 1239))
        self.restore()
        self.assertEqual(self.files(), self.original)

    def test_option_change_requires_restore(self):
        self.apply()
        with self.assertRaisesRegex(ValueError, "restore before"):
            self.apply(defer=True)

    def test_symlink_parent_cannot_escape_rootfs(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.system / (profile.BACKGROUND + ".wants")).symlink_to(outside)
        before = self.files()
        with self.assertRaisesRegex(ValueError, "symlink parent"):
            self.apply(defer=True)
        self.assertEqual(self.files(), before)
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
