# Flash Package Generation & Distribution

This document covers how to generate self-contained flash packages and publish them to GitHub Releases. Customers can then flash a Jetson without cloning the repo or building from source.

## Carrier targets

`build_kernel.sh` supports three ARK carriers: `PAB`, `PAB_V3`, and `JAJ`. Each produces a different set of device-tree binaries (pinmux, pad voltages, carrier-specific peripherals), so a flash package is only valid for the carrier it was built for. Module SKU (Orin Nano 4GB/8GB, Orin NX 8GB/16GB) is auto-detected at flash time within a given carrier package.

A release must include all three carrier packages. `publish_release.sh` enforces this.

## Generating a Flash Package

After running `build_kernel.sh` for a given target, generate its package:

```
./packaging/generate_flash_package.sh
```

The script reads `source_build/LAST_BUILT_TARGET` (written by `build_kernel.sh`) to name the output. No Jetson needs to be connected.

Outputs:
- `ark-<target>-<storage>[-super].tar.gz` (or `_split/` dir if >2GB) — the MFI
- `ark-<target>-<storage>[-super].BUILD_INFO.txt` (or `BUILD_INFO.txt` inside the split dir) — sidecar with commit hash, used by `publish_release.sh`

### Options

| Flag | Description |
|------|-------------|
| `--sdcard` | Generate a package for SD card instead of NVMe |
| `--no-super` | Target the non-super module variant |

```
# Generate for SD card
./packaging/generate_flash_package.sh --sdcard

# Non-super module variant
./packaging/generate_flash_package.sh --no-super
```

### Output

If the package is under 2GB, you get a single `.tar.gz`. If it exceeds 2GB (the GitHub Releases per-file limit), it is automatically split into 1.9GB parts in a `_split/` directory.

## Publishing a Release

Build and generate for each carrier before publishing:

```
# 1. PAB
./build_kernel.sh                     # pick PAB
./packaging/generate_flash_package.sh

# 2. PAB_V3
./build_kernel.sh                     # pick PAB_V3
./packaging/generate_flash_package.sh

# 3. JAJ
./build_kernel.sh                     # pick JAJ
./packaging/generate_flash_package.sh

# 4. Publish
./packaging/publish_release.sh v1.0.0
```

`build_kernel.sh` auto-cleans build artifacts on target switch, so it is safe to cycle through targets in one working tree.

`publish_release.sh`:
1. Collects every `ark-*.tar.gz` / `ark-*_split/` in the project root
2. Verifies all three targets (`pab`, `pab-v3`, `jaj`) are present — errors otherwise
3. Verifies every package's sidecar `BUILD_INFO.txt` reports the same commit hash — errors on mismatch
4. Creates the git tag and pushes it
5. Creates a GitHub Release with flashing instructions and the build commit
6. Uploads every package, every split part, and `flash_from_package.sh`

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

When the release contains multiple carrier packages, the script prompts interactively for which to flash (PAB / PAB_V3 / JAJ). It then downloads only that carrier's assets, reassembles split parts, waits for a Jetson in recovery mode, and flashes. No build tools or kernel source needed — just an Ubuntu 22.04 host with USB.

Each `(version, target)` is cached separately in `~/.ark-jetson-cache/<version>/<target>/` so switching between targets doesn't re-download a target already fetched.

A local `.tar.gz` or split directory can also be passed directly:

```
./flash_from_package.sh ark-pab-v3-nvme-super.tar.gz
```

## Workflow Summary

```
# For each target (PAB, PAB_V3, JAJ):
./build_kernel.sh                              # pick target
./packaging/generate_flash_package.sh          # ark-<target>-*.tar.gz + sidecar

# ... test any/all packages on a Jetson ...

./packaging/publish_release.sh v1.0.0          # verify, tag, upload
```
