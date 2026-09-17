# JetPack 7 application provisioning

JetPack 7.2.1 uses Ubuntu Noble and Python 3.12. Its application package must be
`ark-os-jetson-noble`; the Jammy package contains a Python 3.10 virtual environment
and cannot be reused. Provisioning checks package name, arm64 architecture,
version, Python dependency, and the staged rootfs codename before installation.
It never substitutes another release or OS when the pinned artifact is missing.

`versions.env` pins ARK-OS 1.2.0 at source commit
`423570c1021174ca15801c5182ec288862865ad4`. That source supports Noble packaging,
but its [public release](https://github.com/ARK-Electronics/ARK-OS/releases/tag/v1.2.0)
currently only includes the Jetson Jammy package. Until a
matching Noble asset is published, build it locally:

```bash
./scripts/build_ark_os_noble.sh /tmp/ark-os-noble-build
./build.sh JAJ
```

The first command uses an ARM64 Ubuntu 24.04 container and writes the package plus
source/image/hash metadata into `downloads/`. On x86 hosts, working ARM64 binfmt
emulation is required; the build fails if the container cannot execute ARM64.
The source checkout and package build are isolated from any existing ARK-OS
checkout. The ARK-OS build compiles its native services, creates a Python 3.12
venv, and bundles the Node/MAVSDK runtimes pinned by that source revision. Its
upstream npm/pip dependency resolution is not fully locked, and the Ubuntu base
image and apt packages can change. The build metadata records provenance rather
than promising identical bytes across build dates. Building can take substantially
longer under emulation than on a native ARM64 host.

Release-tag CI builds this same pinned package in a separate native
`ubuntu-24.04-arm` job and transfers the deb plus provenance to the image job.
The image job verifies its checksum and source commit before provisioning.
Normal PR/main builds retain the kernel-only `--no-provision` path. GitHub lists
this runner in its [standard hosted runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).

A matching package built elsewhere can be placed under its canonical filename
in `downloads/`, or selected with `ARK_OS_DEB_PATH`. The override selects a file,
not a different package ABI or version; all metadata checks still apply. For
containerized image builds, keep the file under this repository; the wrapper
translates a relative or absolute host path to the container mount. Files outside
the repository must first be copied into `downloads/`.
Do not rename a Jammy package or edit its dependency metadata.

R39 camera provisioning installs the native BSP camera, GStreamer, multimedia,
and multimedia-utils packages without rewriting dependencies or holding their
versions. NVIDIA's selected `nvgpu`/`openrm` multimedia backend remains intact.
Before first boot publishes the GPU library search path, the chroot plugin
check uses a process-local path for Orin's `nvgpu` backend or the explicit staged
backend override. It does not run device detection or persist a GPU selection.
The R36.5 Argus workaround remains explicitly scoped to the audited R36.4.4
payload and its R36-only installed version pin; it cannot run on R39.

Noble's packaged WiFi firmware remains compressed as `.zst`. The shared kernel
fragment enables `CONFIG_FW_LOADER_COMPRESS` and `CONFIG_FW_LOADER_COMPRESS_ZSTD`
for all three carriers. R39 builds resolve and check the effective Kconfig before
compiling; an old staged defconfig used with `--fast` must include these settings.
Without them, ath11k reports missing firmware even when the compressed payload
is installed. This failure was reproduced on the first R39 JAJ image. Preserve
the packaged firmware and rebuild the matched kernel/modules/initrd with the
loader support; decompressing individual firmware files would conceal the
kernel configuration error.

The builder checks the assembled Python 3.12 environment by importing the API,
MAVLink/DroneCAN, and Flight Review dependencies, and checks backend syntax with
the bundled Node runtime. These are build-time smoke tests. Upstream treats
Lintian as advisory: the tested package reports Debian policy errors and warnings
for its bundled runtime files, so it is not Lintian-clean.

R39's native display script loads `nvgpu` followed immediately by `nvidia_drm`.
On the tested Orin NX, DRM powered the GPU before the asynchronous native GPU
udev handler applied the configured static masks. The later `nvpmodel` service
then requested a reboot, leaving the mode unset. Jetson-stats 4.3.2 subsequently
crashed when it parsed that unset-mode response, so an HTTP 200 response from
ARK-OS did not contain valid Jetson metadata.

Every R39 build applies the release-guarded
[`patch_gpu_power_order.py`](../scripts/patch_gpu_power_order.py) correction.
Within the native `nvgpu-l4t` branch it waits for a change event on exactly the
Orin platform GPU, runs NVIDIA's existing power-policy script successfully, and
requires `nvpmodel -q` to report a named mode and numeric ID before loading DRM.
It preserves the configured/default power policy and both GPU backend choices.
An error fails display startup visibly; it does not answer a reboot prompt.

The helper also owns
`nv-load-display-modules.service.d/20-ark-gpu-power-before-api.conf`, containing
`Before=system-manager.service`. This ordering is on the GPU service so the
early-API profile's consumer-unit rewrite cannot erase it. It matters because
constructing a jtop client starts library discovery, including `vulkaninfo`,
before the connection to jtop.service succeeds. The dependency delays that
probe until display/power setup finishes. It does not make the API depend on
successful display startup: the API may report degraded information after a
failure, while the strict metadata readiness criterion remains unsatisfied.
Native Argus and display-manager ordering is retained.

The helper audits the native scripts, udev rule, service bodies and supported
systemd drop-in locations, and supports `--mode check` and `--mode restore`.
Unknown revisions or custom ordering are rejected. For a staged image:

```bash
sudo python3 scripts/patch_gpu_power_order.py \
  --rootfs staging/JAJ/Linux_for_Tegra/rootfs --product JAJ --mode check
```

Noble provisioning also applies
[`patch_ark_nginx_upstreams.py`](../scripts/patch_ark_nginx_upstreams.py) to the
exact audited ARK-OS nginx site. It changes the API-gateway and Flight Review
upstreams on ports 3000 and 5006 from `localhost` to `127.0.0.1`, matching their
IPv4 listeners. The original name could resolve to `::1` and produce HTTP 502
while the applications themselves were healthy. Service bindings, application
proxy URLs, DNS and hosts configuration are preserved. The helper supports
check/restore, rejects changed site content or a non-Noble/R39 rootfs, and does
not reload nginx. Validate with `nginx -t` before separately reloading a running
target; staged provisioning does not need a daemon reload.

After provisioning, check `apt-get check`, the installed ARK-OS package/version,
`/usr/lib/ark-os/venv/bin/python3 --version`, and loading the NVIDIA GStreamer
plugin. After flashing, verify the selected GPU backend, a valid `nvpmodel -q`
response, stable jtop service and populated ARK-OS metadata before accepting
application readiness. Camera capture/relaunch and customer inference require
separate tests. The [R39 validation record](jetpack7_validation.md) distinguishes
completed host checks and JAJ hardware checks from those outstanding tests;
[fast-boot instructions](fast_boot.md) describe the optional startup profile.

R39 also suppresses upstream `btusb` whenever the out-of-tree `rtk_btusb` module
is installed. The Realtek driver advertises generic Bluetooth USB aliases but
rejects unsupported devices in its probe, leaving the tested Qualcomm controller
without an HCI device. Loading generic `btusb` alongside it restored `hci0`.
The release-guarded [`patch_bluetooth_preference.py`](../scripts/patch_bluetooth_preference.py)
changes only that vendor install rule: load `rtk_btusb` first when available,
then load `btusb` with the requested module options. Both drivers stay available;
the Ethernet and WiFi module preference rules remain unchanged. A Realtek module
load error remains visible and stops that install attempt. The kernel's
[driver binding order](https://docs.kernel.org/driver-api/driver-model/binding.html)
gives the first registered driver the initial probe opportunity. Realtek adapter
hardware was not available for validation.

The Bluetooth helper supports the same `--rootfs`, `--product` and
`--mode apply|check|restore` interface as the GPU helper. Every R39 build applies
it to fresh or reused staging trees. Applying the rule does not load, unload or
rebind a live device; verify automatic Bluetooth and application service startup
after a cold boot. The helper checks the complete vendor/patched configuration
hash and preserves file ownership and permissions, with synthetic fixtures in
the repository instead of a copy of NVIDIA's proprietary configuration file.
