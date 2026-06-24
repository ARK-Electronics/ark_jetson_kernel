# kernel_overlay

Source files layered onto the L4T kernel tree at build time. The contents mirror
`Linux_for_Tegra/source/`; `build.sh` copies them into the staged source tree on
every build (after the NVIDIA sources are extracted) and registers any new
out-of-tree modules in the relevant Makefile.

This exists because the kernel and `nvidia-oot` sources are extracted fresh from
NVIDIA tarballs into `staging/` and have no version-controlled hook of their own —
anything added here survives `--clean` and BSP bumps, unlike a hand-edit of
`staging/`.

## Current contents

- `nvidia-oot/drivers/media/i2c/nv_imx708.c`, `imx708_mode_tbls.h`,
  `nvidia-oot/include/media/imx708.h` — IMX708 sensor driver (Raspberry Pi /
  Arducam Camera Module 3), from RidgeRun's open-access driver. Registered in the
  OOT media Makefile by `build.sh`; the matching device-tree overlays live under
  `products/*/device_tree/`.

## Adding a driver

1. Drop the new source files here under their real `source/`-relative paths.
2. If they need a Makefile/Kconfig entry, add an idempotent, fail-loud edit in
   `build.sh` next to the IMX708 one (don't vendor a whole Makefile — it would
   drift from NVIDIA's on the next BSP bump).
