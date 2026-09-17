# Experimental ARK Orin fast-boot images

The helpers support JAJ, PAB and PAB_V3 with NVMe/ext4 using separate audited
L4T R36.5.0 and R39.2.1 profiles. The build commands below select the current
JetPack 7.2.1 / R39.2.1 port. **All boot timings in this document and the
[historical validation record](fast_boot_validation.md) are R36.5.0 observations,
not R39 results.** The separate [R39 validation record](jetpack7_validation.md)
contains the completed JAJ cold-boot and hardware checks. Camera, customer
inference and PAB/PAB_V3 hardware qualification remain outstanding.
The workflow combines reduced firmware work, a matched initramfs with precomputed module indexes, and
headless userspace. The agreed customer-application stand-in is ARK-OS: a local
`/api/system/info` request must return HTTP 200 and valid JSON before the target
emits a readiness marker over the debug UART. **The historical measured results
remain above 10 seconds; no R39 under-10-second result is claimed.**

ARK-OS services remain enabled and start normally by default. Moving application
work after `multi-user.target` is a separate opt-in experiment, not a substitute
for this application measurement. The basic API endpoint does not establish readiness
of every ARK service, camera, inference workload, or flight-controller connection.
The target probe's optional `--ark-os-ready` criterion additionally requires
populated Jetson hardware metadata; record it separately from the historical
HTTP/JSON-only captures described below.

## Build and stage

Run these commands from the repository root. R39 uses Ubuntu 24.04 and needs the
matching Noble ARK-OS package; the Jammy package cannot run its bundled Python
3.10 environment on this image. Follow [JetPack 7 provisioning](jetpack7_provisioning.md)
to prepare the pinned Noble package before a fully provisioned build:

```sh
./scripts/build_ark_os_noble.sh /tmp/ark-os-noble-build
./build.sh JAJ --precompute-initrd
scripts/build_fast_boot_uefi.sh --bsp R39.2.1
```

The firmware wrapper defaults to R36.5.0 for compatibility, so the explicit
`--bsp R39.2.1` is required here. Its R39 output is
`staging/uefi-r39.2.1/artifacts`. The staging helper requires checksummed release
metadata and rejects a firmware/BSP mismatch. TPM remains enabled by default.
The historical `--skip-uefi-ffc-pcie` firmware experiment is audited only for
R36.5.0 and is rejected for R39; Linux FFC support remains enabled.

| Audited profile | Target OS / kernel | Initrd index tools | USB/coldplug audit |
| --- | --- | --- | --- |
| R36.5.0 | Ubuntu 22.04 / 5.15.185-tegra | kmod 29 | systemd/udev 249 |
| R39.2.1 | Ubuntu 24.04 / 6.8.12-tegra, including NVIDIA's numeric ABI suffix | kmod 31 | systemd/udev 255 |

Other releases and unreviewed configuration changes are rejected by the relevant
guards. Finish package provisioning before applying the profile: package updates
can change the exact USB rules, units and logging configuration being audited.
Restore and review a selected option when its guard rejects changed inputs;
do not substitute the old release's hashes.

`--precompute-initrd` is the explicit optimization flag for each supported target. Every build first
refreshes the production initramfs after installing the final kernel and all
in-tree/out-of-tree modules. With this flag, the build then generates compatible
release-matched module indexes and promotes the optimized image to both `rootfs/boot/initrd`
and `bootloader/l4t_initrd.img`. NVIDIA's flash path can repopulate the first
from the second, so updating both is necessary. The final build stamp records
`precomputed_initrd=1` only after the two promoted copies agree.

The build also installs a self-contained, release-hash-guarded wrapper around
NVIDIA's staged `tools/l4t_update_initrd.sh`. Full flashing runs this updater
again before creating the disk image. The wrapper keeps that vendor refresh,
then reconstructs the exact audited stock `/init`, recomputes the optimized
indexes and checksum manifest against the refreshed modules, and publishes both
initrd copies. It uses the staged AArch64 kmod through `qemu-aarch64-static`, so
a packaged R39 image does not depend on the flash host having kmod 31. The
flashing initrd separately receives NVIDIA's flashing `/init`, which runs its
own unconditional `depmod` before loading drivers.

`tools/ark-initrd/manifest.json` records the staged helper hashes;
`last-refresh.json` records the final production initrd and kernel hashes.
Normal errors restore the previous initrd pair and stop image generation. A
hard interruption leaves `in-progress.json` and durable original images under
`tools/ark-initrd/recovery-*`; inspect and recover or rebuild that staging tree
before continuing. Do not delete the guard to bypass an incomplete update.
These tools are included in the self-contained flash package. Builds without
`--precompute-initrd` keep NVIDIA's original updater.

This opt-in also applies `ARK_ROOT_POLLING_V1` to the exact audited release's
`/init`, using separate R36.5.0 and R39.2.1 hashes. The helper checks the entire
original script's SHA-256 and the reviewed polling transformation's output hash before generating the image. It attempts
one immediate root mount only when retries are enabled. If that fails, all 50
original sleep-and-mount retries remain, including the attempt after 10 seconds
of scheduled waits (51 attempts total). Nonretry mounts retain their original
200 ms wait and single attempt, including encrypted-root callers. NVMe/MMC
device discovery checks every 20 ms for at most 500 checks, retaining the same
10-second scheduled wait budget; command overhead is additional. SD/NFS waits,
encryption commands, recovery branches, and module payloads remain unchanged.
The R39 script's additional T264, UFS and virtual-machine paths are preserved.
The shared mount helper retains its read-only/overlay behavior. These changes
remove 200 ms before a successful first retryable mount and can shorten device-detection
latency by up to another 180 ms; end-to-end savings still require measurement.
The earlier result table and artifact hashes below predate this polling change.

The required refresh runs in the Ubuntu 22.04 build environment. R39 invokes
NVIDIA's `tools/l4t_update_initrd.sh` without a path argument; that version finds
the BSP relative to its own location. R36 uses
`tools/l4t_update_initrd.sh -l <Linux_for_Tegra>`. The script repopulates the
rootfs initrd from the bootloader-side base, runs the staged rootfs's
`nv-update-initrd` against `/boot/Image` and its installed modules, and updates
both copies. The kernel Makefile's `make install` alone does not do this. Working
AArch64 QEMU/binfmt support remains required. The builder installs pinned native
kmod 31 under `/opt/ark-kmod31` for R39 while retaining Ubuntu 22.04's kmod 29 for
R36. The optimizer verifies tool versions rather than accepting the host default.

`build.sh --fast` means reuse the existing staged build tree. It does not select
the fast-boot profile. An unoptimized staged tree can be rebuilt using
`./build.sh JAJ --fast --precompute-initrd`. Once either initrd is optimized, a
subsequent `--fast` invocation fails before recompiling, with instructions to
start a fresh build or restore verified unoptimized sources. This applies even
without the optimization flag: NVIDIA's updater preserves `/init`, so silently
reusing an optimized input could retain an old optimization and mislabel it as
disabled. The runtime checksum fallback is a recovery mechanism, not a rebuild
procedure. Removing the staged flash-time wrapper alone does not make an
optimized pair acceptable to `check_initrd_source.py`. The build removes a
verified old wrapper only after the existing input guard has accepted the
restored unoptimized pair; it does not automatically undo the optimizations for
a rebuild.

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
  -v "$PWD/staging/uefi-r39.2.1/artifacts:/firmware:ro" \
  -w /workspace ark-jetson-builder:22.04 bash -euo pipefail <<'STAGE'
JAJ_L4T=/workspace/staging/JAJ/Linux_for_Tegra
JAJ_ROOTFS="$JAJ_L4T/rootfs"
cmp "$JAJ_L4T/kernel/Image" "$JAJ_ROOTFS/boot/Image"
cmp "$JAJ_L4T/bootloader/l4t_initrd.img" "$JAJ_ROOTFS/boot/initrd"

# Headless and no known utmp delay; enabled ARK services still start normally.
python3 scripts/configure_fast_boot.py apply "$JAJ_ROOTFS" --skip-utmp-delay
python3 scripts/stage_fast_boot_firmware.py \
  --l4t-dir "$JAJ_L4T" --product JAJ --artifacts /firmware --quiet-firmware
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
profiles remains separate from `--precompute-initrd`, which prepares the
production initramfs module indexes and audited root polling changes.

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
  validation, and the enabled ARK service set. On R39 it retains NVIDIA's
  `nv-load-display-modules`, `nv-load-gpu-libs` and `nv-graphics` services;
  selecting headless startup must not remove GPU/inference dependencies.
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
- `--scoped-usb-udev` replaces the USB start script's global udev settle with
  `udevadm trigger --settle` for its newly attached read-only loop device. This
  still waits for the `L4T-README` label rule. An external 120-second timeout
  bounds that wait, with a five-second kill grace; the existing service timeout
  also applies. The two cable-state replays use subsystem filters instead of
  reading properties from unrelated devices. All gadget functions, descriptors,
  and bridge/DHCP configuration remain unchanged. The helper
  requires the matching release, udev version, timeout executable and exact
  vendor-script/unit hashes. R36.5 requires udev 249 and 125 required rules.
  Its known ARK GPIO and fwupd rules are
  optional, with exact contents required when present. Both audited versions
  of `75-net-description.rules` are accepted; the newer version moves `net_id`
  after USB/PCI identity imports. R39.2.1 requires udev 255 and 129 required
  rules, with only the exact known ARK GPIO rule optional. Its separate audit
  retains the new NVIDIA USB controller/role handling and native `ip` commands.
  On R39 it also orders runtime setup after successful gadget/bridge creation
  and queues the handler's native start/stop calls with `--no-block`. These
  changes are paired so early cable events cannot configure a missing bridge
  or block the udev work that gadget startup awaits. R36 retains its existing
  behavior. To upgrade an already applied R39 scoped profile, restore it and
  reapply the same options; both changes are saved for exact restoration.
  Unknown rules, masks and custom unit
  dependencies cause rejection. Restore and review the audit after relevant
  package changes.
  It composes with `--direct-usb-runtime` but remains separately opt-in. Validate
  the loop backing image/label, RNDIS/NCM/ACM/mass-storage functions, DHCP and
  cable reconnect, including booting with no USB cable. Any improvement in
  target setup must be measured separately from host USB/DHCP timing.
- `--skip-lvm-monitor` removes existing LVM-monitor Wants enablement links and
  saves them for exact restoration. Use it only after confirming the deployment
  has **no LVM physical volumes, volume groups, logical volumes, root device,
  mounted filesystems, or swap**. Inspect `pvs`, `vgs`, `lvs`, `lsblk -f`,
  `findmnt`, and swap configuration on the intended device; an ext4 root alone
  does not establish this. An offline rootfs cannot prove the attached disks
  have no LVM. The helper does not mask LVM, remove its packages, or change
  storage activation; custom Requires links cause it to refuse the change.
  Package re-enablement or another unit dependency can start monitoring again.
- `--early-ark-api` lets the audited `system-manager` and `ark-ui-backend`
  services initialize alongside networking. They bind to loopback; nginx and
  the remaining ARK services retain their normal ordering. It copies the exact
  reviewed vendor units into reversible full overrides, removing only their
  network/nginx dependencies and retaining normal systemd default dependencies.
  Custom units, drop-ins and dependency directories are rejected. Restore and
  reapply after a vendor-unit update; stale full overrides must not hide package
  changes. This option conflicts with `--defer-ark-services` and requires a
  provisioned ARK-OS image with matching audited units, including the Noble
  package on R39. It does not change the API implementation.
- `--parallel-jaj-pcie-coldplug` is **JAJ-only** and requires a completed
  `target=JAJ` build stamp plus the audited native udev trigger unit, without
  customer overrides. It runs initial userspace coldplug for the C7 controller
  (`141e0000.pcie`, connected to the JAJ FFC) in a separate service alongside
  the normal device enumeration. The main trigger excludes exactly that sysname;
  the separate service replays its event. R36 retains its separate subsystem and
  device trigger commands. R39 retains the native combined trigger and its
  `module,block,tpmrm,net,tty,input` priority list. **Linux PCIe probing, link timeouts,
  the FFC controller and subsequent hotplug remain enabled.** No peripheral is
  disabled to obtain the timing change. Test the customer's FFC endpoint and
  its cold-boot/hotplug behavior before acceptance. The PAB/PAB_V3 profiles
  cannot select this JAJ-specific option. See the [coldplug implementation](../products/JAJ/fastboot/pcie-coldplug/README.md).
- `--rsyslog-kernel-logging` changes kernel-log ingestion only when the audited
  Ubuntu 22.04 or R39/Ubuntu 24.04 logging topology is present. It requires matching
  configuration and native-unit hashes, an enabled rsyslog service, its independent `imklog`
  input, and the existing `kern.*` route to `/var/log/kern.log`. Unknown included
  rules, competing loggers, unit overrides, unreviewed journal drop-ins, and
  separate or noncanonical log mounts are rejected. The helper adds only a reversible
  journald `ReadKMsg=no` drop-in; it does not add another kernel input or remove
  persistent log output. New kernel records remain available in `kern.log` and
  `dmesg`, while `journalctl -k` no longer receives them. Userspace journal
  settings remain unchanged. Default profiles retain normal journald ingestion.
  The Noble audit additionally pins rsyslog's AppArmor include tree and reload
  helper, the vendor journald forwarding/priority drop-ins, and cloud-init's
  existing log rule. It preserves confinement and all of those vendor files.
  Run both log-route checks below after boot: an offline configuration
  audit cannot establish runtime permissions, daemon health or storage writes.
- `--defer-ark-services` is optional and is deliberately absent above. It moves
  enabled ARK/UI services, nginx, jtop, and first-boot hotspot setup into a later
  startup transaction. It can postpone the functionality the customer actually
  needs. Reaching its target does not prove the application is ready.
- The initrd helper uses kmod 29 on R36 or kmod 31 on R39, matches the single
  module tree to Image, validates dependencies, and preserves existing module payloads. Runtime
  integrity checks skip `depmod` only while those modules/indexes still match;
  otherwise stock `depmod -a` runs. The guarded polling change retains the original delayed retry
  sequence and failure wait budget, adding an immediate attempt for retryable
  mounts. It does not remove USB, thermal control, or
  encryption setup. For R39, a required driver compiled into the kernel must
  appear in the matching `modules.builtin`; a missing module alone is insufficient.
  This manifest is included in runtime integrity verification. Rebuild the
  candidate after updating modules; do not reuse an earlier build's optimized initrd.
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

For JAJ experiments that also use the audited early API, parallel C7 coldplug
and independent rsyslog kernel logging, restore the previous profile first and
select the complete option set explicitly. The following also selects the
separate scoped USB udev experiment:

```sh
python3 scripts/configure_fast_boot.py restore "$JAJ_ROOTFS"
python3 scripts/configure_fast_boot.py apply "$JAJ_ROOTFS" \
  --skip-utmp-delay --direct-usb-runtime --skip-lvm-monitor --scoped-usb-udev \
  --early-ark-api --parallel-jaj-pcie-coldplug --rsyslog-kernel-logging
```

The no-LVM requirement still applies. For PAB/PAB_V3 use the corresponding
staged rootfs and omit `--parallel-jaj-pcie-coldplug`; a provisioned ARK-OS image
is required for `--early-ark-api`. Do not infer hardware validation from the
[completed bare-image build checks](fast_boot_validation.md#scope-and-completed-builds).

ARK services remain enabled with these combinations. LVM-monitor duration can
overlap NVMe device discovery; measure the resulting external readiness time
instead of adding individual service durations as predicted savings.

`configure_fast_boot.py restore ROOTFS` restores its saved default target,
service links, utmp override, USB start/state scripts, API/coldplug overrides and
journald drop-in, including ownership. It refuses conflicting external edits.
Reapply and `status` re-audit selected logging/coldplug inputs; restore and review
the option again after a relevant package update. It does not undo an initrd or firmware replacement. The saved
stock initrd can restore both initrd locations only when it still matches the
current kernel/modules. Otherwise regenerate a matching stock initrd. Keep a
known-good QSPI image and the customer's NVMe backup for recovery.

## Deploy and measure

Wait for explicit handoff of the test device before using it. An R36-to-R39
upgrade requires the matched firmware, kernel, modules and root filesystem;
the historical R36 boot files and disk image are not an R39 deployment.
For an existing installation on the same compatible release,
`./flash.sh JAJ --bootloader-only` deploys the staged firmware
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
The selected interval is **host SCPI power-on command send to host reception of
`JAJ_API_READY`**, emitted by the temporary [target probe](../scripts/emit_boot_ready.py)
after a local `http://127.0.0.1/api/system/info` response passes the HTTP/JSON
check. The probe stores no response body. This interval includes the confirmed
approximately two-second carrier power-on-reset (POR) delay, probe scheduling,
and UART transport/capture latency; it is not an electrical-edge measurement.

For hardware-metadata acceptance, run the temporary target probe with
`--ark-os-ready`. In addition to HTTP 200 and strict JSON, it requires
`device_type=jetson`, `hardware.type=jetson`, and non-placeholder strings for
hardware `model`, `module` and `l4t`. It emits the same marker with
`"criterion":"ark_os_metadata"`; it does not print or save the response body or
device serial fields. This is a stronger criterion than API availability, but
still does not validate cameras, inference, flight links or all ARK features.
Use one selected probe criterion per timing capture, or explicitly match the
metadata criterion on the host so an earlier generic marker cannot satisfy it.

A temporary probe unit should use **`Type=simple`**, with ordinary dependencies
after basic startup. For example, after placing the probe at the shown temporary
target path and confirming the board's debug UART device:

```ini
[Unit]
Description=Temporary ARK-OS metadata readiness probe
After=basic.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /var/tmp/emit_boot_ready.py --ark-os-ready --serial-output /dev/ttyTCU0

[Install]
WantedBy=multi-user.target
```

Remove this diagnostic unit after testing. Do not use `Type=oneshot` for a probe
wanted by `multi-user.target`: the target would wait for the probe while jtop
is ordered after that target, creating a readiness dependency cycle. Earlier
HTTP/JSON captures used such a oneshot probe. Their API-availability observations
remain historical measurements, but jtop was artificially postponed; they do
not establish normal jtop startup timing or metadata readiness. Track stronger
probe results and their exact unit configuration in the [validation record](fast_boot_validation.md).

Record host HTTP-over-USB readiness separately. It also depends on USB gadget
setup and host networking, which can finish after the local API is working.
`systemd-analyze` and the target probe's kernel uptime exclude the earlier power
and firmware interval and must not replace the selected host-clock measurement.

With `--rigol`, the capture helper requires an explicit `--expected-serial`,
records initial output/protection settings, and refuses a latched OCP/OVP fault
before changing output. Its final output/alarm check classifies an OFF output or
protection trip as `supply_fault`, rather than a software readiness timeout. It
never clears a latch or changes protection limits. Retain supply-fault attempts
in the experiment log and separate them from valid software boot timings.

When `--rsyslog-kernel-logging` is selected, perform **two distinct checks**
from an authorized root shell on the target. First, confirm rsyslog is active
and inject a unique userspace record through `/dev/kmsg`:

```sh
systemctl is-active rsyslog.service
ark_kmsg_canary="ARK_KMSG_CHECK_$(cat /proc/sys/kernel/random/uuid)"
printf '<6>%s\n' "$ark_kmsg_canary" > /dev/kmsg
# After rsyslog has processed the record:
grep -F -- "$ark_kmsg_canary" /var/log/syslog
```

The audited `imklog` configuration accepts non-kernel facilities, so this checks
its kernel-log input path into `syslog`. It does **not** prove the `kern.*` route:
Linux's `devkmsg_write()` forces userspace writes with a kernel-facility prefix
such as `<6>` to `LOG_USER`. A working configuration therefore need not put this
canary in `kern.log`. A userspace `logger` command would not exercise `imklog`.

Second, identify a genuine kernel-origin message in the **current boot's**
`dmesg` and verify its message and precise kernel timestamp in `kern.log`. With
the optional asynchronous BPMP setup enabled, its completion record is suitable:

```sh
dmesg --facility=kern --time-format=raw | grep -F 'tegra-bpmp bpmp: debugfs initialized asynchronously'
grep -F 'tegra-bpmp bpmp: debugfs initialized asynchronously' /var/log/kern.log | tail -n 1
sync -f /var/log/syslog /var/log/kern.log
```

Compare the bracketed kernel timestamp and text in both outputs, allowing
whitespace differences, and ensure the file record belongs to this boot rather
than accepting an older matching message. If asynchronous BPMP is disabled,
select another identifiable current-boot kernel record; no module loading is
needed. Retain both results with the capture and restore normal journald
ingestion if either route fails validation. Existing rsyslog buffering and
rotation remain in use; flushing these files does not promise survival of other
records that were still buffered when power was cut.

NVIDIA boot validation must finish successfully before the next reboot/power
cycle. Do not trigger a rapid test loop solely from the earlier application or
SSH marker. Preserve at least one usable recovery/diagnostic path when reducing
serial output.

## Historical R36.5.0 prototype results

The following R36.5.0 comparisons retain their original endpoint and configuration.
For later R36 captures and their board coverage, use the
[historical validation record](fast_boot_validation.md). They do not predict
R39 boot time or validate its application, GPU or camera paths.

The following cold starts used the same matched `5.15.185-tegra #5` kernel,
modules, and optimized initrd, with the BPMP parameter changed between the
synchronous and asynchronous cases. **All three used the temporary no-TPM
firmware experiment; these are not measurements of the default TPM-enabled
profile.** Times are seconds from the host SCPI command reference.

| Capture | Local API, observed through UART | Host API over USB | Kernel + userspace (`systemd-analyze`) |
| --- | ---: | ---: | ---: |
| `cold-new-kernel-sync-01` | 15.045 | 18.096 | 3.390 + 4.449 = 7.839 |
| `cold-new-kernel-async-02` | 13.885 | 16.296 | 2.418 + 4.197 = 6.616 |
| `cold-new-kernel-async-03` | 14.071 | 16.897 | — |

The two valid asynchronous observations span 13.885–14.071 seconds, with a
maximum of 14.071 seconds. The local API preceded host HTTP access by about
2.4–3.1 seconds in these runs. This small sample supports further testing; it
does not establish the worst case or a result under 10 seconds. Captures are
identified by their `metadata.json` and UART/timeline files on the bench host.

`cold-new-kernel-async-01` is a separate **invalid boot-timing attempt**: the
supply's 4.9 A OCP protection tripped before firmware UART output. The earlier
helper recorded `readiness_timeout`; investigation identified a supply fault.
It is retained as a fixture failure, excluded from the two valid boot timings,
and must not be attributed to the kernel change. Retry used the same protection
limits. The third asynchronous capture used the improved supply checks and
ended with output ON and no OCP/OVP alarm.

For context, the original cold SSH-banner observation was 33.371 seconds; that
is a different endpoint from the selected local API. An earlier kernel with
the optional no-TPM firmware reached the local API in 13.649 seconds and host
HTTP in 16.302 seconds. The separate no-TPM comparison suggested only about
half a second of firmware benefit. TPM removal remains an explicit experiment,
absent from the default profile; see its [requirements and limitations](../products/JAJ/fastboot/README.md#optional-no-tpm-timing-experiment).

The matched kernel experiment used these SHA-256 artifact identities; a later
rebuild needs its own recorded hashes:

```text
a13342d52ea607752815dc6afb0276de49c4956e9920b6361cf8b1be677e5661  Image
6c1315eb07f9200a355e8e383be8a08010a2231197f2a69ff73977982722e278  initrd
63abfb9267398d1a1bcf9a4d0d04a7a48bb7458fe96efdf27691b2798bafd11a  jaj-kernel-candidate-v1.tar.zst
```

All three supported products include [optional asynchronous BPMP debugfs setup](../products/JAJ/fastboot/bpmp-debugfs.md).
The default remains synchronous; enable `jaj_fastboot.bpmp_debugfs_async=1`
only after installing the matched patched kernel/modules/initrd and validating
diagnostic consumers. In the asynchronous diagnostic capture, BPMP debugfs
finished at kernel uptime 1.560 seconds, and the subsequent clock query and
NVIDIA boot validation succeeded. Early debugfs consumers must still wait for
completion. Keep global `debugfs=off` out of the profile: the stock NVIDIA
initramfs treats a failed debugfs mount as an error and waits 30 seconds.

The final acceptance record must include the selected cold-power-to-API
observation, exact image/configuration, run count, spread, maximum, and failures.
An under-10-second systemd total alone is insufficient; the measured carrier,
firmware, and application intervals remain part of the requirement.

Later diagnostic captures used the matched #6 kernel with an unbound BPMP
worker and the guarded faster root polling. With temporary `ReadKMsg=no`, EFI
partition automount and initcall logging, the local API marker was 13.571 s.
Adding the two early API unit overrides produced 12.818 s in one cold capture
(0.753 s lower). Both still used the approved temporary no-TPM firmware. These
are single diagnostic observations, not acceptance results; the unbound queue
alone did not remove the remaining CPUfreq probe stall. Logging is now available
through the explicit guarded `--rsyslog-kernel-logging` option; it remains absent
by default. ESP automount remains a separate diagnostic and is not implemented
by the rootfs profile.

HTTP 200 with valid JSON establishes API availability only. The installed
ARK-OS retry logic can initially return default Jetson metadata, and the old
oneshot probe additionally postponed jtop. Use the `Type=simple` probe with
`--ark-os-ready` to measure populated metadata independently. That criterion
still does not prove a camera frame or inference output.
A customer's actual image must provide a readiness marker for its required
functionality, then pass repeated cold tests against the same power reference.
