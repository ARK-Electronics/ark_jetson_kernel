---
name: cut-jetson-release
description: Cut, validate, and publish an ark_jetson_kernel product release (pab / jaj / pab-v3 X.Y.Z.N). Invoke when the user asks to cut, tag, build, validate, or publish a Jetson release, or to promote a draft release to published.
---

# Cut a Jetson release

A release is a pushed annotated tag. The tag build publishes the flash package as an unlisted **draft** on downloads.arkelectron.com; promoting it to a release is a separate manual step after bench validation. Nothing goes to GitHub Releases.

## 1. Pre-flight

- Everything intended for the release must be merged to `main`; the tag build checks out the tagged commit, so an unmerged fix is simply absent.
- Each product being released needs a `## <version> - <YYYY-MM-DD>` section at the top of `products/<TARGET>/CHANGELOG.md` on `main`: customer-facing bullets, no PR numbers. The tag build fails before building without it.
- `versions.env` pins the payload (`ARK_OS_VERSION`, `NV_CAMERA_STACK_VERSION`, BSP). Confirm any referenced ARK-OS version is *published*, or `--provision` fails to resolve it.
- `products/<TARGET>/default_overlays` names the flash-time overlay bake; `generate_flash_package.sh` fails loud at tag time if a dtbo isn't in `kernel/dtb/`.
- A version is built once: the site never replaces a version folder. A fix after a failed validation is the next version, never a moved tag.

## 2. Tag

Three products, three tags, one version. Tag names are parsed by `.github/workflows/build.yml` (`pab-v3-` before `pab-`, so ordering is already handled):

```bash
git tag -a jaj-6.2.2.7    -m "JAJ v6.2.2.7"    <sha>
git tag -a pab-6.2.2.7    -m "PAB v6.2.2.7"    <sha>
git tag -a pab-v3-6.2.2.7 -m "PAB_V3 v6.2.2.7" <sha>
git push origin jaj-6.2.2.7 pab-6.2.2.7 pab-v3-6.2.2.7
```

Per-tag concurrency groups let all three run in parallel. Each build is ~70 min cold, 180 min timeout, and ends by publishing `dev/jetson/<product>/<version>/` and pointing `dev/jetson/<product>/draft/VERSION` at it.

## 3. Validate the draft

Watch with `gh run list --workflow build.yml`. When the build is green, flash it — **always by explicit tag**:

```bash
curl -LO https://raw.githubusercontent.com/ARK-Electronics/ark_jetson_kernel/main/packaging/flash_from_package.sh
chmod +x flash_from_package.sh
./flash_from_package.sh jaj-6.2.2.7          # a tag not yet released resolves to its draft
./flash_from_package.sh jaj --draft          # or: the newest draft for a product
```

**The gotcha that has bitten the bench twice:** a bare product name (`flash_from_package.sh jaj`) resolves the latest *released* version, which is not the one you just cut. ark-bench's Jetson bundles flash the draft only with the Begin-dialog draft checkbox. Confirm what a device actually received with `cat /etc/ark_jetson_kernel` (commit / date / target stamp) before believing a test result.

Validate at minimum: boots, camera live on first boot (the flash-time overlay bake), and whatever subsystem the release exists to fix.

## 4. Promote

```bash
for tag in jaj-6.2.2.7 pab-6.2.2.7 pab-v3-6.2.2.7; do gh workflow run promote.yml -f tag=$tag; done
```

`promote.yml` copies the draft server side to `jetson/<product>/<version>/` (byte for byte what was validated), points `jetson/<product>/latest/` at it, publishes `products/<TARGET>/CHANGELOG.md` as of the tag to `jetson/<product>/CHANGELOG.md`, and rebuilds the index. Promote all three products together so `flash_from_package.sh <product>` doesn't hand out a mixed fleet.

## 5. GitBook release notes

Each product's GitBook "Release Notes" page is a copy of its `products/<TARGET>/CHANGELOG.md`. After promoting, copy each released product's file into its page in `~/code/ark/gitbook` and open a PR:

| Target | GitBook page |
|---|---|
| JAJ | `embedded-computers/ark-just-a-jetson/release-notes.md` |
| PAB | `flight-controller/jetson-pabs/ark-jetson-pab-carrier/release-notes.md` |
| PAB_V3 | `flight-controller/jetson-pabs/ark-jetson-pab-carrier-v3/release-notes.md` |
