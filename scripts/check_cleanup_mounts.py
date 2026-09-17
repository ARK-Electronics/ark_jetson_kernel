#!/usr/bin/env python3
"""Refuse staging cleanup when the tree contains an active mount."""

import os
import re
import sys
from pathlib import Path


def mounts_under(tree, mountinfo):
    tree = os.path.realpath(tree).rstrip("/") or "/"
    if tree == "/":
        raise ValueError("refusing to clean the filesystem root")
    result = []
    for line in mountinfo.splitlines():
        fields = line.split()
        if len(fields) < 6:
            raise ValueError("malformed mountinfo record")
        mount = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), fields[4])
        mount = os.path.realpath(mount)
        if mount == tree or mount.startswith(tree + "/"):
            result.append(mount)
    return result


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: check_cleanup_mounts.py TREE")
    try:
        mounts = mounts_under(sys.argv[1], Path("/proc/self/mountinfo").read_text())
        if mounts:
            raise ValueError("active mounts prevent cleanup: " + ", ".join(mounts))
    except (OSError, ValueError) as error:
        sys.exit("ERROR: " + str(error))


if __name__ == "__main__":
    main()
