JAJ, PAB and PAB_V3 kernel builds include an optional `jaj_fastboot.bpmp_debugfs_async` parameter. Its
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

The optional read-only parameter `jaj_fastboot.bpmp_debugfs_delay_ms` accepts
0–30000 milliseconds and defaults to 0. It delays queueing the mirror after
BPMP probe without delaying clock, reset or power-domain setup, and is ignored
unless `jaj_fastboot.bpmp_debugfs_async=1` is also set. For a controlled timing
experiment, use both parameters:

```text
jaj_fastboot.bpmp_debugfs_async=1 jaj_fastboot.bpmp_debugfs_delay_ms=5000
```

A 5000 ms delay starts the mirror about five seconds after BPMP probe; it does
not mean the debugfs tree is ready at five seconds. Early consumers such as
`jetson_clocks` or jtop must wait for the completion message above. This option
can move firmware traffic into userspace startup, so validate complete API and
peripheral readiness and the consumer's actual data, not only kernel time.
An unbound worker alone did not remove the observed CPUfreq probe stall; the
specific lock or firmware serialization behind that stall has not been proven.

The dedicated unbound, freezable workqueue separates the mirror from per-CPU
driver work. Running work drains before suspend and pending work waits until
thaw. Device-managed cleanup synchronously cancels the timer and joins any
running worker before releasing BPMP resources, destroys its workqueue, then
removes its completed debugfs tree. Failure to allocate worker state or its
workqueue falls back to synchronous creation.
No public BPMP structure or module ABI changes are introduced. The read-only
parameters appear under `/sys/module/jaj_fastboot/parameters/`,
using a distinct namespace from NVIDIA's `tegra_bpmp.ko` hypervisor module.

`build.sh` applies the source patch for JAJ, PAB and PAB_V3. The patch helper accepts only
the exact audited original or current patched R36.5.0 source checksum and
supports `--mode apply`, `--mode check`, and `--mode restore`. A staged source
with an older patch is rejected: perform a fresh build, or use the matching
older helper and patch to restore its original source before applying this
revision. Do not apply a current reverse patch to an older revision. For example:

```sh
python3 scripts/patch_bpmp_debugfs.py \
  --kernel-dir staging/JAJ/Linux_for_Tegra/source/kernel/kernel-jammy-src \
  --mode check
```

Activation requires a kernel built with this patch and matching modules/initrd;
adding the parameter to an older kernel does not enable the optimization. The
source patch remains opt-in at runtime on every build.
