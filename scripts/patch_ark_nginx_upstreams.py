#!/usr/bin/env python3
"""Pin audited ARK-OS nginx upstreams to their IPv4 listeners on R39.2.1/Noble.

Apply/check/restore only the known ARK-OS 1.2.0 nginx configuration. localhost
can resolve to ::1 while the API gateway and flight review listen on IPv4,
causing intermittent connection-refused HTTP 502 responses. This helper changes
no service bindings, Node proxy URLs, DNS/hosts settings or daemon state. On a
running target, validate nginx -t before separately reloading nginx.
"""
import argparse
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile

PRODUCTS = ("JAJ", "PAB", "PAB_V3")
SOURCE = "etc/nginx/sites-available/ark-ui"
ORIGINAL = "182b5564092de59e08e8a908983ca454ba9351255571e7abfdc08bebb0d332a3"
PATCHED = "25ca1526e860c70ef30c3f7afa5f9e2c9b8933eba3d9ddd06b7aca92141913a1"
UPSTREAMS = (3000, 5006)


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
        raise ValueError("Unknown ARK nginx IPv4 upstream product/action")
    root = Path(rootfs).resolve(strict=True)
    release = regular(root, "etc/nv_tegra_release").read_text()
    if not re.search(r"^# R39 \(release\), REVISION: 2\.1,", release, re.M):
        raise ValueError("ARK nginx IPv4 upstream supports only audited R39.2.1")
    os_release = dict(line.split("=", 1) for line in regular(
        root, "usr/lib/os-release").read_text().splitlines() if "=" in line)
    values = {key: value.strip('"') for key, value in os_release.items()}
    if (values.get("ID"), values.get("VERSION_ID"), values.get("VERSION_CODENAME")) != (
            "ubuntu", "24.04", "noble"):
        raise ValueError("ARK nginx IPv4 upstream requires Ubuntu 24.04/Noble")
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
        raise ValueError("ARK nginx IPv4 upstream differs from audited original/patched configuration")
    state = "patched" if fingerprint == PATCHED else "original"
    if mode == "check" or state == ("patched" if mode == "apply" else "original"):
        return state
    updated = original
    for port in UPSTREAMS:
        before = f"proxy_pass http://localhost:{port};".encode()
        after = f"proxy_pass http://127.0.0.1:{port};".encode()
        old, new = (before, after) if mode == "apply" else (after, before)
        if updated.count(old) != 1:
            raise ValueError("Expected exactly one audited upstream per port")
        updated = updated.replace(old, new)
    expected = PATCHED if mode == "apply" else ORIGINAL
    if digest(updated) != expected:
        raise ValueError("ARK nginx IPv4 upstream output checksum mismatch")
    info = path.stat()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".ark-nginx-", dir=path.parent,
                                         delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(updated)
            stream.flush()
            os.fchown(stream.fileno(), info.st_uid, info.st_gid)
            os.fchmod(stream.fileno(), stat.S_IMODE(info.st_mode))
            os.fsync(stream.fileno())
        if regular(root, SOURCE).read_bytes() != original:
            raise ValueError("ARK nginx IPv4 upstream changed during patching")
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
        print("R39 ARK nginx IPv4 upstream:", update(args.rootfs, args.product, args.mode))
    except (OSError, ValueError) as error:
        parser.exit(1, f"ERROR: {error}\n")


if __name__ == "__main__":
    main()
