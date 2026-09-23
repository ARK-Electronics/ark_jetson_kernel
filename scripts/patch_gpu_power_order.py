#!/usr/bin/env python3
"""Apply/check/restore the audited R39.2.1 Orin GPU power-before-display fix.

The native bind/change udev handler applies the configured nvpmodel policy before
powering the GPU. Synchronize that handler after nvgpu loads, then require a
successful native nvpmodel application and meaningful query before loading DRM.
This neither chooses a power mode nor overrides configured GPU mask values,
kernel drivers or rules; NVIDIA's native policy still applies its configured masks.
An owned producer-side Before=system-manager.service drop-in also prevents early
ARK metadata probes from racing this initialization. This is ordering only: a
failed GPU service stays visible while ARK may expose degraded metadata. It does
not add Requires= or declare an application ready. R36 remains unsupported.
"""
import argparse
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile

PRODUCTS = ("JAJ", "PAB", "PAB_V3")
SOURCE = "etc/systemd/nv-load-display-modules.sh"
ORIGINAL = "9f9f270d935da153dbb388ba6e2801a74600cbc01b81946a4c4fad0d10b4eefc"
PATCHED = "55cfaf02d0884ccec340fa29de2b4ee8f93269cefc5678e9f3e6140db27dab01"
DROPIN = "etc/systemd/system/nv-load-display-modules.service.d/20-ark-gpu-power-before-api.conf"
DROPIN_DATA = b"""# Owned by ARK's audited R39 GPU power-order helper.
# Producer ordering survives the early-API profile's consumer After reset.
# A GPU failure remains visible; ARK can still expose degraded metadata.
[Unit]
Before=system-manager.service
"""
COMPANIONS = {
    "etc/systemd/system/nv-load-display-modules.service": "ea30b2e46a3e57fc3cc13f4fc4113471f38634323f84841038e24e38c36a9981",
    "etc/systemd/system/nvpower.service": "415e1c6dba2291fea9e7d5668af175f7b2ff34af511217bdba26723044be6481",
    "etc/systemd/system/nvpmodel.service": "429eed1a87ba78c46ce4a4731c99135f69f00c44bda9a9e2b72868336acc9843",
    "etc/systemd/nvpmodel.sh": "70febbd64c54a76f714f594f507b4a1a0c9e103dd4dede7fb55a6a9e784de8d5",
    "etc/systemd/nvpower.sh": "81faaae01c1ed371827f4a5165d502153d4865cb5dae757d7a5670ce489cb42c",
    "etc/udev/rules.d/99-tegra-devices.rules": "10c81e94eb3c9fad3c3f78f419dc007d80803b0bd98ef8f8cc0b4b3dbfe05359",
    "etc/systemd/nv-load-display-modules-choose-variant.sh": "8457b0560eb2731f1985c16940a69034014102281af062ff66cf6bdefef18074",
}
BEFORE = '''if [[ "${variant}" == *nvgpu* ]]; then
\tmodprobe nvgpu
fi
'''
AFTER = '''if [[ "${variant}" == *nvgpu* ]]; then
\tmodprobe nvgpu
\tif [[ "${variant}" == "nvgpu-l4t" ]]; then
\t\t# ARK R39.2.1 Orin: DRM can power the GPU before its bind udev
\t\t# handler sets the static masks. Replay only that device's native
\t\t# power policy and wait for it before permitting display startup.
\t\tgpu_device=/sys/devices/platform/bus@0/17000000.gpu
\t\tif [[ ! -d "${gpu_device}" || ! -L "${gpu_device}/driver" ]]; then
\t\t\techo "ERROR: Orin GPU is not bound; refusing display before power setup" >&2
\t\t\texit 1
\t\tfi
\t\tif ! timeout --foreground 30s udevadm trigger --action=change --settle --subsystem-match=platform --sysname-match=17000000.gpu "${gpu_device}"; then
\t\t\techo "ERROR: Orin GPU power udev event did not finish" >&2
\t\t\texit 1
\t\tfi
\t\t# udev completion does not propagate RUN helper failures. Require
\t\t# the native policy application to succeed; closed stdin prevents
\t\t# an interactive reboot request from becoming an implicit action.
\t\tif ! LD_LIBRARY_PATH=/usr/lib/aarch64-linux-gnu/nvidia timeout --foreground 30s /etc/systemd/nvpmodel.sh </dev/null; then
\t\t\techo "ERROR: configured NVIDIA power mode could not be applied before display" >&2
\t\t\texit 1
\t\tfi
\t\t# nvpmodel -q can return zero while reporting no configured mode.
\t\tif ! power_mode=$(LD_LIBRARY_PATH=/usr/lib/aarch64-linux-gnu/nvidia timeout --foreground 10s /usr/sbin/nvpmodel -q) ||
\t\t   ! printf '%s\\n' "${power_mode}" | awk '
\t\t       /^NV Power Mode: .+/ { named = 1; next }
\t\t       named && /^[[:space:]]*[0-9]+[[:space:]]*$/ { valid = 1 }
\t\t       END { exit !valid }'; then
\t\t\techo "ERROR: NVIDIA power mode remains unset; refusing display startup" >&2
\t\t\texit 1
\t\tfi
\t\techo "Configured NVIDIA power policy before loading the display stack."
\tfi
fi
'''


def digest(data):
    return hashlib.sha256(data).hexdigest()


def regular(root, relative):
    path = root / relative
    for entry in (path, *path.parents):
        if entry == root:
            break
        if entry.is_symlink():
            raise ValueError(f"Refusing symlink in audited path: {relative}")
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError(f"Expected regular file: {relative}")
    return path


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def replace_file(path, payload, info):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".ark-gpu-power-", dir=path.parent,
                                         delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fchown(stream.fileno(), info.st_uid, info.st_gid)
            os.fchmod(stream.fileno(), stat.S_IMODE(info.st_mode))
            os.fsync(stream.fileno())
        temporary.replace(path)
        sync_directory(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def update(rootfs, product, mode="apply"):
    if mode not in ("apply", "check", "restore"):
        raise ValueError("Unknown GPU power ordering action")
    if product not in PRODUCTS:
        raise ValueError("GPU power ordering supports only JAJ, PAB and PAB_V3")
    root = rootfs.resolve(strict=True)
    release = regular(root, "etc/nv_tegra_release").read_text()
    if not re.search(r"^# R39 \(release\), REVISION: 2\.1,", release, re.M):
        raise ValueError("GPU power ordering supports only audited R39.2.1")
    stamp = root / "etc/ark_jetson_kernel"
    if stamp.exists() or stamp.is_symlink():
        fields = dict(line.split("=", 1) for line in regular(
            root, "etc/ark_jetson_kernel").read_text().splitlines() if "=" in line)
        if fields.get("target") != product:
            raise ValueError("Existing rootfs product stamp does not match --product")
    # The exact native service ordering is part of this audit. Do not ignore
    # customer drop-ins which could change when the patched script executes.
    for base in ("etc/systemd/system", "run/systemd/system", "usr/lib/systemd/system",
                 "usr/local/lib/systemd/system", "lib/systemd/system"):
        for name in ("service.d", "nv-.service.d", "nv-load-.service.d",
                     "nv-load-display-.service.d", "nv-load-display-modules.service.d",
                     "nvpower.service.d", "nvpmodel.service.d"):
            directory = root / base / name
            owned = (str(directory.relative_to(root)) == str(Path(DROPIN).parent)
                     and not directory.is_symlink() and directory.is_dir()
                     and sorted(entry.name for entry in directory.iterdir()) == [Path(DROPIN).name]
                     and regular(root, DROPIN).read_bytes() == DROPIN_DATA)
            if directory.is_symlink() or (directory.exists() and
                    (not directory.is_dir() or any(directory.iterdir())) and not owned):
                raise ValueError(f"Unaudited power/display unit drop-in: {base}/{name}")
    for relative, expected in COMPANIONS.items():
        if digest(regular(root, relative).read_bytes()) != expected:
            raise ValueError(f"Unaudited R39 power helper: {relative}")
    path = regular(root, SOURCE)
    original = path.read_bytes()
    fingerprint = digest(original)
    if fingerprint not in (ORIGINAL, PATCHED):
        raise ValueError("Display startup differs from audited original/patched R39 scripts")
    state = "patched" if fingerprint == PATCHED else "original"
    dropin = root / DROPIN
    had_dropin = dropin.exists()
    if mode == "check":
        if (state == "patched") != had_dropin:
            raise ValueError("Incomplete GPU power ordering: script/drop-in do not match; apply or restore")
        return state
    desired = "patched" if mode == "apply" else "original"
    if state == desired and had_dropin == (mode == "apply"):
        return state
    updated = original
    if state != desired:
        old, new, expected = (BEFORE, AFTER, PATCHED) if mode == "apply" else (AFTER, BEFORE, ORIGINAL)
        text = original.decode()
        if text.count(old) != 1:
            raise ValueError("Expected exactly one audited GPU load sequence")
        updated = text.replace(old, new).encode()
        if digest(updated) != expected:
            raise ValueError("GPU power ordering patch output checksum mismatch")
    info = path.stat()
    dropin_info = dropin.stat() if had_dropin else None
    created_directory = not dropin.parent.exists()
    try:
        if regular(root, SOURCE).read_bytes() != original:
            raise ValueError("Display startup changed during patching; refusing replacement")
        if mode == "apply" and not had_dropin:
            dropin.parent.mkdir(parents=False, exist_ok=True)
            # New owned configuration is readable like the native unit, without
            # inheriting the executable script's mode. Preserve existing metadata.
            values = list(info)
            values[0] = stat.S_IFREG | 0o644
            replace_file(dropin, DROPIN_DATA, os.stat_result(values))
        if updated != original:
            replace_file(path, updated, info)
        if mode == "restore" and had_dropin:
            if regular(root, DROPIN).read_bytes() != DROPIN_DATA:
                raise ValueError("Owned GPU ordering drop-in changed during restore")
            dropin.unlink()
            sync_directory(dropin.parent)
        return desired
    except BaseException:
        # A failure never silently leaves a partial ordinary update. An abrupt
        # host kill can leave the two files mismatched; check fails closed and
        # apply/restore can reconcile only the exact audited pair.
        if updated != original and path.read_bytes() == updated:
            replace_file(path, original, info)
        if had_dropin and not dropin.exists():
            replace_file(dropin, DROPIN_DATA, dropin_info)
        elif not had_dropin and dropin.exists() and dropin.read_bytes() == DROPIN_DATA:
            dropin.unlink()
            sync_directory(dropin.parent)
        raise
    finally:
        if dropin.parent.is_dir() and not any(dropin.parent.iterdir()) and (
                created_directory or (mode == "restore" and had_dropin)):
            dropin.parent.rmdir()
            sync_directory(dropin.parent.parent)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rootfs", type=Path, required=True)
    parser.add_argument("--product", choices=PRODUCTS, required=True)
    parser.add_argument("--mode", choices=("apply", "check", "restore"), default="apply")
    args = parser.parse_args()
    try:
        print("R39 GPU power ordering:", update(args.rootfs, args.product, args.mode))
    except (OSError, ValueError) as error:
        parser.exit(1, f"ERROR: {error}\n")


if __name__ == "__main__":
    main()
