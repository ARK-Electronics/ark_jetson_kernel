#!/usr/bin/env python3
"""Synchronize prepared AArch64 headers with a completed custom kernel build.

Keep the BSP's native ARM build tools and its release-suffixed Makefile. Replace
public/generated headers, configuration and symbol versions as one directory
transaction; never copy host executables from a cross-compiled source tree.
"""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

TREES = ("include", "arch/arm64/include")
FILES = (".config", "Module.symvers", "scripts/module.lds")
TOOLS = ("scripts/basic/fixdep", "scripts/mod/modpost", "scripts/genksyms/genksyms")
REQUIRED = ("include/config/kernel.release", "include/config/auto.conf",
            "include/generated/autoconf.h", "include/generated/utsrelease.h",
            "include/soc/tegra/bpmp.h", "include/soc/tegra/mc.h")
MARKER = ".ark-prepared-headers.json"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def config_values(text):
    return dict(re.findall(r"^(CONFIG_[A-Za-z0-9_]+)=(.*)$", text, re.M))


def source_snapshot(root):
    paths = []
    for name in TREES:
        directory = root / name
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError(f"Expected real header directory: {name}")
        paths.extend(directory.rglob("*"))
    paths.extend(root / name for name in FILES)
    result = {}
    allowed = [root / name for name in TREES]
    for path in sorted(paths):
        relative = str(path.relative_to(root))
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            link = os.readlink(path)
            resolved = path.resolve(strict=True)
            if Path(link).is_absolute() or not resolved.is_file() or not any(resolved.is_relative_to(p) for p in allowed):
                raise ValueError(f"Header symlink escapes synchronized trees: {relative}")
            result[relative] = "link:" + link
        elif stat.S_ISDIR(info.st_mode):
            continue
        elif stat.S_ISREG(info.st_mode):
            data = path.read_bytes()
            if data.startswith(b"\x7fELF"):
                raise ValueError(f"Refusing an ELF executable in synchronized headers: {relative}")
            result[relative] = f"file:{stat.S_IMODE(info.st_mode):o}:" + digest(data)
        else:
            raise ValueError(f"Unsupported header file type: {relative}")
    return result


def validate_source(source):
    for name in (*FILES, *REQUIRED):
        path = source / name
        if path.is_symlink() or not path.is_file() or not path.stat().st_size:
            raise ValueError(f"Missing completed kernel build input: {name}")
    release = (source / REQUIRED[0]).read_text().strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+[-+A-Za-z0-9_.]*", release):
        raise ValueError("Invalid built kernel release")
    config = config_values((source / ".config").read_text())
    if config.get("CONFIG_ARM64") != "y" or config.get("CONFIG_MODULES") != "y":
        raise ValueError("Expected an AArch64 kernel with module support")
    # auto.conf emits Kconfig string values without the .config quotes.
    make_config = {key: json.loads(value) if value.startswith('"') else value
                   for key, value in config.items()}
    if make_config != config_values((source / "include/config/auto.conf").read_text()):
        raise ValueError("Built .config and generated auto.conf disagree")
    defines = dict(re.findall(r"^#define (CONFIG_[A-Za-z0-9_]+) (.*)$",
                             (source / "include/generated/autoconf.h").read_text(), re.M))
    expected = {key + ("_MODULE" if value == "m" else ""):
                "1" if value in ("m", "y") else value for key, value in config.items()}
    if defines != expected:
        raise ValueError("Built .config and generated autoconf.h disagree")
    if (source / "include/generated/utsrelease.h").read_text().strip() != f'#define UTS_RELEASE "{release}"':
        raise ValueError("Generated UTS_RELEASE disagrees with kernel.release")
    return release


def native_tools(headers):
    result = {}
    for name in TOOLS:
        path = headers / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Missing native AArch64 header tool: {name}")
        data = path.read_bytes()
        if len(data) < 64 or data[:6] != b"\x7fELF\x02\x01" or int.from_bytes(data[18:20], "little") != 183:
            raise ValueError(f"Header tool is not native AArch64 ELF: {name}")
        result[name] = digest(data)
    return result


def validate_makefile(source, headers, release):
    # NVIDIA embeds the distro ABI suffix here so on-target external builds
    # retain it without the outer cross-build's LOCALVERSION environment.
    def normalized(path):
        data, count = re.subn(r"(?m)^EXTRAVERSION\s*=.*$", "EXTRAVERSION =", path.read_text())
        if count != 1:
            raise ValueError("Expected one kernel EXTRAVERSION assignment")
        return data
    if normalized(source / "Makefile") != normalized(headers / "Makefile"):
        raise ValueError("Prepared header Makefile differs beyond its BSP release suffix")
    text = (headers / "Makefile").read_text()
    version = []
    for key in ("VERSION", "PATCHLEVEL", "SUBLEVEL"):
        values = re.findall(rf"^{key}\s*=\s*([0-9]+)\s*$", text, re.M)
        if len(values) != 1:
            raise ValueError(f"Invalid prepared Makefile {key}")
        version.append(values[0])
    extra = re.search(r"^EXTRAVERSION\s*=(.*)$", text, re.M).group(1).strip()
    local = json.loads(config_values((source / ".config").read_text()).get("CONFIG_LOCALVERSION", '""'))
    if ".".join(version) + extra + local != release:
        raise ValueError("Prepared Makefile suffix does not reproduce the built kernel release")


def exchange_directories(first, second):
    libc = ctypes.CDLL(None, use_errno=True)
    exchange = libc.renameat2
    exchange.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    exchange.restype = ctypes.c_int
    if exchange(-100, os.fsencode(first), -100, os.fsencode(second), 2):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def synchronize(kernel_source, headers_dir, verify=False):
    source, headers = kernel_source.resolve(strict=True), headers_dir.resolve(strict=True)
    if source.is_relative_to(headers) or headers.is_relative_to(source):
        raise ValueError("Source and installed headers must be separate trees")
    release = validate_source(source)
    if (headers / "include/config/kernel.release").read_text().strip() != release:
        raise ValueError("Installed headers do not match the built kernel release")
    tools = native_tools(headers)
    validate_makefile(source, headers, release)
    expected = source_snapshot(source)
    current = source_snapshot(headers)
    makefile = (headers / "Makefile").read_bytes()
    fingerprint = digest(json.dumps(expected, sort_keys=True).encode())
    if current == expected:
        return {"release": release, "files": len(expected), "source_digest": fingerprint, "changed": False}
    if verify:
        raise ValueError("Prepared headers differ from the completed custom kernel build")

    with tempfile.TemporaryDirectory(prefix=".ark-headers-", dir=headers.parent) as work:
        prepared = Path(work) / "prepared"
        shutil.copytree(headers, prepared, symlinks=True)
        for name in TREES:
            target = prepared / name
            shutil.rmtree(target)
            shutil.copytree(source / name, target, symlinks=True)
        for name in FILES:
            shutil.copy2(source / name, prepared / name)
        if source_snapshot(prepared) != expected or native_tools(prepared) != tools:
            raise ValueError("Prepared headers or preserved native tools failed verification")
        if source_snapshot(source) != expected or validate_source(source) != release:
            raise ValueError("Kernel sources changed during header staging; destination untouched")
        if (source_snapshot(headers) != current or native_tools(headers) != tools or
                (headers / "Makefile").read_bytes() != makefile):
            raise ValueError("Installed headers changed during staging; external changes preserved")
        marker = {"schema_version": 1, "release": release, "source_digest": fingerprint,
                  "synchronized_files": len(expected), "native_tool_sha256": tools}
        (prepared / MARKER).write_text(json.dumps(marker, indent=2, sort_keys=True) + "\n")
        # Atomic exchange publishes the complete tree. The old tree stays in
        # our private directory until this operation succeeds, then is cleaned.
        exchange_directories(prepared, headers)
    return {"release": release, "files": len(expected), "source_digest": fingerprint, "changed": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel-source", type=Path, required=True)
    parser.add_argument("--headers-dir", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    try:
        result = synchronize(args.kernel_source, args.headers_dir, args.verify)
    except (OSError, ValueError) as error:
        parser.exit(1, f"ERROR: {error}\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
