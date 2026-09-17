#!/usr/bin/env python3
"""Keep opted-in initrd indexes current when NVIDIA prepares flash images.

Install a self-contained wrapper in the staged BSP. It always runs the audited
vendor updater, then reoptimizes its result with the staged AArch64 kmod. No
module or host-rootfs index is modified by the optimization step.
"""
import argparse
import fcntl
from functools import wraps
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile

import optimize_initrd as optimizer

UPDATER_HASHES = {
    '36.5.0': 'ab45dfcf3a1f44fdab841eff4823e3c29073389a27e48c326df89888a8d55b53',
    '39.2.1': 'b8b326a5f0eabe332a37c250bb9f56a7aef752230fbdd7327727d31045788ec2',
}
DIRECTORY = 'tools/ark-initrd'
UPDATER = 'tools/l4t_update_initrd.sh'
VENDOR = 'tools/l4t_update_initrd.vendor.sh'
IMAGES = ('bootloader/l4t_initrd.img', 'rootfs/boot/initrd')
SCRIPTS = ('prepare_flash_initrd.py', 'optimize_initrd.py')
WRAPPER = b'''#!/bin/bash
# ARK_FLASH_INITRD_V1: refresh vendor modules before recomputing boot indexes.
set -e
ark_tools_dir="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$ark_tools_dir/ark-initrd/prepare_flash_initrd.py" run --l4t-dir "$ark_tools_dir/.." -- "$@"
'''


def digest(data):
    return hashlib.sha256(data).hexdigest()


def regular(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'Expected a regular file: {path}')
    return path.read_bytes()


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def serialized(operation):
    @wraps(operation)
    def locked(l4t, *args):
        # Outside the removable helper directory: install/remove and refresh
        # must serialize even before the recovery guard has been published.
        descriptor = os.open(l4t / 'tools/.ark-initrd.lock',
                             os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return operation(l4t, *args)
    return locked


def replace(data, path, mode=0o644, owner=None):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.ark-initrd-', dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            if owner is not None:
                os.fchown(stream.fileno(), *owner)
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        temporary.replace(path)
        sync_directory(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def release_for(l4t):
    return optimizer.release_policy((l4t / 'rootfs/etc/nv_tegra_release').read_text())[0]


def depmod_guard(version, count):
    # Must match the existing optimizer's complete generated block. Any edit
    # outside that block must still reconstruct the exact audited stock /init.
    return f'''# {optimizer.MARKER}: validate the final image before skipping runtime depmod.
cd /usr/sbin;
ln -s /bin/kmod depmod
cd /
if ! (
    [[ "${{version}}" == *"Linux version {version} "* ]] || exit 1
    shopt -s globstar nullglob
    ark_modules=(lib/modules/{version}/**/*.ko*)
    [ "${{#ark_modules[@]}}" -eq {count} ] || exit 1
    /bin/sha256sum --status -c /{optimizer.CHECKSUM_FILE}
); then
    depmod -a
fi
'''.encode()


def stock_archive(image, release):
    """Undo only our exact transformations, preserving refreshed module records."""
    entries, trailer = optimizer.read_newc(gzip.decompress(image))
    by_name = {entry['name']: entry for entry in entries}
    init = by_name['init']
    checksum = by_name[optimizer.CHECKSUM_FILE]
    for entry in (init, checksum):
        if not stat.S_ISREG(entry['fields'][1]) or entry['fields'][4] != 1:
            raise ValueError('Expected ordinary initrd init/checksum files')
    data = init['payload']
    pattern = (rb'(?m)^# ' + optimizer.MARKER.encode() +
               rb': validate the final image before skipping runtime depmod\.\n.*?^fi\n')
    blocks = list(re.finditer(pattern, data, re.S | re.M))
    if len(blocks) != 1:
        raise ValueError('Expected one complete ARK depmod guard')
    block = blocks[0].group()
    version = re.search(rb'Linux version ([0-9A-Za-z_.+\-]+) ', block)
    count = re.search(rb'-eq ([0-9]+) \]', block)
    if not version or not count or depmod_guard(version[1].decode(), int(count[1])) != block:
        raise ValueError('Unreviewed ARK depmod guard')
    stock = data.replace(block, optimizer.STOCK_DEPMOD)
    for fast, original in ((optimizer.FAST_MOUNT_LOOP, optimizer.STOCK_MOUNT_LOOP),
                           (optimizer.FAST_DEVICE_POLL, optimizer.STOCK_DEVICE_POLL)):
        if stock.count(fast) != 1:
            raise ValueError('Expected the complete audited polling transformations')
        stock = stock.replace(fast, original)
    # This hashes the entire reconstructed script, not just selected anchors.
    optimizer.optimize_root_polling(stock, release)
    payload = b''.join(optimizer.encode_record(entry['archive_name'], entry['fields'], stock)
                       if entry['name'] == 'init' else entry['record']
                       for entry in entries if entry['name'] != optimizer.CHECKSUM_FILE)
    payload += trailer
    payload += b'\0' * (-len(payload) % 512)
    return gzip.compress(payload, compresslevel=9, mtime=0)


def verify(l4t):
    directory = l4t / DIRECTORY
    if (directory / 'in-progress.json').exists():
        raise ValueError('Interrupted initrd refresh; inspect tools/ark-initrd recovery files before flashing')
    manifest = json.loads(regular(directory / 'manifest.json'))
    release = release_for(l4t)
    if manifest.get('schema_version') != 1 or manifest.get('release') != release:
        raise ValueError('Initrd wrapper release mismatch')
    if regular(l4t / UPDATER) != WRAPPER or digest(regular(l4t / VENDOR)) != UPDATER_HASHES[release]:
        raise ValueError('Staged initrd wrapper/vendor updater changed')
    if set(manifest.get('scripts', {})) != set(SCRIPTS):
        raise ValueError('Unexpected staged initrd helper manifest')
    for name, checksum in manifest['scripts'].items():
        if digest(regular(directory / name)) != checksum:
            raise ValueError(f'Staged initrd helper changed: {name}')
    return manifest


@serialized
def install(l4t):
    release = release_for(l4t)
    directory = l4t / DIRECTORY
    if directory.exists():
        manifest = verify(l4t)
        for name in SCRIPTS:
            if manifest['scripts'][name] != digest(regular(Path(__file__).parent / name)):
                raise ValueError('Staged initrd helper version differs; remove the verified old wrapper first')
        return
    original = regular(l4t / UPDATER)
    if digest(original) != UPDATER_HASHES[release] or (l4t / VENDOR).exists():
        raise ValueError('Unaudited vendor updater or existing vendor backup')
    images = [regular(l4t / name) for name in IMAGES]
    if images[0] != images[1]:
        raise ValueError('Production and base initrd copies differ')
    stock_archive(images[0], release)
    directory.mkdir()
    # Publish the wrapper last. A partial install leaves the original updater
    # intact and is rejected by the next install attempt.
    for name in SCRIPTS:
        replace(regular(Path(__file__).parent / name), directory / name, 0o755)
    replace(original, l4t / VENDOR, 0o755)
    manifest = {'schema_version': 1, 'release': release,
                'scripts': {name: digest(regular(directory / name)) for name in SCRIPTS}}
    replace((json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode(), directory / 'manifest.json')
    replace(WRAPPER, l4t / UPDATER, 0o755)
    verify(l4t)


@serialized
def remove(l4t):
    directory = l4t / DIRECTORY
    if not directory.exists():
        return
    verify(l4t)
    replace(regular(l4t / VENDOR), l4t / UPDATER, 0o755)
    (l4t / VENDOR).unlink()
    shutil.rmtree(directory)


def reoptimize(l4t, image, output, work):
    qemu = shutil.which('qemu-aarch64-static')
    if qemu is None:
        raise ValueError('qemu-aarch64-static is required by the flash initrd hook')
    rootfs = l4t / 'rootfs'
    kmod = rootfs / 'usr/bin/kmod'
    binary = regular(kmod)
    if len(binary) < 64 or binary[:6] != b'\x7fELF\x02\x01' or int.from_bytes(binary[18:20], 'little') != 183:
        raise ValueError('Staged kmod must be AArch64 ELF')
    tools = work / 'bin'
    tools.mkdir()
    for name in ('depmod', 'modprobe'):
        # kmod dispatches these tools by argv[0], not by a subcommand.
        command = [qemu, '-0', name, '-L', str(rootfs), str(kmod)]
        (tools / name).write_text('#!/bin/sh\nexec ' + shlex.join(command) + ' "$@"\n')
        (tools / name).chmod(0o755)
    subprocess.run([sys.executable, str(Path(__file__).with_name('optimize_initrd.py')),
                    '--input', str(image), '--output', str(output),
                    '--l4t-release', str(rootfs / 'etc/nv_tegra_release'),
                    '--kernel-image', str(rootfs / 'boot/Image')], check=True,
                   env={**os.environ, 'PATH': str(tools) + os.pathsep + os.environ.get('PATH', '')})


def vendor_arguments(l4t, release, arguments):
    if arguments in (['-h'], ['--help']):
        return arguments
    if release == '36.5.0':
        if arguments:
            if len(arguments) == 2 and arguments[0] in ('-l', '--ldk_dir'):
                directory = arguments[1]
            elif len(arguments) == 1 and arguments[0].startswith('--ldk_dir='):
                directory = arguments[0].split('=', 1)[1]
            else:
                raise ValueError('Unsupported R36 updater arguments')
            if Path(directory).resolve() != l4t:
                raise ValueError('Vendor updater BSP path differs from the installed wrapper')
        return ['-l', str(l4t)]
    if arguments:
        if len(arguments) == 2 and arguments[0] in ('-f', '--list-files'):
            group = arguments[1]
        elif len(arguments) == 1 and arguments[0].startswith('--list-files='):
            group = arguments[0].split('=', 1)[1]
        else:
            raise ValueError('Unsupported R39 updater arguments')
        if not re.fullmatch(r'[A-Za-z0-9_.,+-]+', group):
            raise ValueError('Unsupported initrd list-file group')
    return arguments


@serialized
def refresh(l4t, vendor_args):
    manifest = verify(l4t)
    vendor_args = vendor_arguments(l4t, manifest['release'], vendor_args)
    if vendor_args in (['-h'], ['--help']):
        subprocess.run([str(l4t / VENDOR), *vendor_args], check=True)
        return
    directory = l4t / DIRECTORY
    images = [regular(l4t / name) for name in IMAGES]
    metadata = [(stat.S_IMODE((l4t / name).stat().st_mode),
                 ((l4t / name).stat().st_uid, (l4t / name).stat().st_gid)) for name in IMAGES]
    if images[0] != images[1]:
        raise ValueError('Production and base initrd copies differ before refresh')
    stock_archive(images[0], manifest['release'])
    if regular(l4t / 'kernel/Image') != regular(l4t / 'rootfs/boot/Image'):
        raise ValueError('Kernel Image copies differ before initrd refresh')
    # Durable originals precede the durable guard. A hard interruption leaves
    # both for explicit recovery; a later invocation refuses silent reuse.
    work = Path(tempfile.mkdtemp(prefix='recovery-', dir=directory))
    for index, data in enumerate(images):
        replace(data, work / f'original-{index}.img')
    guard = directory / 'in-progress.json'
    replace((json.dumps({'recovery_directory': work.name, 'image_metadata': metadata,
                         'original_sha256': digest(images[0])}) + '\n').encode(), guard)
    try:
        subprocess.run([str(l4t / VENDOR), *vendor_args], check=True)
        updated = [regular(l4t / name) for name in IMAGES]
        if updated[0] != updated[1]:
            raise ValueError('Vendor updater left unequal initrd copies')
        source = work / 'refreshed-stock.img'
        source.write_bytes(stock_archive(updated[0], manifest['release']))
        output = work / 'optimized.img'
        reoptimize(l4t, source, output, work)
        result = regular(output)
        stock_archive(result, manifest['release'])
        for name, (mode, owner) in zip(IMAGES, metadata):
            replace(result, l4t / name, mode, owner)
        if regular(l4t / IMAGES[0]) != regular(l4t / IMAGES[1]):
            raise ValueError('Published initrd copies differ')
        replace((json.dumps({'schema_version': 1, 'initrd_sha256': digest(result),
                             'kernel_sha256': digest(regular(l4t / 'kernel/Image'))},
                            sort_keys=True) + '\n').encode(), directory / 'last-refresh.json')
    except Exception:
        # Abort image generation and restore bytes, ownership and modes. Leave
        # the guard and recovery files if rollback itself cannot complete.
        for name, data, (mode, owner) in zip(IMAGES, images, metadata):
            replace(data, l4t / name, mode, owner)
        guard.unlink()
        sync_directory(directory)
        shutil.rmtree(work)
        raise
    guard.unlink()
    sync_directory(directory)
    shutil.rmtree(work)


def main():
    arguments = sys.argv[1:]
    extra = []
    if '--' in arguments:
        index = arguments.index('--')
        arguments, extra = arguments[:index], arguments[index + 1:]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('install', 'remove', 'verify', 'run'))
    parser.add_argument('--l4t-dir', type=Path, required=True)
    args = parser.parse_args(arguments)
    if extra and args.mode != 'run':
        parser.error('Vendor arguments are accepted only by run')
    try:
        l4t = args.l4t_dir.resolve(strict=True)
        if args.mode == 'run':
            refresh(l4t, extra)
        else:
            globals()[args.mode](l4t)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'ERROR: flash initrd hook: {error}\n')


if __name__ == '__main__':
    main()
