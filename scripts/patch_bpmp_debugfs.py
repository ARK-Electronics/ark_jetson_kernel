#!/usr/bin/env python3
"""Apply/check/restore the release-specific R36.5.0/R39.2.1 ARK Orin BPMP debugfs source patch.

The added kernel parameter jaj_fastboot.bpmp_debugfs_async defaults to false;
bpmp_debugfs_delay_ms defaults to zero and applies only in asynchronous mode. This
helper serves PAB, PAB_V3 and JAJ, changes only bpmp.c, and refuses any source
other than the exact audited
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
PATCHED = "ecc2bf3b41ee112b2e89fe950c235076509b3d05de0e69e2c69c6a24532048ec"
R39 = {
    "original": "9a197e986731682e54e2474078f1f7cc4d7369438cd6579752d6d7db711610c9",
    "patched": "cf849c8aaea7d0c709bbba80fd2f1fa5c30b20b1b87e9c9afa205f38c47617f9",
    "patch": PATCH.parent / "r39.2.1/bpmp-debugfs-async.patch",
    "companions": {
        "drivers/firmware/tegra/bpmp-debugfs.c": "081c627087704d1e200ea32bbed4c8cb84fa5d9d5a22ebc2a459fbb4b1d76a7e",
        "include/soc/tegra/bpmp.h": "611a4975295bc14f53290ab84e7e19a7b52170c54a802eed72312cd090181ac8",
    },
}



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
    if digest in (ORIGINAL, PATCHED):
        original_hash, patched_hash, patch_path = ORIGINAL, PATCHED, PATCH
    elif digest in (R39["original"], R39["patched"]):
        original_hash, patched_hash, patch_path = R39["original"], R39["patched"], R39["patch"]
        # R39 changed suspend state and adds a NUMA debugfs alias. The patch
        # preserves these semantics, so audit its private API dependencies too.
        for relative, expected_hash in R39["companions"].items():
            companion = root / relative
            for part in (companion, *companion.parents):
                if part == root:
                    break
                if part.is_symlink():
                    raise ValueError(f"Refusing companion source path with symlink: {relative}")
            if hashlib.sha256(companion.read_bytes()).hexdigest() != expected_hash:
                raise ValueError(f"R39 BPMP companion source checksum mismatch: {relative}")
    else:
        raise ValueError("BPMP source differs from both audited original/patched pairs "
                         "for R36.5.0 and R39.2.1; refusing to overwrite it")
    state = "patched" if digest == patched_hash else "original"
    if mode == "check":
        return state
    expected = patched_hash if mode == "apply" else original_hash
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
        result = subprocess.run(command, input=patch_path.read_bytes(), capture_output=True)
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
