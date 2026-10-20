# Flash Package Generation & Distribution

This document covers how flash packages are generated, released, and consumed. Each release targets a single carrier board product.

## Carrier Targets

| Target | Product | Tag prefix |
|--------|---------|------------|
| PAB | ARK Jetson PAB Carrier | `pab-` |
| JAJ | ARK Just a Jetson Carrier | `jaj-` |
| PAB_V3 | ARK Jetson PAB V3 Carrier | `pab-v3-` |

Each carrier has its own device tree. A flash package built for one carrier will not work on another.

> **Note:** PAB Rev3 is not the same as PAB_V3. PAB_V3 is a separate product.

## Releasing

A release is a git tag `{product}-{version}`. The tag build publishes the package as an unlisted draft on downloads.arkelectron.com; after bench validation, `promote.yml` copies the same bytes into the public release folder.

### Tag format

`{product}-{jetpack_version}.{ark_revision}` — for example `pab-6.2.2.7`.

- The Jetpack version (`6.2.2`) comes from NVIDIA's Jetpack release.
- The ARK revision (`.7`) is incremented for each ARK release on that Jetpack version.
- A version is built once. The site never replaces a version folder, so a fix after a failed validation is a new version.

### Changelog

Each product has `products/<TARGET>/CHANGELOG.md`, newest release first. Before tagging, merge a `## <version> - <YYYY-MM-DD>` section to `main` for every product being released; a tag build without one fails before it builds. The site serves that section in the version folder and the whole file as the product's changelog, and the product's GitBook Release Notes page is a copy of the whole file.

### Cutting a release

```
./packaging/publish_release.sh PAB 6.2.2.7    # checks the changelog, creates tag pab-6.2.2.7 and pushes it
```

When a change affects all carriers, tag each at the same commit:

```
git tag -a pab-6.2.2.7 -m "PAB v6.2.2.7"
git tag -a jaj-6.2.2.7 -m "JAJ v6.2.2.7"
git tag -a pab-v3-6.2.2.7 -m "PAB_V3 v6.2.2.7"
git push origin pab-6.2.2.7 jaj-6.2.2.7 pab-v3-6.2.2.7
```

Each tag build takes about 70 minutes and publishes:

| Path | Contents |
|---|---|
| `dev/jetson/<product>/<version>/` | The draft: package, its `CHANGELOG.md` section, `SHA256SUMS` |
| `dev/jetson/<product>/draft/VERSION` | The newest draft's version |

Drafts are unlisted and expire 90 days after the build. `dev/` has no folder pages, so a folder URL returns Access Denied; open a file by name, e.g. `https://downloads.arkelectron.com/dev/jetson/pab/6.2.2.7/ark-pab-nvme-super.tar.gz`.

### Promoting

Validate the draft with `./flash_from_package.sh pab-6.2.2.7`, then:

```
gh workflow run promote.yml -f tag=pab-6.2.2.7
```

This copies the draft to `jetson/<product>/<version>/`, points `jetson/<product>/latest/` at it and publishes `jetson/<product>/CHANGELOG.md`. Promote every product of a version together, so `flash_from_package.sh <product>` never hands out a mixed fleet.

## Generating a Flash Package (local)

After running `build.sh`, generate a flash package locally:

```
./packaging/generate_flash_package.sh PAB
```

No Jetson needs to be connected to generate the package. It is the staged
`Linux_for_Tegra` tree (minus the kernel build sources), which NVIDIA's initrd
flasher consumes directly. Because the flasher reads the connected module's
EEPROM at flash time, a single package flashes **all** Orin Nano/NX variants
(4GB/8GB/16GB) — it selects both the kernel DTB and the bootloader/SDRAM config
to match the attached module. There is no per-SKU build, so one release per
carrier covers every module variant.

The package also records the product's default camera overlay(s) (`products/<TARGET>/default_overlays`) in its `ark_flash.conf`; `flash_from_package.sh` bakes them into the DTB at flash time, same as `./flash.sh`.

> This replaces the older massflash (`mfi`) package, which had to pre-bake a single `BOARDSKU` and could therefore flash only one variant — NVIDIA massflash requires every unit to be identical hardware (`tools/kernel_flash/README_initrd_flash.txt`). The trade-off: the flasher builds the flash images on the flashing host, which adds several minutes per run — but `flash_from_package.sh` reuses the previous run's images when the connected module is the same variant, so repeat flashes skip the rebuild.

The output is saved to the project root, e.g. `ark-pab-nvme-super.tar.gz`.

## Flashing from a Release (customer)

Download the flasher from `main` and run it:

```
curl -LO https://raw.githubusercontent.com/ARK-Electronics/ark_jetson_kernel/main/packaging/flash_from_package.sh
chmod +x flash_from_package.sh
./flash_from_package.sh pab-6.2.2.6
```

Or flash the latest release, or the newest draft, for a product:

```
./flash_from_package.sh pab
./flash_from_package.sh pab --draft
```

The script downloads the package, extracts it, waits for a Jetson in recovery mode, flashes, and then waits for the board to finish its first boot (`ARK_FIRST_BOOT_TIMEOUT`, default 480 s). No build tools or kernel source needed — just a Debian/Ubuntu host with USB.

Each version is cached in `~/.ark-jetson-cache/<tag>/` so re-running after a failure or switching between versions doesn't re-download.

When flashing several units back to back (e.g. on the production line), the script also reuses the flash images built by the previous run: the module variant is identified from its recovery-mode USB product ID (instantly, with no device interaction) and, if it matches the variant the images were built for, the script flashes with `--flash-only`, skipping the ~5 min image build. Orin Nano 8GB additionally gets an EEPROM probe (~20 s plus a wait for the module to reset) because two module SKUs share its USB ID. A different module variant regenerates automatically, and `--full` forces regeneration.
