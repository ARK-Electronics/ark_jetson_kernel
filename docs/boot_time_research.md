# Initial research: Just a Jetson boot time under 10 seconds

**Historical R36.5.0 / JetPack 6 research, recorded before device testing.**
Repository and package descriptions below refer to that snapshot, including the
then-active hardware handoff. The current branch targets R39.2.1 / JetPack 7.2.1;
use [the fast-boot procedure](fast_boot.md) and [validation record](fast_boot_validation.md)
for implementation status and measured results.

Research date: 2026-09-16. Repository revision inspected: `67743455da8cd58763bfcdb8791e8cd3d055f879`.

**A cold boot under 10 seconds is an unverified engineering target for JAJ.** The credible route is a customer-specific firmware, kernel, root filesystem, and application startup profile. Published NVIDIA examples establish substantial improvements, but do not establish a sub-10-second result on this carrier and software version. Measure the firmware share early: if the system takes 10 seconds to reach Linux, userspace changes alone cannot meet the requirement.

The reported baseline is over 50 seconds. Its measurement endpoint and stage breakdown are not yet known. This research assumes time from applying power at the carrier to the customer application producing its first valid required output. Confirm whether that output requires networking, camera capture, CUDA/TensorRT, a display, or some combination. SSH availability and a login prompt are different acceptance criteria.

No connected-device access, reboots, builds, flashing, service changes, or boot measurements were performed for this research. The other instance retains the device. This document is the only file added by this research task; concurrent hardware/configuration changes belong to the other work.

At the time of this research, the repository targeted Orin NX/Nano, JetPack 6.2.2, L4T R36.5.0, and Linux 5.15. Its normal flash path installs firmware in QSPI and the root filesystem on NVMe. These are repository facts, not confirmation of the currently running device's module, image, or firmware. See [README](../README.md), [versions.env](../versions.env), and [flash.sh](../flash.sh).

| Evidence | Published result | Interpretation for JAJ |
| --- | --- | --- |
| NVIDIA R36.5 boot optimization guide, using AGX Orin / JetPack 6.0 / QSPI + eMMC | About 43 seconds down to 26 seconds, cold power to login | Useful techniques; different module/storage, and still above 10 seconds. |
| NVIDIA staff Orin NX 16 GB example, JetPack 5.1.3 / R35.5.0 | 38 seconds down to 20 seconds; optimized stages reported as 1 second MB1/MB2, 9 seconds bootloader, 10 seconds kernel | Demonstrates why firmware work matters. These are historical example measurements, not R36.5 forecasts or a hardware lower bound. |
| NVIDIA staff miniUEFI adaptation for Orin Nano / NVMe, R36.4.3 | Configuration and build/flash procedure provided; no sub-10-second guarantee | A concrete starting point for a prototype that must be ported and tested on JAJ / R36.5. |

Sources: [NVIDIA boot optimization](https://docs.nvidia.com/jetson/archives/r36.5/DeveloperGuide/SD/BootTimeOptimization.html), [NVIDIA staff NX measurements](https://forums.developer.nvidia.com/t/boot-time-optimization-jetpack-5-x/296123), and [NVIDIA staff NVMe miniUEFI adaptation](https://forums.developer.nvidia.com/t/jetson36-4-3-uefi-uefi-40-uefi-logo/332685/21).

Several common optimizations are already present in the current repository. Verify that the customer's installed image includes them before budgeting additional savings.

| Repository evidence | Current behavior | Follow-up after the device is available |
| --- | --- | --- |
| [build.sh](../build.sh), console section around line 630 | Adds `quiet log_buf_len=4M` and a sysctl override keeping `kernel.printk` quiet after systemd starts. Comments describe a previous JAJ PCIe error/logging storm. | Check `/proc/cmdline`, the sysctl file, and kernel errors. Do not count adding `quiet` as a new improvement. Fix recurrent hardware/driver errors if present. |
| [JAJ boot order overlay](../products/JAJ/overlay/ark_boot_order.dts) and [default overlays](../products/JAJ/default_overlays) | NVMe precedes USB; newly discovered network boot entries go to the bottom. | Verify deployed UEFI variables and actual boot path. This prevents network options outranking the SSD; it does not remove firmware network initialization. |
| [JAJ MB2 configuration](../products/JAJ/device_tree/bootloader/generic/BCT/tegra234-mb2-bct-misc-p3767-0000.dts) | Carrier EEPROM reads are already disabled with `cvb_eeprom_read_size=0`. | Do not count disabling a nonexistent carrier EEPROM as a new saving. |
| [defconfig.fragment](../defconfig.fragment) | Adds support for many Wi-Fi, modem, and Bluetooth families to the generic kernel. | Create an optional customer profile selecting the actual hardware. An enabled driver is a candidate, not proof of a significant delay. |
| [JAJ device-tree overrides](../products/JAJ/device_tree/source/hardware/nvidia/t23x/nv-public/nv-platform/ark-JAJ-overrides.dtsi) and [default overlays](../products/JAJ/default_overlays) | Enables carrier interfaces, HDMI/audio, and a dual IMX219 camera overlay. | Retain the customer's required interfaces; measure whether absent peripherals or unnecessary probes contribute to startup. |
| [provision.sh](../provision.sh) and [versions.env](../versions.env) | Installs ARK-OS and enables jtop; pins the camera stack and protects the ARK boot chain against replacement by NVIDIA packages. | Inventory the deployed services and package versions. Preserve camera compatibility and boot-chain protections when making a smaller image. Installed packages alone do not imply startup cost. |
| [README](../README.md), build options | `--no-provision` skips the ARK provisioning payload. | Useful for an experiment, but it does not itself select NVIDIA's minimal rootfs or produce an optimized kernel/UEFI. |

The adjacent local ARK-OS checkout supplies more specific candidates. These are source/package findings, not confirmation of the device's installed state:

- The UI backend and four manager services request and order after `network-online.target`; RTSP, go2rtc, MAVLink router, and CAN also order after it. The installer enables these nine services by default. Determine which ones actually need networking before becoming useful. The delay depends on the deployed wait-online service and connection profiles. Local evidence: [UI service](https://github.com/ARK-Electronics/ARK-OS/blob/080c984dfc39c22c0fcda610d83702187d55515c/packaging/service-files/jetson/ark-ui-backend.service#L3), [enabled services](https://github.com/ARK-Electronics/ARK-OS/blob/080c984dfc39c22c0fcda610d83702187d55515c/packaging/DEBIAN/postinst#L213).
- The MAVLink wrapper always sleeps three seconds after calling VBUS setup, although the VBUS script skips that operation on R36. The audit also verified this in the cached `downloads/ark-os-jetson-jammy_1.2.0_arm64.deb`. If MAVLink readiness matters, replace the fixed delay with verified device readiness or remove it after demonstrating it is unnecessary. This is a three-second wait in that startup path, not automatically a three-second reduction in total boot time. Local evidence: [wrapper](https://github.com/ARK-Electronics/ARK-OS/blob/080c984dfc39c22c0fcda610d83702187d55515c/services/mavlink-router/start_mavlink_router.sh#L30), [R36 VBUS behavior](https://github.com/ARK-Electronics/ARK-OS/blob/080c984dfc39c22c0fcda610d83702187d55515c/platform/jetson/scripts/vbus_enable.py#L33).
- First-boot hotspot setup polls for a wireless interface 30 times with two-second sleeps. An absent adapter can therefore add about 60 seconds to that setup operation. A completion sentinel guards the first-boot service, so this should not be blamed for every cold boot without evidence. Local evidence: [hotspot setup](https://github.com/ARK-Electronics/ARK-OS/blob/080c984dfc39c22c0fcda610d83702187d55515c/platform/common/scripts/create_hotspot_default.sh#L18), [first-boot guard](https://github.com/ARK-Electronics/ARK-OS/blob/080c984dfc39c22c0fcda610d83702187d55515c/packaging/service-files/ark-os-firstboot.service#L9).
- ARK-OS already disables `systemd-time-wait-sync`. Its DDS service has a two-second startup sleep but is opt-in, not enabled by default. Neither is an established recurring delay on this device. Local evidence: [installer](https://github.com/ARK-Electronics/ARK-OS/blob/080c984dfc39c22c0fcda610d83702187d55515c/packaging/DEBIAN/postinst#L208).

Do not assume a 30-second extlinux delay. An offline staged image for a different product contains `TIMEOUT 30`; that says nothing about the active JAJ file. NVIDIA's `r36.5-updates` L4TLauncher interprets this as three seconds and immediately selects the default when there is only one active boot entry or the timeout is zero. UEFI's own setup/boot delay is separate. Check the exact firmware revision before applying this source behavior to the device. [NVIDIA L4TLauncher source](https://github.com/NVIDIA/edk2-nvidia/blob/r36.5-updates/Silicon/NVIDIA/Application/L4TLauncher/L4TLauncher.c)

The recommended work proceeds in the following order. These are proposed experiments; none have been applied.

1. **Measure the complete cold-boot path and establish the endpoint.** Capture power assertion and externally timestamped UART output, plus an application readiness signal. Separate pre-kernel firmware, kernel/initramfs, userspace dependencies, and application initialization. Use an externally observed output or a GPIO readiness edge for the final test, especially after suppressing serial logs. Record the actual module SKU, NVMe, image commit, firmware version, peripherals, and power configuration. Separate first boot after flashing from normal boots because provisioning and key generation can change the result.

2. **Remove measured waits in the existing image.** Verify the existing boot-order and logging fixes are installed. Inspect UEFI's setup delay, actual fallback boot attempts, storage/PCIe link retries, and the application service dependency chain. Eliminate dependencies on unused interfaces or unavailable remote resources. Services that need no working network should not wait for `network-online.target`; services that do need it must retain a correct readiness condition. Do not assume every long entry in `systemd-analyze blame` lies on the application's critical path. [systemd network dependency guidance](https://systemd.io/NETWORK_ONLINE/)

3. **Build a smaller firmware path if firmware remains the limiting stage.** Use a BSP-matched UEFI RELEASE build and remove unused firmware network/PXE/HTTP, removable-storage, shell, and display functionality as requirements allow. The installed firmware may already be RELEASE, so do not count that alone as a saving. Evaluate quick boot / a miniUEFI-derived configuration retaining the PCIe/NVMe, filesystem, and L4TLauncher path needed by JAJ. First validate the same boot functionality before combining changes. This repository currently provides kernel/device-tree build machinery; a reproducible UEFI source checkout, configuration, build, and packaging step would be additional work.

   Check the actual UEFI `Timeout` variable: NVIDIA describes a five-second default and a zero-delay configuration. This is a potentially useful fixed wait only if it remains enabled in the installed firmware. [NVIDIA UEFI timeout guidance](https://forums.developer.nvidia.com/t/how-to-enable-esc-setup-option-on-screen/269730/15)

   NVIDIA's R36.5 miniUEFI documentation describes AGX Orin/eMMC, so the stock minimal binary is not a demonstrated drop-in for JAJ/NVMe. The R36.4.3 Orin Nano adaptation enables the Orin/L4T quick-boot path and ext4 while disabling several optional features. Treat it as porting evidence, not a ready-made R36.5 image. Match the release's source manifest and build layout instead of assuming old paths still apply. [R36.5 miniUEFI documentation](https://docs.nvidia.com/jetson/archives/r36.5/DeveloperGuide/SD/Bootloader/UEFI.html#miniuefi-support), [NVMe adaptation](https://forums.developer.nvidia.com/t/jetson36-4-3-uefi-uefi-40-uefi-logo/332685/21)

   Minimal firmware changes the product's behavior: NVIDIA documents different persistent-variable and secure-boot assumptions. Review the customer's update, recovery, boot-selection, and security requirements before selecting it. Retain a known-good QSPI image and NVMe backup for experiments. Keep USB recovery available. Use the existing JAJ carrier/BCT setup, not an AGX development-kit flash recipe.

   Once diagnostic measurements exist, suppress MB1/MB2 logs and unnecessary BPMP/UEFI serial output as applicable. The NVIDIA guide's BPMP filename is for AGX Orin: resolve the selected P3767/NX/Nano BPMP binary and BCT before adapting it. BootROM and NVIDIA-owned early firmware remain part of the boot chain; replacing UEFI does not remove them. CBoot is not a supported drop-in fast-boot switch for this R36.5 setup. [Boot optimization](https://docs.nvidia.com/jetson/archives/r36.5/DeveloperGuide/SD/BootTimeOptimization.html), [Orin boot architecture](https://docs.nvidia.com/jetson/archives/r36.5/DeveloperGuide/AR/BootArchitecture/JetsonOrinSeriesBootFlow.html)

4. **Tailor the kernel, device tree, and initramfs.** Use a temporary diagnostic boot with initcall tracing to identify slow drivers, then remove tracing for timing runs. Disable unused controllers and drivers, defer optional drivers, and consider asynchronous probes only where dependencies allow. Converting a driver to a module helps only if it is not immediately loaded again before readiness. Keep the NVMe storage chain and required customer peripherals available on time. These are techniques explicitly supported by NVIDIA's current kernel optimization guide; it does not establish a sub-10-second system result. [R36.5 kernel optimization](https://docs.nvidia.com/jetson/archives/r36.5/DeveloperGuide/SD/Kernel/KernelBootTimeOptimization.html), [Linux 5.15 diagnostic parameters](https://docs.kernel.org/5.15/admin-guide/kernel-parameters.html)

   Inspect initramfs content and startup scripts before shrinking it. Removing `INITRD` requires proving that every root-storage dependency is built into the kernel and that no required encryption/root setup depends on early userspace. Rebuild the production initramfs when changing kernel/modules; keep Image, DTB, modules, and initramfs matched. The initrd used by the flashing tool is a separate concern from the production boot initramfs. [Linux initrd documentation](https://docs.kernel.org/5.15/admin-guide/initrd.html), [NVIDIA kernel customization](https://docs.nvidia.com/jetson/archives/r36.5/DeveloperGuide/SD/Kernel/KernelCustomization.html), [NVIDIA flashing support](https://docs.nvidia.com/jetson/archives/r36.5/DeveloperGuide/SD/FlashingSupport.html)

5. **Build a production rootfs and start only the required application path.** If the customer does not need a desktop, first measure headless startup on the current image, then make the result reproducible with NVIDIA's Jammy `minimal` or `basic` rootfs flavor and explicit package selection. Decide which ARK-OS, monitoring, camera, network, and container services the customer needs. Preserve hardware power/thermal management and the services responsible for boot success and update handling. Specifically, NVIDIA documents `nv-l4t-bootloader-config` / `nvbootctrl verify` and rootfs validation; boot-loop tests must allow validation to succeed before the next reboot. [NVIDIA rootfs flavors and boot validation](https://docs.nvidia.com/jetson/archives/r36.5/DeveloperGuide/SD/RootFileSystem.html)

6. **Optimize the application through its first useful operation.** Preinstall dependencies and any container images. If TensorRT is used, build compatible serialized engines ahead of deployment. Measure imports, model/engine loading, GPU initialization, camera startup, and the first required inference/frame separately. CUDA lazy loading may move work to the first operation, so faster process startup alone is not success. If container startup is significant, compare a direct systemd service against the same application in its container. No fixed container penalty or application startup saving can be predicted from the repository. [TensorRT engine construction](https://docs.nvidia.com/deeplearning/tensorrt/latest/inference-library/c-api-docs.html), [CUDA lazy loading](https://docs.nvidia.com/cuda/cuda-programming-guide/04-special-topics/lazy-loading.html)

   If cameras are required, retain the appropriate Argus/ISP dependencies and this repository's documented userspace compatibility pin while testing. Use the first valid frame as part of readiness. See [local camera regression findings](argus_relaunch_regression.md) and [NVIDIA camera architecture](https://docs.nvidia.com/jetson/archives/r36.5/DeveloperGuide/SD/CameraDevelopment/CameraSoftwareDevelopmentSolution.html). A service can signal readiness with `Type=notify` after initialization completes; this improves dependency correctness and measurement, not initialization speed by itself. [systemd service semantics](https://github.com/systemd/systemd/blob/v249/man/systemd.service.xml)

Once the other instance has finished and device ownership has been handed over, the initial collection should include the following. These commands are a future checklist, not commands run during this research. Replace `customer.service` with the actual application service and capture outputs per boot.

```sh
cat /etc/nv_tegra_release
cat /etc/ark_jetson_kernel
uname -a
cat /proc/cmdline
cat /boot/extlinux/extlinux.conf
findmnt /
sudo efibootmgr -v
systemd-analyze time
systemd-analyze critical-chain customer.service
systemd-analyze blame
systemd-analyze plot > boot.svg
systemctl cat customer.service
systemctl show customer.service -p After -p Wants -p Requires
systemctl --failed
journalctl -b -o short-monotonic
journalctl -b -k -o short-monotonic
```

`systemd-analyze` is diagnostic evidence, not the customer's cold-boot measurement. Firmware timing may be absent or incomplete, process launch need not mean initialization has finished, and parallel service durations cannot be added together. Pair its output with the external power-to-ready timeline. [systemd v249 analysis documentation](https://github.com/systemd/systemd/blob/v249/man/systemd-analyze.xml)

For feasibility, assign explicit stage budgets after the baseline. As an **illustrative design target, not a prediction**, 3.5 seconds before Linux, 2 seconds for kernel/rootfs initialization, and 2.5 seconds from there to application readiness would yield 8 seconds and leave almost 2 seconds of margin. If the firmware prototype cannot fit its allocation, either rebalance against measured application/kernel times or revisit the requirement before investing heavily in small userspace improvements. No published measurement found in this research demonstrates those allocations on JAJ.

Validate each change group separately, then the combined production image. A suggested initial acceptance run is at least 30 fully powered-off starts on the intended hardware; report median, 95th percentile, maximum, and failures. This is an initial engineering sample, not proof of a worst-case guarantee. Exercise the specified peripheral and network conditions, including unavailable networks if the product must work offline. Test warm reboots and first boot after flashing separately. Require every acceptance run to finish the actual customer operation under 10 seconds, with margin for variation; do not accept only the best run or a login/SSH proxy.

The first deliverable after the device is released should be a measured boot-stage breakdown and a decision on firmware feasibility. The likely implementation scope is an optional JAJ customer build profile, a version-pinned reduced UEFI build, a tailored kernel/initramfs/rootfs, and an application readiness test. If genuine cold boot remains above the target, persistent power with supported suspend/resume may be a separate product option, but it changes the requirement and is not a cold-boot result.
