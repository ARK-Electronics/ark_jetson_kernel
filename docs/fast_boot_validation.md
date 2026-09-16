# Fast-boot validation record

Bench date: 2026-09-16. **Under 10 seconds has not been achieved.** The historical
13–14 s results measure generic API availability. Later TPM-enabled observations
reached that endpoint in approximately 11.2–11.4 s. A stronger check requiring
real Jetson metadata reached 17.636 s with the installed ARK-OS application.
Most configurations have one observation; terminal and metadata results are separate.

## Scope and completed builds

The opt-in implementation covers JAJ, PAB and PAB_V3 on **L4T R36.5.0 with NVMe**.
Hardware timing and boot validation were performed only on JAJ with Orin NX 16GB.
See the [procedure and options](fast_boot.md), [firmware profile](../products/JAJ/fastboot/README.md)
and [BPMP debugfs changes](../products/JAJ/fastboot/bpmp-debugfs.md).

| Product | Completed build | Offline validation | Hardware/application coverage |
| --- | --- | --- | --- |
| JAJ | `c2993b3`, kernel `5.15.185-tegra #7`, precomputed initrd | Staged Image/initrd copies match the identities below | JAJ cold tests with installed ARK-OS; latest TPM-enabled firmware passes NVIDIA boot validation |
| PAB | `c2993b3`, kernel `5.15.185-tegra #2`, precomputed initrd | Kernel, modules, initrd, five SKU DTBs, overlays and firmware staging verified | No PAB hardware test; ARK-OS was not provisioned |
| PAB_V3 | `c2993b3`, kernel `5.15.185-tegra #1`, precomputed initrd | Kernel, modules, initrd, five SKU DTBs, overlays and firmware staging verified | No PAB_V3 hardware test; ARK-OS was not provisioned |

All three builds completed in Ubuntu 22.04 containers. The commit is the recorded
build stamp; these experimental builds also contain the reviewed working-tree
changes. The artifact hashes identify the actual outputs.

Each PAB/PAB_V3 validation verified 17 boot-module payloads unchanged and matching
the rootfs, 30 initrd runtime checksum entries, and product-correct DTB models for
SKUs 0000/0001/0003/0004/0005. PAB retains its quad IMX219 overlay; PAB_V3 retains
its dual IMX219 overlay and `spi_ks8995.ko` module. Both staged the default reduced
TPM-enabled firmware, NVMe-priority overlay and quiet-firmware changes with matching
hashes. Their rootfs profiles enabled headless startup, utmp-delay removal and
native USB runtime calls; LVM-monitor removal, early ARK API startup and ARK service
deferral were absent. These are completed bare-image build checks, not validation
of a provisioned customer image, cameras, networking or boot time on those boards.

## Timing method

The customer reported **over 50 s**, without a captured start reference or readiness
endpoint. The first instrumented cold baseline was **33.371 s to an SSH banner**;
no original-stock API timing exists. Do not subtract the latest API result from
either number to claim an application-startup saving.

Cold times start at the host's SCPI power-on command send, rather than a measured
electrical edge, and include the carrier's approximately 2 s POR delay. Local API
readiness is host reception of `JAJ_API_READY`, emitted by a temporary target probe
after loopback `/api/system/info` returns HTTP 200 and valid JSON. Host API timing
uses that endpoint over USB networking. SSH means an SSH protocol banner. Probe
scheduling, polling and UART transport add observation latency; these endpoints
must remain separate. Kernel/systemd totals omit the earlier power/firmware time.

## Cold comparisons and observed savings

Times are seconds. Each reduction compares the **same endpoint** within its row.
These are observations, not independent additive savings; do not sum the rows.
Most steps combined changes or had one run. Earlier diagnostic settings are not
all defaults of the supported profile.

| Change / comparison | Endpoint: before → after | Observed reduction | Interpretation |
| --- | --- | ---: | --- |
| Reduced NVMe UEFI plus quieter early firmware | SSH: 33.371 → 23.216 | 10.155 | Baseline OS restored for this comparison; initial firmware lacked ESRT/FMP and was superseded |
| Combined quiet kernel, precomputed initrd, utmp/LVM wait experiments and headless startup | SSH: 23.216 → 17.952; host API: 23.356 → 18.711 | 5.264 SSH; 4.645 API | Combined OS effect; individual warm observations below |
| Restore required ESRT/FMP support, then repeat | SSH: 17.952 → 17.592 → 16.896 | Unassigned | Correctness repair and repeat, not an ESRT speed claim; NVIDIA validation passes |
| Native USB runtime calls plus removal of LVM monitoring on the confirmed non-LVM device | SSH: 16.896 → 15.163; host API: 16.948 → 15.277 | 1.732 SSH; 1.672 API | No local API marker existed yet; USB setup can affect these endpoints |
| Optional no-TPM firmware | UART ExitBootServices: 6.989 → 6.501 | About 0.488 | Firmware-stage observation only; first local API marker was 13.649; default firmware retains TPM |
| Async BPMP debugfs, matched #5 kernel/modules/initrd | Local API: 15.045 → 13.885 / 14.071 | 1.159 / 0.974 | Two valid async runs; temporary no-TPM firmware in all three |
| Journald `ReadKMsg=no` plus ESP automount diagnostic | Local API: 14.071 → 13.890; host API: 16.897 → 13.947 | 0.180 local; 2.950 host | Most observed gain was host access; neither diagnostic is a rootfs-profile default |
| #6 kernel with unbound BPMP worker plus guarded initrd polling | Local API: 13.890 → 13.571 | 0.319 | Combined change, one run; no independent polling saving measured |
| Early loopback API services, retaining other ARK services | Local API: 13.571 → 12.818 | 0.753 | One run; still temporary no-TPM firmware |
| #7 kernel with 2000 ms deferred BPMP debugfs | Local API: 12.818 → 12.270 | 0.548 | One non-tracing run; extra udev tracing instead yielded 13.510 |
| Restore default TPM-enabled firmware, repeat | Local API: 12.270 → 11.813 | Unassigned | Run variation/configuration repeat; not evidence that restoring TPM accelerates boot |
| Parallel JAJ C7 PCIe coldplug, TPM enabled | Local API: 11.813 → 11.371 | 0.443 | One observation; latest SSH 10.946 and host API 11.335 |
| Earlier broad C4-only and first C7-only UEFI candidates | Rejected; automatic B-slot fallback | — | No timing saving assigned; C7 UART revealed an enumeration assertion, corrected in the later compatibility hook |

The original-to-latest SSH comparison is 33.371 → 10.946 s, an observed **22.424 s
reduction**. It measures host SSH access, not the unknown original application time.
The first recorded host API result, already using reduced firmware, was 23.356 s;
the latest was 11.335 s. There is no original-stock local API baseline.

## Later terminal and metadata checks

The unchanged TPM-enabled profile repeated at 11.283 s generic API, with an
SSH banner at 10.962 s and a UART shell prompt at 12.234 s. An earlier 11.220 s
API capture also booted the stable B-slot firmware: despite its candidate label,
it is not evidence of a C4 firmware saving. The fastest recorded SSH banner in
these captures is 10.946 s; a banner alone does not prove an authenticated shell.

After restoring the stock ESP mount and making both temporary probes nonblocking
`Type=simple`, the generic API marker arrived at 11.592 s, the UART shell prompt
at 12.088 s, and the stronger `ark_os_metadata` marker at **17.636 s**. The latter
requires the actual Jetson type/model/module/L4T fields rather than default JSON.
The installed jtop manager suppresses its first connection until kernel uptime
10 s. These are different readiness criteria and must not be compared as one
application metric. The application code was not modified for these results.

An earlier metadata attempt is excluded: its temporary oneshot probe held
`multi-user.target`, while jtop was ordered after that target, creating a wait
cycle. The Rigol USB connection also dropped during that capture. Both probes
were changed to `Type=simple` before the valid metadata run. Historical generic
API observations remain as measured, but their later jtop startup was influenced
by that earlier diagnostic ordering.

## Scoped USB startup comparison

With corrected C7-only TPM firmware confirmed in slot A, narrowing the USB start
script's loop-device wait and subsystem scans produced the following single-run
comparison. The script's gadget descriptors/functions and native runtime handler
were retained; loop-device labeling and the ACM, mass-storage, NCM and RNDIS
functions were checked after boot.

| Endpoint | Previous startup script | Scoped USB startup | Observed reduction |
| --- | ---: | ---: | ---: |
| UART shell | 12.152 s | 12.186 s | None |
| SSH banner | 14.789 s | 11.636 s | 3.153 s |
| Host API | 14.486 s | 12.078 s | 2.409 s |
| Generic local API | 11.832 s | 11.834 s | None |
| Real Jetson metadata | 18.132 s | 18.049 s | Unassigned variation |

This improves external USB access in this comparison, without establishing a
terminal or application-initialization saving. It does not replace peripheral
traffic or physical reconnect testing. `--scoped-usb-udev` is opt-in and rejects
unknown changes to the audited udev rules or native USB units before mutation.

## Earlier warm-reboot observations

These exploratory per-step comparisons are reboot-command to SSH response,
including shutdown and host network recovery. They are separate from cold tests;
only the named step was intentionally changed, but each has one observation.

| Cumulative configuration | Warm SSH time | Reduction from previous warm observation |
| --- | ---: | ---: |
| Initial image | 37.235 | — |
| Kernel quiet plus UEFI timeout 5 → 0 | 30.976 | 6.259 |
| utmp sleep removal plus LVM-monitor wait experiment | 29.233 | 1.744 |
| Precomputed initrd module indexes with integrity fallback | 25.607 | 3.626 |
| Headless default target | 25.201 | 0.405 |

The warm sequence improved by 12.034 s overall. Quiet and timeout changed together;
the extlinux `TIMEOUT 30` line did not add a menu wait in the single-entry setup.
Do not attribute another three seconds to changing that line.

## Artifact identities

SHA-256 for the current completed builds and firmware candidates. These identify
raw Image/initrd/UEFI files, not a provisioned disk image or signed flash package.
Earlier timing experiments used earlier matched kernels where stated above.

```text
de0e57b1cc3f458dd9e15bf4c0cfe0845dc9a00157e5151aac591f6ed8a3c9a9  JAJ/Image
a50433beeeb744b2633021cf7dddd0328cb0ce7aeed6da5a42a1b8d0b4ba9655  JAJ/initrd
dfb09d906a47c172d505cf3c5017e8ecc3461e087e49e03678ffd6d270b65352  PAB/Image
21ef9e2a991d3d58a70371ce651cbba62d3b231e10831a5352b613811e5030c7  PAB/initrd
9095bc710a255e91552000f4e307a44018aa367830ab2517172548f2d69f6b1f  PAB_V3/Image
29f2b6a500140a02c61e97205788ac64e950fe2d18657e338856c7e59e4e899e  PAB_V3/initrd
12532af62d1f589153727ec6b7047995a697176457ed983b954646601a5c038f  default-TPM/uefi_jaj_nvme_RELEASE.bin
2ee1118982d2efbe09f7a64d7225cbbba979df5a45eeb98a3b1c5dfc4a37b88a  corrected-C7-only-TPM/uefi_jaj_nvme_RELEASE.bin
1d62b89c69df97f50ff8bbc0db0f7c8ee0c34c310b463eeb9bc30183e67432a4  ark_fast_boot.dtbo
```

## Limits and remaining acceptance work

A global `debugfs=off` experiment hit the stock initramfs error/wait path and was
rejected. One async-kernel attempt tripped supply OCP before firmware UART output;
it is excluded as a fixture failure. Slower diagnostics included ESP automount
alone (19.910 s host API) and journald tracing (15.346 s local API). A later
journald comparison reached 13.941 s, but an earlier run already achieved 13.649 s;
that pair does not establish an additive 1.4 s saving.

HTTP 200 with valid JSON establishes the agreed ARK-OS API stand-in only. The
installed jtop retry logic can return existing default Jetson metadata before
kernel uptime 10 s. This does not verify complete hardware metadata, camera or
inference readiness, or all ARK features. No application endpoint was replaced.

Persistent variables, Secure Boot support and ESRT remain in the default reduced
firmware. Full QSPI flashing can reset UEFI settings and enrolled keys; follow the
[deployment constraints](../products/JAJ/fastboot/README.md). Secure Boot enrollment,
signed/encrypted deployments and every board/peripheral combination need separate
validation. Repeated cold runs with the customer's actual readiness criterion,
recorded spread/maximum and failures remain necessary before any under-10-second
acceptance claim. The broader C4-only option was withdrawn. The first C7-only
build reached an assertion after returning `EFI_UNSUPPORTED` from binding; the
corrected build filters the node through `DeviceDiscoveryDeviceTreeCompatibility`
before handle creation. Tests compile the pinned vendor enumeration path,
reproduce the old assertion and preserve genuine failure reporting. The corrected
firmware booted slot A after flashing, passed NVIDIA validation and retained the
Linux FFC node with its driver bound; NVMe, Wi-Fi and Ethernet remained detected.
The subsequent cold capture selected stable slot B from startup, so its 12.117 s
UART shell result is not a timing validation of the corrected candidate. The supported `nvbootctrl set-active-boot-slot 0` procedure followed by a warm
reboot completed the persistent switch. A subsequent cold boot confirmed the
corrected A-slot banner, both slots normal, and Linux FFC driver binding. It
reached the UART shell in **12.152 s**, generic API in **11.832 s**, SSH in
**14.789 s** and host API in **14.486 s**. No speed benefit is established for
the UEFI C7 exclusion in this comparison. An attached FFC
endpoint has not been traffic-tested. Linux FFC support is an explicit requirement.
