---
paths:
  - "**/ark-*-overrides.dtsi"
---

# ARK device-tree override fragments

This file is ARK's entire device-tree delta — `build.sh` appends an `#include` of it to the stock nv-common, so everything not named here comes from the BSP and changes when the BSP does.

- **The deletions are load-bearing.** `/delete-property/ dmas` + `dma-names` on `uarta` forces PIO (stock DMA corrupts telem2/DDS RX at 3 Mbaud); `/delete-property/ hdcp_enabled` on `display@13800000` restores DP→HDMI adapter negotiation on JP6.2.2. Don't drop either while regenerating or tidying.
- `/delete-property/` is required to remove an inherited property — redeclaring the node does not drop it.
- These are LF files, unlike the CRLF BCT dtsi in the same product directory.
- Verify a change by compiling, not by reading: `scripts/device_tree/compile_dtb.sh`, then decompile the built DTB and dump the **full** node — grepping a few properties has already hidden a regression once.
