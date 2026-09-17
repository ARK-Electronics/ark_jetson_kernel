#!/usr/bin/env python3
"""Find a prepared Kbuild root matching the installed kernel release."""

import argparse
from pathlib import Path


def find_headers(rootfs, release):
    rootfs = Path(rootfs).resolve()
    candidates = set()
    for package in (rootfs / "usr/src").glob(f"linux-headers-{release}*"):
        for stamp in package.rglob("include/config/kernel.release"):
            tree = stamp.parents[2].resolve()
            if not tree.is_relative_to(rootfs):
                raise ValueError("headers escape the staged rootfs")
            if (stamp.read_text().strip() == release
                    and (tree / "Makefile").is_file()
                    and (tree / "include/generated/autoconf.h").is_file()):
                candidates.add(tree)
    if len(candidates) != 1:
        raise ValueError(f"expected one prepared Kbuild tree for {release}, found {len(candidates)}")
    return candidates.pop()


def target_path(rootfs, headers):
    """Return the absolute device path, independent of host staging symlinks."""
    relative = Path(headers).resolve(strict=True).relative_to(
        Path(rootfs).resolve(strict=True))
    return Path("/") / relative


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-path", action="store_true",
                        help="print the path as seen in the flashed rootfs")
    parser.add_argument("rootfs", type=Path)
    parser.add_argument("kernel_release")
    args = parser.parse_args()
    try:
        headers = find_headers(args.rootfs, args.kernel_release)
        print(target_path(args.rootfs, headers) if args.target_path else headers)
    except (OSError, ValueError) as error:
        parser.exit(1, "ERROR: " + str(error) + "\n")
