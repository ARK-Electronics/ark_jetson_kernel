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

Releases are driven by git tags. Push a tag matching `{product}-{version}` and CI builds the flash package and creates a GitHub Release automatically.

### Tag format

`{product}-{jetpack_version}.{ark_revision}` — for example `pab-6.2.1.1`.

- The Jetpack version (`6.2.1`) comes from NVIDIA's Jetpack release.
- The ARK revision (`.1`) is incremented for each ARK release on that Jetpack version.
- Each product versions independently.

### Creating a release

Build the kernel for your target, then tag and push:

```
./build.sh PAB
./packaging/publish_release.sh PAB 6.2.1.1    # creates tag pab-6.2.1.1 and pushes
```

Or tag manually:

```
git tag -a pab-6.2.1.1 -m "pab-6.2.1.1"
git push origin pab-6.2.1.1
```

CI will build the kernel, generate the flash package, and publish the release. Monitor progress at https://github.com/ARK-Electronics/ark_jetson_kernel/actions.

### Releasing all products at the same version

When a change affects all carriers, push a tag for each:

```
git tag -a pab-6.2.1.1 -m "pab-6.2.1.1"
git tag -a jaj-6.2.1.1 -m "jaj-6.2.1.1"
git tag -a pab-v3-6.2.1.1 -m "pab-v3-6.2.1.1"
git push origin pab-6.2.1.1 jaj-6.2.1.1 pab-v3-6.2.1.1
```

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

> This replaces the older massflash (`mfi`) package, which had to pre-bake a single `BOARDSKU` and could therefore flash only one variant — NVIDIA massflash requires every unit to be identical hardware (`tools/kernel_flash/README_initrd_flash.txt`). The trade-off: the flasher builds the flash images on the flashing host, which adds several minutes per run — but `flash_from_package.sh` reuses the previous run's images when the connected module is the same variant, so repeat flashes skip the rebuild.

The output is saved to the project root, e.g. `ark-pab-nvme-super.tar.gz`.

### Options

| Flag | Description |
|------|-------------|
| `--no-super` | Target the non-super module variant |

If the package exceeds 2GB (the GitHub Releases per-file limit), it is automatically split into 1.9GB parts in a `_split/` directory.

## Flashing from a Release (customer)

Download and run the flash script:

```
curl -LO https://github.com/ARK-Electronics/ark_jetson_kernel/releases/download/pab-6.2.1.1/flash_from_package.sh
chmod +x flash_from_package.sh
./flash_from_package.sh pab-6.2.1.1
```

Or flash the latest release for a product:

```
./flash_from_package.sh pab
```

The script downloads the package, extracts it, waits for a Jetson in recovery mode, and flashes. No build tools or kernel source needed — just a Debian/Ubuntu host with USB.

Each version is cached in `~/.ark-jetson-cache/<tag>/` so re-running after a failure or switching between versions doesn't re-download.

When flashing several units back to back (e.g. on the production line), the script also reuses the flash images built by the previous run: it probes the connected module's EEPROM (~15 s) and, if the module is the same variant the images were built for, flashes with `--flash-only`, skipping the ~5 min image build. A different module variant regenerates automatically, and `--full` forces regeneration.
