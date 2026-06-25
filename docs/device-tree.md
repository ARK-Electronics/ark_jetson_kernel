# Device-tree customization

ARK carries **only its delta** to NVIDIA's device tree, not a copy of the BSP tree. The build stages the pristine L4T sources, then layers ARK's changes on top. This keeps the diff readable and means a BSP bump is picked up automatically instead of silently reverted.

## What lives under `products/<target>/device_tree/`

Each product carries only the files it actually changes:

- `bootloader/.../tegra234-mb*-bct-*-p3767-*.{dtsi,dts}` — the MB1/MB2 BCT pinmux, GPIO, pad-voltage and misc config for the carrier (ARK's Pinmux spreadsheet output). These are bootloader inputs, compiled separately from the kernel DTBs, so they stay as full files.
- `source/hardware/nvidia/t23x/nv-public/nv-platform/ark-<target>-overrides.dtsi` — the **one fragment** holding every kernel-DTB change for the carrier (UARTB, USB topology, HDA, display, etc.), expressed as overrides (`&{/path} { ... }`) over the stock tree.
- `source/.../nv-platform/tegra234-dcb-p3737-0000-p3701-0000-hdmi.dtsi` (JAJ, PAB_V3 only) — the HDMI display-control block the fragment `#include`s.

Model strings are **not** files — they live in `products/<target>/dtb_models.env`.

## How `build.sh` applies it

After staging the stock BSP, `build.sh`:

1. `cp -r`s the files above into the staged tree.
2. Appends `#include "ark-<target>-overrides.dtsi"` to the **stock** `tegra234-p3768-0000+p3767-xxxx-nv-common.dtsi`, so ARK's fragment is the last thing parsed and its overrides win. (Idempotent; fails loud if the BSP renamed nv-common or the fragment is missing.)
3. Stamps each SKU's model from `dtb_models.env` onto the stock `-nv`/`-nv-super` DTS (the `-nv-super` DTB gets `" Super"` appended).

Because ARK only overrides nodes, the rest of every node — and anything NVIDIA adds in a future BSP — flows through untouched.

## Updating after a BSP bump

Usually nothing: the fragment overrides specific nodes, so unrelated BSP changes are inherited automatically. After bumping `versions.env`, do a clean build (`./build.sh <target> --clean`). Only if `dtc` errors — e.g. a node the fragment references was renamed or removed upstream — adjust the affected line in the fragment. (This is exactly the failure the old full-tree copies hid: they pinned the whole tree to one BSP and quietly reverted everything else.)

## Adding or changing a carrier edit

Edit `ark-<target>-overrides.dtsi`. Add nodes/properties by referencing the target by label or path, e.g.:

```dts
&{/bus@0/hda@3510000} {
	status = "okay";
};
```

Remove a stock node with `/delete-node/`. For a new SoC node that stock leaves undefined (e.g. UARTB), define it under its parent (`&{/bus@0} { uartb: serial@3110000 { ... }; }`). The dt-binding macros (`GIC_SPI`, `TEGRA234_CLK_*`, `TEGRA234_MAIN_GPIO`, …) are already in scope because the fragment is included at the end of the full nv-common chain. For a new model string, edit `dtb_models.env`.
