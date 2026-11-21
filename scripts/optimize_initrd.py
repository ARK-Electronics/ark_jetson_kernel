#!/usr/bin/env python3
"""Precompute NVIDIA R36.5 initramfs module indexes using Ubuntu 22.04 kmod 29.

Writes a separate gzip/newc image, preserving all modules and original metadata.
Runtime checksum validation falls back to depmod if an update changes the image.
Also applies exact-hash-guarded root polling changes with unchanged failure budgets.
"""

import argparse
import gzip
import hashlib
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile


STOCK_DEPMOD = b"cd /usr/sbin;\nln -s /bin/kmod depmod\ndepmod -a\ncd /\n"
CHECKSUM_FILE = "etc/ark-initrd-depmod.sha256"
MARKER = "ARK_PRECOMPUTED_DEPMOD_V1"

# Exact source and output hashes guard changes outside these two reviewed loops.
# This source is shared by audited R36.5.0 rootfs images, independent of product.
POLLING_MARKER = "ARK_ROOT_POLLING_V1"
STOCK_INIT_SHA256 = "4f9f83acc62d168957a7ab6dd88f8c80bb19e367384a6dca5b3e30b4e928e059"
POLLING_INIT_SHA256 = "9a0219efa548772d6c2ed33841a109f28ff3d19f482ba28357d06111aa800181"
STOCK_MOUNT_LOOP = b'''	while [ ${count} -lt 50 ]; do
		sleep 0.2;
		count="$(expr ${count} + 1)"
		if [ "${readonly}" -eq 1 ]; then
			mount -r "${dev}" "${mnt}"
		else
			mount "${dev}" "${mnt}"
		fi
		if [ $? -eq 0 ]; then
			mounted=1
			break;
		fi
		if [ "${retry}" -eq 0 ]; then
			break
		fi
	done
'''
FAST_MOUNT_LOOP = b'''	# ARK_ROOT_POLLING_V1: try immediately only when retry is allowed.
	# Keep the original retry window and nonretry/encrypted-root behavior.
	if [ "${retry}" -eq 1 ]; then
		if [ "${readonly}" -eq 1 ]; then
			mount -r "${dev}" "${mnt}"
		else
			mount "${dev}" "${mnt}"
		fi
		if [ $? -eq 0 ]; then
			mounted=1
		fi
	fi
	if [ "${mounted}" -ne 1 ]; then
''' + STOCK_MOUNT_LOOP + b'''	fi
'''
STOCK_DEVICE_POLL = b'''elif [[ "${rootdev}" == mmcblk* || "${rootdev}" == nvme* ]]; then
	if [ ! -e "/dev/${rootdev}" ]; then
		count=0;
		while [ ${count} -lt 50 ]
		do
			sleep 0.2;
			count=`expr $count + 1`;
			if [ -e "/dev/${rootdev}" ]; then
				break;
			fi
		done
	fi'''
FAST_DEVICE_POLL = b'''elif [[ "${rootdev}" == mmcblk* || "${rootdev}" == nvme* ]]; then
	if [ ! -e "/dev/${rootdev}" ]; then
		count=0;
		while [ ${count} -lt 500 ]
		do
			sleep 0.02;
			count=$((count + 1));
			if [ -e "/dev/${rootdev}" ]; then
				break;
			fi
		done
	fi'''


def read_newc(data):
    entries = []
    names = set()
    offset = 0
    while True:
        start = offset
        header = data[offset:offset + 110]
        if len(header) != 110 or header[:6] != b"070701":
            raise ValueError("Expected a single uncompressed newc archive inside gzip")
        fields = [int(header[i:i + 8], 16) for i in range(6, 110, 8)]
        size, name_size = fields[6], fields[11]
        offset += 110
        name_bytes = data[offset:offset + name_size]
        if not name_bytes.endswith(b"\0"):
            raise ValueError("Invalid newc archive filename")
        name = name_bytes[:-1].decode()
        normalized = str(PurePosixPath(name))
        if name.startswith("/") or ".." in PurePosixPath(name).parts:
            raise ValueError(f"Unsafe archive path: {name}")
        offset = (offset + name_size + 3) & ~3
        payload = data[offset:offset + size]
        if len(payload) != size:
            raise ValueError("Truncated newc archive payload")
        offset = (offset + size + 3) & ~3
        if normalized == "TRAILER!!!":
            if any(data[offset:]):
                raise ValueError("Appended initramfs data is unsupported")
            return entries, data[start:offset]
        if normalized in names:
            raise ValueError(f"Duplicate archive entry: {normalized}")
        names.add(normalized)
        entries.append({"name": normalized, "archive_name": name, "fields": fields,
                        "payload": payload, "record": data[start:offset]})


def encode_record(name, fields, payload):
    fields = list(fields)
    fields[6] = len(payload)
    fields[11] = len(name.encode()) + 1
    record = b"070701" + b"".join(f"{value:08x}".encode() for value in fields)
    record += name.encode() + b"\0"
    record += b"\0" * (-len(record) % 4)
    record += payload
    return record + b"\0" * (-len(record) % 4)


def run(command):
    return subprocess.run(command, check=True, text=True, capture_output=True).stdout


def optimize_root_polling(init):
    """Change only the two audited loops; retain mount and discovery budgets."""
    if hashlib.sha256(init).hexdigest() != STOCK_INIT_SHA256:
        raise ValueError("Unaudited /init SHA256; inspect the complete script before adapting root polling")
    updated = init
    for original, replacement in ((STOCK_MOUNT_LOOP, FAST_MOUNT_LOOP),
                                  (STOCK_DEVICE_POLL, FAST_DEVICE_POLL)):
        if updated.count(original) != 1:
            raise ValueError("Expected exactly one audited root-mount/device-poll sequence")
        updated = updated.replace(original, replacement)
    if hashlib.sha256(updated).hexdigest() != POLLING_INIT_SHA256:
        raise ValueError("Root-polling /init output checksum mismatch")
    return updated


def optimize(args):
    source = args.input.resolve()
    destination = args.output.resolve()
    if source == destination or destination.exists():
        raise ValueError("Output must be a new file distinct from the original initrd")
    release = args.l4t_release.read_text()
    if not re.search(r"^# R36 \(release\), REVISION: 5\.0,", release, re.M):
        raise ValueError("Only the audited NVIDIA L4T R36.5.0 initrd is supported")
    for tool in ("depmod", "modprobe"):
        if not run([tool, "--version"]).startswith("kmod version 29\n"):
            raise ValueError("Run with Ubuntu 22.04 kmod 29 (e.g. the build container)")
    source_bytes = source.read_bytes()
    entries, trailer = read_newc(gzip.decompress(source_bytes))
    by_name = {entry["name"]: entry for entry in entries}
    init = by_name["init"]
    if not stat.S_ISREG(init["fields"][1]) or init["fields"][4] != 1:
        raise ValueError("Expected a single regular /init file")
    if MARKER.encode() in init["payload"] or POLLING_MARKER.encode() in init["payload"] or CHECKSUM_FILE in by_name:
        raise ValueError("Initrd is already optimized; regenerate from the stock image")
    if init["payload"].count(STOCK_DEPMOD) != 1:
        raise ValueError("Unexpected /init depmod sequence; inspect this BSP before adapting")
    if b"modprobe -v pcie-tegra194;" not in init["payload"]:
        raise ValueError("Expected NVIDIA PCIe initialization is absent")
    polled_init = optimize_root_polling(init["payload"])
    usr_merged = by_name.get("lib", {}).get("payload") == b"usr/lib"
    module_prefix = "usr/lib/modules/" if usr_merged else "lib/modules/"
    checksum_binary = "usr/bin/sha256sum" if by_name.get("bin", {}).get("payload") == b"usr/bin" else "bin/sha256sum"
    if checksum_binary not in by_name:
        raise ValueError("The existing initrd must provide /bin/sha256sum")
    module_entries = [entry for entry in entries if entry["name"].startswith(module_prefix)]
    versions = {entry["name"][len(module_prefix):].split("/")[0] for entry in module_entries}
    if len(versions) != 1:
        raise ValueError("Exactly one kernel module tree is required")
    version = versions.pop()
    if not re.fullmatch(r"5\.15\.185-tegra(?:[-+][A-Za-z0-9_.+-]+)?", version):
        raise ValueError(f"Unaudited kernel release: {version}")
    if b"Linux version " + version.encode() + b" " not in args.kernel_image.read_bytes():
        raise ValueError("Kernel Image release does not match the initrd module tree")
    modules = [entry for entry in module_entries if entry["name"].endswith(".ko")]
    if not modules or any(entry["name"].endswith((".ko.gz", ".ko.xz", ".ko.zst"))
                          for entry in module_entries):
        raise ValueError("Only the audited uncompressed module layout is supported")
    required = {"pcie-tegra194", "phy-tegra194-p2u", "nvme", "nvme-core",
                "typec", "typec_ucsi", "ucsi_ccg", "tegra-xudc",
                "tegra-bpmp-thermal", "pwm-tegra", "pwm-fan"}
    names = {Path(entry["name"]).stem for entry in modules}
    if not required <= names:
        raise ValueError("Required storage, USB, or thermal module is missing")
    updated = {}
    with tempfile.TemporaryDirectory(prefix="ark-initrd-depmod-") as directory:
        root = Path(directory)
        # Extract only known regular module files; never unpack devices or
        # follow archive symlinks as root in the build container.
        for entry in entries:
            name = entry["name"]
            if not name.startswith(module_prefix):
                continue
            mode = entry["fields"][1]
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode) or entry["fields"][4] != 1:
                raise ValueError(f"Unsupported module/config entry: {name}")
            if not re.fullmatch(r"[A-Za-z0-9_./+-]+", name):
                raise ValueError(f"Unsupported module/config filename: {name}")
            extracted_name = name[4:] if name.startswith("usr/lib/") else name
            path = root / extracted_name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(entry["payload"])
        (root / "etc/modprobe.d").mkdir(parents=True)
        run(["depmod", "--basedir", str(root), version])
        for module in sorted(names):
            dependencies = run(["modprobe", "--show-depends", "--ignore-install",
                                "--dirname", str(root), "--set-version", version,
                                "--config", str(root / "etc/modprobe.d"), module])
            for line in dependencies.splitlines():
                if line.startswith("insmod "):
                    dependency = Path(line.split()[1])
                    if not dependency.is_relative_to(root) or not dependency.is_file():
                        raise ValueError(f"Invalid module dependency: {line}")
        for path in sorted((root / "lib/modules" / version).glob("modules.*")):
            name = ("usr/" if usr_merged else "") + str(path.relative_to(root))
            if name not in by_name:
                raise ValueError(f"Unexpected new index: {name}")
            if path.read_bytes() != by_name[name]["payload"]:
                updated[name] = path.read_bytes()
        if f"{module_prefix}{version}/modules.dep.bin" not in updated:
            raise ValueError("Expected full-tree indexes to change for the initrd subset")
    checksum_entries = [entry for entry in module_entries
                        if stat.S_ISREG(entry["fields"][1])]
    checksums = "".join(hashlib.sha256(updated.get(entry["name"], entry["payload"])).hexdigest()
                        + "  " + entry["name"] + "\n"
                        for entry in sorted(checksum_entries, key=lambda item: item["name"]))
    replacement = f'''# {MARKER}: validate the final image before skipping runtime depmod.
cd /usr/sbin;
ln -s /bin/kmod depmod
cd /
if ! (
    [[ "${{version}}" == *"Linux version {version} "* ]] || exit 1
    shopt -s globstar nullglob
    ark_modules=(lib/modules/{version}/**/*.ko*)
    [ "${{#ark_modules[@]}}" -eq {len(modules)} ] || exit 1
    /bin/sha256sum --status -c /{CHECKSUM_FILE}
); then
    depmod -a
fi
'''.encode()
    updated["init"] = polled_init.replace(STOCK_DEPMOD, replacement)
    payload = bytearray()
    for entry in entries:
        if entry["name"] in updated:
            payload += encode_record(entry["archive_name"], entry["fields"], updated[entry["name"]])
        else:
            payload += entry["record"]
    # Unique inode, root-owned regular checksum file, stable timestamp.
    inode = max(entry["fields"][0] for entry in entries) + 1
    fields = [inode, stat.S_IFREG | 0o644, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0]
    payload += encode_record(CHECKSUM_FILE, fields, checksums.encode())
    payload += trailer
    payload += b"\0" * (-len(payload) % 512)
    output = gzip.compress(bytes(payload), compresslevel=9, mtime=0)
    checked, _ = read_newc(gzip.decompress(output))
    checked_by_name = {entry["name"]: entry for entry in checked}
    for entry in entries:
        if entry["name"] not in updated and checked_by_name[entry["name"]]["record"] != entry["record"]:
            raise ValueError(f"Unexpected change to an original archive record: {entry['name']}")
    if checked_by_name["init"]["payload"] != updated["init"]:
        raise ValueError("Final /init verification failed")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation preserves earlier experiments and the source backup.
    with destination.open("xb") as stream:
        stream.write(output)
    destination.chmod(stat.S_IMODE(source.stat().st_mode))
    print(f"Optimized R36.5 initrd for {version}: {len(modules)} unchanged modules")
    print(f"Root polling: {POLLING_MARKER}; immediate mount, 20 ms device checks, unchanged failure budgets")
    print(f"Init source SHA256: {hashlib.sha256(init['payload']).hexdigest()}")
    print(f"Init output SHA256: {hashlib.sha256(updated['init']).hexdigest()}")
    print(f"Input SHA256:  {hashlib.sha256(source_bytes).hexdigest()}")
    print(f"Output SHA256: {hashlib.sha256(output).hexdigest()}")
    print(f"Output: {destination} ({len(output)} bytes); original preserved")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="stock production initrd")
    parser.add_argument("--output", type=Path, required=True, help="new, separate optimized initrd")
    parser.add_argument("--l4t-release", type=Path, required=True, help="matching etc/nv_tegra_release")
    parser.add_argument("--kernel-image", type=Path, required=True, help="matching boot/Image")
    args = parser.parse_args()
    try:
        optimize(args)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f"optimize_initrd: {error}", file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError):
            print(error.stderr, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
