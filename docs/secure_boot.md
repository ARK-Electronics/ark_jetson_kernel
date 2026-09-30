# Secure Boot

> **Draft: not yet run on hardware.** The key generation, the EKB and UEFI key artifacts, and signed image generation have been checked offline against the R36.5 BSP. No module has been flashed or fused with this flow yet. Treat everything below as the plan until it has been.

Secure boot makes a Jetson run only software you signed. Combined with [disk encryption](disk_encryption.md), it closes the gap disk encryption leaves open: someone who takes the module and the SSD together.

This drives NVIDIA's own [Secure Boot](https://docs.nvidia.com/jetson/archives/r36.5/DeveloperGuide/SD/Security/SecureBoot.html) tooling. ARK adds a key generator, and two flags on `flash.sh` and `flash_from_package.sh` that sign every flash consistently, including the three-pass encrypted flash.

## The layers

| Layer | Flag | What it checks | Fuses | Undo |
| --- | --- | --- | --- | --- |
| UEFI Secure Boot | `--uefi-secure-boot KEYS` | UEFI runs L4TLauncher, the kernel, its DTB, the initrd and `extlinux.conf` only if they are signed by your db key | None | A plain reflash |
| Boot chain signing and encryption | `--secure-boot KEYS` | The BootROM runs MB1, and everything after it up to UEFI, only if signed by your PKC key, and decrypts it with your SBK | PublicKeyHash, SecureBootKey, BootSecurityInfo, SecurityMode | **Never** |
| Key store under OEM_K1 | `--secure-boot KEYS` | The EKB in QSPI (disk key, UEFI variable key) is encrypted with a key only the module holds | OemK1 | **Never** |

`--secure-boot` includes UEFI Secure Boot. Each flag works with or without `--encrypt`.

Fuses are one-way. Once `fuses.xml` is burned, the module boots only images signed with that key directory, and no ARK release or NVIDIA image will run on it again. If the keys are lost, the module can never be reflashed.

## What protects what, with `--encrypt`

| Attack | `--encrypt` | `+ --uefi-secure-boot` | `+ --secure-boot` (fused) |
| --- | --- | --- | --- |
| Read a removed SSD | Protected | Protected | Protected |
| Swap the kernel or initrd on the SSD's plaintext `/boot` to capture the disk passphrase | Open | Blocked | Blocked |
| Take module and SSD, reflash the QSPI or read the disk key out of it | Open | Open | Blocked |
| Someone with a login on the running system | Open | Open | Open |

Debug interfaces, such as the JTAG and debug-authentication fuses, are not burned by this flow. NVIDIA's *Jetson Orin Fuse Specification* application note covers them.

## Generate keys

Once per product or fleet, on a host with the L4T tree (a staged `staging/<TARGET>/Linux_for_Tegra` or an extracted flash package under `~/.ark-jetson-cache/`):

```
sudo apt-get install openssl efitools uuid-runtime
./scripts/secure_boot/gen_secure_boot_keys.sh <Linux_for_Tegra> ~/jaj-secure-boot-keys
```

| File | Purpose |
| --- | --- |
| `pkc.pem` | ECDSA P-521 key that signs the boot chain |
| `sbk.key` | Secure Boot Key that encrypts the boot chain |
| `oem_k1.key` | OEM_K1, the root key of the EKB once burned |
| `uefi_auth.key` | UEFI variable authentication key, stored in the EKB; UEFI refuses to boot a K1-fused module without it |
| `uefi/` | UEFI PK, KEK and db keys, certificates and signature lists, and `uefi_keys.conf` |
| `fuses.xml` | The fuse configuration for this key set: NVIDIA's documented P-521 + SBK + OemK1 example |

`PublicKeyHash` in `fuses.xml` comes from NVIDIA's `tegrasign_v3.py`: it is not a plain hash of `pkc.pem`, and a wrong value burned can never be corrected.

The certificates are self-signed, as in NVIDIA's examples, and the keys are plain files. For production, generate and keep them the way your security process requires; NVIDIA recommends an HSM. Anyone holding `pkc.pem` and `sbk.key` can sign images your fused modules will run.

## Develop with UEFI Secure Boot (no fuses)

```
./flash_from_package.sh jaj --uefi-secure-boot ~/jaj-secure-boot-keys --encrypt
```

UEFI enrolls the keys on the first boot. Check it on the Jetson; the last number printed is `1` when Secure Boot is on:

```
od -An -t u1 /sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c
```

A plain reflash turns UEFI Secure Boot off again.

## Production: fuse, then flash

1. **Dry-run the burn.** NVIDIA's lab tool checks the fuse file against the module without burning anything:

   ```
   cd <Linux_for_Tegra>
   sudo ./odmfuse.sh --test -X ~/jaj-secure-boot-keys/fuses.xml -i 0x23 jetson-orin-nano-devkit-super
   ```

   NVIDIA has deprecated `odmfuse.sh` in favor of [Factory Secure Key Provisioning](https://docs.nvidia.com/jetson/archives/r36.5/DeveloperGuide/SD/Security/FSKP.html) (FSKP), which takes the same fuse file and is meant for the factory floor; it needs expansion keys requested from NVIDIA. Burn all the fuses in one operation, as NVIDIA recommends.
2. **Burn.** The same command without `--test`. This cannot be undone.
3. **Flash:**

   ```
   ./flash_from_package.sh jaj --secure-boot ~/jaj-secure-boot-keys --encrypt
   ```

NVIDIA's `flash.sh` reads the module's fuses and refuses a mismatch: `--secure-boot` on an unfused module, or `--uefi-secure-boot` alone on a fused one.

## Living with it

- **`/boot` is signed at flash time.** Anything that rewrites it on the device leaves unsigned files that UEFI refuses. That includes jetson-io switching the camera overlay (it edits `extlinux.conf`) and an `apt upgrade` of the kernel or initrd packages. ARK images hold the kernel packages, but not `nvidia-l4t-initrd`. Pick the camera overlay at flash time (`products/<TARGET>/default_overlays`). Not yet tested: what L4TLauncher does when a payload fails verification; NVIDIA documents a fallback to the kernel partition.
- **Bootloader updates need signing.** ARK images hold `nvidia-l4t-bootloader`; NVIDIA's capsule update flow covers signed updates.
- **Kernel modules are not signature-checked** by this flow. See NVIDIA's *Kernel Module Signing* section.
- **UEFI key rotation** (new db or dbx entries) goes through NVIDIA's capsule update flow, which this does not wrap.
