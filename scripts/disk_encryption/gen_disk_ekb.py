#!/usr/bin/env python3
"""Build or inspect a T234 EKB (bootloader/eks_t234.img) that carries a disk encryption key.

    gen_disk_ekb.py --disk-key disk.key --out eks_t234.img     # EKB holding just the disk key
    gen_disk_ekb.py --oem-k1 k1.key --auth-key auth.key [--disk-key disk.key] --out eks_t234.img
                                                               # for a module with OEM_K1 burned
    gen_disk_ekb.py --dump eks_t234.img [--oem-k1 k1.key]      # verify the MAC and list the entries

OP-TEE's luks-srv TA derives each LUKS passphrase from the EKB's disk encryption key (tag 2), so an
image encrypted with `flash.sh -i disk.key` only unlocks on a module whose EKS partition carries that
same key. The BSP's own eks_t234.img carries no entries at all, so without this nothing unlocks.

This writes the layout NVIDIA's gen_ekb.py (OP-TEE samples/hwkey-agent/host/tool/gen_ekb) produces,
EKB v2.0, holding only the keys asked for: none of them are in the stock EKB. It exists because
gen_ekb.py is not in the BSP or in ARK's flash packages, needs pycryptodome, and stops at an
interactive prompt when no UEFI variable authentication key is given.

The EKB root key comes from OEM_K1, which is all zeros on a module whose fuses have not been burned.
Once OEM_K1 is burned the EKB must be built with it (--oem-k1), and must also carry the UEFI variable
authentication key (--auth-key): NVIDIA's UEFI refuses to boot a K1-fused module without one.
"""
import argparse
import os
import struct
import sys
from math import ceil

from cryptography.hazmat.primitives import cmac
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

EKB_MAGIC = b"NVEKBP\0\0"
CONTENT_MAGIC = b"EEKB"
VERSION = (2, 0)
MIN_SIZE = 1024
TAG_DISK_ENCRYPTION = 2
TAG_UEFI_VAR_AUTH = 4
TAG_NAMES = {1: "UEFI payload encryption", 2: "disk encryption", 4: "UEFI variable authentication"}


def nist_sp_800_108_cmac(key, context, label, length=16):
    out = b""
    for counter in range(ceil(length / 16)):
        mac = cmac.CMAC(algorithms.AES(key))
        mac.update(bytes([counter + 1]) + label.encode() + b"\0" + context.encode() + (length * 8).to_bytes(4, "big"))
        out += mac.finalize()
    return out[:length]


def ekb_keys(oem_k1, fv):
    encryptor = Cipher(algorithms.AES(oem_k1), modes.ECB()).encryptor()
    root_key = encryptor.update(fv) + encryptor.finalize()
    return nist_sp_800_108_cmac(root_key, "ekb", "encryption"), nist_sp_800_108_cmac(root_key, "ekb", "authentication")


def cmac_of(key, data):
    mac = cmac.CMAC(algorithms.AES(key))
    mac.update(data)
    return mac.finalize()


def read_hex_key(path, size):
    text = open(path).read().strip()
    if text[:2].lower() == "0x":
        text = text[2:]
    key = bytes.fromhex(text)
    if len(key) != size:
        sys.exit(f"ERROR: {path} holds {len(key)} bytes; expected {size} as hex")
    return key


def build(entries, oem_k1):
    fv = os.urandom(16)
    iv = os.urandom(16)
    encryption_key, authentication_key = ekb_keys(oem_k1, fv)
    # gen_ekb.py's entry order: kernel (1), disk (2), UEFI variable authentication (4).
    content = b"".join(struct.pack("<II", tag, len(key)) + key for tag, key in sorted(entries.items()))
    content += b"\0" * 8
    if len(content) % 16:
        pad = 16 - len(content) % 16
        content += bytes([pad]) * pad  # PKCS#7, only when unaligned, as gen_ekb.py pads
    encryptor = Cipher(algorithms.AES(encryption_key), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(content) + encryptor.finalize()
    content_header = struct.pack("<I4s8x", len(ciphertext), CONTENT_MAGIC)
    mac = cmac_of(authentication_key, content_header + iv + ciphertext)
    body = fv + mac + content_header + iv + ciphertext
    # gen_ekb.py pads short blobs to 1 KiB after the MAC; OP-TEE rejects anything smaller.
    size = 12 + len(body)
    if size < MIN_SIZE:
        body += bytes([(MIN_SIZE - size) % 256]) * (MIN_SIZE - size)
        size = MIN_SIZE
    return struct.pack("<I8sHH", size, EKB_MAGIC, *VERSION) + body


def dump(path, oem_k1):
    blob = open(path, "rb").read()
    size, magic, major, minor = struct.unpack_from("<I8sHH", blob, 0)
    if magic != EKB_MAGIC or (major, minor) != VERSION:
        sys.exit(f"ERROR: {path} is not an EKB v2.0 (magic {magic!r}, version {major}.{minor})")
    fv, mac = blob[16:32], blob[32:48]
    content_size, content_magic = struct.unpack_from("<I4s", blob, 48)
    if content_magic != CONTENT_MAGIC:
        sys.exit(f"ERROR: {path} has content magic {content_magic!r}")
    iv, ciphertext = blob[64:80], blob[80:80 + content_size]
    encryption_key, authentication_key = ekb_keys(oem_k1, fv)
    if cmac_of(authentication_key, blob[48:80 + content_size]) != mac:
        sys.exit(f"ERROR: {path} fails its MAC check under this OEM_K1; OP-TEE would reject it")
    decryptor = Cipher(algorithms.AES(encryption_key), modes.CBC(iv)).decryptor()
    content = decryptor.update(ciphertext) + decryptor.finalize()
    print(f"{path}: EKB v{major}.{minor}, {size} bytes, MAC OK")
    offset, entries = 0, 0
    while offset + 8 <= len(content):
        tag, length = struct.unpack_from("<II", content, offset)
        offset += 8
        if tag == 0 and length == 0:
            break
        entries += 1
        print(f"  tag {tag}: {TAG_NAMES.get(tag, 'unknown')}, {length} bytes")
        offset += length
    if entries == 0:
        print("  no entries")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--disk-key", help="16-byte disk encryption key, hex (the key flash.sh -i takes)")
    parser.add_argument("--auth-key", help="16-byte UEFI variable authentication key, hex")
    parser.add_argument("--out", help="EKB image to write")
    parser.add_argument("--dump", metavar="EKB", help="verify and list an existing EKB instead")
    parser.add_argument("--oem-k1", help="32-byte OEM_K1 fuse key, hex (default: all zeros, an unfused module)")
    args = parser.parse_args()

    oem_k1 = read_hex_key(args.oem_k1, 32) if args.oem_k1 else bytes(32)
    if args.dump:
        dump(args.dump, oem_k1)
    elif args.out and (args.disk_key or args.auth_key):
        entries = {}
        if args.disk_key:
            entries[TAG_DISK_ENCRYPTION] = read_hex_key(args.disk_key, 16)
        if args.auth_key:
            entries[TAG_UEFI_VAR_AUTH] = read_hex_key(args.auth_key, 16)
        with open(args.out, "wb") as out:
            out.write(build(entries, oem_k1))
    else:
        parser.error("give --out and at least one of --disk-key and --auth-key, or --dump")


if __name__ == "__main__":
    main()
