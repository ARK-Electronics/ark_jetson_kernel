#!/usr/bin/env python3
"""Apply or restore an opt-in ARK headless profile in an offline rootfs."""

import argparse
import base64
import hashlib
import json
import os
import posixpath
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
USB_MAIN = "nv-l4t-usb-device-mode.service"
USB_START = f"{USB_DIR}/nv-l4t-usb-device-mode-start.sh"
USB_UDEV_AUDIT_PATH = Path(__file__).resolve().parents[1] / "products/JAJ/fastboot/usb-udev-audit.json"
USB_UDEV_R39_AUDIT_PATH = USB_UDEV_AUDIT_PATH.with_name("usb-udev-r39-audit.json")
USB_START_SCOPED_HASH = "d5e6c1fd41c21e034c9495c5e48f93679a62247dca24d8dcddf250fe72898cf2"
USB_HANDLER_ORDERED_HASH = "3406913cc970c3006e1aeb6426109c1b6fac6554807928618fcd903832d35bd1"
USB_RUNTIME_ORDER = f"{SYSTEM}/{USB_RUNTIME}.d/90-ark-gadget-order.conf"
USB_RUNTIME_ORDER_CONTENT = (
    "# Managed by configure_fast_boot.py; restore with that tool.\n"
    "# Cable events may arrive before the gadget bridge exists.\n"
    "# The udev handler queues jobs without blocking coldplug or gadget setup.\n"
    "[Unit]\n"
    f"Requires={USB_MAIN}\n"
    f"After={USB_MAIN}\n"
).encode()
LVM_MONITOR = "lvm2-monitor.service"
UNIT_DIRS = ("etc/systemd/system", "run/systemd/system", "usr/lib/systemd/system", "lib/systemd/system")
USB_RUNTIME_DIRECTIVES = [
    "[Unit]", "Description=Runtime configuration for USB device mode",
    "[Service]", "Type=oneshot", "RemainAfterExit=yes",
    f"ExecStart=/{USB_DIR}/nv-l4t-usb-device-mode-runtime-start.sh",
    f"ExecStopPost=/{USB_DIR}/nv-l4t-usb-device-mode-runtime-stop.sh",
]
# Audited ARK-OS Jetson package units. Both bind to loopback and connect to
# upstreams only on requests. Exact content prevents a customized network
# requirement or service property from being silently changed by this option.
EARLY_API_UNITS = {
    "system-manager.service": """[Unit]
Description=Microservice backend for monitoring and managing the linux system
Wants=network.target network-online.target
After=network-online.target

[Service]
Type=simple
User=jetson
Group=jetson
ExecStart=/usr/lib/ark-os/venv/bin/python3 /usr/lib/ark-os/python/system_manager.py
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1
Environment=PORT=3004

[Install]
WantedBy=multi-user.target ark-os.target
""",
    "ark-ui-backend.service": """[Unit]
Description=ARK UI Backend Service
Wants=network-online.target
After=network-online.target nginx.service

[Service]
Type=simple
User=jetson
Group=jetson
WorkingDirectory=/usr/lib/ark-os/ark-ui-backend
ExecStart=/usr/lib/ark-os/bin/node /usr/lib/ark-os/ark-ui-backend/index.js
Restart=on-failure
Environment=NODE_ENV=production
Environment=PATH=/usr/lib/ark-os/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

[Install]
WantedBy=multi-user.target ark-os.target
""",
}
EARLY_API_UNIT_DIRS = (*UNIT_DIRS, "etc/systemd/system.control",
                       "run/systemd/system.control", "run/systemd/transient",
                       "run/systemd/generator", "run/systemd/generator.early",
                       "run/systemd/generator.late")
UDEV_TRIGGER = "systemd-udev-trigger.service"
JAJ_PCIE_SYSNAME = "141e0000.pcie"
PCIE_REPLAY = "ark-jaj-pcie-coldplug.service"
PCIE_DROPIN = f"{SYSTEM}/{UDEV_TRIGGER}.d/90-ark-jaj-pcie-coldplug.conf"
PCIE_REPLAY_TEMPLATE = (Path(__file__).resolve().parents[1] /
                        "products/JAJ/fastboot/pcie-coldplug" / PCIE_REPLAY)
UDEV_TRIGGER_DIRECTIVES = [
    "[Unit]", "Description=Coldplug All udev Devices",
    "Documentation=man:udev(7) man:systemd-udevd.service(8)",
    "DefaultDependencies=no", "Wants=systemd-udevd.service",
    "After=systemd-udevd-kernel.socket systemd-udevd-control.socket",
    "Before=sysinit.target", "ConditionPathIsReadWrite=/sys",
    "[Service]", "Type=oneshot", "RemainAfterExit=yes",
    "ExecStart=-udevadm trigger --type=subsystems --action=add",
    "ExecStart=-udevadm trigger --type=devices --action=add",
]
# Ubuntu 24.04/systemd 255 combines subsystem and device coldplug while
# prioritizing boot-critical subsystems. Keep its command and priority order.
UDEV_TRIGGER_DIRECTIVES_NOBLE = UDEV_TRIGGER_DIRECTIVES[:-2] + [
    "ExecStart=-udevadm trigger --type=all --action=add "
    "--prioritized-subsystem=module,block,tpmrm,net,tty,input",
]


def require_r39_noble(root):
    stamp = resolve_offline(root, "etc/nv_tegra_release")
    distro = resolve_offline(root, "etc/os-release")
    if not stamp.is_file() or not re.search(r"^# R39 \(release\), REVISION: 2\.1,", stamp.read_text(), re.M):
        raise ValueError("Noble fast-boot options require audited L4T R39.2.1")
    values = dict(line.split("=", 1) for line in distro.read_text().splitlines()
                  if "=" in line and not line.startswith("#")) if distro.is_file() else {}
    if values.get("ID", "").strip('"') != "ubuntu" or values.get("VERSION_ID", "").strip('"') != "24.04":
        raise ValueError("L4T R39.2.1 fast-boot options require Ubuntu 24.04")


# Ubuntu 22.04 sample-rootfs logging topology, audited for the R36.5 image.
# imklog is loaded exactly once and routes kern.* to persistent kern.log. Pin
# all included configuration and native units rather than infer arbitrary
# rsyslog rules, which can discard, duplicate, or redirect kernel records.
NOBLE_LOG_AUDIT_PATH = USB_UDEV_AUDIT_PATH.with_name("rsyslog-noble-audit.json")
KERNEL_LOG_DROPIN = "etc/systemd/journald.conf.d/90-ark-rsyslog-kernel.conf"
KERNEL_LOG_CONFIG_HASHES = {
    "etc/rsyslog.conf": "8533c0f97f104dc24bff092e1d1c157b50865cdf6446474d2dd68c1b392ab5a0",
    "etc/rsyslog.d/50-default.conf": "41c17b77c5b3bc4b2c18dd495d0078268091d67d11ce2cc694b1bc83ca3bae80",
    "etc/logrotate.d/rsyslog": "f495045e2d4ac8078cf22c77ebf2f397a4cae0b2b1339ffda06392e745de6c65",
    "etc/systemd/journald.conf": "991bc49e7132af51249f5260ab030ca6503bc27556baca5fce353b5d311bf9d0",
}
KERNEL_LOG_UNIT_HASHES = {
    "rsyslog.service": "726b9641b81ede267adc4180b3e25b85122a4e798e0f748cac0e9e3a414e1622",
    "syslog.socket": "78fc9f6b50902378345ffc4d5e2ffd50a5119932625d0c6f5c7324b0c4005bdf",
    "systemd-journald.service": "42df40d2c4c44c312642542d1cdca697ada2b402baeea8611bde9ec045a2a020",
    "systemd-journald.socket": "c17504f4c86653882070215fc089a610f69d8149e1860b92e439b008cce3f1d1",
    "systemd-journald-dev-log.socket": "fd449ae03beeebab53ad75f70cba4c4d0080fe52ad5003cae9fb55c7381290e2",
    "systemd-journald-audit.socket": "2c6e0f03250c09114aa7a137b513c0c17d4361badaa1f822d87e6c57779e7f44",
}
KERNEL_LOG_CONFIG_DIRS = ("etc/systemd", "run/systemd", "usr/local/lib/systemd",
                          "usr/lib/systemd", "lib/systemd")
KERNEL_LOG_CONTENT = (
    "# Managed by configure_fast_boot.py; restore with that tool.\n"
    "# Audited rsyslog imklog independently persists kernel messages in /var/log/kern.log.\n"
    "# journalctl -k will not receive new kernel records; userspace journals are unchanged.\n"
    "[Journal]\nReadKMsg=no\n"
).encode()
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


def validate_usb_runtime(root, allow_ordered=False):
    # Removing the SysV wrapper's socket enumeration is equivalent only for the
    # audited native NVIDIA unit, with no associated socket or custom drop-in.
    installed = resolve_offline(root, f"{SYSTEM}/{USB_RUNTIME}")
    if not installed.is_file() or active_lines(installed) != USB_RUNTIME_DIRECTIVES:
        raise ValueError("Direct USB runtime requires the known NVIDIA runtime service")
    for relative in UNIT_DIRS:
        directory = resolve_offline(root, relative)
        dropins = directory / f"{USB_RUNTIME}.d"
        if dropins.exists() or dropins.is_symlink():
            expected = path_in(root, USB_RUNTIME_ORDER)
            if not (allow_ordered and dropins == expected.parent and
                    not dropins.is_symlink() and dropins.is_dir() and
                    list(dropins.iterdir()) == [expected] and
                    not expected.is_symlink() and expected.is_file() and
                    expected.read_bytes() == USB_RUNTIME_ORDER_CONTENT):
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


def scoped_usb_udev_changes(root, applied=False):
    """Scope startup udev work while retaining every gadget function and rule.

    Pin the complete installed rule inventory: arbitrary RUN programs can
    introduce dependencies that made the original global settle significant.
    Only explicitly reviewed rule variants/optional rules are allowed; new
    packages and changed rule contents require review.
    """
    release_path = resolve_offline(root, "etc/nv_tegra_release")
    release_text = release_path.read_text() if release_path.is_file() else ""
    noble = re.search(r"^# R39 \(release\), REVISION: 2\.1,", release_text, re.M) is not None
    if noble:
        require_r39_noble(root)
    expected_release, udev_major = ("39.2.1", 255) if noble else ("36.5.0", 249)
    audit_path = USB_UDEV_R39_AUDIT_PATH if noble else USB_UDEV_AUDIT_PATH
    audit = json.loads(audit_path.read_text())
    if audit.get("version") != 1 or audit.get("l4t_release") != expected_release:
        raise ValueError("Unsupported scoped USB udev audit manifest")
    output_hash = audit["scoped_start_sha256"] if noble else USB_START_SCOPED_HASH

    def reject(reason):
        raise ValueError(f"Scoped USB udev requires the audited R{expected_release} topology: " + reason)

    def regular(relative):
        relative = Path(relative)
        path = resolve_offline(root, relative.parent) / relative.name
        if path.is_symlink() or not path.is_file():
            reject(f"expected regular file {relative}")
        return path

    release = regular("etc/nv_tegra_release").read_text().splitlines()[0]
    expected_stamp = r"# R39 \(release\), REVISION: 2\.1," if noble else r"# R36 \(release\), REVISION: 5\.0,"
    if not re.match(expected_stamp, release):
        reject(f"expected L4T R{expected_release}")
    installed_udev = False
    for paragraph in regular("var/lib/dpkg/status").read_text().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in paragraph.splitlines()
                      if ": " in line and not line.startswith(" "))
        if fields.get("Package") == "udev":
            installed_udev = (fields.get("Status") == "install ok installed" and
                              fields.get("Version", "").startswith(f"{udev_major}."))
    if not installed_udev:
        reject(f"installed udev {udev_major} with scoped trigger --settle support")
    for relative in ("usr/bin/timeout", "bin/udevadm"):
        executable = resolve_offline(root, relative)
        if not executable.is_file() or not executable.stat().st_mode & 0o111:
            reject(f"installed executable {relative}")
        with executable.open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                reject(f"expected packaged ELF {relative}")
    for relative, hashes in audit["files"].items():
        expected = [output_hash] if applied and relative == USB_START else hashes
        if applied and noble and relative == USB_HANDLER:
            expected = [USB_HANDLER_ORDERED_HASH]
        if hashlib.sha256(regular(relative).read_bytes()).hexdigest() not in expected:
            reject(f"vendor file hash differs: {relative}")
    validate_usb_runtime(root, allow_ordered=applied and noble)
    for unit in (USB_MAIN, USB_RUNTIME):
        parts = unit.removesuffix(".service").split("-")
        unsupported = [f"{unit}.d", f"{unit}.wants", f"{unit}.requires", "service.d"]
        unsupported += ["-".join(parts[:i]) + "-.service.d" for i in range(1, len(parts))]
        for relative in (*EARLY_API_UNIT_DIRS, "usr/local/lib/systemd/system"):
            directory = resolve_offline(root, relative)
            for name in unsupported:
                candidate = directory / name
                if (applied and noble and unit == USB_RUNTIME and relative == SYSTEM
                        and name == f"{USB_RUNTIME}.d"):
                    # validate_usb_runtime accepts only our exact single drop-in.
                    continue
                if candidate.exists() or candidate.is_symlink():
                    reject(f"custom USB unit drop-ins/dependencies: {candidate}")
            candidate = directory / unit
            if not candidate.exists() and not candidate.is_symlink():
                continue
            if relative != SYSTEM or not candidate.is_symlink() or \
                    resolve_offline(root, candidate.relative_to(root)) != root / USB_DIR / unit:
                reject(f"custom USB unit override/mask: {candidate}")
        link = path_in(root, f"{SYSTEM}/{unit}")
        if not link.is_symlink() or resolve_offline(root, link.relative_to(root)) != root / USB_DIR / unit:
            reject(f"known native USB unit symlink: {unit}")

    actual = {}
    seen = set()
    for relative in ("etc/udev/rules.d", "run/udev/rules.d", "usr/local/lib/udev/rules.d",
                     "usr/lib/udev/rules.d", "lib/udev/rules.d"):
        directory = resolve_offline(root, relative)
        if directory in seen:
            continue
        seen.add(directory)
        if not directory.exists():
            continue
        if not directory.is_dir():
            reject(f"udev rules directory: {relative}")
        for path in directory.glob("*.rules"):
            key = str(path.relative_to(root))
            if path.is_symlink() or not path.is_file():
                reject(f"custom rule alias/mask: {key}")
            actual[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    for relative, digest in audit["optional_rules"].items():
        if relative in actual:
            if actual.pop(relative) != digest:
                reject(f"unknown optional udev rule: {relative}")
    # Package revisions may change a reviewed rule without adding dependencies.
    # Normalize only its explicitly pinned alternatives; missing or unknown
    # contents still fail the complete inventory comparison below.
    for relative, alternatives in audit.get("rule_variants", {}).items():
        if relative not in audit["rules"] or not isinstance(alternatives, list):
            reject("invalid rule variant audit entry")
        if actual.get(relative) in alternatives:
            actual[relative] = audit["rules"][relative]
    if actual != audit["rules"]:
        different = sorted(name for name in set(actual) | set(audit["rules"])
                           if actual.get(name) != audit["rules"].get(name))
        reject("udev rule inventory differs: " + ", ".join(different))

    path = path_in(root, USB_START)
    original = snapshot(path)
    data = path.read_bytes()
    if not applied:
        before = (b'udevadm trigger "${loop_dev}" # Ensure that the symlink '
                  b'/dev/disk/by-label/L4T-README is created via udev rules.\nudevadm settle\n')
        after = (b'# Wait only for this loop device; retain its persistent-label processing.\n'
                 b'/usr/bin/timeout --kill-after=5s 120s udevadm trigger --settle '
                 b'--action=change "${loop_dev}"\n')
        if data.count(before) != 1:
            reject("expected unique loop-device trigger/global-settle sequence")
        data = data.replace(before, after, 1)
        for subsystem in (b"android_usb", b"usb_role"):
            before = b"--property-match=SUBSYSTEM=" + subsystem
            if data.count(before) != 1:
                reject("expected unique cable-role property filter")
            data = data.replace(before, b"--subsystem-match=" + subsystem, 1)
    if hashlib.sha256(data).hexdigest() != output_hash:
        reject("reviewed scoped-start output hash differs")
    changes = {USB_START: file_state(data, original["mode"], original["uid"], original["gid"])}
    if noble:
        # R39 can deliver a cable-role event after configfs creation but before
        # l4tbr0 exists. Order runtime configuration after successful gadget
        # setup. Never wait synchronously in the udev RUN handler: main startup
        # itself waits on udev, and early coldplug precedes default service deps.
        handler = path_in(root, USB_HANDLER)
        state = snapshot(handler)
        content = handler.read_bytes()
        if not applied:
            for action in ("start", "stop"):
                calls = (
                    f"    service nv-l4t-usb-device-mode-runtime {action}\n".encode(),
                    f"    /bin/systemctl {action} {USB_RUNTIME}\n".encode(),
                )
                if sum(content.count(call) for call in calls) != 1:
                    reject("expected unique known USB runtime handler call")
                for call in calls:
                    content = content.replace(call,
                        f"    /bin/systemctl --no-block {action} {USB_RUNTIME}\n".encode())
        if hashlib.sha256(content).hexdigest() != USB_HANDLER_ORDERED_HASH:
            reject("reviewed nonblocking USB handler output hash differs")
        order = path_in(root, USB_RUNTIME_ORDER)
        if applied and (not order.is_file() or order.is_symlink() or
                        order.read_bytes() != USB_RUNTIME_ORDER_CONTENT):
            reject("missing audited runtime ordering; restore and reapply this profile")
        changes[USB_HANDLER] = file_state(content, state["mode"], state["uid"], state["gid"])
        changes[USB_RUNTIME_ORDER] = file_state(USB_RUNTIME_ORDER_CONTENT)
    return changes


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


def early_api_changes(root, applied=False):
    """Copy audited units; dependency removal cannot be expressed by drop-ins."""
    changes = {}
    for unit, expected in EARLY_API_UNITS.items():
        # These exact units have only the reviewed network/nginx dependencies.
        # Keep default dependencies, service properties and enablement intact.
        content = "".join(line for line in expected.splitlines(keepends=True)
                          if not line.startswith(("Wants=", "After=")))
        stem = unit.removesuffix(".service").split("-")
        unsupported = [f"{unit}.d", f"{unit}.wants", f"{unit}.requires", "service.d"]
        unsupported += ["-".join(stem[:i]) + "-.service.d" for i in range(1, len(stem))]
        vendors = {}
        for relative in EARLY_API_UNIT_DIRS:
            directory = resolve_offline(root, relative)
            for name in unsupported:
                candidate = directory / name
                if candidate.exists() or candidate.is_symlink():
                    raise ValueError(f"Early ARK API refuses custom drop-ins or dependencies: {candidate}")
            candidate = directory / unit
            if not candidate.exists() and not candidate.is_symlink():
                continue
            if relative in ("usr/lib/systemd/system", "lib/systemd/system"):
                if candidate.is_symlink() or not candidate.is_file() or candidate.read_bytes() != expected.encode():
                    raise ValueError(f"Early ARK API requires the exact known regular vendor unit: {candidate}")
                vendors[candidate] = snapshot(candidate)
            elif relative != SYSTEM or not applied:
                raise ValueError(f"Early ARK API refuses an existing unit override or mask: {candidate}")
        if len(vendors) != 1:
            raise ValueError(f"Early ARK API requires one known vendor unit for {unit}")
        original = next(iter(vendors.values()))
        state = file_state(content.encode(), original["mode"], original["uid"], original["gid"])
        relative = f"{SYSTEM}/{unit}"
        if applied and snapshot(path_in(root, relative)) != state:
            raise ValueError(f"Early ARK API override no longer matches the audited vendor unit: {unit}")
        changes[relative] = state
    return changes


def sysname_complement(name):
    """Return fnmatch globs matching every nonempty sysname except this literal.

    systemd 249/255 udevadm has no sysname-nomatch option. Its OR-ed sysname matches run before
    reading uevent; attribute/property filters run after that blocking read.
    Partition by first differing character, shorter prefix, or longer suffix.
    """
    if not re.fullmatch(r"[A-Za-z0-9_.]+", name):
        raise ValueError("Expected a literal sysname without glob or unit syntax")
    return ([name[:i] + "[!" + char + "]*" for i, char in enumerate(name)] +
            [name[:i] for i in range(1, len(name))] + [name + "?*"])


def parallel_jaj_pcie_changes(root, applied=False):
    """Replay one JAJ controller independently; retain kernel PCIe probing."""
    stamp_path = resolve_offline(root, "etc/ark_jetson_kernel")
    if not stamp_path.is_file():
        raise ValueError("Parallel JAJ PCIe coldplug requires a completed target=JAJ rootfs")
    stamp = dict(line.split("=", 1) for line in stamp_path.read_text().splitlines() if "=" in line)
    if stamp.get("target") != "JAJ":
        raise ValueError("Parallel JAJ PCIe coldplug requires a completed target=JAJ rootfs")
    vendor = resolve_offline(root, "lib/systemd/system/" + UDEV_TRIGGER)
    directives = active_lines(vendor) if vendor.is_file() else []
    if directives == UDEV_TRIGGER_DIRECTIVES_NOBLE:
        require_r39_noble(root)
        expected_directives = UDEV_TRIGGER_DIRECTIVES_NOBLE
        commands = [expected_directives[-1]]
    else:
        expected_directives = UDEV_TRIGGER_DIRECTIVES
        commands = expected_directives[-2:]
    patterns = sysname_complement(JAJ_PCIE_SYSNAME)
    # Quoted argv words are parsed by systemd, not a shell. Preserve udevadm's
    # normal executable lookup and the stock ignore-failure ExecStart prefix.
    args = " ".join('"--sysname-match=' + pattern + '"' for pattern in patterns)
    dropin = ("# Managed by configure_fast_boot.py; restore with that tool.\n"
              "# Exclude exactly 141e0000.pcie before libudev reads its locked uevent.\n"
              "# A separate service replays it without delaying the main enumeration.\n"
              "[Unit]\nWants=" + PCIE_REPLAY + "\n"
              "[Service]\nExecStart=\n"
              + "\n".join(commands[:-1]) + ("\n" if len(commands) > 1 else "")
              + commands[-1] + " " + args + "\n")
    payloads = {PCIE_DROPIN: dropin.encode(),
                f"{SYSTEM}/{PCIE_REPLAY}": PCIE_REPLAY_TEMPLATE.read_bytes()}
    vendor_paths = set()
    for unit in (UDEV_TRIGGER, PCIE_REPLAY):
        stem = unit.removesuffix(".service").split("-")
        unsupported = [f"{unit}.wants", f"{unit}.requires", "service.d"]
        unsupported += ["-".join(stem[:i]) + "-.service.d" for i in range(1, len(stem))]
        for relative in EARLY_API_UNIT_DIRS:
            directory = resolve_offline(root, relative)
            for name in unsupported:
                candidate = directory / name
                if candidate.exists() or candidate.is_symlink():
                    raise ValueError(f"Parallel JAJ PCIe refuses custom drop-ins or dependencies: {candidate}")
            dropins = directory / f"{unit}.d"
            if dropins.exists() or dropins.is_symlink():
                owned = (applied and relative == SYSTEM and unit == UDEV_TRIGGER and
                         not dropins.is_symlink() and dropins.is_dir() and
                         sorted(path.name for path in dropins.iterdir()) == [Path(PCIE_DROPIN).name])
                if not owned:
                    raise ValueError(f"Parallel JAJ PCIe refuses custom drop-ins or dependencies: {dropins}")
            candidate = directory / unit
            if not candidate.exists() and not candidate.is_symlink():
                continue
            if unit == UDEV_TRIGGER and relative in ("usr/lib/systemd/system", "lib/systemd/system"):
                if candidate.is_symlink() or not candidate.is_file() or active_lines(candidate) != expected_directives:
                    raise ValueError(f"Parallel JAJ PCIe requires the known regular vendor trigger unit: {candidate}")
                vendor_paths.add(candidate)
            elif not (applied and relative == SYSTEM and unit == PCIE_REPLAY):
                raise ValueError(f"Parallel JAJ PCIe refuses an existing unit override or mask: {candidate}")
    if len(vendor_paths) != 1:
        raise ValueError("Parallel JAJ PCIe requires one known vendor trigger unit")
    if applied:
        for relative, expected in payloads.items():
            path = path_in(root, relative)
            if path.is_symlink() or not path.is_file() or path.read_bytes() != expected:
                raise ValueError(f"Parallel JAJ PCIe override differs from the current audited profile: {path}")
    return {relative: file_state(data) for relative, data in payloads.items()}


def rsyslog_kernel_changes(root, applied=False):
    """Opt in only to the audited independent, persistent kernel-log route.

    This changes journal ingestion, not kernel printk or persistent rsyslog
    output. No extra imklog input or forwarding rule is added, avoiding duplicate
    ingestion. Post-boot syslog and genuine kernel-record checks verify runtime
    permissions/storage: an offline image audit cannot prove a running logger.
    """
    def reject(reason):
        raise ValueError("Rsyslog kernel logging requires an audited Ubuntu logging topology: " + reason)

    def regular(relative):
        relative = Path(relative)
        path = resolve_offline(root, relative.parent) / relative.name
        if path.is_symlink() or not path.is_file():
            reject(f"regular file: {relative}")
        return path

    release_path = resolve_offline(root, "etc/os-release")
    if not release_path.is_file():
        reject("distribution metadata")
    release = dict(line.split("=", 1) for line in release_path.read_text().splitlines()
                   if "=" in line and not line.startswith("#"))
    version = release.get("VERSION_ID", "").strip('"')
    if release.get("ID", "").strip('"') != "ubuntu" or version not in ("22.04", "24.04"):
        reject("distribution")
    noble = version == "24.04"
    config_hashes, unit_hashes = KERNEL_LOG_CONFIG_HASHES, KERNEL_LOG_UNIT_HASHES
    optional_journal_names = []
    if noble:
        require_r39_noble(root)
        audit = json.loads(NOBLE_LOG_AUDIT_PATH.read_text())
        if (audit.get("version"), audit.get("ubuntu_version"), audit.get("l4t_release")) != (1, "24.04", "39.2.1"):
            reject("Noble logging audit manifest")
        config_hashes, unit_hashes = audit["configs"], audit["units"]
        # ARK-OS can request persistent, size-bounded userspace journals. This
        # exact optional package file changes neither kernel inputs nor routes;
        # preserve it and reject any edited or additional journal configuration.
        for relative, expected in audit.get("optional_journal_dropins", {}).items():
            if Path(relative).parent != Path("etc/systemd/journald.conf.d"):
                reject("Noble optional journal audit path")
            path = path_in(root, relative)
            if path.exists() or path.is_symlink():
                if hashlib.sha256(regular(relative).read_bytes()).hexdigest() != expected:
                    reject(f"optional journal configuration hash: {relative}")
                optional_journal_names.append(Path(relative).name)
        # Noble reloads an AppArmor profile before rsyslog starts. Pin the
        # profile's complete include closure and optional-directory inventory,
        # including path aliases and local overrides; do not disable confinement.
        apparmor = audit["apparmor"]
        for relative, expected in apparmor["files"].items():
            if hashlib.sha256(regular(relative).read_bytes()).hexdigest() != expected:
                reject(f"AppArmor include hash: {relative}")
        for relative, expected in apparmor["directories"].items():
            directory = path_in(root, relative)
            if directory.is_symlink() or not directory.is_dir() or \
                    sorted(p.name for p in directory.iterdir()) != expected:
                reject(f"AppArmor include directory: {relative}")
        for relative in apparmor["absent"]:
            path = path_in(root, relative)
            if path.exists() or path.is_symlink():
                reject(f"unexpected AppArmor optional include: {relative}")
        if not regular("usr/lib/rsyslog/reload-apparmor-profile").stat().st_mode & 0o111:
            reject("executable AppArmor profile reload helper")
    for relative, expected in config_hashes.items():
        if hashlib.sha256(regular(relative).read_bytes()).hexdigest() != expected:
            reject(f"configuration hash: {relative}")
    includes = path_in(root, "etc/rsyslog.d")
    expected_includes = sorted(Path(name).name for name in config_hashes
                               if Path(name).parent == Path("etc/rsyslog.d"))
    if includes.is_symlink() or sorted(p.name for p in includes.iterdir()) != expected_includes:
        reject("rsyslog include directory; custom inputs/rules are unsupported")
    for relative in KERNEL_LOG_CONFIG_DIRS:
        directory = resolve_offline(root, relative)
        alternate = directory / "journald.conf"
        if relative != "etc/systemd" and (alternate.exists() or alternate.is_symlink()):
            reject(f"journald configuration without custom overrides: {directory}")
        dropins = directory / "journald.conf.d"
        if dropins.exists() or dropins.is_symlink():
            if relative == "etc/systemd" and (noble or applied):
                expected_names = sorted(optional_journal_names +
                                        ([Path(KERNEL_LOG_DROPIN).name] if applied else []))
            else:
                expected_names = (["syslog.conf"] if noble and
                                  relative in ("usr/lib/systemd", "lib/systemd") else None)
            allowed = (expected_names is not None and not dropins.is_symlink() and
                       dropins.is_dir() and sorted(p.name for p in dropins.iterdir()) == expected_names)
            if not allowed:
                reject(f"journald configuration without custom drop-ins: {dropins}")

    vendors = {}
    unit_dirs = (*EARLY_API_UNIT_DIRS, "usr/local/lib/systemd/system")
    for unit, expected in {**unit_hashes, "syslog.service": None}.items():
        stem, suffix = unit.rsplit(".", 1)
        parts = stem.split("-")
        unsupported = [f"{unit}.d", f"{unit}.wants", f"{unit}.requires", f"{suffix}.d"]
        unsupported += ["-".join(parts[:i]) + f"-.{suffix}.d" for i in range(1, len(parts))]
        found = set()
        for relative in unit_dirs:
            directory = resolve_offline(root, relative)
            for name in unsupported:
                candidate = directory / name
                if candidate.exists() or candidate.is_symlink():
                    known = (audit["unit_dropin_directories"].get(str(candidate.relative_to(root)))
                             if noble else None)
                    if not (known is not None and not candidate.is_symlink() and candidate.is_dir()
                            and sorted(p.name for p in candidate.iterdir()) == known):
                        reject(f"units without custom drop-ins/dependencies: {candidate}")
            candidate = directory / unit
            if not candidate.exists() and not candidate.is_symlink():
                continue
            if unit == "syslog.service" and relative == SYSTEM and candidate.is_symlink():
                # The known alias is checked against the audited rsyslog unit below.
                continue
            if relative not in ("usr/lib/systemd/system", "lib/systemd/system") or \
                    candidate.is_symlink() or not candidate.is_file() or \
                    hashlib.sha256(candidate.read_bytes()).hexdigest() != expected:
                reject(f"native unit hash without overrides/masks: {candidate}")
            found.add(candidate)
        if unit != "syslog.service":
            if len(found) != 1:
                reject(f"single native unit: {unit}")
            vendors[unit] = next(iter(found))
    for relative in (f"{SYSTEM}/syslog.service",
                     f"{SYSTEM}/multi-user.target.wants/rsyslog.service"):
        link = path_in(root, relative)
        if not link.is_symlink() or resolve_offline(root, relative) != vendors["rsyslog.service"]:
            reject(f"enabled rsyslog service and syslog alias: {relative}")
    for relative in unit_dirs:
        directory = resolve_offline(root, relative)
        for name in ("syslog-ng.service", "syslogd.service", "klogd.service"):
            candidate = directory / name
            if candidate.exists() or candidate.is_symlink():
                reject(f"topology without a competing kernel/syslog daemon: {candidate}")
    for relative in ("usr/sbin/rsyslogd", "usr/lib/aarch64-linux-gnu/rsyslog/imklog.so",
                     "usr/lib/aarch64-linux-gnu/rsyslog/imuxsock.so"):
        binary = regular(relative)
        with binary.open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                reject(f"installed logger/input ELF: {relative}")
        if relative.endswith("rsyslogd") and not binary.stat().st_mode & 0o111:
            reject("executable rsyslogd")
    for relative in ("var", "var/log"):
        directory = path_in(root, relative)
        if directory.is_symlink() or not directory.is_dir():
            reject(f"persistent ordinary log directories: {relative}")
    output = path_in(root, "var/log/kern.log")
    if output.is_symlink() or (output.exists() and not output.is_file()):
        reject("ordinary persistent kern.log output")
    # Separate/custom mounts need their own persistence and ordering audit.
    # Do not claim persistence for tmpfs, overlay, or redirected /var/log.
    root_mounts = 0
    for line in regular("etc/fstab").read_text().splitlines():
        fields = line.split()
        if not fields or fields[0].startswith("#"):
            continue
        if len(fields) < 4:
            reject("valid fstab")
        mountpoint = fields[1]
        if fields[2] == "swap" and mountpoint in ("none", "swap"):
            continue
        # mount and systemd normalize aliases such as /var/log/ and /var//log.
        # Reject noncanonical spellings before comparing protected log paths;
        # interpreting arbitrary fstab escapes or aliases needs a separate audit.
        if (not mountpoint.startswith("/") or mountpoint.startswith("//") or
                "\\" in mountpoint or posixpath.normpath(mountpoint) != mountpoint):
            reject("canonical absolute fstab mountpoints; custom mounts require review")
        if mountpoint in ("/var", "/var/log", "/var/log/kern.log"):
            reject("log storage without separate/custom mounts")
        if mountpoint == "/":
            root_mounts += 1
            if fields[2] != "ext4" or "ro" in fields[3].split(","):
                reject("writable ext4 root for persistent logs")
    if root_mounts != 1:
        reject("one writable ext4 root for persistent logs")
    for relative in unit_dirs:
        directory = resolve_offline(root, relative)
        for mountpoint in ("var", "var-log", "var-log-kern.log"):
            for suffix in ("mount", "automount"):
                for name in (f"{mountpoint}.{suffix}", f"{mountpoint}.{suffix}.d"):
                    candidate = directory / name
                    if candidate.exists() or candidate.is_symlink():
                        reject(f"log storage without custom mount units: {candidate}")
    if applied:
        path = path_in(root, KERNEL_LOG_DROPIN)
        if path.is_symlink() or not path.is_file() or path.read_bytes() != KERNEL_LOG_CONTENT:
            reject("unchanged managed journald drop-in")
    return {KERNEL_LOG_DROPIN: file_state(KERNEL_LOG_CONTENT)}


def make_changes(root, defer, skip_utmp, direct_usb=False, skip_lvm=False, early_api=False,
                 parallel_pcie=False, rsyslog_kernel=False, scoped_usb=False):
    changes = {f"{SYSTEM}/default.target":
               symlink_state("/lib/systemd/system/multi-user.target")}
    deferred = []
    if scoped_usb:
        changes.update(scoped_usb_udev_changes(root))
    if rsyslog_kernel:
        changes.update(rsyslog_kernel_changes(root))
    if parallel_pcie:
        changes.update(parallel_jaj_pcie_changes(root))
    if direct_usb and USB_HANDLER not in changes:
        changes.update(direct_usb_changes(root))
    if skip_lvm:
        changes.update(lvm_monitor_changes(root))
    if early_api:
        if defer:
            raise ValueError("Early ARK API cannot be combined with deferring ARK services")
        changes.update(early_api_changes(root))
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


def apply(root, defer, skip_utmp, direct_usb=False, skip_lvm=False, early_api=False,
          parallel_pcie=False, rsyslog_kernel=False, scoped_usb=False):
    previous = read_manifest(root)
    if previous:
        options = {"defer_ark_services": defer, "skip_utmp_delay": skip_utmp,
                   "direct_usb_runtime": direct_usb, "skip_lvm_monitor": skip_lvm,
                   "early_ark_api": early_api, "parallel_jaj_pcie_coldplug": parallel_pcie,
                   "rsyslog_kernel_logging": rsyslog_kernel, "scoped_usb_udev": scoped_usb}
        if any(previous.get(key, False) != value for key, value in options.items()):
            raise ValueError("Profile options differ; restore before applying new options")
        check_changes(root, previous)
        if direct_usb:
            validate_usb_runtime(root, allow_ordered=scoped_usb)
        if early_api:
            early_api_changes(root, applied=True)
        if parallel_pcie:
            parallel_jaj_pcie_changes(root, applied=True)
        if rsyslog_kernel:
            rsyslog_kernel_changes(root, applied=True)
        if scoped_usb:
            scoped_usb_udev_changes(root, applied=True)
        print("ARK headless profile already applied; no changes")
        return
    desired, deferred = make_changes(root, defer, skip_utmp, direct_usb, skip_lvm,
                                     early_api, parallel_pcie, rsyslog_kernel, scoped_usb)
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
                "early_ark_api": early_api, "parallel_jaj_pcie_coldplug": parallel_pcie,
                "rsyslog_kernel_logging": rsyslog_kernel, "scoped_usb_udev": scoped_usb,
                "deferred_units": deferred,
                "changes": changes, "created_dirs": sorted(created_dirs)}
    manifest_path = path_in(root, MANIFEST)
    write_state(manifest_path, file_state((json.dumps(manifest, indent=2) + "\n").encode()))
    # The manifest is written first so restore can recover a partial application.
    for relative, change in changes.items():
        write_state(path_in(root, relative), change["applied"])
    print("Applied ARK headless profile (multi-user target)")
    if defer:
        print("Deferred enabled units: " + (", ".join(deferred) or "none"))
        print("ARK/application readiness occurs later; this is not a readiness benchmark")


def restore(root):
    manifest = read_manifest(root)
    if not manifest:
        print("No ARK headless profile is applied; no changes")
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
    print("Restored the rootfs settings saved before the ARK headless profile")


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
    parser.add_argument("--early-ark-api", action="store_true",
                        help="let audited loopback ARK API services start after basic startup, "
                             "without waiting for network-online/nginx; keeps nginx unchanged")
    parser.add_argument("--parallel-jaj-pcie-coldplug", action="store_true",
                        help="JAJ only: replay C7 PCIe coldplug independently while preserving kernel probing")
    parser.add_argument("--rsyslog-kernel-logging", action="store_true",
                        help="audited Ubuntu 22.04/24.04: persist kernel logs through rsyslog imklog "
                             "in kern.log instead of journalctl -k; verify runtime persistence after boot")
    parser.add_argument("--scoped-usb-udev", action="store_true",
                        help="audited R36.5/R39.2.1: wait for the USB loop device and scope cable-role "
                             "enumeration; preserve all gadget functions and hotplug")
    args = parser.parse_args()
    root = args.rootfs.resolve()
    if root == Path("/") or not (root / "etc").is_dir():
        parser.error("rootfs must be a staged root filesystem with an etc directory, not /")
    if (root / "run/systemd/system").exists():
        parser.error("rootfs appears to contain a running systemd instance; use an offline image")
    if (args.defer_ark_services or args.skip_utmp_delay or args.direct_usb_runtime or
            args.skip_lvm_monitor or args.early_ark_api or
            args.parallel_jaj_pcie_coldplug or args.rsyslog_kernel_logging or
            args.scoped_usb_udev) and args.action != "apply":
        parser.error("Profile options are valid only with apply")
    try:
        if args.action == "apply":
            apply(root, args.defer_ark_services, args.skip_utmp_delay,
                  args.direct_usb_runtime, args.skip_lvm_monitor, args.early_ark_api,
                  args.parallel_jaj_pcie_coldplug, args.rsyslog_kernel_logging, args.scoped_usb_udev)
        elif args.action == "restore":
            restore(root)
        else:
            manifest = read_manifest(root)
            if manifest:
                check_changes(root, manifest)
                if manifest.get("direct_usb_runtime", False):
                    validate_usb_runtime(root, allow_ordered=manifest.get("scoped_usb_udev", False))
                if manifest.get("early_ark_api", False):
                    early_api_changes(root, applied=True)
                if manifest.get("parallel_jaj_pcie_coldplug", False):
                    parallel_jaj_pcie_changes(root, applied=True)
                if manifest.get("rsyslog_kernel_logging", False):
                    rsyslog_kernel_changes(root, applied=True)
                if manifest.get("scoped_usb_udev", False):
                    scoped_usb_udev_changes(root, applied=True)
                print(json.dumps({key: manifest.get(key, False) for key in (
                    "profile", "defer_ark_services", "skip_utmp_delay", "direct_usb_runtime",
                    "skip_lvm_monitor", "early_ark_api", "parallel_jaj_pcie_coldplug",
                    "rsyslog_kernel_logging", "scoped_usb_udev", "deferred_units")}, indent=2))
            else:
                print("ARK headless profile is not applied")
    except (OSError, ValueError, KeyError) as error:
        print(f"configure_fast_boot: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
