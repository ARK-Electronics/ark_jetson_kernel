# Device-tree customization

ARK keeps **only its changes** to NVIDIA's device tree, not a copy of the BSP tree. The build stages the untouched L4T sources and then lays ARK's changes on top. The diff stays readable, and a BSP bump comes through by itself, where a copied tree would quietly undo it.

## Where each change belongs

An ARK change goes in one of three layers, and each layer is read at a different point in the boot. Put a change in the **earliest** layer that its purpose needs.

| Layer | Files | Applied | Use for |
|-------|-------|---------|---------|
| **MB1 BCT** | `device_tree/bootloader/.../tegra234-mb1-bct-{pinmux,gpio}-*.dtsi` | MB1, before the kernel | Pad state that must be right **from power-on**: pinmux function / pull / tristate / input-enable, and a GPIO's boot direction + drive level. Anything safety- or sequencing-critical at power-on (relay safe-off, peripheral reset release). |
| **Base fragment** | `ark-<target>-overrides.dtsi` | Kernel, at DTB parse | Always-present, kernel-level carrier wiring: a SoC block stock leaves off (UARTB, HDA), USB/PCIe topology, `/delete-node/` for chips the carrier lacks. |
| **Overlay** | `overlay/*.dts` + `overlay/dtbo.list` (optionally baked via `default_overlays`) | Flash-time bake, or runtime jetson-io | **Optional or one-of-N selectable** configs one image chooses between: which camera, an add-on board. |

Ask these questions in order:

1. **Must the state be right before the kernel runs?** Then it belongs in the **BCT**. An overlay applies seconds into Linux and cannot guarantee a state at power-on.
2. **Is it always true for this carrier, and at kernel level?** Then it belongs in the **base fragment**.
3. **Is it optional, or one of several choices?** Then it is an **overlay**.

For example, the I2S0 connector comes up as GPIO. Its four outputs are undriven and held HIGH by the carrier's pull-ups, and DIN is a hi-z input. The whole point is a known safe level *from power-on*, so the setting lives in the BCT and not in an overlay. [gpio.md](gpio.md) has the details.

[camera_overlays.md](camera_overlays.md) documents camera overlays: their jetson-io metadata, building and installing them, and writing your own. This page only shows where the overlay layer sits in the boot. It does not cover writing CSI or sensor overlays.

The BCT dtsi files come out of the NVIDIA Pinmux spreadsheet, `products/<target>/*.xlsm`. In practice they are edited by hand, and they are the source of truth. Treat the `.xlsm` as a reference, and bring it back in sync if you ever regenerate from it.

## What lives under `products/<target>/device_tree/`

Each product carries only the files it changes:

- `bootloader/.../tegra234-mb*-bct-*-p3767-*.{dtsi,dts}` holds the MB1 and MB2 BCT config for the carrier: pinmux, GPIO, pad voltage and misc. It is the output of ARK's Pinmux spreadsheet. These files are inputs to the bootloader and are compiled separately from the kernel DTBs, so they stay as full files.
- `source/hardware/nvidia/t23x/nv-public/nv-platform/ark-<target>-overrides.dtsi` is the **one fragment** that holds every kernel DTB change for the carrier, such as UARTB, the USB topology, HDA and the display. Each change is written as an override over the stock tree, in the form `&{/path} { ... }`.
- `source/.../nv-platform/tegra234-dcb-p3737-0000-p3701-0000-hdmi.dtsi` is the HDMI display-control block that the fragment pulls in with `#include`. Only JAJ and PAB_V3 have it.

Model strings are **not** files. They live in `products/<target>/dtb_models.env`.

## How `build.sh` applies it

After it stages the stock BSP, `build.sh` does four things:

1. It copies the files above into the staged tree with `cp -r`.
2. It appends `#include "ark-<target>-overrides.dtsi"` to the **stock** `tegra234-p3768-0000+p3767-xxxx-nv-common.dtsi`. ARK's fragment is then the last thing parsed, so its overrides win within the DTB. The step can run twice without harm, and it fails loudly if the BSP renamed nv-common or the fragment is missing.
3. It drops the SKU overlay's `max-link-speed` write for C7, so that the carrier fragment decides that rate. The next section explains why. The build fails if that write can no longer be found.
4. It stamps each SKU's model from `dtb_models.env` onto the stock `-nv` and `-nv-super` DTS. The `-nv-super` DTB gets `" Super"` appended.

ARK only overrides nodes, so the rest of every node comes through untouched, and so does anything NVIDIA adds in a later BSP.

## What UEFI applies after the DTB

UEFI applies `tegra234-p3768-0000+p3767-0000-dynamic.dtbo` over the kernel DTB at boot, after every layer above, so its fragments beat the base fragment. Each fragment has a condition:

| Condition | Writes |
|-----------|--------|
| fuse `disable-{nvenc,pva,dla0,dla1}` | `status = "disabled"` on that engine |
| ids `3767-0000/-0001/-0003/-0004` | `mmc@3400000` disabled |
| ids `3767-0003/-0004/-0005` (Orin Nano) | `max-link-speed = <3>` on C1, C4, C4-EP, C7, C8, C9 |
| odm-data `gbe-uphy-config-9` | C7 and C9 to x1, C9 enabled |
| odm-data `hsio-uphy-config-40`/`-41` | C4 disabled, C4-EP enabled, C1 to Gen2 |

If the base fragment sets one of those properties, its value is reverted and nothing tells you. The DTB on disk keeps ARK's value while `/proc/device-tree` reports NVIDIA's. Today only the Orin Nano PCIe rates collide with anything ARK sets, and `build.sh` handles that case.

To check a live board, compare `dtc -I fs -O dts /proc/device-tree` against `dtc -I dtb -O dts` of the DTB in `/boot/dtb/`.

## UEFI variables

Two overlays write `/firmware/uefi/variables/` into the kernel DTB, which is where UEFI reads its variables: the BSP's `L4TConfiguration.dtbo` and ARK's `ark_boot_order.dtbo` from `products/*/overlay/`. Both are passed in `OVERLAY_DTB_FILE`. ARK's is appended through `ADDITIONAL_DTB_OVERLAY`, so it lands last and wins.

| Variable | Stock | ARK |
|----------|-------|-----|
| `NewDeviceHierarchy` | `[01]` — new devices go to the top of the boot order | `[00]` — bottom |
| `DefaultBootPriority` | `usb,nvme,emmc,sd,ufs` | `nvme,usb,emmc,sd,ufs` |
| `PlatformBootOrderSet` | set once the first boot has sorted | locked `[00]` — sort every boot |

UEFI sorts BootOrder by `DefaultBootPriority` only while `PlatformBootOrderSet` is unset. On stock firmware that is the first boot only. Options that UEFI finds again later are placed by `NewDeviceHierarchy` alone. Under `01`, a NIC's four HTTP and PXE options land above the SSD. Under `00`, an SSD that was missed for one boot comes back last, behind the UEFI Shell, and the Shell never returns.

With `PlatformBootOrderSet` locked, every boot sorts again. The SSD is then first whenever it is present, and a boot order you change in the UEFI menu is undone on the next boot.

`DefaultBootPriority` and `PlatformBootOrderSet` are `locked` and volatile. UEFI recreates them from the DTB on every boot, so they change only with the QSPI bootloader, through a reflash or a capsule update. To recover a unit on an older image whose SSD fell to the bottom, press `s` at the UEFI menu on the debug UART. Then run `bcfg boot dump` and `bcfg boot mv <NVMe position> 0`.

## Updating after a BSP bump

Usually there is nothing to do. The fragment overrides specific nodes, so unrelated BSP changes come through by themselves. After you bump `versions.env`, do a clean build with `./build.sh <target> --clean`. You only need to touch the fragment if `dtc` reports an error, for example because a node the fragment refers to was renamed or removed upstream. Adjust the affected line.

The old full-tree copies hid exactly this failure. They pinned the whole tree to one BSP and quietly reverted everything else.

## Adding or changing a carrier edit

Edit `ark-<target>-overrides.dtsi`. To add a node or a property, refer to the target by its label or path, like this:

```dts
&{/bus@0/hda@3510000} {
	status = "okay";
};
```

Remove a stock node with `/delete-node/`. For a SoC node that stock leaves undefined, such as UARTB, define it under its parent: `&{/bus@0} { uartb: serial@3110000 { ... }; }`. The dt-binding macros such as `GIC_SPI`, `TEGRA234_CLK_*` and `TEGRA234_MAIN_GPIO` are already in scope, because the fragment is included at the end of the full nv-common chain. For a new model string, edit `dtb_models.env`.
