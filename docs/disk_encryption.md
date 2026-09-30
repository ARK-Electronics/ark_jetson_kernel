# Disk Encryption

`--encrypt` flashes the root filesystem to a LUKS2-encrypted partition. The Jetson unlocks it by itself at every boot, with no passphrase to type. An SSD pulled from the carrier, on its own, cannot be read.

It is NVIDIA's [disk encryption](https://docs.nvidia.com/jetson/archives/r36.5/DeveloperGuide/SD/Security/DiskEncryption.html) for Orin NX/Nano on NVMe. Followed as written, that guide produces an image that never unlocks; see [Differences from NVIDIA's guide](#differences-from-nvidias-guide) for why, and what this flag does instead.

## Flash

Connect the Jetson over USB and power it on with the Force Recovery button held, as for a normal flash. Then, from a prebuilt release:

```
./flash_from_package.sh jaj --encrypt
```

or from a source build:

```
./flash.sh JAJ --encrypt
```

Bench-validated on a JAJ with an Orin Nano 4GB. `PAB` / `pab` and `PAB_V3` / `pab-v3` take the same flag through the same code, but have not been flashed encrypted yet. It needs an NVMe SSD (not `--sdcard` or `--usb`) and, on the host, `cryptsetup` and `python3-cryptography`: `flash_from_package.sh` installs them, and `flash.sh` names them if they are missing. `flash_from_package.sh --encrypt` needs a release that bundles the `ark/` helpers; older ones refuse with a message.

An encrypted flash takes longer than a plain one: the image is built in three passes (QSPI, encrypted NVMe, write), and the rootfs is encrypted on the host. The board resets itself between passes; no extra button presses are needed.

## The disk key

Each encrypted flash generates a random 16-byte disk key and leaves two files in `~/.ark-jetson-keys/`:

```
jaj-6.2.2.6-20260930-150206.key   # the disk key
jaj-6.2.2.6-20260930-150206.txt   # the module's ECID and the LUKS partition UUIDs
```

The Jetson does not need them to boot; they are for you. Together they unlock the SSD on any Linux host (see [Reading the SSD off the Jetson](#reading-the-ssd-off-the-jetson)). Without them, the SSD opens only inside the module it was flashed with. Back them up somewhere safe, and treat the key as a secret: whoever has the key and the record can read the SSD.

To use your own key instead, for example one from your key management, pass it as a file holding 32 hex digits:

```
openssl rand -hex 16 > my-disk.key
./flash_from_package.sh jaj --encrypt --disk-key my-disk.key
```

Set `ARK_DISK_KEY_DIR` to keep keys and records somewhere other than `~/.ark-jetson-keys/`.

## What it protects

| Situation | Protected? |
| --- | --- |
| SSD removed and read on another computer | Yes |
| SSD moved into another Jetson | Yes: it does not unlock there |
| Jetson module and SSD taken together | **No**, unless the module's fuses are burned ([secure_boot.md](secure_boot.md)) |
| Someone with a login on the running Jetson | No: the disk is unlocked while it runs |

The unlock passphrase is derived inside the module's secure world (OP-TEE) from three things: the disk key, which lives in the module's QSPI flash, not on the SSD; the module's unique chip ID (ECID); and the partition's UUID. After the initrd unlocks the rootfs it shuts that service down until the next reset, so nothing that runs later, including a root shell, can ask for the passphrase again.

That is what the SSD-removal case needs. It is not full-device protection. On a module whose fuses are not burned, the QSPI's key store is encrypted with an all-zero fuse key, and the kernel and initrd on the SSD's unencrypted `/boot` partition are not signature-checked. So someone holding both the module and the SSD can read the disk key back, or boot a modified initrd. Closing that takes secure boot with burned fuses, which is irreversible and not part of `--encrypt`: see [secure_boot.md](secure_boot.md), whose `--secure-boot` also builds the EKB with the burned OEM_K1.

## What changes on the SSD

| Partition | Contents |
| --- | --- |
| `nvme0n1p1` (APP) | `/boot`: kernel, initrd, DTBs, `extlinux.conf`. Unencrypted, because UEFI has to read it. 400 MiB. |
| `nvme0n1p2` (APP_ENC) | Everything else, LUKS2 (AES-XTS, 256-bit key). Mapped to `/dev/mapper/crypt_root` and mounted at `/`. Fills the rest of the SSD. |
| UDA | NVIDIA's small user-data partition, also encrypted, mounted at `/mnt/crypt_UDA`. |

The rootfs is written as a compact image and the partition is stretched to the end of the SSD at flash time; `ark-grow-encrypted-root.service` grows the filesystem to fill it on the first boot. (NVIDIA's own encrypted layout keeps the rootfs at a fixed 55 GiB.)

Unlocking both volumes adds about 2.6 s to boot on an Orin Nano 4GB; measured on a JAJ, sshd answers 35 s after power-on.

## Check it on the Jetson

```
lsblk -o NAME,FSTYPE,SIZE,MOUNTPOINT /dev/nvme0n1
sudo cryptsetup status crypt_root
```

`nvme0n1p2` should show `crypto_LUKS` with `crypt_root` (`ext4`, mounted at `/`) under it, and `cryptsetup status` should report `aes-xts-plain64`.

## Reading the SSD off the Jetson

With the SSD in a USB enclosure on a Linux host, and the key and record from `~/.ark-jetson-keys/`:

```
# From the record: ecid=0x... and rootfs_luks_uuid=...
python3 Linux_for_Tegra/tools/disk_encryption/gen_luks_passphrase.py \
    -k jaj-6.2.2.6-20260930-150206.key -u -e <ecid> -c <rootfs_luks_uuid> \
    | sudo cryptsetup luksOpen /dev/sdX2 jetson_root
sudo mount /dev/mapper/jetson_root /mnt
```

`gen_luks_passphrase.py` is NVIDIA's; it is in every L4T tree, including an extracted flash package under `~/.ark-jetson-cache/`. Use the partition that `lsblk -f` shows as `crypto_LUKS`.

## Caveats

- **Reflashing replaces everything.** A later plain flash writes NVIDIA's stock key store back to the QSPI, so the old encrypted SSD no longer unlocks on that module. An encrypted reflash generates a new key.
- **Keep `nvidia-l4t-bootloader` held.** ARK images hold it already. A bootloader update from NVIDIA's apt repo rewrites the QSPI, including the key store, and the next boot would not unlock.
- **A failed unlock reboots.** If the initrd cannot unlock the rootfs, for example the SSD was flashed for a different module, it offers a shell on the debug UART for 30 s and then resets. It shuts down the passphrase service first, so that shell cannot unlock anything. After a few failed boots UEFI stops trying and boots NVIDIA's recovery kernel instead (`L4TLauncher: Attempting Recovery Boot` on the debug UART), which cannot unlock the rootfs either; reflash to recover.

## Differences from NVIDIA's guide

The flag runs NVIDIA's own tools (`ROOTFS_ENC=1`, `l4t_initrd_flash.sh`, `flash_l4t_t234_nvme_rootfs_enc.xml`). What ARK adds, all in `scripts/disk_encryption/disk_encryption.sh`, and why:

- **A key store that holds the disk key.** The BSP's `bootloader/eks_t234.img` (the EKB, flashed to QSPI) carries no disk key at all, and OP-TEE's `luks-srv` has no fallback, so an image encrypted with any `-i` key never unlocks against it. Each encrypted flash writes a fresh EKB holding its disk key (`scripts/disk_encryption/gen_disk_ekb.py`, byte-compatible with NVIDIA's `gen_ekb.py`, which is not in the BSP), then puts the stock EKB back so a later plain flash is unchanged.
- **Three passes, as NVIDIA's initrd-flash README prescribes.** BSP `flash.sh` refuses `ROOTFS_ENC=1` for a root device it does not boot from, so the QSPI and the NVMe images are generated separately and then flashed with `--flash-only`.
- **Zero blocks are written.** NVIDIA's flasher discards the partition and then skips all-zero blocks, trusting the SSD to read discarded blocks back as zeros. NVMe lets a drive return ones instead, and the bench SSD does: the zero padding in both LUKS2 headers read back `0xff`, both header checksums failed, and the initrd reported the partition as "not LUKS". The encrypted flash writes the zeros; it costs nothing measurable, since the image crosses USB compressed.
- **A cheap keyslot PBKDF.** `cryptsetup` sizes the keyslot's key-stretching by benchmarking the flashing PC: argon2id with 1 GiB, which NVIDIA's initrd runs twice per volume, for two volumes, at every boot. On an Orin Nano 4GB flashed from a desktop that was 19 s of unlocking (rootfs mounted 42 s after power-on), and it grows with the flashing PC's speed. The passphrase is 128 pseudorandom bits from OP-TEE's KDF, so stretching protects nothing; the flash pins PBKDF2 at 1000 iterations, and unlocking takes 2.6 s (rootfs mounted 25.5 s after power-on).
- **The rootfs fills the SSD.** NVIDIA's layout fixes the encrypted rootfs at 55 GiB and `nvresizefs` skips a LUKS root. The flash stretches the partition to the end of the SSD and `ark-grow-encrypted-root.service` grows the filesystem.
- **A recovery record.** The ECID and LUKS UUIDs are saved next to the key, so the SSD can be read off the Jetson.
