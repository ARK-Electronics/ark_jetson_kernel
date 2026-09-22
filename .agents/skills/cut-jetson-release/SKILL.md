---
name: cut-jetson-release
description: Cut, validate, and publish an ark_jetson_kernel product release (pab / jaj / pab-v3 X.Y.Z.N). Invoke when the user asks to cut, tag, build, validate, or publish a Jetson release, or to promote a draft release to published.
---

# Cut a Jetson release

A release is a pushed annotated tag. Tag builds are the only thing that produces a flash package, and every release is **born a draft** — publishing is a separate manual step after bench validation.

## 1. Pre-flight

- Everything intended for the release must be merged to `main`; the tag build checks out the tagged commit, so an unmerged fix is simply absent.
- `versions.env` pins the payload (`ARK_OS_VERSION`, `NV_CAMERA_STACK_VERSION`, BSP). Confirm any referenced ARK-OS version is *published*, or `--provision` fails to resolve it.
- `products/<TARGET>/default_overlays` names the flash-time camera bake; `generate_flash_package.sh` fails loud at tag time if the dtbo isn't in `kernel/dtb/`.

## 2. Tag

Three products, three tags, one version. Tag names are parsed by `.github/workflows/build.yml` (`pab-v3-` before `pab-`, so ordering is already handled):

```bash
git tag -a jaj-6.2.2.7    -m "JAJ v6.2.2.7"    <sha>
git tag -a pab-6.2.2.7    -m "PAB v6.2.2.7"    <sha>
git tag -a pab-v3-6.2.2.7 -m "PAB_V3 v6.2.2.7" <sha>
git push origin jaj-6.2.2.7 pab-6.2.2.7 pab-v3-6.2.2.7
```

Per-tag concurrency groups let all three run in parallel without cancelling each other. Each build is ~70 min cold, 180 min timeout.

## 3. Validate the draft

Watch with `gh run list --workflow build.yml`. When a draft exists, flash it on the production laptop — **always by explicit tag**:

```bash
curl -LO https://raw.githubusercontent.com/ARK-Electronics/ark_jetson_kernel/main/packaging/flash_from_package.sh
chmod +x flash_from_package.sh
./flash_from_package.sh jaj-6.2.2.7          # auto-detects the unpublished draft
./flash_from_package.sh jaj --draft          # or: latest draft for a product
```

**The gotcha that has bitten the bench twice:** a bare product name (`flash_from_package.sh jaj`) resolves the latest *published* release, which is not the one you just cut. Drafts need an authenticated `gh`. Confirm what a device actually received with `cat /etc/ark_jetson_kernel` (commit / date / target stamp) before believing a test result.

Validate at minimum: boots, camera live on first boot (the flash-time overlay bake), and whatever subsystem the release exists to fix.

## 4. Publish

```bash
gh release edit jaj-6.2.2.7 --draft=false
```

Same artifact, same tag — no rebuild, no rename. Publish all three products together so `flash_from_package.sh <product>` doesn't hand out a mixed fleet.
