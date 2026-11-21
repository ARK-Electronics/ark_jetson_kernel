# PAB fast boot

PAB uses the [shared ARK Orin NVMe profile](../../JAJ/fastboot/README.md)
and [build, stage, recovery and measurement procedure](../../../docs/fast_boot.md).
Build this board with `./build.sh PAB --precompute-initrd`, apply the shared
rootfs configuration to `staging/PAB/Linux_for_Tegra/rootfs`, then stage
firmware with `--l4t-dir staging/PAB/Linux_for_Tegra --product PAB`.

The shared profile preserves this product's own DTBs, pinmux, default camera
overlays and modules. It supports R36.5.0 NVMe/ext4 boot; do not select SD or USB
boot with this firmware. The legacy artifact name `uefi_jaj_nvme_RELEASE.bin`
and opt-in parameter `jaj_fastboot.bpmp_debugfs_async=1` apply to all three boards.

The published cold-boot timings are JAJ measurements. PAB still needs its
own power-to-application and camera/peripheral validation; no under-10-second
claim is made for this board.
