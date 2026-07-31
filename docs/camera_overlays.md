# Camera overlays

How ARK camera device-tree overlays are laid out, built, installed, and how to write or port a custom one. For which sensors are supported and how to test them, see [cameras.md](cameras.md). For where overlays sit relative to BCT and the base DT fragment, see [device-tree.md](device-tree.md).

## How jetson-io discovers overlays

NVIDIA's `jetson-io` (`config-by-hardware.py`) does **not** list every `.dtbo` under `/boot`. It scans `/boot/*.dtbo` and keeps only overlays whose root properties match the running board and its known headers.

| Property | Role |
|----------|------|
| `jetson-header-name` | Must **exactly** match a header name jetson-io knows on this image. On Orin Nano/NX carriers (PAB, JAJ, PAB_V3) that string is `Jetson 22pin CSI Connector`. A mismatch is **silent**: the file is ignored and does not appear in `-l`. |
| `overlay-name` | Human-readable label shown by `-l` and passed to `-n`. Must be unique among overlays for that header. |
| `compatible` | Board/SKU match. ARK camera overlays use `JETSON_COMPATIBLE_P3768` (expanded at compile time to the Orin Nano/NX + p3768 set, including Super variants). |

On this platform the CSI header definition lives in jetson-io as `Headers/csi22.py` (`name = "Jetson 22pin CSI Connector"`). Camera addon overlays set `jetson-header-name` to that same string; the separate header overlay (`tegra234-p3767-0000+p3768-0000-csi.dts`) uses the same string as its `overlay-name` so jetson-io can attach the header.

**This is the usual reason a custom overlay “is in `/boot` but missing from `config-by-hardware.py -l`.”** Fix `jetson-header-name` (and rebuild for the target L4T if you copied the dtbo from another JetPack release).

Inspect a built overlay:

```
dtc -I dtb -O dts -o - /boot/<overlay>.dtbo | head -20
```

Confirm the three root properties above before debugging camera drivers or CSI wiring.

## In-tree layout

Per product under `products/<TARGET>/`:

| Path | Purpose |
|------|---------|
| `overlay/*.dts`, `overlay/*.dtsi` | Overlay sources ARK ships for that carrier |
| `overlay/dtbo.list` | Explicit set of `.dtbo` basenames built into the image and offered by jetson-io |
| `default_overlays` | Overlay(s) baked into the base DTB at flash time so the default camera works on first boot |

`build.sh` copies `products/<TARGET>/overlay/` into the staged BSP overlay tree, disables the stock `tegra234-p3767-camera-p3768-*` dtbo entries, and builds **exactly** the set in `dtbo.list` (plus non-camera stock overlays such as audio/CSI header). Add or drop a shipped camera overlay by editing those product files — not a BSP source mirror.

### Product examples

Use these as templates for custom work. Dual vs quad is carrier wiring, not sensor choice alone.

| Product | Ports | I2C mux style | Example overlay |
|---------|-------|---------------|-----------------|
| **PAB** | 4 CSI (quad) | TCA9546 (`nxp,pca9546`) | `products/PAB/overlay/tegra234-p3767-camera-p3768-imx219-quad.dts` (+ `tegra234-camera-quad-imx219.dtsi`) |
| **JAJ** | 2 CSI (dual) | `i2c-mux-gpio` | `products/JAJ/overlay/tegra234-p3767-camera-p3768-imx219-dual.dts` |
| **PAB_V3** | 2 CSI (dual) | `i2c-mux-gpio` | `products/PAB_V3/overlay/tegra234-p3767-camera-p3768-imx219-dual.dts` |

Shipped set per product is listed in that product's `overlay/dtbo.list` (IMX219 / IMX477 / IMX708 dual or quad as appropriate). Sensor modes and platform nodes often live in a sibling `.dtsi` included by the thin `.dts` that only sets jetson-io metadata and enable/reset GPIOs.

Every ARK camera overlay root looks like this pattern (names vary):

```dts
/ {
	overlay-name = "Camera IMX219 Dual";
	jetson-header-name = "Jetson 22pin CSI Connector";
	compatible = JETSON_COMPATIBLE_P3768;

	fragment@0 {
		target-path = "/";
		__overlay__ {
			/* vi / nvcsi / i2c / tegra-camera-platform … */
		};
	};
};
```

## Creating a custom overlay

1. **Pick a product tree that matches your carrier** (dual vs quad mux and GPIO map). Start from the closest in-tree `.dts`/`.dtsi` pair rather than a random NVIDIA sample for another board.
2. **Set the three root properties** as above. Choose a unique `overlay-name` (e.g. `"Camera MySensor Dual"`). Keep `jetson-header-name = "Jetson 22pin CSI Connector"` unless you are deliberately targeting a different header (you almost never are on these carriers).
3. **Wire CSI / I2C / reset** for your ports the same way the example for that product does (port-index, bus-width, mux channel, `reset-gpios`). Wrong port-index or mux channel is a common “driver loads, no video” failure mode after jetson-io already lists the overlay.
4. **Include `tegra-camera-platform`** (and capture-vi / nvcsi channel counts) consistent with the number of sensors. Compare IMX219 dual vs quad examples for structure.
5. **Register the overlay for the build** if you want it in the image and jetson-io list:
   - Add `my-overlay.dts` under `products/<TARGET>/overlay/`
   - Add `my-overlay.dtbo` as a line in `products/<TARGET>/overlay/dtbo.list`
6. **Optional default:** to bake it at flash time instead of IMX219, put that `.dtbo` basename in `products/<TARGET>/default_overlays` and re-flash.

If the sensor needs an out-of-tree driver, that is separate from the overlay (see `kernel_overlay/` and [kernel_development.md](kernel_development.md)). The overlay only describes hardware topology and platform nodes the existing driver expects.

## Build

From the host (same product you target on hardware):

```
./build.sh PAB   # or JAJ, PAB_V3
```

Built dtbos land under:

```
staging/<TARGET>/Linux_for_Tegra/source/kernel-devicetree/generic-dts/dtbs/
```

A full flash already installs every `dtbo.list` overlay into the image's `/boot`. The copy steps below are for **iterating without reflashing**.

## Install and select (on the Jetson)

Copy a rebuilt overlay:

```
DTB_PATH="staging/PAB/Linux_for_Tegra/source/kernel-devicetree/generic-dts/dtbs"
scp $DTB_PATH/<overlay>.dtbo jetson@192.168.55.1:~
```

On the Jetson:

```
sudo mv <overlay>.dtbo /boot
sudo /opt/nvidia/jetson-io/config-by-hardware.py -l
sudo /opt/nvidia/jetson-io/config-by-hardware.py -n 2="Camera IMX477 Quad"
sudo reboot
```

Use the exact `overlay-name` string from the list (and the header number shown for the 22pin CSI connector — typically header 2). Example list shape:

```
 Header 2: Jetson 22pin CSI Connector
   Available hardware modules:
   1. Camera IMX219 Quad
   2. Camera IMX477 Quad
   …
```

### Flash-time default vs jetson-io

Each carrier ships with an IMX219 overlay baked at flash time (quad on PAB, dual on JAJ and PAB_V3). `flash.sh` reads `products/<TARGET>/default_overlays` and passes each dtbo to tegraflash as `ADDITIONAL_DTB_OVERLAY`, which merges it into the base DTB for the detected Orin Nano/NX SKU. That is intentional: an `extlinux` `OVERLAYS` line would apply against the symbol-stripped UEFI DTB and can fail to resolve, so the ship default is baked at flash time rather than pre-selected in `extlinux.conf`.

A later `jetson-io` choice still supersedes cleanly: jetson-io boots its own `FDT`'d entry off the clean `/boot/dtb` kernel DTB, so selecting another camera does not collide with the flash-time bake. Change the ship default by editing `default_overlays` and re-flashing.

## JetPack / L4T version skew

Overlays are **not** portable across major L4T drops without review. When moving between JetPack releases (e.g. r36.4.4 → r36.5):

- Rebuild the overlay against the **same** L4T sources as the image on the board (`./build.sh` for that tree / `versions.env`).
- Re-check `jetson-header-name` and `compatible` against jetson-io and the stock overlays on the **target** image. NVIDIA can rename header strings or expand SKU compatible lists; an old string makes the overlay disappear from `-l` with no error.
- Prefer shipping sources under `products/<TARGET>/overlay/` and rebuilding over copying `.dtbo` binaries between images.

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| Overlay in `/boot` but not in `config-by-hardware.py -l` | Wrong or outdated `jetson-header-name`; wrong `compatible`; not a valid plugin dtbo |
| `-l` errors about multiple overlays | Duplicate `overlay-name` for the same header — remove or rename one |
| Overlay listed but no `/dev/video*` or Argus sensors | CSI port-index / bus-width / I2C address / reset GPIO / mux channel; or missing sensor driver module |
| Works on one JetPack, vanishes after upgrade | Header name or compatible changed; rebuild and fix root properties (see above) |
| Default camera wrong after flash | `default_overlays` points at the wrong dtbo, or that dtbo is not in `dtbo.list` so it was never built into `kernel/dtb/` |

After the overlay is selected and the board reboots, use the verification and stream commands in [cameras.md](cameras.md#test-commands).
