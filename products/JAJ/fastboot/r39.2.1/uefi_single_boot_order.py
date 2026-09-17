#!/usr/bin/env python3
"""Apply/restore the R39.2.1 single-launcher boot-order fix in an isolated UEFI checkout."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

SOURCE = "Silicon/NVIDIA/Library/PlatformBootManagerLib/PlatformBm.c"
BEFORE = "6d28b8c7bea509f5aedb096da65e018834cb01330704f14f0061cbe14acfc148"
AFTER = "40b78cbd5790d5669588d5a1a9eeda59518777a00bc229c4ee2d516ee6906e82"
PATCH_HASH = "ebde46918aaf99c7b6912da56ab8403906c8e6ebe6f1e856e30762ac538a3565"
PATCH = Path(__file__).with_name("uefi-single-boot-order.patch")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def patch_source(checkout, patch, relative, before, after, reverse=False):
    """Never reset a checkout or overwrite a file changed during the build."""
    source = checkout / relative
    expected, result = (after, before) if reverse else (before, after)
    if source.is_symlink() or digest(source) != expected:
        raise ValueError(f"Refusing unexpected source hash: {source}")
    git = ["git", "-C", str(checkout), "-c",
           "core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol"]
    if not reverse:
        for diff in (["diff", "--quiet"], ["diff", "--cached", "--quiet"]):
            if subprocess.run(git + diff, check=False).returncode:
                raise ValueError("Refusing modified tracked source files")
    # Keep the reviewed patch LF-only; NVIDIA's source uses CRLF. Expand
    # only diff payload lines, preserving the exact source bytes on restore.
    patch_data = patch.read_bytes()
    if b"\r\n" in source.read_bytes():
        patch_data = b"".join(
            line[:-1] + b"\r\n"
            if line.startswith((b"+", b"-", b" ")) and not line.startswith((b"+++", b"---"))
            else line for line in patch_data.splitlines(keepends=True))
    options = ["--unidiff-zero", *(["--reverse"] if reverse else [])]
    subprocess.run(git + ["apply", *options, "--check", "-"], input=patch_data, check=True)
    subprocess.run(git + ["apply", *options, "-"], input=patch_data, check=True)
    if digest(source) != result:
        # Only undo the exact patch we just applied; never reset unrelated work.
        undo = ["--unidiff-zero", *([] if reverse else ["--reverse"])]
        subprocess.run(git + ["apply", *undo, "-"], input=patch_data, check=True)
        raise ValueError("Patched source hash does not match the reviewed result; undone")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("apply", "restore", "describe"))
    parser.add_argument("checkout", type=Path, nargs="?")
    args = parser.parse_args()
    if digest(PATCH) != PATCH_HASH:
        parser.error("Patch checksum does not match the reviewed experiment")
    if args.action == "describe":
        print(json.dumps({"experiment": "r39-single-boot-app-first",
                          "behavior": "promote selected FV app in BootOrder; retain other entries",
                          "source_repository": "edk2-nvidia",
                          "source_revision": "7e9f9ec4de8d979807683a7b4605a15ec3299b89",
                          "source_file": SOURCE, "source_sha256_before": BEFORE,
                          "source_sha256_after": AFTER, "patch": PATCH.name,
                          "patch_sha256": PATCH_HASH}, indent=2))
        return
    if args.checkout is None:
        parser.error("checkout is required for apply/restore")
    try:
        patch_source(args.checkout, PATCH, SOURCE, BEFORE, AFTER, args.action == "restore")
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"uefi_single_boot_order: {error}\n")


if __name__ == "__main__":
    main()
