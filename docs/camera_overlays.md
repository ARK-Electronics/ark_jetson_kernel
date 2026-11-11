# Camera overlays

This page explains how ARK's camera device-tree overlays are laid out, built and installed, and how to write or port one of your own. [cameras.md](cameras.md) lists the supported sensors and how to test them. [device-tree.md](device-tree.md) shows where overlays sit relative to the BCT and the base DT fragment.

## How jetson-io discovers overlays

NVIDIA's `jetson-io`, which you run as `config-by-hardware.py`, does **not** list every `.dtbo` under `/boot`. It scans `/boot/*.dtbo` and keeps only the overlays whose root properties match the running board and the headers it knows.

| Property | Role |
|----------|------|
| `jetson-header-name` | Must **exactly** match a header name jetson-io knows on this image. On Orin Nano/NX carriers (PAB, JAJ, PAB_V3) that string is `Jetson 22pin CSI Connector`. A mismatch is **silent**: the file is ignored and does not appear in `-l`. |
| `overlay-name` | Human-readable label shown by `-l` and passed to `-n`. Must be unique among overlays for that header. |
| `compatible` | Board/SKU match. ARK camera overlays use `JETSON_COMPATIBLE_P3768` (expanded at compile time to the Orin Nano/NX + p3768 set, including Super variants). |

On this platform, jetson-io defines the CSI header in `Headers/csi22.py`, with `name = "Jetson 22pin CSI Connector"`. Camera overlays set `jetson-header-name` to that same string. The separate header overlay, `tegra234-p3767-0000+p3768-0000-csi.dts`, uses the string as its `overlay-name`, so that jetson-io can attach the header.

A wrong `jetson-header-name` is the usual reason a custom overlay is in `/boot` but missing from `config-by-hardware.py -l`. Fix the property. If you copied the dtbo from another JetPack release, also rebuild it for the L4T on the board.

To see the root properties of a built overlay, decompile it:

```
dtc -I dtb -O dts -o - /boot/<overlay>.dtbo | head -20
```

Check the three properties above before you debug camera drivers or CSI wiring.

## In-tree layout

Each product keeps its overlays under `products/<TARGET>/`:

| Path | Purpose |
|------|---------|
| `overlay/*.dts`, `overlay/*.dtsi` | Overlay sources ARK ships for that carrier |
| `overlay/dtbo.list` | Explicit set of `.dtbo` basenames built into the image and offered by jetson-io |
| `default_overlays` | Overlay(s) baked into the base DTB at flash time so the default camera works on first boot |

`build.sh` copies `products/<TARGET>/overlay/` into the staged BSP overlay tree and disables the stock `tegra234-p3767-camera-p3768-*` dtbo entries. It then builds **exactly** the set in `dtbo.list`, plus the stock overlays that are not for cameras, such as the audio and CSI header ones. To add or drop a shipped camera overlay, edit those product files, not a copy of the BSP sources.

### Product examples

Use these as templates for your own overlay. Whether an overlay is dual or quad follows from the carrier's wiring, not from the sensor alone.

| Product | Ports | I2C mux style | Example overlay |
|---------|-------|---------------|-----------------|
| **PAB** | 4 CSI (quad) | TCA9546 (`nxp,pca9546`) | `products/PAB/overlay/tegra234-p3767-camera-p3768-imx219-quad.dts` (+ `tegra234-camera-quad-imx219.dtsi`) |
| **JAJ** | 2 CSI (dual) | `i2c-mux-gpio` | `products/JAJ/overlay/tegra234-p3767-camera-p3768-imx219-dual.dts` |
| **PAB_V3** | 2 CSI (dual) | `i2c-mux-gpio` | `products/PAB_V3/overlay/tegra234-p3767-camera-p3768-imx219-dual.dts` |

Each product's `overlay/dtbo.list` names the overlays it ships: IMX219, IMX477 and IMX708, as dual or quad to suit the carrier. The sensor modes and platform nodes often live in a sibling `.dtsi`. A thin `.dts` includes it and sets only the jetson-io metadata and the enable and reset GPIOs.

The root of every ARK camera overlay follows this pattern, with its own names:

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

1. **Pick the product tree that matches your carrier.** The I2C mux and the GPIO map differ between dual and quad carriers. Start from the closest `.dts`/`.dtsi` pair in this repo, not from an NVIDIA sample for another board.
2. **Set the three root properties** described above. Choose an `overlay-name` that no other overlay uses, such as `"Camera MySensor Dual"`. Keep `jetson-header-name = "Jetson 22pin CSI Connector"` unless you mean to target a different header, which is rare on these carriers.
3. **Wire CSI, I2C and reset** for your ports the way that product's example does: port-index, bus-width, mux channel and `reset-gpios`. A wrong port-index or mux channel is a common cause of a driver that loads but gives no video, even though jetson-io lists the overlay.
4. **Include `tegra-camera-platform`**, and set the capture-vi and nvcsi channel counts to match the number of sensors. Compare the IMX219 dual and quad examples to see the structure.
5. **Register the overlay for the build** if you want it in the image and in the jetson-io list:
   - Add `my-overlay.dts` under `products/<TARGET>/overlay/`
   - Add `my-overlay.dtbo` as a line in `products/<TARGET>/overlay/dtbo.list`
6. **Make it the default** if you want it applied at flash time in place of the IMX219. Put the `.dtbo` basename in `products/<TARGET>/default_overlays` and flash again.

A sensor that needs an out-of-tree driver gets it separately from the overlay. See `kernel_overlay/` and [kernel_development.md](kernel_development.md). The overlay only describes the hardware topology and the platform nodes that the driver expects.

## Build

Build on the host, for the product you have on the bench:

```
./build.sh PAB   # or JAJ, PAB_V3
```

The built dtbos land here:

```
staging/<TARGET>/Linux_for_Tegra/source/kernel-devicetree/generic-dts/dtbs/
```

A full flash already installs every overlay in `dtbo.list` into the image's `/boot`. The copy steps below are for trying a change without flashing again.

## Install and select (on the Jetson)

Copy the rebuilt overlay to the Jetson:

```
DTB_PATH="staging/PAB/Linux_for_Tegra/source/kernel-devicetree/generic-dts/dtbs"
scp $DTB_PATH/<overlay>.dtbo jetson@jetson.local:~
```

Then, on the Jetson, move it into `/boot`, select it and reboot:

```
sudo mv <overlay>.dtbo /boot
sudo /opt/nvidia/jetson-io/config-by-hardware.py -l
sudo /opt/nvidia/jetson-io/config-by-hardware.py -n 2="Camera IMX477 Quad"
sudo reboot
```

Use the overlay's name exactly as the list shows it, along with the header number the list gives for the 22pin CSI connector, which is usually header 2. The list looks like this:

```
 Header 2: Jetson 22pin CSI Connector
   Available hardware modules:
   1. Camera IMX219 Quad
   2. Camera IMX477 Quad
   …
```

### Flash-time default vs jetson-io

Each carrier ships with an IMX219 overlay applied at flash time: the quad on PAB, and the dual on JAJ and PAB_V3. `flash.sh` reads `products/<TARGET>/default_overlays` and passes each dtbo to tegraflash as `ADDITIONAL_DTB_OVERLAY`. Tegraflash merges it into the base DTB for the Orin Nano/NX SKU it detects.

The default is applied this way on purpose. An `OVERLAYS` line in `extlinux.conf` would apply against the UEFI DTB, which has its symbols stripped, and can fail to resolve.

A camera you select later with `jetson-io` still takes over cleanly. jetson-io boots its own entry, which names an `FDT` built from the clean kernel DTB in `/boot/dtb`, so your choice does not collide with the flash-time default. To change the shipped default, edit `default_overlays` and flash again.

## JetPack / L4T version skew

An overlay does **not** carry over to another L4T release without a review. When you move between JetPack releases, for example from r36.4.4 to r36.5:

- Rebuild the overlay against the same L4T sources as the image on the board. That means running `./build.sh` in a tree whose `versions.env` names that release.
- Check `jetson-header-name` and `compatible` again, against jetson-io and the stock overlays on the target image. NVIDIA can rename a header string or extend a SKU's compatible list, and an old string makes the overlay vanish from `-l` with no error.
- Keep the sources under `products/<TARGET>/overlay/` and rebuild them. Don't copy `.dtbo` binaries from one image to another.

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| Overlay in `/boot` but not in `config-by-hardware.py -l` | Wrong or outdated `jetson-header-name`; wrong `compatible`; not a valid plugin dtbo |
| `-l` errors about multiple overlays | Duplicate `overlay-name` for the same header — remove or rename one |
| Overlay listed but no `/dev/video*` or Argus sensors | CSI port-index / bus-width / I2C address / reset GPIO / mux channel; or missing sensor driver module |
| Works on one JetPack, vanishes after upgrade | Header name or compatible changed; rebuild and fix root properties (see above) |
| Default camera wrong after flash | `default_overlays` points at the wrong dtbo, or that dtbo is not in `dtbo.list` so it was never built into `kernel/dtb/` |

Once the overlay is selected and the board has rebooted, test the camera with the commands in [cameras.md](cameras.md#test-commands).
