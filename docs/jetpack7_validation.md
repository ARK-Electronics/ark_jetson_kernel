# JetPack 7.2.1 validation record

This record covers the R39.2.1 / Ubuntu 24.04 port and its fast-boot helpers on
a JAJ with Orin NX 16GB and NVMe storage. **All three final-profile cold runs
passed readiness, but the under-ten-second target was not achieved.** UART
terminal readiness was 15.808–16.184 seconds, local HTTP/JSON 13.541–14.066 seconds,
and populated ARK metadata 18.771–19.006 seconds. The earlier failed candidate
and failed pre-fix repeat remain recorded below.

These are limited bench checks, not full product or customer-workload
qualification. No camera is connected to the test JAJ.
The [R36 timing record](fast_boot_validation.md) is historical and must not be
used as a measurement of this R39 image.

## Build and application identity

| Component | Validated selection |
| --- | --- |
| JetPack / L4T | 7.2.1 / R39.2.1 |
| Root filesystem | Ubuntu 24.04 Noble, arm64 |
| Kernel | `6.8.12-1021-tegra`, NVIDIA Linux 6.8.12 plus the ARK carrier/configuration changes; corrected-image hashes below |
| ARK-OS | `ark-os-jetson-noble` 1.2.0, source `423570c1021174ca15801c5182ec288862865ad4` |
| Python / jetson-stats | Python 3.12.3 / jetson-stats 4.3.2 |
| NVIDIA camera/multimedia quartet | Native `39.2.1-20260806224157` camera, GStreamer, multimedia and multimedia-utils packages |
| Module indexes | Target-compatible kmod 31; separate R36/kmod 29 guards retained |

The Noble application package was built from the pinned source because the
published 1.2.0 asset was the Jammy/Python 3.10 package. Package ABI checks,
application dependency/import checks, native NVIDIA plugin loading in the
provisioning chroot, and final `apt-get check` passed. The backend library path
used by the chroot check was process-local; NVIDIA's runtime GPU selection was
preserved. See [provisioning details](jetpack7_provisioning.md) for package
provenance and reproducibility limits.

JAJ kernel, in-tree/out-of-tree modules, both NVIDIA display backend trees,
device trees and refreshed initramfs were built. Prepared headers were checked
against the custom configuration and module symbol versions. The flash-time
initrd hook retains NVIDIA's module refresh and recomputes the audited indexes
afterward with target kmod under QEMU, including in a packaged BSP. The native
header probe was built and loaded on the actual corrected kernel, rather than
only comparing host-generated files.

All 282 final helper/test cases were exercised successfully: 281 passed in the
Ubuntu 22.04 builder, and its one skipped systemd dependency-ordering case passed
separately on the host where `systemd-analyze` was available. The generated units
also passed native systemd verification on JAJ. These tests supplement the
hardware checks below; they do not qualify untested peripherals.

## Completed checks and remaining acceptance

| Area | Evidence / current limit |
| --- | --- |
| JAJ full flash and boot | Corrected image fully flashed and booted; SSH over the NVIDIA USB gadget works. ARK first-boot and native bootloader/rootfs validation services completed successfully. First-boot provisioning is distinct from repeat cold-boot timing. |
| CUDA execution | Passed again on the corrected JAJ image: loaded `libcuda.so.1`, created a GPU context, allocated/copied memory, JIT-compiled and launched a 32-element integer-add kernel, synchronized and checked every returned value. This is a CUDA smoke test, not TensorRT/model inference or sustained-load qualification. |
| Native module headers | A small external module compiled against the installed prepared headers and loaded successfully on the corrected JAJ kernel. A GCC minor-version warning remained; this verifies the probe, not every external module. See [kernel development](kernel_development.md). |
| ARK HTTP endpoint | All three final-profile cold runs passed generic HTTP/JSON and populated Jetson metadata after fixing the nginx loopback mismatch; earlier failed captures remain below. |
| Power/display startup | Final health checks pass the native display, nvpower and nvpmodel services. `nvpmodel -q` reports the native configured 40W mode, ID 4; jtop is active. |
| WiFi firmware loading | Corrected running kernel reports all three required loader options enabled. Packaged WCN6855 `.zst` files remain intact; ath11k reports the loaded firmware and its WiFi netdev is UP. No WiFi credentials were inspected; controlled association/throughput qualification is outside this check. |
| Wired Ethernet | Interface is present but reports no carrier. An external PHY/link/traffic test was not performed. |
| JAJ C7 / FFC | Live device-tree status is `okay` and the controller is bound to `tegra194-pcie`. The connector is empty; an external endpoint and hotplug remain untested. |
| Fast-boot rootfs profile | All nine live states tracked by the profile manifest match exactly. GPU, Bluetooth and nginx patch hashes match. Native systemd verification passes; NVIDIA GPU, thermal and boot-validation services remain intact. |
| Camera | No camera attached. Native Argus source, conversion and V4L2 codec plugins all load for registration inspection with no registry updates. This does not establish sensor capture, restart behavior, encoding or camera-to-inference readiness. |
| PAB / PAB_V3 | Private isolated R39 device-tree builds and structural overlay checks passed. No R39 full-image or hardware acceptance for these carriers is claimed. |
| Bluetooth / Remote ID | The coexistence correction automatically registers `hci0` on the tested Qualcomm USB controller, and rid-transmitter starts successfully after cold boot. Bluetooth peer connections and Remote ID reception/transmission qualification were not tested. A physical Realtek controller was not available. |
| MAVLink / FMU | No external ARK FMU is attached. The configured USB endpoint is absent and the scanner finds no matching device; the service remains enabled. Telemetry transport requires the matching external hardware. |
| Remaining service health | Final snapshots have only the expected MAVLink-router failure for the absent FMU; that service remains enabled. Earlier failures demonstrate why failed-unit state alone is insufficient for readiness. |

The first R39 image exposed two migration issues worth preserving as regression
checks. Noble supplies ath11k/WCN6855 firmware in `.zst` files, so the effective
kernel must enable `CONFIG_FW_LOADER`, `CONFIG_FW_LOADER_COMPRESS` and
`CONFIG_FW_LOADER_COMPRESS_ZSTD`. The build validates the resolved configuration
before compilation, including reused `--fast` trees. The corrected device
confirmed these options in `/proc/config.gz` and successfully loaded the WiFi
firmware without altering the packaged compressed files.

Separately, immediate DRM loading raced NVIDIA's GPU udev power-policy handler.
The resulting golden GPU context prevented the requested mask changes, leaving
`nvpmodel -q` with an unset-mode warning even though that query returned zero.
Jetson-stats then failed while parsing the result. The
[guarded correction](../scripts/patch_gpu_power_order.py) synchronizes only the
Orin GPU event, validates native power-policy application before DRM, and places
that producer before System Manager. This also orders ARK's early jtop library
probe, which can run `vulkaninfo` before connecting to the jtop service. It keeps
the native mode choice, GPU drivers, Argus and display-manager services intact.
The corrected image passed this sequence and retained the configured mode.
The scoped udev wait is bounded to 30 seconds, native policy application to
30 seconds and the mode query to 10 seconds; an error still fails visibly.
See the [provisioning guide](jetpack7_provisioning.md) for failure behavior.

The [Bluetooth preference correction](../scripts/patch_bluetooth_preference.py)
loads the vendor Realtek driver first, then allows generic `btusb` to bind other
supported devices. This removes the vendor rule that suppressed all generic
USB Bluetooth when `rtk_btusb` was installed. The tested Qualcomm controller
now registers automatically at boot; keeping the Realtek driver first is
source/test coverage, not physical Realtek qualification.

The first reduced R39 UEFI candidate reached ReadyToBoot without starting Linux
and is excluded from timing comparisons. The corrected
[single-launcher boot-order patch](../products/JAJ/fastboot/r39.2.1/uefi-single-boot-order.patch)
promotes the selected embedded Boot Application while preserving the other EFI
entries. The rebuilt candidate boots Linux, reports that application as
BootCurrent and first in BootOrder, retains the stock entries, and uses timeout
0. Slot A is current/active, both slots are normal, and native NVIDIA validation
has completed. This was checked on the device, not inferred from staged files.

Firmware provenance records edk2-nvidia revision
`7e9f9ec4de8d979807683a7b4605a15ec3299b89` and source fix
`r39-single-boot-order-v1`; the wrapper records the exact before/after source
hashes in `source-patches.json`. Fresh R39 staging requires the fix marker in the
checksum-protected `artifact-release.json`, preventing reuse of the failed older
artifact. Existing-manifest verification and restore remain available for
rollback.

The second corrected-UEFI cold run exposed two independent rootfs startup
races. USB runtime setup ran before `l4tbr0` existed; its unit returned success
despite bridge/DHCP errors, leaving host SSH unavailable. Separately, nginx
resolved its `localhost` proxy upstream to `::1`, while the ARK gateway listened
on `127.0.0.1`, producing persistent 502 responses. Direct gateway and System
Manager requests returned HTTP 200, and GPU initialization completed normally.
The guarded nginx correction was applied to staged and live files; `nginx -t`,
reload, full local HTTP and populated metadata checks passed. The USB ordering
correction also passed the three fresh cold runs: gadget setup completed before
the runtime service began, and runtime completed successfully with the bridge
UP and addressed. Restarting the USB runtime for recovery does not turn the
earlier failed capture into a passing result. The post-fix rows below identify
the changed rootfs configuration.

PAB and PAB_V3 validation compiled both normal and Super device trees for SKUs
0000, 0001, 0003, 0004 and 0005, checking all 20 resulting model strings. Their
unwired C7 controller remains disabled, including after the matching NVIDIA SKU
overlay is merged. Default boot-order and IMX219 overlays resolve with four
camera nodes on PAB and two on PAB_V3. Alternate IMX477/IMX708 overlays also merge
with reciprocal sensor endpoint links. Existing device-tree warnings remain;
structural success does not validate electrical interfaces or camera operation.
JAJ's exposed C7/FFC support remains enabled.

## Cold-boot measurements

Use the procedure in [fast_boot.md](fast_boot.md#deploy-and-measure), with the
same image/profile and power reference for every recorded run. The host clock
starts when the SCPI power-on command is sent and ends when the host receives
the chosen UART marker. That interval includes carrier POR and firmware; it is
not a measurement of the electrical power edge. Record UART terminal readiness,
local application readiness and USB-host connectivity separately.

The ARK stand-in criterion requires local HTTP 200, strict valid JSON, and
populated Jetson model/module/L4T metadata. HTTP success with placeholder fields
does not pass. Diagnostic marker services must use `Type=simple`, so waiting
for jtop cannot hold `multi-user.target` and thereby prevent jtop from starting.
ARK-OS 1.2.0 initializes the jtop retry timestamp to zero and suppresses the
first connection attempt until kernel monotonic uptime reaches 10 seconds.
The final captures place kernel startup about 7.60–7.65 seconds after the power
command, including carrier POR, firmware and UART receipt delay (the earlier
successful pre-fix capture was about 7.46 seconds). That source-level gate,
followed by jtop/library discovery, explains why populated metadata arrives
later than generic HTTP. Neither terminal nor metadata readiness proves that an attached
camera or customer inference pipeline is usable.

| Corrected matched image | Cold UART terminal | Cold local HTTP/JSON | Cold populated ARK metadata | Host SSH banner | Host HTTP/JSON | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| Stock R39 UEFI + corrected fast OS | 22.636 s | 20.318 s | 25.282 s | 22.512 s | 22.405 s | Cold baseline; EFI timeout 5 s |
| Reduced R39 UEFI, first candidate | No Linux boot | No Linux boot | No Linux boot | No Linux boot | No Linux boot | Reached ReadyToBoot then stalled; excluded from performance comparisons |
| Corrected reduced UEFI run 1 | 15.582 s | 13.481 s | 18.561 s | 12.359 s | 13.465 s | Valid cold capture; no supply fault |
| Corrected reduced UEFI run 2 | 15.634 s | Not observed in 60 s | Not observed in 60 s | Not observed in 60 s | Not observed in 60 s | Pre-fix USB bridge ordering and nginx IPv6-upstream failures; excluded from post-fix pass set |
| Corrected UEFI + USB/nginx fixes, run 1 | 16.184 s | 14.066 s | 19.006 s | 15.856 s | 16.418 s | Valid fresh capture; no supply fault; USB setup completes before runtime |
| Corrected UEFI + USB/nginx fixes, run 2 | 16.099 s | 13.974 s | 18.780 s | 12.692 s | 13.960 s | Valid matching repeat; no supply fault; USB ordering and native boot validation pass |
| Corrected UEFI + USB/nginx fixes, run 3 | 15.808 s | 13.541 s | 18.771 s | 12.441 s | 13.579 s | Valid matching repeat; no supply fault; final health checks pass |

| Final identity / health | Result |
| --- | --- |
| Source and kernel release | Reviewed [PR #109 implementation](https://github.com/ARK-Electronics/ark_jetson_kernel/pull/109), built from the reviewed worktree before its final commit; running kernel `6.8.12-1021-tegra`. Exact tested artifacts are identified below. |
| Image SHA-256 | `a9e8ed55c035c14ed419b8544b013b475ec545de0566fabf9b9d4236cd91223f` |
| Initrd SHA-256 | `a8eb5314a926618b69f1b380c506c8de3366e6a0903fdeea47e70674e3f75bce` |
| UEFI artifact SHA-256 | `cecf920f4f90bb28e042f6392205c01eeef501dacffeffb22470eb82ac466727` |
| UEFI boot state | Booted/active slot A, both slots normal, version/ESRT 39.2.1, capsule status 0; embedded Boot Application first, other entries retained, timeout 0 |
| Rootfs profile and startup verification | Profile SHA-256 `b3fa06575fa5902797dd9020c1f9b3d7630d1023dda1d2df792485def760a392`; all nine tracked live states match, GPU/Bluetooth/nginx guards pass, and USB runtime starts after completed gadget setup. |
| NVIDIA boot validation, nvpmodel and jtop | Native bootloader/rootfs validation succeeded after corrected UEFI boot; nvpmodel 40W/ID 4 and jtop active. |
| Cold USB reconnect and persistent kernel logging | All three final cold captures pass host SSH/HTTP. Rsyslog configuration is valid and active; a current-boot kernel-facility message from the kernel ring buffer is present in persistent `kern.log`. |
| Final failed-unit inventory | Only MAVLink router failed; its configured external FMU is absent. |

All three final-profile captures completed with readiness observed and no
supply fault. NVIDIA validation completed, slot A remained current/active and
both slots remained normal. Final checks repeated the CUDA integer-add probe,
confirmed WiFi UP and automatic `hci0`, verified retained C7 support, and loaded
the three camera plugins for inspection. A userspace `/dev/kmsg` canary was not
used as evidence for `kern.log`: Linux assigns that injected record a userspace
facility. Persistence was verified using an actual current-boot kernel message.

Temporary benchmark services, readiness probes and private diagnostic/shutdown
helpers were removed after testing. The API still returns HTTP 200 with populated
metadata after cleanup; only the expected absent-FMU service failure remains.
The JAJ was left powered on and operational with the final profile.

A supply fault or failed NVIDIA boot validation invalidates the affected run.
Kernel/systemd startup totals omit the earlier carrier and firmware interval.
Every final recorded readiness endpoint remains above ten seconds. Reducing the
ARK/jtop first-attempt gate requires application work and would not itself meet
the UART target or establish camera/inference readiness.
