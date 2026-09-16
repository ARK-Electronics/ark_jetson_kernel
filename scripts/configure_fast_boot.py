#!/usr/bin/env python3
"""Apply or restore an opt-in JAJ headless profile in an offline rootfs."""

import argparse
import base64
import json
import os
import re
from pathlib import Path
import stat
import sys
import tempfile


SYSTEM = "etc/systemd/system"
MANIFEST = "var/lib/ark-fastboot/profile.json"
USB_DIR = "opt/nvidia/l4t-usb-device-mode"
USB_RUNTIME = "nv-l4t-usb-device-mode-runtime.service"
USB_HANDLER = f"{USB_DIR}/nv-l4t-usb-device-mode-state-change.sh"
LVM_MONITOR = "lvm2-monitor.service"
UNIT_DIRS = ("etc/systemd/system", "run/systemd/system", "usr/lib/systemd/system", "lib/systemd/system")
USB_RUNTIME_DIRECTIVES = [
    "[Unit]", "Description=Runtime configuration for USB device mode",
    "[Service]", "Type=oneshot", "RemainAfterExit=yes",
    f"ExecStart=/{USB_DIR}/nv-l4t-usb-device-mode-runtime-start.sh",
    f"ExecStopPost=/{USB_DIR}/nv-l4t-usb-device-mode-runtime-stop.sh",
]
BACKGROUND = "ark-fastboot-background.target"
LAUNCHER = "ark-fastboot-background.service"
TEMPLATES = Path(__file__).resolve().parents[1] / "products/JAJ/fastboot/rootfs"
# Keep this explicit: these are application/UI services, not a blacklist of
# NVIDIA boot validation, thermal/power, SSH, or network setup services.
DEFER_CANDIDATES = (
    "ark-os.target", "ark-os-firstboot.service", "ark-ui-backend.service",
    "autopilot-manager.service", "connection-manager.service",
    "service-manager.service", "system-manager.service", "mavlink-router.service",
    "rtsp-server.service", "go2rtc.service", "jetson-can.service",
    "dds-agent.service", "logloader.service", "polaris.service",
    "pointperfect.service", "flight-review.service", "rid-transmitter.service",
    "nginx.service", "jtop.service",
)


def path_in(root, relative):
    """Never follow a rootfs directory symlink into the build host."""
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Unsafe managed path: {relative}")
    path = root / relative
    for parent in path.parents:
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError(f"Managed path has a symlink parent: {parent}")
    return path


def snapshot(path):
    if path.is_symlink():
        info = path.lstat()
        return {"type": "symlink", "target": os.readlink(path),
                "uid": info.st_uid, "gid": info.st_gid}
    if not path.exists():
        return {"type": "absent"}
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"Expected a regular file or symlink: {path}")
    return {"type": "file", "data": base64.b64encode(path.read_bytes()).decode(),
            "mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid}


def file_state(data, mode=0o644, uid=None, gid=None):
    return {"type": "file", "data": base64.b64encode(data).decode(), "mode": mode,
            "uid": os.geteuid() if uid is None else uid,
            "gid": os.getegid() if gid is None else gid}


def symlink_state(target):
    return {"type": "symlink", "target": target, "uid": os.geteuid(), "gid": os.getegid()}


def write_state(path, state):
    if state["type"] == "absent":
        if path.exists() or path.is_symlink():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    # A unique temporary directory on the same filesystem allows atomic rename
    # without a stale fixed filename blocking restore after an interrupted run.
    with tempfile.TemporaryDirectory(prefix=".ark-fastboot-", dir=path.parent) as directory:
        temporary = Path(directory) / "entry"
        if state["type"] == "symlink":
            temporary.symlink_to(state["target"])
        else:
            temporary.write_bytes(base64.b64decode(state["data"]))
        os.chown(temporary, state["uid"], state["gid"], follow_symlinks=False)
        if state["type"] == "file":
            temporary.chmod(state["mode"])
        temporary.replace(path)


def read_manifest(root):
    path = path_in(root, MANIFEST)
    if path.is_symlink():
        raise ValueError(f"Manifest must not be a symlink: {path}")
    if not path.exists():
        return None
    manifest = json.loads(path.read_text())
    if manifest.get("version") != 1:
        raise ValueError("Unsupported fast-boot manifest version")
    return manifest


def check_changes(root, manifest, allow_original=False):
    conflicts = []
    for relative, change in manifest["changes"].items():
        current = snapshot(path_in(root, relative))
        if current != change["applied"] and not (
                allow_original and current == change["original"]):
            conflicts.append(relative)
    if conflicts:
        raise ValueError("Managed files were changed outside this profile; refusing "
                         "to overwrite them:\n  " + "\n  ".join(conflicts))


def resolve_offline(root, relative):
    """Resolve rootfs-owned unit symlinks without dereferencing host paths."""
    pending = list(Path(relative).parts)
    resolved = []
    links = 0
    while pending:
        part = pending.pop(0)
        if part in ("/", ""):
            resolved = []
        elif part == ".":
            continue
        elif part == "..":
            if not resolved:
                raise ValueError(f"Unit path escapes the rootfs: {relative}")
            resolved.pop()
        else:
            candidate = root.joinpath(*resolved, part)
            if candidate.is_symlink():
                links += 1
                if links > 40:
                    raise ValueError(f"Too many unit symlinks: {relative}")
                pending = list(Path(os.readlink(candidate)).parts) + pending
            else:
                resolved.append(part)
    return root.joinpath(*resolved)


def active_lines(path):
    return [line.strip() for line in path.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith(("#", ";"))]


def validate_usb_runtime(root):
    # Removing the SysV wrapper's socket enumeration is equivalent only for the
    # audited native NVIDIA unit, with no associated socket or custom drop-in.
    installed = resolve_offline(root, f"{SYSTEM}/{USB_RUNTIME}")
    if not installed.is_file() or active_lines(installed) != USB_RUNTIME_DIRECTIVES:
        raise ValueError("Direct USB runtime requires the known NVIDIA runtime service")
    for relative in UNIT_DIRS:
        directory = resolve_offline(root, relative)
        dropins = directory / f"{USB_RUNTIME}.d"
        if dropins.exists() or dropins.is_symlink():
            raise ValueError("Direct USB runtime refuses custom runtime service drop-ins")
        if not directory.is_dir():
            continue
        for socket in [*directory.glob("*.socket"), *directory.glob("*.socket.d/*.conf")]:
            # Include implicitly named sockets and explicit Service= references.
            if socket.name == USB_RUNTIME.removesuffix(".service") + ".socket" or \
                    socket.name.startswith(USB_RUNTIME.removesuffix(".service") + "@"):
                raise ValueError("Direct USB runtime refuses an associated socket")
            source = resolve_offline(root, socket.relative_to(root))
            if source.is_file():
                for line in active_lines(source):
                    if re.match(r"Service\s*=", line) and USB_RUNTIME.removesuffix(".service") in line:
                        raise ValueError("Direct USB runtime refuses an associated socket")


def direct_usb_changes(root):
    validate_usb_runtime(root)
    path = path_in(root, USB_HANDLER)
    original = snapshot(path)
    if original["type"] != "file":
        raise ValueError("Direct USB runtime requires the regular NVIDIA state-change script")
    lines = path.read_text().splitlines(keepends=True)
    for action in ("start", "stop"):
        expected = f"    service nv-l4t-usb-device-mode-runtime {action}\n"
        if lines.count(expected) != 1:
            raise ValueError("Expected exactly the known NVIDIA USB runtime start/stop calls")
        lines[lines.index(expected)] = f"    /bin/systemctl {action} {USB_RUNTIME}\n"
    return {USB_HANDLER: file_state("".join(lines).encode(), original["mode"],
                                    original["uid"], original["gid"])}


def lvm_monitor_changes(root):
    changes = {}
    # The flag is an explicit assertion that the deployment has no LVM. Only
    # remove enabled Wants links: do not mask the unit or change LVM packages.
    for directory in path_in(root, SYSTEM).iterdir():
        if not directory.name.endswith((".wants", ".requires")):
            continue
        relative = f"{SYSTEM}/{directory.name}/{LVM_MONITOR}"
        path = path_in(root, relative)
        if not path.exists() and not path.is_symlink():
            continue
        if not path.is_symlink():
            raise ValueError(f"Expected an LVM enablement symlink: {path}")
        if os.readlink(path) == "/dev/null":
            continue
        if directory.name.endswith(".requires"):
            raise ValueError("LVM monitoring has a required dependency; review it before disabling")
        changes[relative] = {"type": "absent"}
    return changes


def make_changes(root, defer, skip_utmp, direct_usb=False, skip_lvm=False):
    changes = {f"{SYSTEM}/default.target":
               symlink_state("/lib/systemd/system/multi-user.target")}
    deferred = []
    if direct_usb:
        changes.update(direct_usb_changes(root))
    if skip_lvm:
        changes.update(lvm_monitor_changes(root))
    if skip_utmp:
        relative = f"{SYSTEM}/systemd-update-utmp.service.d/override.conf"
        path = path_in(root, relative)
        if path.exists() or path.is_symlink():
            original = snapshot(path)
            if original["type"] != "file":
                raise ValueError(f"Expected a regular utmp override: {path}")
            content = path.read_text()
            lines = content.splitlines(keepends=True)
            section = None
            matching = []
            for index, line in enumerate(lines):
                if line.strip().startswith("["):
                    section = line.strip()
                if section == "[Service]" and line.rstrip("\n") == "ExecStartPre=/bin/sleep 2":
                    matching.append(index)
            if len(matching) != 1:
                raise ValueError("Expected exactly the known NVIDIA two-second utmp sleep")
            del lines[matching[0]]
            changes[relative] = file_state("".join(lines).encode(), original["mode"],
                                           original["uid"], original["gid"])
    if not defer:
        return changes, deferred
    for unit in DEFER_CANDIDATES:
        link = f"{SYSTEM}/multi-user.target.wants/{unit}"
        path = path_in(root, link)
        # Do not create an enablement for a previously disabled service, or
        # rewrite unusual non-symlink custom unit installations.
        if not path.is_symlink():
            if path.exists():
                raise ValueError(f"Expected an enablement symlink: {path}")
            continue
        destination = os.readlink(path)
        if destination == "/dev/null":
            continue
        deferred.append(unit)
        changes[link] = {"type": "absent"}
        changes[f"{SYSTEM}/{BACKGROUND}.wants/{unit}"] = symlink_state(destination)
        # Keep ARK's group-target memberships intact. Package re-enables should
        # use this target rather than put the unit back before multi-user.
        targets = BACKGROUND
        if unit.endswith(".service") and unit not in (
                "ark-os-firstboot.service", "nginx.service", "jtop.service"):
            targets += " ark-os.target"
        content = ("# Managed by configure_fast_boot.py; restore with that tool.\n"
                   "[Install]\nWantedBy=\nWantedBy=" + targets + "\n")
        changes[f"{SYSTEM}/{unit}.d/90-ark-fastboot.conf"] = file_state(content.encode())
    for template in sorted(TEMPLATES.rglob("*")):
        if template.is_file():
            relative = str(template.relative_to(TEMPLATES))
            changes[relative] = file_state(template.read_bytes())
    changes[f"{SYSTEM}/multi-user.target.wants/{LAUNCHER}"] = symlink_state(
        "../" + LAUNCHER)
    return changes, deferred


def apply(root, defer, skip_utmp, direct_usb=False, skip_lvm=False):
    previous = read_manifest(root)
    if previous:
        options = {"defer_ark_services": defer, "skip_utmp_delay": skip_utmp,
                   "direct_usb_runtime": direct_usb, "skip_lvm_monitor": skip_lvm}
        if any(previous.get(key, False) != value for key, value in options.items()):
            raise ValueError("Profile options differ; restore before applying new options")
        check_changes(root, previous)
        if direct_usb:
            validate_usb_runtime(root)
        print("JAJ headless profile already applied; no changes")
        return
    desired, deferred = make_changes(root, defer, skip_utmp, direct_usb, skip_lvm)
    changes = {}
    created_dirs = set()
    for relative, state in desired.items():
        path = path_in(root, relative)
        changes[relative] = {"original": snapshot(path), "applied": state}
        for parent in path.parents:
            if parent == root:
                break
            if not parent.exists():
                created_dirs.add(str(parent.relative_to(root)))
    manifest = {"version": 1, "profile": "jaj-headless",
                "defer_ark_services": defer, "skip_utmp_delay": skip_utmp,
                "direct_usb_runtime": direct_usb, "skip_lvm_monitor": skip_lvm,
                "deferred_units": deferred,
                "changes": changes, "created_dirs": sorted(created_dirs)}
    manifest_path = path_in(root, MANIFEST)
    write_state(manifest_path, file_state((json.dumps(manifest, indent=2) + "\n").encode()))
    # The manifest is written first so restore can recover a partial application.
    for relative, change in changes.items():
        write_state(path_in(root, relative), change["applied"])
    print("Applied JAJ headless profile (multi-user target)")
    if defer:
        print("Deferred enabled units: " + (", ".join(deferred) or "none"))
        print("ARK/application readiness occurs later; this is not a readiness benchmark")


def restore(root):
    manifest = read_manifest(root)
    if not manifest:
        print("No JAJ headless profile is applied; no changes")
        return
    check_changes(root, manifest, allow_original=True)
    for relative, change in reversed(list(manifest["changes"].items())):
        write_state(path_in(root, relative), change["original"])
    for relative in sorted(manifest["created_dirs"], key=lambda p: p.count("/"), reverse=True):
        directory = path_in(root, relative)
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    manifest_path = path_in(root, MANIFEST)
    manifest_path.unlink()
    if not any(manifest_path.parent.iterdir()):
        manifest_path.parent.rmdir()
    print("Restored the rootfs settings saved before the JAJ headless profile")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("apply", "restore", "status"))
    parser.add_argument("rootfs", type=Path, help="staged, offline root filesystem (not /)")
    parser.add_argument("--defer-ark-services", action="store_true",
                        help="explicitly start enabled ARK/UI services after multi-user.target")
    parser.add_argument("--skip-utmp-delay", action="store_true",
                        help="remove the known NVIDIA two-second utmp wall-clock wait")
    parser.add_argument("--direct-usb-runtime", action="store_true",
                        help="replace known USB runtime SysV-wrapper calls with native systemctl")
    parser.add_argument("--skip-lvm-monitor", action="store_true",
                        help="disable enabled LVM monitoring; requires confirmed absence of LVM")
    args = parser.parse_args()
    root = args.rootfs.resolve()
    if root == Path("/") or not (root / "etc").is_dir():
        parser.error("rootfs must be a staged root filesystem with an etc directory, not /")
    if (root / "run/systemd/system").exists():
        parser.error("rootfs appears to contain a running systemd instance; use an offline image")
    if (args.defer_ark_services or args.skip_utmp_delay or args.direct_usb_runtime or args.skip_lvm_monitor) and args.action != "apply":
        parser.error("Profile options are valid only with apply")
    try:
        if args.action == "apply":
            apply(root, args.defer_ark_services, args.skip_utmp_delay,
                  args.direct_usb_runtime, args.skip_lvm_monitor)
        elif args.action == "restore":
            restore(root)
        else:
            manifest = read_manifest(root)
            if manifest:
                check_changes(root, manifest)
                if manifest.get("direct_usb_runtime", False):
                    validate_usb_runtime(root)
                print(json.dumps({key: manifest.get(key, False) for key in (
                    "profile", "defer_ark_services", "skip_utmp_delay", "direct_usb_runtime",
                    "skip_lvm_monitor", "deferred_units")}, indent=2))
            else:
                print("JAJ headless profile is not applied")
    except (OSError, ValueError, KeyError) as error:
        print(f"configure_fast_boot: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
