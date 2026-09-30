#!/bin/bash
set -euo pipefail

# Generate a secure boot key set for ARK Jetson carriers (Orin Nano / Orin NX), in the
# layout ./flash.sh --secure-boot / --uefi-secure-boot and flash_from_package.sh take.
#
# Usage: scripts/secure_boot/gen_secure_boot_keys.sh <Linux_for_Tegra dir> <new key dir>
#
#   pkc.pem        ECDSA P-521 key that signs the boot chain (flash -u)
#   sbk.key        32-byte Secure Boot Key that encrypts the boot chain (flash -v)
#   oem_k1.key     32-byte OEM_K1: once burned, the root key of the EKB
#   uefi_auth.key  16-byte UEFI variable authentication key, stored in the EKB
#   uefi/          UEFI Secure Boot PK, KEK and db_1 keys, certificates and signature
#                  lists, and the uefi_keys.conf NVIDIA's flash.sh --uefi-keys reads
#   fuses.xml      the fuse configuration that burns pkc.pem's hash, sbk.key and
#                  oem_k1.key and then locks the module (see docs/secure_boot.md)
#
# The L4T tree (a staged staging/<TARGET>/Linux_for_Tegra or an extracted flash package)
# is only needed for NVIDIA's tegrasign_v3.py, which computes the PublicKeyHash fuse
# value. That value is not a plain hash of the key file, and a wrong one burned into a
# module can never be corrected, so it comes from NVIDIA's tool and nothing else.
#
# The certificates are self-signed, as in NVIDIA's examples. The keys are files on this
# host: for production, generate and keep them the way your security process requires
# (NVIDIA recommends an HSM). Whoever holds pkc.pem and sbk.key can sign images that a
# fused module will run.

if [ $# -ne 2 ]; then
    echo "Usage: $0 <Linux_for_Tegra dir> <new key dir>" >&2
    exit 1
fi
L4T_DIR="$(readlink -f "$1")"
OUT_DIR="$2"

TEGRASIGN="$L4T_DIR/bootloader/tegrasign_v3.py"
if [ ! -f "$TEGRASIGN" ]; then
    echo "ERROR: $TEGRASIGN not found; pass a Linux_for_Tegra directory." >&2
    exit 1
fi
for tool in openssl cert-to-efi-sig-list uuidgen python3; do
    if ! command -v "$tool" > /dev/null; then
        echo "ERROR: $tool not found. sudo apt-get install openssl efitools uuid-runtime python3" >&2
        exit 1
    fi
done
if [ -e "$OUT_DIR" ]; then
    echo "ERROR: $OUT_DIR already exists; refusing to overwrite keys." >&2
    exit 1
fi

umask 077
mkdir -p "$OUT_DIR/uefi"
OUT_DIR="$(readlink -f "$OUT_DIR")"
cd "$OUT_DIR"

hex_key() { python3 -c "import secrets, sys; print(secrets.token_hex(int(sys.argv[1])))" "$1"; }

echo "Generating the boot chain keys..."
openssl ecparam -name secp521r1 -genkey -noout -out pkc.pem
SBK_HEX="$(hex_key 32)"
# flash.sh -v wants the SBK as eight big-endian 32-bit words.
echo "$SBK_HEX" | sed -E 's/(.{8})/0x\1 /g; s/ $//' > sbk.key
OEM_K1_HEX="$(hex_key 32)"
echo "$OEM_K1_HEX" > oem_k1.key
hex_key 16 > uefi_auth.key

echo "Computing the PublicKeyHash fuse value with NVIDIA's tegrasign_v3.py..."
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
# tegrasign writes scratch files into its working directory; keep them out of the tree.
SIGN_OUT="$(cd "$WORK" && python3 "$TEGRASIGN" --pubkeyhash "$WORK/pkc.pubkey" "$WORK/pkc.hash" --key "$OUT_DIR/pkc.pem")"
PKC_HASH="$(echo "$SIGN_OUT" | sed -n 's/.*tegra-fuse format (big-endian): 0x\([0-9a-fA-F]*\).*/\1/p')"
if ! [[ "$PKC_HASH" =~ ^[0-9a-fA-F]{128}$ ]] || [ "$PKC_HASH" != "$(xxd -p -c 256 "$WORK/pkc.hash")" ]; then
    echo "ERROR: tegrasign_v3.py gave no consistent 64-byte PublicKeyHash:" >&2
    echo "$SIGN_OUT" >&2
    exit 1
fi

echo "Generating the UEFI Secure Boot keys..."
GUID="$(uuidgen)"
for name in PK KEK db_1; do
    openssl req -newkey rsa:2048 -nodes -keyout "uefi/$name.key" -new -x509 -sha256 -days 3650 \
        -subj "/CN=ARK Jetson UEFI $name/" -out "uefi/$name.crt" 2> /dev/null
    cert-to-efi-sig-list -g "$GUID" "uefi/$name.crt" "uefi/$name.esl"
done
cat > uefi/uefi_keys.conf <<'EOF'
UEFI_DB_1_KEY_FILE="db_1.key";
UEFI_DB_1_CERT_FILE="db_1.crt";
UEFI_DEFAULT_PK_ESL="PK.esl"
UEFI_DEFAULT_KEK_ESL_0="KEK.esl"
UEFI_DEFAULT_DB_ESL_0="db_1.esl"
EOF

# NVIDIA's documented Orin example for an ECDSA P-521 key plus SBK plus OemK1, in its
# order: the keys first, BootSecurityInfo (0x3 P-521 | 0x8 SBK | 0x200 OemK1) after them,
# SecurityMode last, because once it is burned no further fuse can be written.
cat > fuses.xml <<EOF
<genericfuse MagicId="0x45535546" version="1.0.0">
    <fuse name="PublicKeyHash" size="64" value="0x$PKC_HASH"/>
    <fuse name="SecureBootKey" size="32" value="0x$SBK_HEX"/>
    <fuse name="OemK1" size="32" value="0x$OEM_K1_HEX"/>
    <fuse name="BootSecurityInfo" size="4" value="0x20b"/>
    <fuse name="SecurityMode" size="4" value="0x1"/>
</genericfuse>
EOF

echo ""
echo "Secure boot keys written to $OUT_DIR:"
ls -l "$OUT_DIR" "$OUT_DIR/uefi"
echo ""
echo "Back this directory up and keep it private. A module fused with fuses.xml boots only"
echo "images signed with pkc.pem and sbk.key; lose them and the module can never be reflashed."
