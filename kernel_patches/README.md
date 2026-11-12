# kernel_patches

Patches to files that NVIDIA's kernel tree already has. `build.sh` applies each `*.patch` here, in name order, to `Linux_for_Tegra/source/kernel/<kernel dir>/` before it compiles. A patch that no longer applies stops the build, so a BSP bump cannot drop one without anyone noticing.

New source files, such as a sensor driver, go in `kernel_overlay/` instead.

## Current contents

| Patch | What it fixes |
| --- | --- |
| `0001-efi-keep-runtime-services-and-optee-calls-apart.patch` | A boot that hangs when a UEFI variable is read or written while a Linux OP-TEE call, such as the fTPM loading, is waiting on a lock in OP-TEE. The patch header has the mechanism. |
| `0002-nvme-pci-look-again-after-a-flush.patch` | NVMe commands that wait out the 30 s timeout with `completion polled`, because their interrupt arrived before the completion was visible. |

## Adding a patch

1. Make the change in a copy of the kernel tree and write it out with `git diff`, with paths relative to the kernel's top directory.
2. Put what breaks and why at the top of the file, above the diff.
3. Name it with the next number, so the order is fixed.
4. Run `./build.sh <TARGET> --fast` twice. The first run applies it and the second must report it as already applied.
