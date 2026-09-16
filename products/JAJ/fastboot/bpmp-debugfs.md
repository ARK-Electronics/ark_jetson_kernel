JAJ kernel builds include an optional `jaj_fastboot.bpmp_debugfs_async` parameter. Its
default value is false; setting `jaj_fastboot.bpmp_debugfs_async=1` in the selected boot
entry runs BPMP debugfs creation in a background kernel worker. Clock, reset,
power-domain and BPMP transport initialization retain their existing order.

The change targets a measured slow initcall: one R36.5.0 cold boot spent
1,444,117 microseconds in `tegra_bpmp_driver_init`. That includes more than
just debugfs creation, so this measurement alone does not establish the saving.
Use cold A/B measurements with the same kernel and change only the parameter.

The worker creates the existing `/sys/kernel/debug/bpmp/debug` tree. Once
`dmesg` reports `debugfs initialized asynchronously`, existing consumers such
as `jetson_clocks` can use the completed tree. A tool that needs these diagnostic
interfaces very early must wait for initialization; removing the parameter
restores synchronous behavior. Global `debugfs=off` is not part of this profile.

The freezable worker drains before suspend. Device-managed cleanup cancels and
joins it before releasing BPMP resources, then removes its completed debugfs
tree. Failure to allocate worker state falls back to synchronous creation.
No public BPMP structure or module ABI changes are introduced. The read-only
state appears at `/sys/module/jaj_fastboot/parameters/bpmp_debugfs_async`,
using a distinct namespace from NVIDIA's `tegra_bpmp.ko` hypervisor module.

`build.sh` applies the source patch only for JAJ. The patch helper accepts only
the exact audited original or patched R36.5.0 source checksum and supports
`--mode apply`, `--mode check`, and `--mode restore`. For example:

```sh
python3 scripts/patch_bpmp_debugfs.py \
  --kernel-dir staging/JAJ/Linux_for_Tegra/source/kernel/kernel-jammy-src \
  --mode check
```

Activation requires a kernel built with this patch and matching modules/initrd;
adding the parameter to an older kernel does not enable the optimization. The
source patch remains opt-in at runtime on every build.
