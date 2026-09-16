#!/usr/bin/env python3
"""Stage and verify the opt-in R36.5 JAJ NVMe firmware without flashing hardware."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ARTIFACTS = {
    "uefi_jaj_nvme_RELEASE.bin": "bootloader/uefi_jetson.bin",
    "ark_fast_boot.dtbo": "kernel/dtb/ark_fast_boot.dtbo",
}
BCT = "bootloader/tegra234-mb1-bct-misc-common.dtsi"
BPMP = [f"bootloader/generic/tegra234-bpmp-3767-{sku}-3768-super.dtb"
        for sku in ("0000", "0001", "0003", "0004")]
MARKER = "ark-fast-boot.json"
IN_PROGRESS = "ark-fast-boot.in-progress.json"
BACKUPS = "bootloader/ark-fastboot-stock"
FLASH_TARGET = "jetson-orin-nano-devkit-super"
STORAGE = "nvme0n1p1"


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else hashlib.sha256(source.read()).hexdigest()


def check_product(l4t):
    stamp = dict(line.split("=", 1) for line in
                 (l4t / "rootfs/etc/ark_jetson_kernel").read_text().splitlines()
                 if "=" in line)
    if stamp.get("target") != "JAJ":
        raise ValueError("Fast firmware requires a completed JAJ staging tree (target=JAJ)")
    release = (l4t / "rootfs/etc/nv_tegra_release").read_text()
    if not re.search(r"^# R36 \(release\), REVISION: 5\.0,", release, re.M):
        raise ValueError("Fast firmware is pinned to Jetson Linux R36.5.0")


def reject_incomplete_stage(l4t):
    guard = l4t / IN_PROGRESS
    if guard.exists() or guard.is_symlink():
        raise ValueError(f"Firmware staging is active or was interrupted: {guard}. "
                         "Do not flash this tree; recover the saved stock files or rebuild it first.")


def load_verified_manifest(l4t, flash_target=FLASH_TARGET, storage=STORAGE, *, _allow_in_progress=False):
    if not _allow_in_progress:
        reject_incomplete_stage(l4t)
    check_product(l4t)
    manifest = json.loads((l4t / MARKER).read_text())
    expected = {"schema_version": 1, "target": "JAJ", "bsp": "R36.5.0",
                "flash_target": flash_target, "storage": storage}
    if flash_target != FLASH_TARGET or storage != STORAGE:
        raise ValueError("Fast firmware supports only jetson-orin-nano-devkit-super with nvme0n1p1")
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError(f"Fast firmware manifest mismatch: {key}")
    files = manifest.get("files", {})
    required = set(ARTIFACTS.values())
    if manifest.get("quiet_firmware"):
        required.update([BCT, *BPMP])
    if set(files) != required:
        raise ValueError("Fast firmware manifest does not contain exactly the expected files")
    for relative, expected_hash in files.items():
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash) or digest(l4t / relative) != expected_hash:
            raise ValueError(f"Staged fast firmware checksum mismatch: {relative}")
    return manifest


def command(*args):
    return subprocess.run([str(arg) for arg in args], check=True,
                          text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def without_serial_contents(dts):
    match = re.search(r"(?m)^\tserial \{\n", dts)
    if not match:
        raise ValueError("Expected a top-level BPMP /serial node")
    depth = 1
    position = match.end()
    while depth and position < len(dts):
        depth += (dts[position] == "{") - (dts[position] == "}")
        position += 1
    if depth:
        raise ValueError("Unbalanced BPMP serial node")
    return dts[:match.end()] + dts[position - 1:]


def quiet_bpmp(path):
    before = command("dtc", "-I", "dtb", "-O", "dts", path)
    properties = command("fdtget", "-p", path, "/serial").splitlines()
    children = command("fdtget", "-l", path, "/serial").splitlines()
    if properties:
        command("fdtput", "-d", path, "/serial", *properties)
    for child in children:
        command("fdtput", "-r", path, f"/serial/{child}")
    if command("fdtget", "-p", path, "/serial").strip() or command("fdtget", "-l", path, "/serial").strip():
        raise ValueError("Failed to empty the BPMP /serial node")
    after = command("dtc", "-I", "dtb", "-O", "dts", path)
    if without_serial_contents(before) != without_serial_contents(after):
        raise ValueError("BPMP modification changed content outside /serial")


def artifact_hashes(directory):
    checksums = {}
    for line in (directory / "SHA256SUMS").read_text().splitlines():
        if not line.strip():
            continue
        value, filename = line.split(maxsplit=1)
        filename = filename.lstrip("*")
        if filename in checksums:
            raise ValueError(f"Duplicate artifact checksum: {filename}")
        checksums[filename] = value
    for name in ARTIFACTS:
        if not re.fullmatch(r"[0-9a-f]{64}", checksums.get(name, "")) or digest(directory / name) != checksums[name]:
            raise ValueError(f"Artifact checksum mismatch: {name}")
    if not 0 < (directory / "uefi_jaj_nvme_RELEASE.bin").stat().st_size <= 3670016:
        raise ValueError("UEFI image is empty or larger than the T234 3.5 MiB partition")
    return {name: checksums[name] for name in ARTIFACTS}


def replace_file(source, destination):
    """Replace one file atomically without following a stale temporary symlink."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.ark-fastboot-', dir=destination.parent,
                                         delete=False) as stream:
            temporary = Path(stream.name)
            with source.open('rb') as original:
                shutil.copyfileobj(original, stream)
            stream.flush()
            os.fsync(stream.fileno())
        shutil.copystat(source, temporary)
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def stage(l4t, artifacts, quiet):
    reject_incomplete_stage(l4t)
    check_product(l4t)
    source_hashes = artifact_hashes(artifacts)
    prior = load_verified_manifest(l4t) if (l4t / MARKER).exists() else None
    # Restaging a firmware artifact preserves an already enabled quiet profile.
    quiet = quiet or bool(prior and prior.get("quiet_firmware"))
    backup_dir = l4t / BACKUPS
    destinations = [*ARTIFACTS.values(), *([BCT, *BPMP] if quiet else [])]
    for relative in ["bootloader/uefi_jetson.bin", *([BCT, *BPMP] if quiet else [])]:
        if not (l4t / relative).is_file():
            raise ValueError(f"Missing staged BSP component: {relative}")
    if quiet:
        for tool in ("dtc", "fdtget", "fdtput"):
            if not shutil.which(tool):
                raise ValueError(f"Quiet firmware requires {tool} (device-tree-compiler package)")

    # Prepare and validate every output before changing the staged BSP.
    with tempfile.TemporaryDirectory(prefix="jaj-fastboot-stage-") as work:
        prepared = {}
        for name, relative in ARTIFACTS.items():
            target = Path(work) / name
            shutil.copy2(artifacts / name, target)
            if digest(target) != source_hashes[name]:
                raise ValueError(f"Artifact changed during staging: {name}")
            prepared[relative] = target
        if quiet:
            for relative in [BCT, *BPMP]:
                source = backup_dir / relative
                if not source.is_file():
                    source = l4t / relative
                target = Path(work) / Path(relative).name
                shutil.copy2(source, target)
                if relative == BCT:
                    content, count = re.subn(r"(?m)^(\s*log_level\s*=\s*<)4(>;[^\n]*)$", r"\g<1>0\2", target.read_text())
                    if count != 1:
                        raise ValueError(f"Expected exactly one stock log_level=<4> in {BCT}, found {count}")
                    target.write_text(content)
                else:
                    quiet_bpmp(target)
                prepared[relative] = target

        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_manifest_path = backup_dir / "backup-manifest.json"
        backup_manifest = json.loads(backup_manifest_path.read_text()) if backup_manifest_path.exists() else {}
        for relative in destinations:
            original = l4t / relative
            backup = backup_dir / relative
            if relative not in backup_manifest:
                if original.exists():
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    if backup.exists():
                        raise ValueError(f"Unrecorded backup already exists; inspect before staging: {backup}")
                    shutil.copy2(original, backup)
                    backup_manifest[relative] = digest(backup)
                else:
                    backup_manifest[relative] = None
            elif backup_manifest[relative] is not None and digest(backup) != backup_manifest[relative]:
                raise ValueError(f"Stock backup checksum mismatch: {relative}")
        backup_manifest_path.write_text(json.dumps(backup_manifest, indent=2, sort_keys=True) + "\n")

        # A per-file atomic rename is not a transaction across the firmware and
        # overlay. Leave a guard behind on any interruption so flash.sh cannot
        # mistake partially replaced firmware for an ordinary BSP without a marker.
        guard = l4t / IN_PROGRESS
        with guard.open('x') as stream:
            json.dump({'schema_version': 1, 'target': 'JAJ', 'backup_dir': BACKUPS,
                       'planned_files': destinations, 'artifact_hashes': source_hashes}, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        for relative, source in prepared.items():
            replace_file(source, l4t / relative)
        manifest = {"schema_version": 1, "target": "JAJ", "bsp": "R36.5.0",
                    "flash_target": FLASH_TARGET, "storage": STORAGE,
                    "quiet_firmware": quiet, "backup_dir": BACKUPS,
                    "artifact_hashes": source_hashes,
                    "files": {relative: digest(l4t / relative) for relative in destinations}}
        prepared_manifest = Path(work) / MARKER
        prepared_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        replace_file(prepared_manifest, l4t / MARKER)
        load_verified_manifest(l4t, _allow_in_progress=True)
        guard.unlink()
    load_verified_manifest(l4t)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--l4t-dir", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--quiet-firmware", action="store_true")
    parser.add_argument("--verify", action="store_true", help="verify a staged manifest without modifying files")
    parser.add_argument("--flash-target", default=FLASH_TARGET)
    parser.add_argument("--storage", default=STORAGE)
    args = parser.parse_args()
    try:
        if args.verify:
            load_verified_manifest(args.l4t_dir, args.flash_target, args.storage)
            print("Verified JAJ R36.5 NVMe fast-boot firmware and overlay")
        else:
            if args.artifacts is None:
                parser.error("--artifacts is required unless --verify is used")
            stage(args.l4t_dir, args.artifacts, args.quiet_firmware)
            print(f"Staged fast firmware: {args.l4t_dir / MARKER}")
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"ERROR: {error}\n")


if __name__ == "__main__":
    main()
