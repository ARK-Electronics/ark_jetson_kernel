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

No Jetson needs to be connected. The package includes DTBs for all module variants (Orin Nano 4GB/8GB, Orin NX 8GB/16GB) — the correct one is selected automatically at flash time.

The output is saved to the project root, e.g. `ark-pab-nvme-super.tar.gz`.

### Options

| Flag | Description |
|------|-------------|
| `--sdcard` | Generate a package for SD card instead of NVMe |
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
