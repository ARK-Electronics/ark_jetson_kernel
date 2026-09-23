#!/usr/bin/env python3
"""Apply/check/restore the audited R39.2.1 Bluetooth driver preference fix.

Load NVIDIA's Realtek driver before generic btusb, allowing both to bind their
supported devices. Preserve all other vendor driver preferences. This modifies
only the staged modprobe rule; it does not load modules or touch USB devices.
"""
import argparse
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile

PRODUCTS = ("JAJ", "PAB", "PAB_V3")
SOURCE = "etc/modprobe.d/nvidia-preferred-oot-modules.conf"
ORIGINAL = "cbd35f014dcf25b9b5ee5144b661b2997c0b77bbfe00c04cd0aa91937ca5aca0"
PATCHED = "82e3892f34cc1fe0163143efab71834c3379fd395a7a9b275bab10883bf8312f"
# Only the replacement context is retained; the proprietary vendor file is not
# redistributed. Its complete original and resulting content are hash-checked.
BEFORE = r'''# We only want to load the following upstream drivers if the associated
# out-of-tree driver is not present. If out-of-tree driver is present,
# it will be loaded via its own device alias.
install btusb \
    if /sbin/modinfo rtk_btusb >/dev/null 2>&1; then \
        echo "<6>modprobe: btusb: not loading in-tree driver, out-of-tree rtk_btusb is present" >/dev/kmsg; \
    else \
        /sbin/modprobe --ignore-install btusb $CMDLINE_OPTS; \
    fi
'''
AFTER = r'''# Register the Realtek driver first so its supported devices keep that driver.
# Then permit the generic driver to bind other USB Bluetooth controllers.
install btusb \
    if /sbin/modinfo rtk_btusb >/dev/null 2>&1; then \
        /sbin/modprobe rtk_btusb || exit $?; \
    fi; \
    /sbin/modprobe --ignore-install btusb $CMDLINE_OPTS

# Keep the vendor out-of-tree preference for the following network drivers.
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


def update(rootfs, product, mode="apply"):
    if product not in PRODUCTS or mode not in ("apply", "check", "restore"):
        raise ValueError("Unknown Bluetooth preference product/action")
    root = Path(rootfs).resolve(strict=True)
    release = regular(root, "etc/nv_tegra_release").read_text()
    if not re.search(r"^# R39 \(release\), REVISION: 2\.1,", release, re.M):
        raise ValueError("Bluetooth preference supports only audited R39.2.1")
    stamp = root / "etc/ark_jetson_kernel"
    if stamp.exists() or stamp.is_symlink():
        fields = dict(line.split("=", 1) for line in regular(
            root, "etc/ark_jetson_kernel").read_text().splitlines() if "=" in line)
        if fields.get("target") != product:
            raise ValueError("Rootfs product stamp does not match --product")
    path = regular(root, SOURCE)
    original = path.read_bytes()
    fingerprint = digest(original)
    if fingerprint not in (ORIGINAL, PATCHED):
        raise ValueError("Bluetooth preference differs from audited vendor/patched rule")
    state = "patched" if fingerprint == PATCHED else "original"
    if mode == "check" or state == ("patched" if mode == "apply" else "original"):
        return state
    old, new, expected = ((BEFORE, AFTER, PATCHED) if mode == "apply"
                          else (AFTER, BEFORE, ORIGINAL))
    text = original.decode()
    if text.count(old) != 1:
        raise ValueError("Expected exactly one audited Bluetooth preference stanza")
    updated = text.replace(old, new).encode()
    if digest(updated) != expected:
        raise ValueError("Bluetooth preference output checksum mismatch")
    info = path.stat()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".ark-btusb-", dir=path.parent,
                                         delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(updated)
            stream.flush()
            os.fchown(stream.fileno(), info.st_uid, info.st_gid)
            os.fchmod(stream.fileno(), stat.S_IMODE(info.st_mode))
            os.fsync(stream.fileno())
        if regular(root, SOURCE).read_bytes() != original:
            raise ValueError("Bluetooth preference changed during patching")
        temporary.replace(path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return "patched" if mode == "apply" else "original"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rootfs", type=Path, required=True)
    parser.add_argument("--product", choices=PRODUCTS, required=True)
    parser.add_argument("--mode", choices=("apply", "check", "restore"), default="apply")
    args = parser.parse_args()
    try:
        print("R39 Bluetooth preference:", update(args.rootfs, args.product, args.mode))
    except (OSError, ValueError) as error:
        parser.exit(1, f"ERROR: {error}\n")


if __name__ == "__main__":
    main()
