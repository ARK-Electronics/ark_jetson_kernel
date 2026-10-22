#!/usr/bin/env python3
"""Apply/restore the audited C7-only exclusion experiment in an isolated UEFI checkout."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

SOURCE = "Silicon/NVIDIA/Drivers/PcieDWControllerDxe/PcieControllerDxe.c"
BEFORE = "47918d0164f84a9fd52b1c067d1cc12e4e1e78e28fc5617a63595cd3d19ac2b3"
AFTER = "99440ca6c0db747c8187be901900486553de09bfb401e9f24be728eb121a6c0c"
PATCH_HASH = "a229ce868e03a06d5a465912426bafb26ca038a4e16f2f37a528121eba259028"
PATCH = Path(__file__).with_name("uefi-pcie-skip-ffc.patch")


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
        print(json.dumps({"experiment": "jaj-t234-uefi-skip-ffc-c7",
                          "filter_phase": "DeviceDiscoveryDeviceTreeCompatibility",
                          "source_repository": "edk2-nvidia",
                          "source_revision": "79ad0c17aa4f13fdd5164d5b026fa49436b34891",
                          "source_file": SOURCE, "source_sha256_before": BEFORE,
                          "source_sha256_after": AFTER, "patch": PATCH.name,
                          "patch_sha256": PATCH_HASH}, indent=2))
        return
    if args.checkout is None:
        parser.error("checkout is required for apply/restore")
    try:
        patch_source(args.checkout, PATCH, SOURCE, BEFORE, AFTER, args.action == "restore")
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"uefi_pcie_filter: {error}\n")


if __name__ == "__main__":
    main()
