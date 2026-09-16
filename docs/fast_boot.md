# Experimental JAJ fast-boot image

This workflow targets Just a Jetson, NVMe/ext4, and L4T R36.5.0. It combines
reduced firmware work, a matched initramfs with precomputed module indexes, and
headless userspace. **It does not establish a boot time under 10 seconds.** The
customer's application-ready endpoint still needs to be defined and measured.

ARK-OS services remain enabled and start normally by default. Moving application
work after `multi-user.target` is a separate opt-in experiment, not a substitute
for measuring application readiness. SSH listening, authenticated SSH, the first
camera frame, and a complete inference are different endpoints.

## Build and stage

Run these commands from the repository root. The normal build provisions ARK-OS
and its camera compatibility pins; retain that path for customer-image testing:

```sh
./build.sh JAJ --precompute-initrd
scripts/build_fast_boot_uefi.sh --build-dir /tmp/jaj-uefi-build
```

`--precompute-initrd` is the explicit JAJ optimization flag. Every build first
refreshes the production initramfs after installing the final kernel and all
in-tree/out-of-tree modules. With this flag, the build then generates compatible
kmod 29 indexes and promotes the optimized image to both `rootfs/boot/initrd`
and `bootloader/l4t_initrd.img`. NVIDIA's flash path can repopulate the first
from the second, so updating both is necessary. The final build stamp records
`precomputed_initrd=1` only after the two promoted copies agree.

The required refresh runs in the Ubuntu 22.04 build environment, using NVIDIA's
`tools/l4t_update_initrd.sh -l <Linux_for_Tegra>`. That script repopulates the
rootfs initrd from the bootloader-side base, runs the staged rootfs's
`nv-update-initrd` against `/boot/Image` and its installed modules, and updates
both copies. The kernel Makefile's `make install` alone does not do this. Working
AArch64 QEMU/binfmt support remains required. [NVIDIA's kernel customization
procedure](https://docs.nvidia.com/jetson/archives/r36.5/DeveloperGuide/SD/Kernel/KernelCustomization.html)

`build.sh --fast` means reuse the existing staged build tree. It does not select
the fast-boot profile. An unoptimized staged tree can be rebuilt using
`./build.sh JAJ --fast --precompute-initrd`. Once either initrd is optimized, a
subsequent `--fast` invocation fails before recompiling, with instructions to
start a fresh build or restore verified unoptimized sources. This applies even
without the optimization flag: NVIDIA's updater preserves `/init`, so silently
reusing an optimized input could retain an old optimization and mislabel it as
disabled. The runtime checksum fallback is a recovery mechanism, not a rebuild
procedure.

`--no-provision` omits ARK-OS and is useful for a bare-system experiment; its
measurements do not establish the fully provisioned product's boot time. A
normal build replaces the staged tree. Apply the rootfs and firmware profile
after the build, as below. See [build host requirements](build_host.md) and the
[firmware profile](../products/JAJ/fastboot/README.md).

The following block configures headless userspace and stages the firmware pair.
It uses the existing build image and firmware output. It does not access or
flash a connected device, regenerate the initrd, or defer ARK services:

```sh
docker run --rm -i --network none \
  -v "$PWD:/workspace" \
  -v /tmp/jaj-uefi-build/artifacts:/firmware:ro \
  -w /workspace ark-jetson-builder:22.04 bash -euo pipefail <<'STAGE'
JAJ_L4T=/workspace/staging/JAJ/Linux_for_Tegra
JAJ_ROOTFS="$JAJ_L4T/rootfs"
cmp "$JAJ_L4T/kernel/Image" "$JAJ_ROOTFS/boot/Image"
cmp "$JAJ_L4T/bootloader/l4t_initrd.img" "$JAJ_ROOTFS/boot/initrd"

# Headless and no known utmp delay; enabled ARK services still start normally.
python3 scripts/configure_fast_boot.py apply "$JAJ_ROOTFS" --skip-utmp-delay
python3 scripts/stage_fast_boot_firmware.py \
  --l4t-dir "$JAJ_L4T" --artifacts /firmware --quiet-firmware
python3 scripts/stage_fast_boot_firmware.py --l4t-dir "$JAJ_L4T" --verify
python3 scripts/configure_fast_boot.py status "$JAJ_ROOTFS"
STAGE
```

The freshly refreshed unoptimized image is saved as
`bootloader/l4t_initrd.before-precompute.img`. Preserve it with the image's test
record, firmware checksums, source lock, resolved configuration, and logs. To
reuse a previously optimized staged tree, verify that this backup belongs to
that BSP, use `check_initrd_source.py --target JAJ` to check that it is
unoptimized, and restore it to **both** initrd locations before a `--fast`
rebuild. The build then refreshes its module payloads for the newly installed
kernel. If that backup is missing or its origin is unclear, use a fresh build.
Do not restore only one copy or run the optimizer directly on an older build's
initrd.

The normal build already adds `quiet log_buf_len=4M` and its console loglevel
override. The firmware staging helper verifies its firmware/overlay pair and
records the selected files in `ark-fast-boot.json`. Staging these additional
profiles remains separate from `--precompute-initrd`, which changes only how
the production initramfs module indexes are prepared.

A reused `--fast` build verifies an existing firmware profile before compiling,
preserves its verified `kernel/dtb/ark_fast_boot.dtbo`, and verifies all recorded
firmware files again before recording build metadata. A fresh build replaces
the BSP and therefore requires staging the firmware profile again. Run the
firmware staging/verification block after each build to confirm the intended
firmware artifacts. If a prior failed build already removed a recorded overlay,
verification deliberately fails: restore the exact overlay from its recorded
artifact set and verify it, or use a fresh BSP before staging. Do not delete the
manifest to bypass a checksum or missing-file error.

## What the options change

- The rootfs helper selects `multi-user.target`. It preserves SSH, network
  setup including the NVIDIA USB gadget, thermal/power services, NVIDIA boot
  validation, and the enabled ARK service set.
- `--skip-utmp-delay` removes only NVIDIA's exact two-second sleep directive.
  The tradeoff is that `last reboot` history can record an inaccurate wall-clock
  time before the clock is corrected; ordinary clock synchronization remains.
  Omit this flag if that history timestamp is required. [NVIDIA explanation](https://forums.developer.nvidia.com/t/why-systemd-update-utmp-service-need-sleep-2s/363767)
- `--direct-usb-runtime` replaces exactly the two known `service ... start/stop`
  calls in NVIDIA's USB state handler with native `/bin/systemctl` calls. The
  Ubuntu wrapper enumerates socket units before each start/stop, even though
  this runtime service has none. The helper requires the known native NVIDIA
  runtime unit and rejects custom runtime drop-ins or associated sockets. It
  preserves cable-role checks, bridge/DHCP setup, and subsequent udev hotplug
  handling. Verify USB connection, disconnect/reconnect, and ARK API access after
  applying this option; it does not change which USB functions are exposed.
- `--skip-lvm-monitor` removes existing LVM-monitor Wants enablement links and
  saves them for exact restoration. Use it only after confirming the deployment
  has **no LVM physical volumes, volume groups, logical volumes, root device,
  mounted filesystems, or swap**. Inspect `pvs`, `vgs`, `lvs`, `lsblk -f`,
  `findmnt`, and swap configuration on the intended device; an ext4 root alone
  does not establish this. An offline rootfs cannot prove the attached disks
  have no LVM. The helper does not mask LVM, remove its packages, or change
  storage activation; custom Requires links cause it to refuse the change.
  Package re-enablement or another unit dependency can start monitoring again.
- `--defer-ark-services` is optional and is deliberately absent above. It moves
  enabled ARK/UI services, nginx, jtop, and first-boot hotspot setup into a later
  startup transaction. It can postpone the functionality the customer actually
  needs. Reaching its target does not prove the application is ready.
- The initrd helper uses kmod 29, matches the single module tree to Image,
  validates dependencies, and preserves existing module payloads. Runtime
  integrity checks skip `depmod` only while those modules/indexes still match;
  otherwise stock `depmod -a` runs. It does not remove root-device retries, USB,
  thermal control, or encryption setup. Rebuild the candidate after updating
  modules; do not reuse an earlier build's optimized initrd.
- The firmware profile changes firmware boot-device discovery and update/setup
  capabilities. Read its [requirements and limitations](../products/JAJ/fastboot/README.md)
  before selecting it. Signed/encrypted deployments need their normal signing
  process after image generation and separate validation.

For a confirmed deployment without LVM, the combined optional userspace profile
is applied inside the build container with:

```sh
# When changing an already-applied profile, restore its recorded originals first.
python3 scripts/configure_fast_boot.py restore "$JAJ_ROOTFS"
python3 scripts/configure_fast_boot.py apply "$JAJ_ROOTFS" \
  --skip-utmp-delay --direct-usb-runtime --skip-lvm-monitor
```

ARK services remain enabled with this combination. LVM-monitor duration can
overlap NVMe device discovery; measure the resulting external readiness time
instead of adding individual service durations as predicted savings.

`configure_fast_boot.py restore ROOTFS` restores its saved default target,
service links, utmp override, and USB state handler, including ownership. It refuses conflicting
external edits. It does not undo an initrd or firmware replacement. The saved
stock initrd can restore both initrd locations only when it still matches the
current kernel/modules. Otherwise regenerate a matching stock initrd. Keep a
known-good QSPI image and the customer's NVMe backup for recovery.

## Deploy and measure

Wait for explicit handoff of the test device before using it. For an existing
NVMe installation, `./flash.sh JAJ --bootloader-only` deploys the staged firmware
without replacing the root filesystem; it does not apply the staged rootfs or
initrd changes. For a newly provisioned complete image, `./flash.sh JAJ` writes
the staged image to NVMe as well. Use the intended deployment route and preserve
the existing customer's data. Both require the appropriate USB recovery setup.

Measure first-boot provisioning separately from repeated normal cold starts.
Record the exact image, firmware, module SKU, SSD, peripherals, and endpoint for
each run. Use external power-to-ready measurement alongside kernel and systemd
timestamps. The [measurement helper](../scripts/measure_boot.py) records host
monotonic times; its SCPI reference is command-send time, not a measured power
edge, and its SSH banner probe is not authentication or application readiness.
For acceptance, instrument the actual customer operation.

NVIDIA boot validation must finish successfully before the next reboot/power
cycle. Do not trigger a rapid test loop solely from the earlier application or
SSH marker. Preserve at least one usable recovery/diagnostic path when reducing
serial output.

Hardware validation is ongoing. The original image reached an SSH banner in
33.371 seconds on one cold start. Reduced firmware plus the tested userspace and
initrd changes reached SSH in 15.163 seconds on another cold start. These are
SSH milestones, not the agreed application endpoint. The explicitly approved
no-TPM experiment reached a valid local ARK-OS API JSON response in 13.649 seconds,
observed through the debug UART; host USB-network access took 16.302 seconds on
that same boot. These single-run observations do not establish a distribution
or a result under 10 seconds.

The user selected ARK-OS as the customer-application stand-in and confirmed an
approximately two-second carrier POR delay. The measurement includes that delay.
The temporary [readiness probe](../scripts/emit_boot_ready.py) emits its UART
marker only after `/api/system/info` returns HTTP 200 and valid JSON. Camera and
inference readiness require their own first-output tests.

JAJ builds also include [optional asynchronous BPMP debugfs setup](../products/JAJ/fastboot/bpmp-debugfs.md).
The default remains synchronous; enable the documented parameter only after
installing the matched patched kernel/modules/initrd and validating diagnostic
consumers. Keep global `debugfs=off` out of the production profile: the stock
NVIDIA initramfs treats a failed debugfs mount as an error and waits 30 seconds.


The final acceptance record must include cold-power timing through the required
customer output, the run count, spread, maximum, and failures. An under-10-second
systemd total alone is insufficient; firmware and remaining application work
still contribute to the customer-observed interval.
