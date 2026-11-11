# JAJ boot-time test plan

Temporary. Lives in this PR for its history and is removed in a final commit before merge. Bench tooling lives in `bench/` and goes with it.

## Goal

Find the least intrusive changes with the largest reduction in the time from power-on to "the OS is up and a user's systemd service is running" on a JAJ. Firmware stays opt-in (this PR). Everything else is evaluated one change per commit, with its own measurement, and lands only if the saving is worth its trade-off. The end state for OS-side options is an opt-in image configuration file per product, kconfig-style; that design comes after the numbers.

## What we measure

Two markers, written to the debug UART by `bench/ark-boot-mark.service`, plus `systemd-analyze` collected over SSH after each boot.

| Marker | Meaning | Unit ordering |
| --- | --- | --- |
| `ARK_BOOT_STARTED` | systemd started a unit that is `WantedBy=multi-user.target`, i.e. a user application's service would be running now | plain service, no `After=` |
| `ARK_BOOT_REACHED` | `multi-user.target` itself is reached, i.e. every enabled unit has started | `DefaultDependencies=no`, `After=multi-user.target` |

Time zero is the host sending `OUTP ON` to the DP712. That includes the carrier's ~2 s power-on reset, so all numbers are relative and compared only against each other. Per run we also record the UART timestamp of the first UEFI line (firmware share) and the kernel's first line (kernel + initrd share), and save `systemd-analyze time`, `blame` and `critical-chain multi-user.target` from the booted unit.

`ARK_BOOT_STARTED` is the number we optimize. The other three explain where a change acted.

## Bench

- JAJ, debug UART (`ttyTCU0`) and USB-C device port on this host. SSH at `192.168.55.1`.
- Rigol DP712, single channel, 16 V, SCPI over USB serial. The bench script only ever sends `OUTP ON` / `OUTP OFF`.
- `bench/measure_boot.py`: power off, wait 5 s, power on, timestamp every UART line, stop at `ARK_BOOT_REACHED` or a 120 s timeout, then fetch `systemd-analyze` over SSH. Writes one JSON per run under `bench/results/<config>/`.
- Flashing needs the recovery button held; everything else is hands-off.

## Protocol

1. Five cold boots per configuration. The first boot after a flash is discarded (SSH key generation, rootfs resize, `ark-os-firstboot`). Report min, median and max; compare medians.
2. Rootfs-only changes are first applied live over SSH and measured. Only a change that earns its place is then baked into `provision.sh` and committed. Kernel and firmware changes need a flash each.
3. One change per commit. Each commit message carries its median delta. The PR description table is updated with every commit.
4. A change lands as a default only if it has no user-visible trade-off. A change with a trade-off lands as an opt-in option or is dropped, whichever its saving justifies.

## Baseline

Fresh `./build.sh JAJ` from `main` at the commit this branch is based on, flashed with `./flash.sh JAJ`. Recorded as `baseline`.

## Candidates, in test order

| # | Change | How applied | Expected saving | Trade-off to weigh |
| --- | --- | --- | --- | --- |
| 1 | Disable every service with no user-facing function as one group: snapd (5 units), cups, whoopsie, apport, kerneloops, unattended-upgrades + apt-daily timers, ubuntu-advantage + ua-timer, sssd, rpcbind + remote-fs, lvm2-monitor, mdadm, open-iscsi, grub-common, grub-initrd-fallback, networkd-dispatcher, e2scrub_reap | live, then `provision.sh` | unknown; #137 saw 1.7 s from lvm2-monitor + utmp alone | none identified. This is the upper bound for "cruft"; bisect only if the group saving is large enough to be worth attributing |
| 2 | Individually: ModemManager, avahi-daemon, openvpn, lm-sensors, anacron | live | small each | ModemManager: ARK LTE. avahi: `jetson.local` discovery. openvpn: customer VPNs. Keep unless the saving is surprising |
| 3 | Remove `ExecStartPre=/bin/sleep 2` from NVIDIA's `systemd-update-utmp` drop-in | live, then `provision.sh` | ~1–2 s; it runs `Before=sysinit.target` | `last reboot` shows a wrong wall-clock time on a unit with no RTC and no NTP |
| 4 | Headless: `multi-user.target` default (no gdm3, plymouth, nvweston, seatd, graphical wants) | live, then opt-in | #137: 0.4 s to SSH, more under CPU contention | HDMI shows a console instead of a desktop. Opt-in only |
| 5 | Boot without the initramfs: `CONFIG_PCIE_TEGRA194=y`, `CONFIG_PHY_TEGRA194_P2U=y`, `CONFIG_BLK_DEV_NVME=y`, `CONFIG_NVME_CORE=y`, drop `INITRD` from extlinux | flash | ~3.5 s; #137 saved 3.6 s just by caching the initrd's per-boot `depmod` | Loses NVIDIA's initrd features (overlayfs root, A/B rootfs, encrypted root). None are used. Check `nv-l4t-usb-device-mode` still comes up, since the initrd preloads its gadget modules |
| 6 | Firmware profile (this PR): `scripts/enable_fast_boot.sh JAJ` | flash | ~10 s on #137's bench | listed in `docs/fast_boot.md` |
| 7 | All kept changes together, fresh build, fresh flash | flash | sum, minus overlap | final numbers for the PR |

After 7, one extra run with a WiFi profile for an SSID that does not exist, to confirm a customer unit that cannot find its network boots no slower.

Not on the list, on purpose: #137's udev sysname filtering, NVIDIA USB gadget script edits, journald `ReadKMsg=no`, the bpmp.c patch, and initrd `depmod` precomputation. Each is invasive for at most ~1 s, or is made moot by candidate 5.

## Result table

Filled in per commit; the PR description mirrors it.

| Config | Commit | STARTED median (s) | REACHED median (s) | UEFI (s) | kernel (s) | Δ vs previous |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| baseline | | | | | | |
