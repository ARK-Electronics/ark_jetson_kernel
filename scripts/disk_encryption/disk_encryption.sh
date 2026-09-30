# Encrypted-rootfs flashing for ARK Jetson carriers (Orin Nano / Orin NX on NVMe).
#
# Sourced by flash.sh, and by packaging/flash_from_package.sh from the copy that
# generate_flash_package.sh bundles into each package as Linux_for_Tegra/ark_disk_encryption/.
# Needs sudo, cryptsetup and python3-cryptography on the host.
#
# NVIDIA's reference flow (Jetson Linux Developer Guide, Security > Disk Encryption), and why
# each ARK step exists:
#   - The rootfs goes to a LUKS2 partition, APP_ENC; /boot stays plain in APP so UEFI can read
#     the kernel. The initrd unlocks APP_ENC with a passphrase that OP-TEE's luks-srv TA derives
#     from the EKB's disk encryption key, the module's ECID and the partition UUID, then tells the
#     TA to stop answering. Nothing on the SSD alone opens it.
#   - The BSP's bootloader/eks_t234.img carries no disk key at all, and luks-srv has no fallback,
#     so an image encrypted against it never unlocks. Each flash writes a fresh EKB holding the
#     disk key into the QSPI, and puts the stock EKB back afterwards.
#   - BSP flash.sh refuses ROOTFS_ENC=1 for any root device other than the one it boots from,
#     so the QSPI and the NVMe images come from two --no-flash passes and one --flash-only
#     pass (README_initrd_flash.txt, workflow 11).
#   - NVIDIA's encrypted NVMe layout sizes APP_ENC from -S and never grows it; nvresizefs skips
#     a LUKS root. ARK marks APP_ENC to expand to the end of the SSD, as the plain layout marks
#     APP, and a boot-time unit grows the filesystem inside it.
#   - NVIDIA's flasher skips all-zero blocks after discarding a partition. On an SSD that reads
#     discarded blocks back as 0xff that corrupts both LUKS2 headers, so ARK writes the zeros.
#   - The keyslot PBKDF is pinned to the cheapest LUKS2 allows: the passphrase is already a
#     128-bit key, and the host-benchmarked default cost ~16 s of every boot.

ARK_DISK_ENC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARK_DISK_KEY_DIR="${ARK_DISK_KEY_DIR:-$HOME/.ark-jetson-keys}"
ARK_DISK_ENC_LAYOUT="tools/kernel_flash/ark_flash_l4t_t234_nvme_rootfs_enc.xml"
ARK_GROW_UNIT="ark-grow-encrypted-root.service"

# Fail before the Jetson is touched if the host cannot build an encrypted image. NVIDIA's
# helper runs /sbin/cryptsetup by that exact path.
disk_enc_check_host() {
    local missing=()
    [ -x /sbin/cryptsetup ] || missing+=(cryptsetup)
    python3 -c 'import cryptography' 2>/dev/null || missing+=(python3-cryptography)
    if [ ${#missing[@]} -gt 0 ]; then
        echo "ERROR: an encrypted flash needs ${missing[*]} on this host:" >&2
        echo "       sudo apt-get install ${missing[*]}" >&2
        return 1
    fi
}

# Print the disk key file to flash with. $1 is a key the user supplied (16 bytes as 32 hex
# digits, the same file flash.sh -i takes), or empty to generate a new random key under
# $ARK_DISK_KEY_DIR. $2 names the product, for the file name.
disk_enc_key_file() {
    local supplied="$1" target="$2" key
    if [ -n "$supplied" ]; then
        if [ ! -f "$supplied" ]; then
            echo "ERROR: disk key $supplied not found." >&2
            return 1
        fi
        if ! tr -d '[:space:]' < "$supplied" | grep -qiE '^(0x)?[0-9a-f]{32}$'; then
            echo "ERROR: $supplied must hold a 16-byte key as 32 hex digits." >&2
            return 1
        fi
        readlink -f "$supplied"
        return 0
    fi
    mkdir -p "$ARK_DISK_KEY_DIR" && chmod 700 "$ARK_DISK_KEY_DIR" || return 1
    key="$ARK_DISK_KEY_DIR/$(echo "$target" | tr '[:upper:]_' '[:lower:]-')-$(date +%Y%m%d-%H%M%S).key"
    (umask 077 && python3 -c 'import secrets; print(secrets.token_hex(16))' > "$key") || return 1
    echo "$key"
}

# Size for -S: APP (/boot, 400 MiB) plus APP_ENC. APP_ENC only has to hold the rootfs until
# the partition is stretched at flash time and the filesystem grown at boot, so it is sized
# from the rootfs with a quarter spare for ext4 metadata, not NVIDIA's 55 GiB default, which
# a 64 GB SSD barely holds.
disk_enc_image_size() {
    local l4t="$1" used
    used=$(sudo du -s -x -B1 "$l4t/rootfs" | cut -f1) || return 1
    echo "$(( (used * 5 / 4 + 400 * 1024 * 1024) / (1024 * 1024 * 1024) + 2 ))GiB"
}

disk_enc_prepare_tree() {
    local l4t="$1" key="$2" eks="$1/bootloader/eks_t234.img"

    # Keep the stock EKB aside. A copy already there means an earlier run died before
    # putting it back, so the file in place is that run's key, not NVIDIA's. The tree is
    # root-owned and not always readable, so every look inside it goes through sudo.
    if ! sudo test -f "$eks.stock"; then
        sudo cp -p "$eks" "$eks.stock" || return 1
    fi
    sudo python3 "$ARK_DISK_ENC_DIR/gen_disk_ekb.py" --disk-key "$key" --out "$eks" || return 1
    sudo python3 "$ARK_DISK_ENC_DIR/gen_disk_ekb.py" --dump "$eks" || return 1

    # 0x808 is the plain layout's APP attribute: the flasher stretches that partition to
    # the end of the device (l4t_flash_from_kernel.sh "expand").
    local stock_layout="$l4t/tools/kernel_flash/flash_l4t_t234_nvme_rootfs_enc.xml"
    sudo sed '/<partition name="APP_ENC"/,/<\/partition>/ s|<allocation_attribute> 0x8 </allocation_attribute>|<allocation_attribute> 0x808 </allocation_attribute>|' \
        "$stock_layout" | sudo tee "$l4t/$ARK_DISK_ENC_LAYOUT" > /dev/null || return 1
    local app_enc
    app_enc=$(sudo sed -n '/<partition name="APP_ENC"/,/<\/partition>/p' "$l4t/$ARK_DISK_ENC_LAYOUT") || return 1
    if ! grep -q '<allocation_attribute> 0x808 </allocation_attribute>' <<< "$app_enc"; then
        echo "ERROR: could not mark APP_ENC expandable in $stock_layout; the BSP's encrypted" >&2
        echo "       layout changed. Update disk_enc_prepare_tree for it." >&2
        return 1
    fi

    # NVIDIA's write_zstd_image discards the partition, then skips every all-zero block
    # (dd conv=sparse), trusting the SSD to read discarded blocks back as zeros. NVMe
    # lets a drive return ones instead, and on such a drive the zero padding inside both
    # LUKS2 headers reads back 0xff: both checksums fail and the rootfs never unlocks
    # (seen on the JAJ bench SSD). Write the zeros. The images cross the USB link as
    # zstd, so the extra writes add next to nothing. The flasher copies this script into
    # tools/kernel_flash/images/ while generating, which is the copy the flash runs.
    local kflash="$l4t/tools/kernel_flash/l4t_flash_from_kernel.sh" write_zstd
    if ! sudo test -f "$kflash.stock"; then
        sudo cp -p "$kflash" "$kflash.stock" || return 1
    fi
    sudo sed -i '/^function write_zstd_image/,/^}/ s/ conv=sparse//' "$kflash" || return 1
    write_zstd=$(sudo sed -n '/^function write_zstd_image/,/^}/p' "$kflash") || return 1
    if ! grep -q 'dd of=' <<< "$write_zstd" || grep -q 'conv=sparse' <<< "$write_zstd"; then
        echo "ERROR: could not make write_zstd_image in $kflash write zero blocks; the" >&2
        echo "       BSP's flasher changed. Update disk_enc_prepare_tree for it." >&2
        return 1
    fi

    # cryptsetup sizes a keyslot's PBKDF by benchmarking the host it runs on: argon2id with
    # 1 GiB, which the Orin Nano's initrd then pays twice per volume, at every boot (~8 s
    # a volume on an Orin Nano 4GB), and more the faster the flashing PC. It buys nothing
    # here. The passphrase is 128 pseudorandom bits from OP-TEE's KDF, not something a
    # person chose, so there is nothing for key stretching to protect. Pin the cheapest
    # PBKDF LUKS2 allows, so unlock time is the same whichever PC flashed the board.
    local helper="$l4t/tools/disk_encryption/disk_encryption_helper.func" luks_formats
    if ! sudo test -f "$helper.stock"; then
        sudo cp -p "$helper" "$helper.stock" || return 1
    fi
    sudo sed -i 's/--type luks2 \\$/--type luks2 --pbkdf pbkdf2 --pbkdf-force-iterations 1000 \\/' "$helper" || return 1
    luks_formats=$(sudo grep -c -- '--type luks2 --pbkdf pbkdf2 --pbkdf-force-iterations 1000 \\$' "$helper") || true
    if [ "$luks_formats" != 2 ]; then
        echo "ERROR: expected to pin the PBKDF on 2 luksFormat calls in $helper, pinned" >&2
        echo "       ${luks_formats:-0}; the BSP's disk encryption helper changed." >&2
        return 1
    fi

    # The dm-crypt mapping already spans the stretched partition (LUKS2 data segments are
    # sized dynamically), so growing the filesystem is all that is left. Online resize2fs
    # is a no-op once it fills the partition, so it runs every boot.
    sudo tee "$l4t/rootfs/etc/systemd/system/$ARK_GROW_UNIT" > /dev/null <<'EOF' || return 1
[Unit]
Description=Grow the encrypted root filesystem to fill its partition
ConditionPathExists=/dev/mapper/crypt_root

[Service]
Type=oneshot
ExecStart=/sbin/resize2fs /dev/mapper/crypt_root

[Install]
WantedBy=multi-user.target
EOF
    sudo mkdir -p "$l4t/rootfs/etc/systemd/system/multi-user.target.wants" || return 1
    sudo ln -sfn "../$ARK_GROW_UNIT" "$l4t/rootfs/etc/systemd/system/multi-user.target.wants/$ARK_GROW_UNIT"
}

# Undo disk_enc_prepare_tree, so a plain flash from the same tree is exactly what it was.
disk_enc_restore_tree() {
    local l4t="$1" stock
    for stock in bootloader/eks_t234.img tools/kernel_flash/l4t_flash_from_kernel.sh \
                 tools/disk_encryption/disk_encryption_helper.func; do
        if sudo test -f "$l4t/$stock.stock"; then
            sudo mv -f "$l4t/$stock.stock" "$l4t/$stock"
        fi
    done
    sudo rm -f "$l4t/$ARK_DISK_ENC_LAYOUT" \
        "$l4t/rootfs/etc/systemd/system/$ARK_GROW_UNIT" \
        "$l4t/rootfs/etc/systemd/system/multi-user.target.wants/$ARK_GROW_UNIT"
}

# Write what, with the key, unlocks this SSD off the Jetson: the module's ECID and the LUKS
# UUIDs. Kept beside the key; see docs/disk_encryption.md for the recovery command.
disk_enc_write_record() {
    local l4t="$1" key="$2" target="$3" log="$4" record ecid
    record="$ARK_DISK_KEY_DIR/$(basename "${key%.key}").txt"
    [ "$(dirname "$key")" = "$ARK_DISK_KEY_DIR" ] || \
        record="$ARK_DISK_KEY_DIR/$(echo "$target" | tr '[:upper:]_' '[:lower:]-')-$(date +%Y%m%d-%H%M%S).txt"
    ecid=$(grep -oE 'ECID is 0x[0-9a-fA-F]+' "$log" | tail -1 | awk '{print $3}')
    if [ -z "$ecid" ]; then
        echo "ERROR: the image generation log names no ECID; cannot write the recovery record." >&2
        return 1
    fi
    mkdir -p "$ARK_DISK_KEY_DIR" && chmod 700 "$ARK_DISK_KEY_DIR" || return 1
    (umask 077 && cat > "$record" <<EOF
# ARK Jetson encrypted rootfs. With the key below, this unlocks the SSD off the Jetson.
target=$target
date=$(date -Iseconds)
key_file=$key
ecid=$ecid
rootfs_luks_uuid=$(sudo cat "$l4t/bootloader/l4t-rootfs-uuid.txt_ext_enc" 2>/dev/null)
uda_luks_uuid=$(sudo cat "$l4t/bootloader/l4t-datafs-uuid.txt_UDA_ext" 2>/dev/null)
EOF
    ) || return 1
    echo "$record"
}

# Generate and flash the QSPI (with the disk key's EKB) and the encrypted NVMe image.
#   $1 L4T dir  $2 key file  $3 board config  $4 external device (nvme0n1p1)
#   $5 QSPI layout, relative to $1  $6 product dtbo list for ADDITIONAL_DTB_OVERLAY_OPT (may be empty)
#   $7 product name, for the recovery record
disk_enc_flash() {
    local l4t="$1" key="$2" board="$3" device="$4" qspi_cfg="$5" overlays="$6" target="$7"
    local size log status=0
    size=$(disk_enc_image_size "$l4t") || return 1
    log=$(mktemp) || return 1
    # A subshell, so its EXIT trap restores the tree however the flash ends, and so the
    # caller's own traps and set -e stay untouched.
    (
        cd "$l4t" || exit 1
        trap 'disk_enc_restore_tree "$PWD"' EXIT
        disk_enc_prepare_tree "$PWD" "$key" || exit 1

        echo "Encrypted flash 1/3: QSPI images, EKB carrying the disk key"
        sudo ${overlays:+ADDITIONAL_DTB_OVERLAY_OPT=$overlays} ./tools/kernel_flash/l4t_initrd_flash.sh \
            --no-flash -p "-c ./$qspi_cfg" --showlogs --network usb0 "$board" internal || exit 1

        echo "Encrypted flash 2/3: encrypted $device images (rootfs image $size, grown to fill the SSD)"
        sudo ROOTFS_ENC=1 ${overlays:+ADDITIONAL_DTB_OVERLAY_OPT=$overlays} ./tools/kernel_flash/l4t_initrd_flash.sh \
            --no-flash --external-device "$device" -S "$size" -c "./$ARK_DISK_ENC_LAYOUT" \
            --external-only --append -i "$key" --showlogs --network usb0 "$board" external 2>&1 | tee "$log"
        [ "${PIPESTATUS[0]}" -eq 0 ] || exit 1
        record=$(disk_enc_write_record "$PWD" "$key" "$target" "$log") || exit 1

        echo "Encrypted flash 3/3: writing QSPI and $device"
        sudo ./tools/kernel_flash/l4t_initrd_flash.sh --flash-only --showlogs --network usb0 || exit 1

        echo ""
        echo "Encrypted rootfs flashed. Back up the disk key and its record: together they unlock"
        echo "this SSD on any Linux host, and without them it opens only inside this module:"
        echo "  key:    $key"
        echo "  record: $record"
    ) || status=$?
    rm -f "$log"
    return "$status"
}
