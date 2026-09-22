---
paths:
  - "**/device_tree/bootloader/**"
  - "**/tegra234-mb1-bct-*.dtsi"
---

# MB1 BCT dtsi line endings

These files are CRLF — NVIDIA's Pinmux spreadsheet generates them (JAJ also indents with tabs plus a trailing space). Edit with a tool that preserves the line endings.

A `sed`/`perl` one-liner emitting a bare `\n` produces a mixed-ending file and whole-file diff churn. If an edit must be scripted, match `\r?\n` and emit `\r\n`, then confirm with `file` — the answer should say CRLF with no LF alongside it.
