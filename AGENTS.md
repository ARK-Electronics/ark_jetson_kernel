# ark_jetson_kernel

Before editing a file matching a row's paths, read that rule in `.agents/rules/`.

| Rule | Paths |
|---|---|
| `build-scripts.md` | `setup.sh`, `build.sh`, `build_kernel.sh`, `flash.sh`, `provision.sh` |
| `bct-crlf.md` | `products/*/device_tree/bootloader/**` (MB1 BCT dtsi) |
| `dt-overrides.md` | `products/*/device_tree/**/ark-*-overrides.dtsi` |

Skills live in `.agents/skills/`: `cut-jetson-release` tags, validates and publishes a product release.

Claude Code reads neither `AGENTS.md` nor `.agents/` directly: `CLAUDE.md` imports this file, and `.claude/rules` and `.claude/skills/<name>` are symlinks into `.agents/`. Add the symlink when adding a skill, and give each rule `paths:` frontmatter plus a row above.
