#!/usr/bin/env python3
"""Apply/check/restore the audited R36.5.0 JAJ BPMP debugfs source patch.

The added kernel parameter jaj_fastboot.bpmp_debugfs_async defaults to false. This
helper changes only bpmp.c, and refuses any source other than the exact audited
original or patched version. It never builds a kernel or edits boot arguments.
"""
import argparse
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import tempfile

SOURCE = Path("drivers/firmware/tegra/bpmp.c")
PATCH = Path(__file__).resolve().parents[1] / "products/JAJ/fastboot/bpmp-debugfs-async.patch"
ORIGINAL = "b8dd7126e0c3432d7d903785d291bf169f6f2a02c9d8719a6e2c28a460f449a5"
PATCHED = "8bdc230d4815ec430425e08a3e33e6e7fba740272f195cbc0c2eda74e19ab53f"


def update(kernel_dir, mode):
    root = kernel_dir.resolve(strict=True)
    path = root / SOURCE
    for part in (path, *path.parents):
        if part == root:
            break
        if part.is_symlink():
            raise ValueError(f"Refusing source path with symlink: {part}")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"Expected regular source file: {path}")
    original = path.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    if digest not in (ORIGINAL, PATCHED):
        raise ValueError("BPMP source differs from both audited R36.5.0 versions; "
                         "refusing to overwrite it")
    state = "patched" if digest == PATCHED else "original"
    if mode == "check":
        return state
    expected = PATCHED if mode == "apply" else ORIGINAL
    if digest == expected:
        return state

    # Apply the human-reviewable diff to a disposable file first. Exact input
    # and output hashes prevent fuzzy matches and partial source modifications.
    with tempfile.TemporaryDirectory(prefix="ark-bpmp-patch-") as directory:
        candidate = Path(directory) / "bpmp.c"
        candidate.write_bytes(original)
        command = ["patch", "--batch", "--fuzz=0", "--no-backup-if-mismatch"]
        if mode == "restore":
            command.append("--reverse")
        command.append(str(candidate))
        result = subprocess.run(command, input=PATCH.read_bytes(), capture_output=True)
        if result.returncode:
            raise ValueError("BPMP patch failed in temporary fixture: " +
                             result.stdout.decode(errors="replace") +
                             result.stderr.decode(errors="replace"))
        updated = candidate.read_bytes()
        if hashlib.sha256(updated).hexdigest() != expected:
            raise ValueError("Patched BPMP source checksum mismatch; source left untouched")

    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".ark-bpmp-", dir=path.parent,
                                         delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(updated)
            stream.flush()
            os.fchown(stream.fileno(), info.st_uid, info.st_gid)
            os.fchmod(stream.fileno(), stat.S_IMODE(info.st_mode))
            os.fsync(stream.fileno())
        if path.is_symlink() or path.read_bytes() != original:
            raise ValueError("BPMP source changed during patching; source left untouched")
        temporary.replace(path)
        return "patched" if mode == "apply" else "original"
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel-dir", type=Path, required=True)
    parser.add_argument("--mode", choices=("apply", "check", "restore"), default="apply")
    args = parser.parse_args()
    try:
        print("BPMP debugfs source:", update(args.kernel_dir, args.mode))
    except (OSError, ValueError) as error:
        parser.exit(1, f"ERROR: {error}\n")


if __name__ == "__main__":
    main()
