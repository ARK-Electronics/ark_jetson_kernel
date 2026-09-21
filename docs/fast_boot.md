# Fast Boot

Opt-in boot firmware profile: NVIDIA's [boot-time optimization steps](https://docs.nvidia.com/jetson/archives/r36.5/DeveloperGuide/SD/BootTimeOptimization.html) for R36.5, applied to a staged tree. Measured on a JAJ (Orin NX 16GB, NVMe) cold boot to SSH: 33 s stock, 23 s with this profile. The rootfs is untouched; `quiet` is already on the stock command line.

## Enable

Build the reduced UEFI once (docker + network, output in `staging/uefi/`):

```
./scripts/build_uefi.sh
```

Then, after every full build of a target:

```
./build.sh JAJ
./scripts/enable_fast_boot.sh JAJ
./flash.sh JAJ
```

`--fast` rebuilds keep the profile; `./scripts/enable_fast_boot.sh JAJ --restore` puts the stock files back. A flashed unit records `fast_boot=1` in `/etc/ark_jetson_kernel`.

## What changes

| Step | Staged file | Stock → fast |
| --- | --- | --- |
| MB1/MB2 UART log | `bootloader/generic/BCT/tegra234-mb1-bct-misc-p3767-0000.dts` | `log_level` 4 → 0 via NVIDIA's `DISABLE_UART_MB1_MB2` |
| BPMP UART log | `bootloader/generic/tegra234-bpmp-3767-*-3768-super.dtb` | `/serial` node emptied |
| UEFI | `bootloader/uefi_jetson.bin` | general profile → [`uefi/ark_nvme.defconfig`](../uefi/ark_nvme.defconfig), NVIDIA's `t23x_embedded` with NVMe/ext4 in place of eMMC |
| Boot device | flash-time overlay | `BootOrderNvme.dtbo` appended after `ark_boot_order.dtbo` |

## Trade-offs

The reduced UEFI has no display or logo, no setup menu (Esc), no boot menu or timeout, no UEFI shell, no USB/SD/eMMC/SATA boot, no PXE/HTTP network boot, no FAT/ESP access, and no capsule (OTA) firmware update. It keeps NVMe/ext4 boot, RCM recovery, the serial console, Secure Boot, fTPM measured boot, persistent variables, and the ESRT that `nv-l4t-bootloader-config` and `nvbootctrl verify` need.

- Only a full `./flash.sh <TARGET>` to NVMe is supported. The boot-device overlay lives in the NVMe `kernel-dtb` partition, and `--sdcard`/`--usb` are refused.
- Nothing prints on the debug UART before UEFI. Restore the stock firmware to debug a unit that dies in MB1/MB2/BPMP.
- A flash package generated from a fast-boot tree ships this firmware; `BUILD_INFO.txt` says so.
