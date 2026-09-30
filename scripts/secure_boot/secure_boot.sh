# Secure boot for ARK Jetson carriers (Orin Nano / Orin NX on NVMe).
#
# Sourced by flash.sh, and by packaging/flash_from_package.sh from the copy that
# generate_flash_package.sh bundles into each package under Linux_for_Tegra/ark/.
# Takes a key directory made by gen_secure_boot_keys.sh. See docs/secure_boot.md.
#
# Two modes:
#   uefi  UEFI Secure Boot only. UEFI checks L4TLauncher, the kernel, its DTB, the initrd
#         and extlinux.conf against the db key before running them. No fuses: a plain
#         reflash turns it off again, so it is the mode to develop against. It stops a
#         tampered /boot, but not someone who can put the module in recovery and reflash
#         its QSPI.
#   full  The whole chain, for a module fused with the key directory's fuses.xml. The
#         BootROM checks the boot chain against the burned key hash and decrypts it with
#         the burned SBK, UEFI Secure Boot runs on top, and the EKB is built with the
#         burned OEM_K1 and carries the UEFI variable authentication key, without which
#         UEFI will not boot a K1-fused module.
# NVIDIA's flash.sh reads the module's fuses and refuses a mismatch: signing keys for an
# unfused module, or none for a fused one.

ARK_SB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The EKB helpers and the arrays the encrypted flash reads.
# shellcheck source-path=SCRIPTDIR source=../disk_encryption/disk_encryption.sh
source "$ARK_SB_DIR/../disk_encryption/disk_encryption.sh"

SB_MODE=""
SB_KEYS_DIR=""
SB_WORK_DIR=""
SB_DTBO_ADDED=""
SB_FLASH_ARGS=()
SB_EKB_ARGS=()

sb_check_host() {
    local missing=()
    command -v sbsign > /dev/null || missing+=(sbsigntool)
    command -v dtc > /dev/null || missing+=(device-tree-compiler)
    command -v openssl > /dev/null || missing+=(openssl)
    python3 -c 'import cryptography' 2>/dev/null || missing+=(python3-cryptography)
    if [ ${#missing[@]} -gt 0 ]; then
        echo "ERROR: a secure boot flash needs ${missing[*]} on this host:" >&2
        echo "       sudo apt-get install ${missing[*]}" >&2
        return 1
    fi
}

# $1 mode (uefi | full), $2 key directory.
sb_load() {
    local mode="$1" dir="$2" file required
    case "$mode" in
        uefi) required=(uefi/uefi_keys.conf uefi/db_1.key uefi/db_1.crt uefi/PK.esl uefi/KEK.esl uefi/db_1.esl) ;;
        full) required=(pkc.pem sbk.key oem_k1.key uefi_auth.key
                        uefi/uefi_keys.conf uefi/db_1.key uefi/db_1.crt uefi/PK.esl uefi/KEK.esl uefi/db_1.esl) ;;
        *) echo "ERROR: unknown secure boot mode '$mode'." >&2; return 1 ;;
    esac
    if [ ! -d "$dir" ]; then
        echo "ERROR: secure boot key directory $dir not found." >&2
        echo "       Make one with scripts/secure_boot/gen_secure_boot_keys.sh." >&2
        return 1
    fi
    dir="$(readlink -f "$dir")"
    for file in "${required[@]}"; do
        if [ ! -f "$dir/$file" ]; then
            echo "ERROR: $dir/$file is missing; $mode secure boot needs it." >&2
            return 1
        fi
    done
    SB_MODE="$mode"
    SB_KEYS_DIR="$dir"
}

# Build UefiDefaultSecurityKeys.dtbo, which enrolls PK, KEK and db on the first boot, and
# set the flasher arguments. It is built in a scratch copy of uefi/, because NVIDIA's
# gen_uefi_keys_dts.sh writes its output next to the conf and runs as root, and the key
# directory stays exactly as the user keeps it. $1 L4T dir.
sb_prepare() {
    local l4t="$1"
    SB_WORK_DIR="$(mktemp -d)" || return 1
    cp -r "$SB_KEYS_DIR/uefi/." "$SB_WORK_DIR/" || return 1
    (cd "$l4t" && sudo ./tools/gen_uefi_keys_dts.sh "$SB_WORK_DIR/uefi_keys.conf") || return 1
    if [ ! -f "$SB_WORK_DIR/UefiDefaultSecurityKeys.dtbo" ]; then
        echo "ERROR: gen_uefi_keys_dts.sh produced no UefiDefaultSecurityKeys.dtbo." >&2
        return 1
    fi
    # flash.sh copies the dtbo into bootloader/; note whether that is new, to undo it.
    sudo test -e "$l4t/bootloader/UefiDefaultSecurityKeys.dtbo" || SB_DTBO_ADDED=1

    SB_FLASH_ARGS=(--uefi-keys "$SB_WORK_DIR/uefi_keys.conf")
    SB_EKB_ARGS=()
    if [ "$SB_MODE" = full ]; then
        SB_FLASH_ARGS=(-u "$SB_KEYS_DIR/pkc.pem" -v "$SB_KEYS_DIR/sbk.key" "${SB_FLASH_ARGS[@]}")
        SB_EKB_ARGS=(--oem-k1 "$SB_KEYS_DIR/oem_k1.key" --auth-key "$SB_KEYS_DIR/uefi_auth.key")
    fi
    # An encrypted flash signs its generating passes the same way, and puts these EKB
    # entries in beside its disk key.
    DISK_ENC_SIGN_ARGS=("${SB_FLASH_ARGS[@]}")
    DISK_ENC_EKB_ARGS=("${SB_EKB_ARGS[@]}")
}

# For a plain (unencrypted) flash: the EKB a K1-fused module needs. The encrypted flash
# writes its own EKB with these entries included, so it does not call this. $1 L4T dir.
sb_install_ekb() {
    [ ${#SB_EKB_ARGS[@]} -eq 0 ] && return 0
    ark_ekb_install "$1" "${SB_EKB_ARGS[@]}"
}

# Undo sb_prepare and sb_install_ekb. $1 L4T dir.
sb_cleanup() {
    local l4t="$1"
    ark_ekb_restore "$l4t"
    if [ -n "$SB_DTBO_ADDED" ]; then
        sudo rm -f "$l4t/bootloader/UefiDefaultSecurityKeys.dtbo"
    fi
    if [ -n "$SB_WORK_DIR" ]; then
        sudo rm -rf "$SB_WORK_DIR"
    fi
}

# Run a flash command with secure boot set up around it: sb_prepare, the EKB a fused
# module needs, then "$@", then sb_cleanup however it ends. A subshell, like
# disk_enc_flash, so the trap and the arrays stay out of the caller. The encrypted flash
# rewrites the EKB with its disk key added and restores the stock one itself; the
# cleanup here is then a no-op for the EKB. $1 L4T dir, then the command.
sb_run() {
    local l4t="$1"
    shift
    (
        trap 'sb_cleanup "$l4t"' EXIT
        sb_prepare "$l4t" || exit 1
        sb_install_ekb "$l4t" || exit 1
        "$@"
    )
}
