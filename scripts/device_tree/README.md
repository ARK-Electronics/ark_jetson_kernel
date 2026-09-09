# Device-tree tooling

Helpers for working on the per-product device-tree overlays (see [docs/device-tree.md](../../docs/device-tree.md) for the overlay model itself).

## `classify.sh [PAB|JAJ|PAB_V3|PAB_CAN|all]`

Drift check: flags any file under `products/<target>/device_tree/source/` that is byte-identical to the pinned BSP. Such files are pointless copies that go stale silently — the thing the overlay refactor removed. Extracts the stock tree from the BSP tarball in `downloads/` (cached), and exits non-zero if a duplicate is found, so it can gate CI or a BSP bump.

```sh
./setup.sh                              # ensure downloads/ has the BSP
scripts/device_tree/classify.sh all
```

## `compile_dtb.sh <nv-public-dir> <dts> [out.dtb]`

Compiles one p3768 DTB from a staged/extracted `nv-public` tree (cpp + dtc, mirroring the kernel build), so you can validate an `ark-<target>-overrides.dtsi` edit without a full kernel build.

```sh
./build.sh PAB                          # stages the tree under staging/PAB/
NVP=staging/PAB/Linux_for_Tegra/source/hardware/nvidia/t23x/nv-public
scripts/device_tree/compile_dtb.sh "$NVP" tegra234-p3768-0000+p3767-0000-nv.dts /tmp/pab.dtb
dtc -I dtb -O dts /tmp/pab.dtb | less   # inspect; diff against the stock DTB to see the delta
```

Requires `device-tree-compiler` (`dtc`) and `cpp`.
