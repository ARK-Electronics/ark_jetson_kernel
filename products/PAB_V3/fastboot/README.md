# PAB_V3 fast boot

PAB_V3 uses the [shared ARK Orin NVMe profile](../../JAJ/fastboot/README.md)
and [build, stage, recovery and measurement procedure](../../../docs/fast_boot.md).
Build this board with `./build.sh PAB_V3 --precompute-initrd`, apply the shared
rootfs configuration to `staging/PAB_V3/Linux_for_Tegra/rootfs`, then stage
firmware with `--l4t-dir staging/PAB_V3/Linux_for_Tegra --product PAB_V3`.

The shared profile preserves this product's own DTBs, pinmux, default camera
overlays and modules. It supports R36.5.0 NVMe/ext4 boot; do not select SD or USB
boot with this firmware. The legacy artifact name `uefi_jaj_nvme_RELEASE.bin`
and opt-in parameter `jaj_fastboot.bpmp_debugfs_async=1` apply to all three boards.

The published cold-boot timings are JAJ measurements. PAB_V3 still needs its
own power-to-application and camera/peripheral validation; no under-10-second
claim is made for this board.
