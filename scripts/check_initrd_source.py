#!/usr/bin/env python3
"""Reject optimized or unreadable initramfs inputs before a staged rebuild."""

import argparse
import gzip
import hashlib
from pathlib import Path
import sys

from optimize_initrd import (AUDITED_UNNORMALIZED_SOURCES, CHECKSUM_FILE, MARKER,
                             POLLING_MARKER, read_newc)


def check_source(path):
    image = path.read_bytes()
    audited_base = hashlib.sha256(image).hexdigest() in AUDITED_UNNORMALIZED_SOURCES
    entries, _ = read_newc(gzip.decompress(image), allow_duplicate_directories=audited_base)
    by_name = {entry["name"]: entry for entry in entries}
    if "init" not in by_name:
        raise ValueError(f"Missing /init in {path}")
    if any(marker.encode() in by_name["init"]["payload"] for marker in (MARKER, POLLING_MARKER)) or CHECKSUM_FILE in by_name:
        raise ValueError(f"Already optimized initramfs: {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, choices=("JAJ", "PAB", "PAB_V3"))
    parser.add_argument("images", nargs="+", type=Path)
    args = parser.parse_args()
    try:
        for path in args.images:
            check_source(path)
    except (OSError, ValueError, KeyError, EOFError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        print("NVIDIA's updater preserves /init, so reusing this source could retain "
              "an old optimization while replacing its modules.", file=sys.stderr)
        print(f"Run a fresh './build.sh {args.target}' build (add --precompute-initrd "
              "when desired), or restore BOTH initrd copies from a verified "
              "unoptimized backup before retrying --fast. See docs/fast_boot.md.",
              file=sys.stderr)
        return 1
    print("Verified unoptimized initramfs sources")
    return 0


if __name__ == "__main__":
    sys.exit(main())
