# kernel_patches

Patches to files that NVIDIA's kernel tree already has. `build.sh` applies each `*.patch` here, in name order, to `Linux_for_Tegra/source/kernel/kernel-jammy-src/` before it compiles. A patch that no longer applies stops the build, so a BSP bump cannot drop one without anyone noticing.

New source files, such as a sensor driver, go in `kernel_overlay/` instead.

## Current contents

| Patch | What it fixes |
| --- | --- |
| `0001-efi-keep-runtime-services-and-optee-calls-apart.patch` | A board that hangs when a UEFI variable is read or written while a Linux OP-TEE call, such as an fTPM command, is in OP-TEE. The patch header has what was seen. |

## Adding a patch

1. Make the change in a copy of the kernel tree and write it out with `git diff`, with paths relative to the kernel's top directory.
2. Put what breaks and why at the top of the file, above the diff.
3. Name it with the next number, so the order is fixed.
4. Run `./build.sh <TARGET> --fast` twice. The first run applies it and the second must report it as already applied.
