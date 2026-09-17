#!/usr/bin/env python3
"""Reject an effective R39 kernel configuration that cannot load Noble firmware."""

import argparse
from pathlib import Path
import re
import sys

REQUIRED = ("CONFIG_FW_LOADER", "CONFIG_FW_LOADER_COMPRESS", "CONFIG_FW_LOADER_COMPRESS_ZSTD")


def check_config(path):
    values = {}
    for line in path.read_text().splitlines():
        match = re.fullmatch(r"(CONFIG_[A-Z0-9_]+)=(.*)", line)
        if match:
            values[match[1]] = match[2]
        match = re.fullmatch(r"# (CONFIG_[A-Z0-9_]+) is not set", line)
        if match:
            values[match[1]] = "n"
    missing = [name + "=y" for name in REQUIRED if values.get(name) != "y"]
    if missing:
        raise ValueError("effective kernel configuration lacks " + ", ".join(missing))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, choices=("JAJ", "PAB", "PAB_V3"))
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    try:
        check_config(args.config)
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        print("Noble supplies .zst firmware, including ath11k/WCN6855. Re-stage with "
              f"'./build.sh {args.target}', or bring the staged defconfig up to date "
              "with the shared firmware-loader settings before retrying --fast. "
              "Do not decompress or replace the packaged firmware to hide this mismatch.",
              file=sys.stderr)
        return 1
    print(f"Verified {args.target} effective ZSTD firmware-loader support")
    return 0


if __name__ == "__main__":
    sys.exit(main())
