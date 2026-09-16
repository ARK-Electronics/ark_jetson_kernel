# Shared ARK Orin NVMe UEFI build

This directory holds the common profile for JAJ, PAB and PAB_V3 on R36.5.0.
The legacy `jaj_nvme` artifact name and `jaj_fastboot` kernel parameter namespace
are shared by all three. Keep each product's own staged DTBs, pinmux, camera
overlays, and modules; stage with `--product JAJ`, `--product PAB`, or
`--product PAB_V3` to enforce its build stamp. Only JAJ has been measured on
hardware in this work.

This experimental RELEASE firmware reduces the boot-time hardware and boot
manager work for a headless Just a Jetson using an NVMe/ext4 root filesystem.
It must be validated on the exact module, SSD and customer application before
making a boot-time guarantee. The build script does not access any device.

Build from the repository root:

```sh
DOCKER=docker scripts/build_fast_boot_uefi.sh --build-dir /tmp/jaj-uefi-build
```

`artifacts/` contains `uefi_jaj_nvme_RELEASE.bin`, `ark_fast_boot.dtbo`, checksums,
the resolved configuration, source lock, Python versions, compiler version and
container image ID. Build logs and sources stay under the specified directory.
Requires Linux x86-64, Git, Python 3 and a working Docker connection. Source and
container downloads require network access. Builds use the caller's UID/GID.
The source inputs are pinned; build timestamps and distro package updates mean
this is a reproducible build procedure, not a byte-identical reproducible image.

## Optional no-TPM timing experiment

The default profile retains firmware TPM support. To build a separate candidate
for a device that does not require TPM measured boot, provisioning or attestation:

```sh
DOCKER=docker scripts/build_fast_boot_uefi.sh --without-tpm \
  --build-dir /tmp/jaj-uefi-no-tpm
```

This explicit option substitutes `CONFIG_SECURITY_TPM_NONE=y` in a generated
copy of the base configuration. It omits UEFI TPM initialization and measurements;
OP-TEE standalone MM, persistent variables, UEFI Secure Boot, FMP and ESRT remain
enabled. Artifacts record the configuration actually built. Use a separate build
directory to keep the standard candidate intact. This does not remove the OP-TEE
OS or its earlier fTPM provisioning messages, and its boot-time effect must be
measured on hardware. It is unsuitable for workflows requiring TPM measurements,
sealed secrets or attestation. It does not modify TPM provisioning data or fuses.

## Optional JAJ FFC PCIe firmware experiment

This separate candidate skips only the positively identified T234 PCIe controller
C7, the JAJ FFC port, during UEFI discovery. C1 Wi-Fi, C4 NVMe, C8 Ethernet and all
other controllers retain their original UEFI behavior. TPM, OP-TEE persistent
variables, Secure Boot verification support, FMP and ESRT remain configured.
Linux's device tree and its enabled FFC node are unchanged. This option is for
JAJ's audited controller mapping only; other carrier mappings need separate review.

```sh
DOCKER=docker scripts/build_fast_boot_uefi.sh --skip-uefi-ffc-pcie \
  --build-dir /tmp/jaj-uefi-skip-ffc
```

Use an independent build directory to preserve the standard artifact. The option
cannot be combined with `--without-tpm`, so the comparison retains TPM. The
default build does not apply this patch. The earlier broad C4-only experimental
option was withdrawn after a failed boot/fallback and is not a supported profile.

The reviewed `uefi-pcie-skip-ffc.patch` adds a device-tree compatibility predicate
to NVIDIA's T234 DesignWare PCIe driver. A valid `nvidia,controller-id` takes precedence;
otherwise an absent property falls back to `linux,pci-domain`, used by the JAJ
DTB. Only controller ID 7 returns `EFI_UNSUPPORTED`. Other IDs, missing nodes,
missing/malformed properties and other SoCs return `EFI_SUCCESS`, preserving the
original driver's subsequent decisions instead of rejecting an unknown port.

The pinned `DeviceDiscoveryDriverLib.c` supplies the original device-tree base
and node offset to its compatibility predicate. `GetSupportedDeviceTreeNodes`
treats a rejected predicate as a normal nonmatch and computes its final status
from accepted nodes. Thus C7 is excluded before handle creation, controller
power/clock/reset changes, threads or exit callbacks. No shared enumeration code
or assertions are changed. The original binding-phase experiment was incorrect:
when C7 was the last enumerated node and other controller threads were pending,
its `EFI_UNSUPPORTED` leaked out as the enumerator's final status and triggered
the driver's initialization assertion. The compatibility-phase regression test
covers that exact failure path and preserves genuine initialization errors.
The skipped C7 gets no UEFI initialization or teardown callback. The existing
FDT handoff callback visits only
GPU/GOP devices with an initialized parent controller; it does not disable every
uninitialized controller. This patch writes no device-tree properties, and Linux
still has the enabled C7 node and its driver. UEFI cannot boot from the FFC port
while this candidate is installed. Verify the actual controller exclusion, NVMe,
Ethernet, Wi-Fi and any attached FFC device on a complete cold boot before use.

`uefi_pcie_filter.py` verifies exact original/patched source SHA-256, refuses
modified tracked checkouts, and reverses only the reviewed patch after the build,
including failure exits. It refuses restoration if the source changes while
building. Artifacts include the patch, `source-patches.json` with its pinned
revision and all three hashes, and `build-options.json`. The LF patch is applied
with NVIDIA's original CRLF source endings, also checked by the complete file
hashes. The upstream source lock and separate patch record together identify the
firmware. Builds lock their directory and publish only complete, validated
artifacts after source restoration; previous successful artifacts are retained.

The pinned driver has an unconditional 100 ms busy wait for each initialized
controller, plus a 201 ms link-training wait that can overlap other work. Skipping
C7 avoids its UEFI work, but these durations do not establish a total boot saving.
This narrower candidate needs a paired hardware timing and functionality test.
It retains the full-QSPI deployment and key-persistence limitations below.

## R36.5 source compatibility

The R36.5 UEFI source uses `Platform/NVIDIA/Tegra/build.sh` with a
`t23x_embedded` profile. The older `JetsonMinimal/build.sh` path in the BSP guide
and R36.4.3 forum examples does not exist in this release.

`uefi-sources.lock` records the exact R36.5 release-tag commits from NVIDIA's
manifest. The manifest calls `r36.5` a branch, but the repositories expose tags.
The release source also references `AvbLib`, although the R36.5 manifest
combination omits it; this build adds the same Android Verified Boot revision
pinned elsewhere in that NVIDIA manifest. EDK2's Python dependencies require
`pkg_resources`, so setuptools is pinned to 75.8.2. kconfiglib is pinned to
14.1.0. `UEFI_SKIP_UPDATE=1` uses the container's IASL/NASM tool binaries instead
of downloading Stuart tools; it does not disable boot image verification.

## Behavior and integration

- Starts the built-in L4TLauncher directly, with NVMe/PCIe and ext4 enabled.
- Omits UEFI display/logo, network boot, shell, USB boot-device discovery,
  eMMC, SD and SATA support. These settings do not disable Linux drivers.
- Retains NVIDIA's SoC USB initialization for OS handoff, serial console,
  RCM L4TLauncher, device tree support, SMBIOS and native kernel-partition
  fallback.
- Includes fTPM, OP-TEE standalone MM, hardware RNG, UEFI Secure Boot support,
  post-enrollment physical-presence restrictions and a persistent-variable
  backend. This overrides embedded UEFI's emulated-variable default and allows
  normal persistence between boots. It does **not** guarantee that existing
  variables or enrolled keys survive deployment; the full-QSPI flash described
  below can reset them. Building this image does not enroll keys, modify fuses
  or change rootfs encryption settings.
- Retains direct-access Firmware Management Protocol (FMP), the EFI System
  Resource Table (ESRT), and the `SystemFwVersions` variable required by
  NVIDIA's `nv-l4t-bootloader-config.service` and `nvbootctrl` validation. The
  embedded default omits these: Linux boots, but NVIDIA's service exits before
  `nvbootctrl verify` with a misleading runtime-service error. This profile
  explicitly enables `CONFIG_FIRMWARE_MANAGEMENT` and
  `CONFIG_FIRMWARE_MANAGEMENT_DIRECT`, while capsule delivery remains disabled.
  It uses the shared fast-profile ESRT image GUID `0245bd35-a5ca-5267-9656-3486ecc85a78`.
- Firmware capsule delivery and the UEFI setup menu remain absent. The FMP
  version provider reads the existing A/B VER partitions; it does not invent a
  replacement version or suppress NVIDIA's validation. Keep the stock BSP and
  recovery access available and validate the product's update/rollback workflow
  before production use. After flashing, require the bootloader-config service
  to succeed, ESRT to report R36.5.0, and `nvbootctrl dump-slots-info` to report
  the expected version and valid slots. Measure boot time again: the added FMP
  and ESRT initialization has a hardware-dependent cost.

The firmware and `ark_fast_boot.dtbo` must be deployed together. Apply the
fast-boot overlay **after** the normal `ark_boot_order.dtbo` in the UEFI DTB.
The R36.5 embedded L4TLauncher compares `DefaultBootPriority` to one complete
device class, so its value must be exactly `nvme`. The normal comma-separated
priority list, or only editing the standard EFI `BootOrder`, is insufficient.
NVIDIA's `BootOrderNvme.dtbo` sets the same value; the shared ARK overlay makes this
requirement explicit in this repository.

`flash.sh JAJ --bootloader-only` performs a **full QSPI flash**, including both
bootloader slots and their matching DTBs. It leaves the NVMe rootfs intact, but
it is not a preservation-oriented update of just the UEFI binary. The R36.5
`bootloader/generic/cfg/flash_t234_qspi.xml` layout includes `uefi_variables`
(262,144 bytes) and `uefi_ftw` (524,288 bytes), both without an input filename.
The classic flasher's `tegradevflash --create` path can erase/recreate these
partitions. Existing UEFI boot settings and enrolled Secure Boot keys can
therefore be reset even though this firmware supports persistent variables.
The backups made by the staging helper contain host BSP files, not a backup of
those device-resident variables or keys.

For a configured secure device, retain the deployment's normal signed/encrypted
firmware and kernel artifacts, signing arguments and key-enrollment device-tree
configuration. Reprovision UEFI keys through NVIDIA's supported DTB enrollment
flow as required by that deployment, and verify Secure Boot state and intended
keys after flashing. The convenience wrapper does not supply those signing or
enrollment inputs. This experimental flow has no existing-key preservation
guarantee; secure deployments need their own validated update/recovery process.

This profile is intentionally separate from the normal boot configuration.
It is unsuitable for a USB rootfs or workflows requiring a firmware display,
interactive UEFI setup, network boot, or capsule updates. Linux display,
networking, camera and USB behavior still need board testing after boot.

## Primary references

- [R36.5 UEFI guide](https://docs.nvidia.com/jetson/archives/r36.5/DeveloperGuide/SD/Bootloader/UEFI.html#miniuefi-support)
- [NVIDIA's R36.4.3 Nano/NVMe adaptation](https://forums.developer.nvidia.com/t/jetson36-4-3-uefi-uefi-40-uefi-logo/332685/21)
- [NVIDIA R36.5 embedded profile](https://github.com/NVIDIA/edk2-nvidia/blob/r36.5/Platform/NVIDIA/KconfigIncludes/BuildEmbedded.conf)
- [NVIDIA R36.5 firmware-management configuration](https://github.com/NVIDIA/edk2-nvidia/blob/r36.5/Platform/NVIDIA/Kconfig#L1082)
- [NVIDIA R36.5 A/B version provider](https://github.com/NVIDIA/edk2-nvidia/blob/r36.5/Silicon/NVIDIA/Library/FmpVersionLib/FmpVersionLibPartition.c)
- [NVIDIA R36.5 L4TLauncher](https://github.com/NVIDIA/edk2-nvidia/blob/r36.5/Silicon/NVIDIA/Application/L4TLauncher/L4TLauncher.c)
- [Pinned release manifest](https://github.com/NVIDIA/edk2-edkrepo-manifest/blob/51b5da39c05d21b2c1df13958364386de901737d/edk2-nvidia/Platform/NVIDIAPlatformsManifest.xml)

The separate `--skip-utmp-delay` option removes only the exact NVIDIA
`ExecStartPre=/bin/sleep 2` line in
`systemd-update-utmp.service.d/override.conf`, preserving other contents and
saving the original for restore. This removes a fixed boot wait but can make
`last reboot` history record an incorrect wall-clock time on an RTC-less device;
NVIDIA uses the delay to let time updates settle. It does not disable normal
clock synchronization. Select it only when this boot-history tradeoff is
acceptable. [NVIDIA explanation](https://forums.developer.nvidia.com/t/why-systemd-update-utmp-service-need-sleep-2s/363767)

## Precomputed initramfs module dependencies

NVIDIA's production `/init` runs `depmod -a` before loading PCIe/NVMe. The stock
image contains only a small module subset but indexes copied from the entire
kernel tree. `scripts/optimize_initrd.py` builds correct indexes for the final
initramfs at image construction time. Run it after every kernel/module/initramfs
update using Ubuntu 22.04 kmod 29, for example in the build container:

```sh
python3 scripts/optimize_initrd.py \
  --input /path/to/rootfs/boot/initrd \
  --output /path/to/rootfs/boot/initrd.precomputed \
  --kernel-image /path/to/rootfs/boot/Image \
  --l4t-release /path/to/rootfs/etc/nv_tegra_release
```

The helper accepts the audited R36.5.0 / 5.15.185-tegra layout only, matches the
kernel Image to the single module tree, checks all module dependency resolutions,
and writes a new image without replacing its input. Existing archive metadata,
module payloads, and unrelated scripts are preserved. A runtime checksum of the
module/index files and a module-count check enable the fast path only while the
image still matches; otherwise the original `depmod -a` runs. No root-device
waits, USB drivers, fan control, encryption support, or mount retry logic are
removed. Retain the original initrd as a fallback and test the actual boot path
before promoting the candidate. Signed/encrypted deployments require their
normal signing/encryption step after generation; this helper does not sign an
image or change secure-boot policy.

## Temporary API readiness marker

`scripts/emit_boot_ready.py` runs on the Jetson and polls
`http://127.0.0.1/api/system/info` every 50 ms between attempts. It requires HTTP
200 and valid JSON, follows no redirects, and accepts at most 1 MiB. Responses
are parsed in memory and never logged or saved. A 250 ms per-attempt deadline
also bounds slow responses; the overall deadline defaults to 60 seconds.

On success it writes `JAJ_API_READY` followed by JSON containing kernel uptime
and elapsed probe time. Output goes to stdout unless an explicit
`--serial-output /dev/ttyTCU0` is supplied. The UART marker measures local API
availability without waiting for host DHCP or SSH. It verifies this endpoint's
response, not every customer application function. Kernel uptime starts after
the firmware phases; use the host UART capture's power reference for total boot
time. A timeout exits with status 1 and emits no ready marker.

For a temporary experiment, copy the script to
`/var/tmp/jaj-emit-boot-ready.py` and create this unit under
`/run/systemd/system/jaj-api-ready-marker.service`:

```ini
[Unit]
Description=Temporary JAJ local API readiness marker

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /var/tmp/jaj-emit-boot-ready.py --timeout 60 --serial-output /dev/ttyTCU0
TimeoutStartSec=65
```

Start it with `systemctl daemon-reload` and
`systemctl start --no-block jaj-api-ready-marker.service`. It waits for the
actual response without depending on the application service's start result.
The unit is not enabled or included in the image's normal startup. `/run` units
do not survive reboot: for a cold-boot diagnostic the operator must arrange a
temporary early-start unit and remove it after the measurement. Keep the probe
running early enough to observe the endpoint's first successful response.
