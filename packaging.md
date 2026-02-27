# Flash Package Generation & Distribution

This document covers how to generate self-contained flash packages and publish them to GitHub Releases. Customers can then flash a Jetson without cloning the repo or building from source.

## Generating a Flash Package

After running `build_kernel.sh`, generate a flash package:

```
./generate_flash_package.sh
```

No Jetson needs to be connected. The package includes DTBs for all module variants (Orin Nano 4GB/8GB, Orin NX 8GB/16GB) — the correct one is selected automatically at flash time.

The output is saved to the project root, e.g. `ark-pab-v3-nvme-super.tar.gz`.

### Options

| Flag | Description |
|------|-------------|
| `--sdcard` | Generate a package for SD card instead of NVMe |
| `--no-super` | Target the non-super module variant |

```
# Generate for SD card
./generate_flash_package.sh --sdcard

# Non-super module variant
./generate_flash_package.sh --no-super
```

### Output

If the package is under 2GB, you get a single `.tar.gz`. If it exceeds 2GB (the GitHub Releases per-file limit), it is automatically split into 1.9GB parts in a `_split/` directory.

## Publishing a Release

After generating and testing the flash package:

```
./publish_release.sh v1.0.0
```

This script:
1. Creates a git tag and pushes it
2. Creates a GitHub Release with flashing instructions
3. Uploads the flash package and `flash_from_package.sh`

Requires the [GitHub CLI](https://cli.github.com/) (`gh`). Install with `sudo apt install gh` and authenticate with `gh auth login`.

## Flashing from a Package (Customer)

Customers download and run the flash script — it handles everything:

```
curl -LO https://github.com/ARK-Electronics/ark_jetson_kernel/releases/download/<version>/flash_from_package.sh
chmod +x flash_from_package.sh
./flash_from_package.sh <version>
```

Or to flash the latest release:

```
./flash_from_package.sh
```

The script downloads the package from GitHub Releases, reassembles if split, extracts, waits for a Jetson in recovery mode, and flashes. No build tools or kernel source needed — just an Ubuntu 22.04 host with USB.

A local `.tar.gz` or split directory can also be passed directly:

```
./flash_from_package.sh ark-pab-v3-nvme-super.tar.gz
```

## Workflow Summary

```
./build_kernel.sh                    # build the kernel
./generate_flash_package.sh          # generate ark-*.tar.gz / _split
# ... test the package on a Jetson ...
./publish_release.sh v1.0.0          # tag, upload to GitHub Releases
```
