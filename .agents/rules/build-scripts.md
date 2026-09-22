---
paths:
  - "**/setup.sh"
  - "**/build.sh"
  - "**/build_kernel.sh"
  - "**/flash.sh"
  - "**/provision.sh"
---

# ark_jetson_kernel build and flash scripts

- **Fail loud, never fallback-rescue.** Fix a failure by surfacing it — `set -e -o pipefail`, a post-condition check, a loud error. Never add a parallel service or wrapper that re-runs the failing operation as a safety net; that hides the upstream bug and adds maintenance surface. If the upstream mechanism is broken, say so instead of working around it.
- **Only deps with a proven failure.** Install the one package a user demonstrably hit, not a vendor's full "required" list. Never add a dependency gate in the hot path that can newly refuse hosts that have been flashing for months.
- **Host deps go before the container re-exec.** `setup.sh` re-execs into the ephemeral 22.04 build container partway through; anything apt-installed after `run_in_container` lands inside the container on a non-22.04 host.
- **Don't run `./build.sh` yourself.** It calls host `sudo -v` before the container handoff, so it hangs non-interactively. Hand the user the exact command to run. Docker itself needs no sudo.
