# Flash Package Generation & Distribution

This document covers how to generate self-contained flash packages and publish them to GitHub Releases. Customers can then flash a Jetson without cloning the repo or building from source.

## Generating a Flash Package

After running `build_kernel.sh`, generate a flash package:

```
./generate_flash_package.sh
```

No Jetson needs to be connected. The package includes DTBs for all module variants (Orin Nano 4GB/8GB, Orin NX 8GB/16GB) — the correct one is selected automatically at flash time.

The output is saved to the project root, e.g. `ark-pab-v3-nvme-super-dev.tar.gz`. The filename includes `dev` if the current commit isn't tagged.

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

If the package is under 2GB, you get a single `.tar.gz`. If it exceeds 2GB (the GitHub Releases per-file limit), it is automatically split into 1.9GB parts in a `_split/` directory with a `reassemble.sh` script included.

## Publishing a Release

After generating and testing the flash package:

```
./publish_release.sh v1.0.0
```

This script:
1. Renames the `*-dev*` tarball/split directory with the version
2. Creates a git tag and pushes it
3. Creates a GitHub Release and uploads the files

Requires the [GitHub CLI](https://cli.github.com/) (`gh`). Install with `sudo apt install gh` and authenticate with `gh auth login`.

## Flashing from a Package

Customers download the package from the [Releases page](https://github.com/ARK-Electronics/ark_jetson_kernel/releases) and run:

```
./flash_from_package.sh ark-pab-v3-nvme-super-v1.0.0.tar.gz
```

Or if the package was split:

```
./flash_from_package.sh ark-pab-v3-nvme-super-v1.0.0_split/
```

The script extracts the package, waits for a Jetson in recovery mode, and flashes it. No build tools or kernel source needed — just a Linux host with USB.

## Workflow Summary

```
./build_kernel.sh                    # build the kernel
./generate_flash_package.sh          # generate ark-*-dev.tar.gz / _split
# ... test the package on a Jetson ...
./publish_release.sh v1.0.0          # tag, rename, upload to GitHub Releases
```
