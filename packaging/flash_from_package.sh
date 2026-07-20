#!/bin/bash

# Flash a Jetson from a prebuilt ARK flash package.
# Downloads the package from GitHub Releases and flashes the QSPI bootloader plus
# the rootfs to NVMe. NVIDIA's initrd flasher reads the connected module's EEPROM
# and selects the matching bootloader/SDRAM config, so one package flashes any
# Orin Nano/NX variant (4GB/8GB/16GB).
#
# A successful flash leaves the generated images behind, and when the next
# module is the same variant (verified by its recovery-mode USB product ID,
# plus an EEPROM probe on Orin Nano 8GB where two SKUs share one ID) they are
# reflashed directly with --flash-only, skipping the ~5 min image build.
#
# Can also flash an unpublished draft release: a specific tag that isn't published is
# looked up as a draft automatically, and --draft selects the latest draft for a
# product. Drafts are invisible to the public API, so that path uses the gh CLI and
# needs an account with read access to the repo.
#
# Usage:
#   ./flash_from_package.sh <tag>             # specific release (e.g. pab-6.2.1.1); auto-detects a draft of that tag
#   ./flash_from_package.sh <product>         # latest published release for a product (pab, jaj, pab-v3, pab-can)
#   ./flash_from_package.sh <product> --draft # latest DRAFT release for a product (needs gh read access)
#   ./flash_from_package.sh <tag> --full      # regenerate images even if cached ones match
#   ./flash_from_package.sh --clean           # remove all cached data

set -euo pipefail

REPO="ARK-Electronics/ark_jetson_kernel"
API_URL="https://api.github.com/repos/$REPO/releases"
CACHE_BASE="$HOME/.ark-jetson-cache"

usage() {
    echo "Usage: $(basename "$0") <tag|product>"
    echo ""
    echo "  $(basename "$0") pab-6.2.1.1    Flash a specific release (published or unpublished draft)"
    echo "  $(basename "$0") pab             Flash the latest PAB release"
    echo "  $(basename "$0") jaj             Flash the latest JAJ release"
    echo "  $(basename "$0") pab-v3          Flash the latest PAB_V3 release"
    echo "  $(basename "$0") pab-can         Flash the latest PAB_CAN release"
    echo "  $(basename "$0") pab-v3 --draft  Flash the latest PAB_V3 DRAFT (needs gh read access)"
    echo "  $(basename "$0") pab --full      Regenerate flash images even if cached ones match"
    echo "  $(basename "$0") --clean         Remove all cached data"
    echo ""
    echo "Note: PAB Rev3 is not the same as PAB_V3. PAB_V3 is a separate product."
    exit 1
}

is_product_name() {
    case "$1" in
        pab|jaj|pab-v3|pab-can) return 0 ;;
        *) return 1 ;;
    esac
}

# --- Handle no args / --help / --clean ---

FORCE_FULL=0
WANT_DRAFT=0
args=()
for arg in "$@"; do
    case "$arg" in
        --full)  FORCE_FULL=1 ;;
        --draft) WANT_DRAFT=1 ;;
        *)       args+=("$arg") ;;
    esac
done
set -- "${args[@]}"

if [ $# -eq 0 ] || [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    usage
fi

if [ "$1" = "--clean" ]; then
    if [ -d "$CACHE_BASE" ]; then
        echo "Removing cached data in $CACHE_BASE ..."
        sudo rm -rf "$CACHE_BASE"
        echo "Done."
    else
        echo "No cached data found."
    fi
    exit 0
fi

# --- Check prerequisites available ---

if ! command -v apt-get &>/dev/null; then
    echo "ERROR: apt-get not found. This script requires a Debian/Ubuntu host."
    exit 1
fi

# --- Install prerequisites ---

# Superset of NVIDIA's tools/l4t_flash_prerequisites.sh (plus curl/lz4): the
# flasher builds the QSPI + rootfs images on this host at flash time, so it needs
# the full partitioning/imaging toolchain (gdisk, parted, xxd, file, ...), not
# the minimal set the old --flash-only replay got away with.
FLASH_PREREQS=(abootimg binfmt-support binutils cpio cpp curl
    device-tree-compiler dosfstools file gdisk
    iproute2 iputils-ping lbzip2 libxml2-utils lz4
    netcat-openbsd nfs-kernel-server openssl
    parted python3-yaml qemu-user-static rsync sshpass
    udev usbutils uuid-runtime whois xmlstarlet xxd zstd zlib1g)

missing=()
for pkg in "${FLASH_PREREQS[@]}"; do
    if ! dpkg -s "$pkg" &>/dev/null; then
        missing+=("$pkg")
    fi
done

if [ ${#missing[@]} -gt 0 ]; then
    echo "Installing ${#missing[@]} missing prerequisites..."
    sudo apt-get update -qq
    sudo apt-get install -y "${missing[@]}"
else
    echo "All prerequisites installed."
fi

# --- Resolve input to a release tag ---

INPUT="$1"
RELEASE_TAG=""
# Whether the resolved release is an unpublished draft (fetched via gh, not the
# public API). Set here for the product --draft case, and later by the automatic
# fallback when a specific tag turns out to be an unpublished draft.
IS_DRAFT=0

if is_product_name "$INPUT"; then
    if [ "$WANT_DRAFT" = 1 ]; then
        # Drafts are invisible to the public API; list them through gh.
        ensure_gh
        echo "Finding latest $INPUT draft release..."
        RELEASE_TAG=$(gh release list -R "$REPO" --limit 100 --json tagName,isDraft \
            -q '.[] | select(.isDraft) | .tagName' \
            | grep "^${INPUT}-[0-9]" \
            | sort -V \
            | tail -1)
        if [ -z "$RELEASE_TAG" ]; then
            echo "ERROR: No draft releases found for product '$INPUT'"
            exit 1
        fi
        IS_DRAFT=1
        echo "Latest draft: $RELEASE_TAG"
    else
        echo "Finding latest $INPUT release..."
        releases_json=$(curl -sfL "${API_URL}?per_page=100")

        RELEASE_TAG=$(echo "$releases_json" \
            | grep -o '"tag_name": *"[^"]*"' \
            | sed 's/"tag_name": *"//;s/"//' \
            | grep "^${INPUT}-[0-9]" \
            | sort -V \
            | tail -1)

        if [ -z "$RELEASE_TAG" ]; then
            echo "ERROR: No releases found for product '$INPUT'"
            echo ""
            echo "Available releases:"
            echo "$releases_json" | grep -o '"tag_name": *"[^"]*"' | sed 's/"tag_name": *"//;s/"//' | head -10
            exit 1
        fi
        echo "Latest: $RELEASE_TAG"
    fi
else
    RELEASE_TAG="$INPUT"
    # A specific tag that's actually a draft is detected at download time (the
    # public tags endpoint 404s on it) and handled by the automatic fallback below;
    # --draft skips that probe and goes straight to gh.
    [ "$WANT_DRAFT" = 1 ] && IS_DRAFT=1
fi

CACHE_DIR="$CACHE_BASE/$RELEASE_TAG"
EXTRACT_DIR="$CACHE_DIR/extracted"

# --- Log output ---

mkdir -p "$CACHE_DIR/logs"
LOG_FILE="$CACHE_DIR/logs/flash-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee "$LOG_FILE") 2>&1

# --- Helpers ---

find_tarball() {
    if [ -f "$CACHE_DIR/package.tar.gz" ]; then
        echo "$CACHE_DIR/package.tar.gz"
    else
        ls "$CACHE_DIR"/*.tar.gz 2>/dev/null | grep -v '\.tmp$' | head -1 || true
    fi
}

find_flash_script() {
    sudo find "$EXTRACT_DIR" -name "l4t_initrd_flash.sh" -path "*/tools/kernel_flash/*" 2>/dev/null | head -1 || true
}

# Ensures the GitHub CLI is installed and authenticated. Draft releases are not
# visible to the public API, so fetching one needs gh with an account that has
# read access to the repo. Installs gh from GitHub's apt repo if missing and runs
# the interactive login if needed. This only runs on the draft path, never for a
# normal published flash.
ensure_gh() {
    if ! command -v gh &>/dev/null; then
        echo "gh CLI not found; installing from the GitHub apt repo..."
        sudo mkdir -p -m 755 /etc/apt/keyrings
        curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
            | sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg >/dev/null
        sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
            | sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null
        # Scope the update to just this new source so an unrelated broken third-party
        # apt repo on the host can't block the gh install; the command -v gate below
        # is what actually decides success.
        sudo apt-get update \
            -o Dir::Etc::sourcelist="sources.list.d/github-cli.list" \
            -o Dir::Etc::sourceparts="-" \
            -o APT::Get::List-Cleanup="0" || true
        sudo apt-get install -y gh || true
        command -v gh &>/dev/null || { echo "ERROR: failed to install the gh CLI." >&2; exit 1; }
    fi
    if ! gh auth status &>/dev/null; then
        echo "gh CLI is not authenticated; launching 'gh auth login'..."
        # Output is mirrored through tee (exec redirect above), so gh's stdout is a
        # pipe, not a TTY, and its prompts won't run; point login at the terminal.
        if [ -e /dev/tty ]; then
            gh auth login </dev/tty >/dev/tty 2>&1 || true
        else
            gh auth login || true
        fi
        gh auth status &>/dev/null || {
            echo "ERROR: draft releases need an authenticated gh CLI ('gh auth login' or set GH_TOKEN)." >&2
            exit 1
        }
    fi
}

# Downloads the release's package assets into $CACHE_DIR via gh — the only way to
# fetch a draft, which the public API can't see. --clobber re-pulls the parts
# cleanly so an interrupted run can't leave a half-written split fragment that
# would corrupt the reassembly; the outer find_tarball guard means this only runs
# when the assembled package isn't already cached.
download_draft() {
    echo ""
    echo "Downloading draft $RELEASE_TAG to $CACHE_DIR via gh (multi-GB image)..."
    if ! gh release download "$RELEASE_TAG" -R "$REPO" --dir "$CACHE_DIR" --clobber; then
        echo "ERROR: failed to download draft assets for '$RELEASE_TAG'." >&2
        echo "  Confirm access with: gh release view $RELEASE_TAG -R $REPO" >&2
        exit 1
    fi
    echo ""
}

# Waits for a Jetson in recovery mode and records its USB product ID in
# JETSON_USB_PID. The BootROM RCM product ID identifies the module variant:
# 7323=Orin NX 16GB, 7423=Orin NX 8GB, 7523=Orin Nano 8GB, 7623=Orin Nano 4GB.
wait_for_jetson() {
    while true; do
        for pid in 7323 7423 7523 7623; do
            if lsusb -d "0955:${pid}" > /dev/null 2>&1; then
                JETSON_USB_PID="$pid"
                return 0
            fi
        done
        sleep 1
    done
}

variant_name() {
    case "$1" in
        7323) echo "Orin NX 16GB" ;;
        7423) echo "Orin NX 8GB" ;;
        7523) echo "Orin Nano 8GB" ;;
        7623) echo "Orin Nano 4GB" ;;
        *)    echo "unknown" ;;
    esac
}

# Bus/device number of the connected Jetson's current USB enumeration
# (e.g. "003-012"). Changes when the chip resets and re-enumerates; empty
# while the chip is off the bus mid-reset. lsusb exits nonzero in that
# window, which must not escape the pipeline: under set -e/pipefail it would
# abort the script exactly in the state this function exists to observe.
jetson_devnum() {
    { lsusb -d "0955:${JETSON_USB_PID}" 2>/dev/null || true; } | head -1 | awk '{print $2 "-" $4}' | tr -d ':'
}

# The EEPROM probe ends with "reboot recovery", but the MB2 applet only acks
# it and can idle in that state indefinitely: it keeps answering RCM queries,
# and the pending reset actually fires when the next download attempt pokes
# it — the applet dies mid-download into BootROM recovery with nothing
# written. A passive pre-flash wait therefore hangs on such modules, so the
# replay flashes straight at the module, treats a fast first failure as that
# poke, and uses this wait to bridge the crash to the re-enumeration before
# retrying. $1 is the devnum the poked attempt talked to — the applet's,
# sampled right after the probe returns (the probe's own BootROM → applet
# transition makes any earlier sample stale).
wait_for_module_reset() {
    local prev="$1" elapsed=0 now
    echo "Waiting for the module to reset back into recovery mode..."
    while true; do
        now=$(jetson_devnum)
        if [ -n "$now" ] && [ "$now" != "$prev" ]; then
            echo "Module re-enumerated after ${elapsed}s."
            # Let the fresh BootROM settle before an RCM session opens on it.
            sleep 5
            return 0
        fi
        if [ "$elapsed" -ge 300 ]; then
            echo "ERROR: module did not come back into recovery mode within ${elapsed}s." >&2
            echo "Power-cycle the Jetson into recovery mode and rerun (or use --full)." >&2
            exit 1
        fi
        sleep 3
        elapsed=$((elapsed + 3))
        if [ $((elapsed % 30)) -eq 0 ]; then
            echo "  still waiting (${elapsed}s)..."
        fi
    done
}

# Identity of the module the flash images were built for. BOARDID/FAB/BOARDSKU/
# BOARDREV are the EEPROM fields that parameterize NVIDIA's image generation
# (tools/kernel_flash/README_initrd_flash.txt, offline mode), and generation also
# branches on the chip SKU and DRAM ramcode (p3767.conf.common selects e.g.
# Micron vs Samsung memory config by ramcode), so those are part of the key too.
# The ark_flash.conf values pin the flash configuration. Reads bootloader/cvm.bin
# and chip_info.bin_bak, the EEPROM/chip dumps that both nvautoflash.sh and a
# full flash run leave behind. Must run from the Linux_for_Tegra directory.
module_key() {
    local id fab sku rev chip ram
    [ -f bootloader/cvm.bin ] && [ -f bootloader/chip_info.bin_bak ] || return 1
    id=$(./bootloader/chkbdinfo -i bootloader/cvm.bin 2>/dev/null | tr -d '[:space:]')
    fab=$(./bootloader/chkbdinfo -f bootloader/cvm.bin 2>/dev/null | tr -d '[:space:]')
    sku=$(./bootloader/chkbdinfo -k bootloader/cvm.bin 2>/dev/null | tr -d '[:space:]')
    rev=$(./bootloader/chkbdinfo -r bootloader/cvm.bin 2>/dev/null | tr -d '[:space:]')
    chip=$(./bootloader/chkbdinfo -C bootloader/chip_info.bin_bak 2>/dev/null | tr -d '[:space:]')
    ram=$(./bootloader/chkbdinfo -R bootloader/chip_info.bin_bak 2>/dev/null | tr -d '[:space:]')
    # chkbdinfo reports errors on stdout, so validate the fields rather than
    # trusting non-empty output.
    if ! [[ "$id" =~ ^[0-9]+$ && "$sku" =~ ^[0-9]+$ && "$chip" =~ ^[0-9A-Fa-f:]+$ && "$ram" =~ ^[0-9A-Fa-f:]+$ ]]; then
        return 1
    fi
    echo "usbpid=$JETSON_USB_PID boardid=$id fab=$fab boardsku=$sku boardrev=$rev chipsku=$chip ramcode=$ram target=$FLASH_TARGET storage=$STORAGE_DEV qspi=$QSPI_CFG external=$EXTERNAL_CFG"
}

# --- Check if already extracted ---

FLASH_SCRIPT=""
if [ -f "$CACHE_DIR/.extract-done" ] && [ -d "$EXTRACT_DIR" ]; then
    FLASH_SCRIPT=$(find_flash_script)
fi

if [ -n "$FLASH_SCRIPT" ]; then
    echo "Using cached flash package."
else
    # --- Download if not cached ---

    TARBALL=$(find_tarball)

    if [ -n "$TARBALL" ]; then
        echo "Using cached download: $(basename "$TARBALL")"
    else
        mkdir -p "$CACHE_DIR"

        if [ "$IS_DRAFT" = 1 ]; then
            download_draft
        else
            echo "Fetching release $RELEASE_TAG..."
            release_json=$(curl -sL "$API_URL/tags/$RELEASE_TAG")

            if echo "$release_json" | grep -q '"message"'; then
                msg=$(echo "$release_json" | grep -o '"message": *"[^"]*"' | head -1)
                # A draft release has no git tag, so the public tags endpoint 404s
                # on it. Before giving up, fall back to gh: the tag may just be an
                # unpublished draft awaiting validation.
                if echo "$msg" | grep -qi 'not found'; then
                    echo "Release '$RELEASE_TAG' is not published; checking for a draft..."
                    ensure_gh
                    if ! gh release view "$RELEASE_TAG" -R "$REPO" &>/dev/null; then
                        echo "ERROR: '$RELEASE_TAG' is neither a published release nor a draft visible to your gh account." >&2
                        echo "  It may not exist, or your account may lack read access to $REPO (check 'gh auth status')." >&2
                        echo "" >&2
                        echo "Available published releases:" >&2
                        curl -sL "$API_URL" | grep -o '"tag_name": *"[^"]*"' | sed 's/"tag_name": *"//;s/"//' | head -10 >&2
                        exit 1
                    fi
                    IS_DRAFT=1
                    download_draft
                else
                    echo "ERROR: Release '$RELEASE_TAG' not found ($msg)"
                    echo ""
                    echo "Available releases:"
                    curl -sL "$API_URL" | grep -o '"tag_name": *"[^"]*"' | sed 's/"tag_name": *"//;s/"//' | head -10
                    exit 1
                fi
            else
                assets=$(echo "$release_json" | grep -o '"browser_download_url": *"[^"]*"' | sed 's/"browser_download_url": *"//;s/"//' | grep -v '/archive/')

                if [ -z "$assets" ]; then
                    echo "ERROR: No downloadable assets found in release $RELEASE_TAG"
                    exit 1
                fi

                rm -f "$CACHE_DIR"/*.tmp

                echo ""
                echo "Downloading to $CACHE_DIR ..."

                while IFS= read -r url; do
                    filename=$(basename "$url")
                    [ "$filename" = "flash_from_package.sh" ] && continue

                    if [ -f "$CACHE_DIR/$filename" ]; then
                        echo "  Already downloaded: $filename"
                        continue
                    fi

                    echo "  Downloading $filename..."
                    curl -fL --progress-bar -o "$CACHE_DIR/$filename.tmp" "$url"
                    mv "$CACHE_DIR/$filename.tmp" "$CACHE_DIR/$filename"
                done <<< "$assets"
                echo ""
            fi
        fi

        # Reassemble split parts if needed. Both the curl and gh paths leave the
        # package as either a single *.tar.gz or a set of *.part.* fragments.
        if ls "$CACHE_DIR"/*.part.* &>/dev/null; then
            echo "Reassembling split parts..."
            cat "$CACHE_DIR"/*.part.* > "$CACHE_DIR/package.tar.gz.tmp"
            mv "$CACHE_DIR/package.tar.gz.tmp" "$CACHE_DIR/package.tar.gz"
            rm -f "$CACHE_DIR"/*.part.*
        fi

        TARBALL=$(find_tarball)
        if [ -z "$TARBALL" ]; then
            echo "ERROR: No .tar.gz found in $CACHE_DIR after download"
            exit 1
        fi
    fi

    # --- Extract ---

    if [ -d "$EXTRACT_DIR" ] && [ ! -f "$CACHE_DIR/.extract-done" ]; then
        echo "Cleaning up incomplete extraction..."
        sudo rm -rf "$EXTRACT_DIR"
    fi

    mkdir -p "$EXTRACT_DIR"
    echo "Extracting $(basename "$TARBALL") ..."
    # --numeric-owner + --xattrs restore rootfs ownership and file capabilities
    # so the flashed OS matches what was staged at build time.
    sudo tar --numeric-owner --xattrs --xattrs-include='*' -xpf "$TARBALL" -C "$EXTRACT_DIR"
    touch "$CACHE_DIR/.extract-done"

    FLASH_SCRIPT=$(find_flash_script)
    if [ -z "$FLASH_SCRIPT" ]; then
        rm -f "$CACHE_DIR/.extract-done"
        echo "ERROR: l4t_initrd_flash.sh not found in extracted package."
        exit 1
    fi
fi

FLASH_DIR=$(dirname "$FLASH_SCRIPT")          # .../tools/kernel_flash
L4T_DIR=$(cd "$FLASH_DIR/../.." && pwd)        # .../Linux_for_Tegra

# --- Patch the gadget-rename race in NVIDIA's initrd flasher ---

# Between l4t_initrd_flash_internal.sh's ping_device() listing /sys/class/net
# and configuring the flashing gadget, udev can rename the interface from its
# kernel name (usb0) to the persistent enx<mac> name. The stock script's
# one-shot IP_SET latch is then consumed by the vanished name: the
# fc00:1:1:<n>::1/fe80::2 host addresses are never added to the renamed
# interface and the flash dies at "Waiting for device to expose ssh" (seen on
# desktop hosts; the bench laptops happen to list the interface after the
# rename and never hit the window). Rewrite the latch into an idempotent
# per-name check so a later 1 s retry configures the interface under its final
# name. Runs every flash so caches extracted before this fix get patched too;
# a no-op once applied, and if NVIDIA restructures the script the pattern
# simply won't match, leaving stock behavior.
INTERNAL_FLASH_SCRIPT="$FLASH_DIR/l4t_initrd_flash_internal.sh"
if sudo grep -qF 'if [ -z "${IP_SET}" ]; then' "$INTERNAL_FLASH_SCRIPT" 2>/dev/null; then
    echo "Patching interface-rename race in l4t_initrd_flash_internal.sh..."
    sudo sed -i \
        -e 's|if \[ -z "\${IP_SET}" \]; then|if ! ip -6 addr show dev "${REPLY}" 2>/dev/null \| grep -q "fc00:1:1:${device_instance}::1"; then|' \
        -e 's|"\$(sysctl -n "net.ipv6.conf.\${REPLY}.disable_ipv6")" -eq 1|"$(sysctl -n "net.ipv6.conf.${REPLY}.disable_ipv6" 2>/dev/null)" = "1"|' \
        -e '/^[[:space:]]*IP_SET=0$/d' \
        "$INTERNAL_FLASH_SCRIPT"
fi

# Flash parameters (board config + storage + default DTB overlays) travel with
# the package in ark_flash.conf. Defaults match flash.sh for older packages that
# predate it.
FLASH_TARGET="jetson-orin-nano-devkit-super"
STORAGE_DEV="nvme0n1p1"
QSPI_CFG="bootloader/generic/cfg/flash_t234_qspi.xml"
EXTERNAL_CFG="tools/kernel_flash/flash_l4t_t234_nvme.xml"
ADDITIONAL_DTB_OVERLAY=""
if [ -f "$L4T_DIR/ark_flash.conf" ]; then
    # shellcheck disable=SC1091
    source "$L4T_DIR/ark_flash.conf"
fi

# --- Display package build info ---

BUILD_INFO=$(sudo find "$EXTRACT_DIR" -maxdepth 3 -name "BUILD_INFO.txt" 2>/dev/null | head -1 || true)
if [ -n "$BUILD_INFO" ]; then
    echo ""
    echo "========================================="
    echo "  Package build info"
    echo "========================================="
    sudo cat "$BUILD_INFO"
    echo "========================================="
fi

# --- Wait for Jetson and flash ---

cd "$L4T_DIR"

echo ""
echo "========================================="
echo "  Ready to flash"
echo "========================================="
echo ""
echo "Waiting for Jetson in recovery mode..."
echo "  Connect USB and hold Force Recovery button while powering on."
echo ""

wait_for_jetson

echo "Jetson detected: $(variant_name "$JETSON_USB_PID") (USB ID 0955:$JETSON_USB_PID)"
echo ""

# A successful flash leaves everything needed to flash again without rebuilding:
# the flash images (tools/kernel_flash/images), the signed boot binaries
# (bootloader/), and the saved flash arguments (initrdflashparam.txt), which
# l4t_initrd_flash.sh --flash-only replays verbatim. Those images are SKU-locked
# — the rcmboot blob embeds the generating module's SDRAM config, and replaying
# it on a different variant hangs in RCM boot — so replay requires proof the
# module matches the images. The recovery-mode USB product ID identifies the
# module variant, and within a product ID the generated images are identical
# (NX 16GB's two SKUs, 0000/0002, select the same files), with one exception:
# Orin Nano 8GB (7523) covers SKU 0003 and 0005, which need different kernel
# DTBs, so only that variant needs an EEPROM probe — and probing reboots the
# module through the MB2 applet, which is exactly why the other variants avoid
# it and flash straight from the operator-entered recovery state. The marker is
# written only after a fully successful run, so anything interrupted falls back
# to full regeneration.
GEN_MARKER="$L4T_DIR/.ark-flash-gen"
REPLAY=0
if [ "$FORCE_FULL" = "1" ]; then
    echo "--full: regenerating flash images."
elif [ -f "$GEN_MARKER" ] && [ -f tools/kernel_flash/initrdflashparam.txt ] && [ -d tools/kernel_flash/images ]; then
    CACHED_KEY=$(cat "$GEN_MARKER")
    CACHED_PID=$(echo "$CACHED_KEY" | grep -o 'usbpid=[0-9a-f]*' | cut -d= -f2 || true)
    if [ -z "$CACHED_PID" ]; then
        echo "Existing images predate module-variant tracking. Regenerating."
    elif [ "$JETSON_USB_PID" != "$CACHED_PID" ]; then
        echo "Connected module ($(variant_name "$JETSON_USB_PID")) differs from the existing images ($(variant_name "$CACHED_PID")). Regenerating."
    elif [ "$JETSON_USB_PID" != "7523" ]; then
        REPLAY=1
        echo "Module variant matches the existing images. Flashing without regenerating (~5 min faster)."
    else
        echo "Found Orin Nano 8GB images. Probing module EEPROM to confirm the SKU..."
        sudo rm -f bootloader/cvm.bin bootloader/chip_info.bin_bak
        if ! sudo ./nvautoflash.sh --print_boardid; then
            echo "ERROR: EEPROM probe failed. Power-cycle the Jetson into recovery mode and retry." >&2
            exit 1
        fi
        # The applet's enumeration — the reset-wait baseline for the replay's
        # retry path (see wait_for_module_reset).
        POST_PROBE_DEVNUM=$(jetson_devnum)
        MODULE_KEY=$(module_key) || { echo "ERROR: cannot parse module EEPROM dump." >&2; exit 1; }
        if [ "$MODULE_KEY" = "$CACHED_KEY" ]; then
            echo "Module matches the existing images. Flashing without regenerating (~5 min faster)."
            REPLAY=1
        else
            # No reset wait needed here: the full flash starts with NVIDIA's
            # applet-aware detection, which handles the post-probe state itself.
            echo "Module differs from the existing images. Regenerating."
            echo "  images: $CACHED_KEY"
            echo "  module: $MODULE_KEY"
        fi
    fi
fi

if [ "$REPLAY" = "1" ]; then
    # Same invocation as the full flash plus --flash-only: the flasher replays
    # the recorded images instead of regenerating them.
    replay_flash() {
        sudo ./tools/kernel_flash/l4t_initrd_flash.sh \
            --flash-only \
            --external-device "$STORAGE_DEV" \
            -p "-c ./$QSPI_CFG" \
            -c "./$EXTERNAL_CFG" \
            --showlogs --network usb0 \
            "$FLASH_TARGET" "$STORAGE_DEV"
    }
    replay_failed() {
        echo "" >&2
        echo "ERROR: flashing from existing images failed." >&2
        echo "Power-cycle the Jetson into recovery mode and rerun (add --full to regenerate the images from scratch)." >&2
        exit 1
    }
    # The EEPROM probe regenerates several boot binaries in bootloader/ with
    # its readinfo/diag-boot flavor, silently replacing the generation-era
    # set that the saved flash command replays by name (the BR/MB1 BCTs
    # differ materially). Downloading that mixed set makes the chip reject
    # the boot chain and reset mid-download — tegrarcm's "might be timeout
    # in USB write". The signing stage keeps canonical generation-era copies
    # in bootloader/signed/, which the probe never touches: restore any that
    # shadow a top-level file before replaying.
    if [ -d bootloader/signed ]; then
        echo "Restoring generation-era boot binaries over the probe's..."
        for _signed in bootloader/signed/*; do
            _shadowed="bootloader/$(basename "${_signed}")"
            if [ -f "${_shadowed}" ]; then
                sudo cp -f "${_signed}" "${_shadowed}"
            fi
        done
    fi
    replay_started=$(date +%s)
    if ! replay_flash; then
        # A fast failure after an EEPROM probe is the applet taking its
        # deferred reset mid-download (see wait_for_module_reset): nothing was
        # written and the module lands in BootROM recovery, so wait for it and
        # flash again. Anything else — no probe ran (nothing can be holding a
        # reset), or the flash died past the download phase — is a real
        # failure.
        replay_elapsed=$(( $(date +%s) - replay_started ))
        if [ -z "${POST_PROBE_DEVNUM+x}" ] || [ "$replay_elapsed" -ge 60 ]; then
            replay_failed
        fi
        echo "That failure was the module taking the EEPROM probe's deferred reset; retrying."
        wait_for_module_reset "$POST_PROBE_DEVNUM"
        replay_flash || replay_failed
    fi
else
    # Drop the marker (and any stale EEPROM dump) before generating, so an
    # interrupted run is never mistaken for valid replay state and the marker
    # below provably reflects this run's EEPROM read.
    sudo rm -f "$GEN_MARKER" bootloader/cvm.bin bootloader/chip_info.bin_bak
    if [ -n "$ADDITIONAL_DTB_OVERLAY" ]; then
        echo "Baking default device-tree overlay(s) into the image: $ADDITIONAL_DTB_OVERLAY"
    fi
    # The initrd flasher reads the connected module's EEPROM and picks the matching
    # bootloader + SDRAM config, so one package flashes any Orin Nano/NX variant. It
    # writes the QSPI bootloader (-p) and the rootfs to the external device (-c) in
    # a single pass. ADDITIONAL_DTB_OVERLAY_OPT merges the product's default
    # overlay(s) into the DTB during image generation; the --flash-only replay
    # above needs no equivalent because its images already contain the merged DTB.
    # dtbo basenames carry no spaces, so the unquoted ${var:+NAME=$var} prefix
    # passes cleanly as a single sudo environment assignment.
    sudo ${ADDITIONAL_DTB_OVERLAY:+ADDITIONAL_DTB_OVERLAY_OPT=$ADDITIONAL_DTB_OVERLAY} \
        ./tools/kernel_flash/l4t_initrd_flash.sh \
        --external-device "$STORAGE_DEV" \
        -p "-c ./$QSPI_CFG" \
        -c "./$EXTERNAL_CFG" \
        --showlogs --network usb0 \
        "$FLASH_TARGET" "$STORAGE_DEV"

    # Key the cache off the EEPROM dump the generation step itself read, not the
    # probe, so the marker always describes what the images were built for.
    if MODULE_KEY=$(module_key); then
        echo "$MODULE_KEY" | sudo tee "$GEN_MARKER" > /dev/null
        echo "Recorded module info: same-variant reflashes will skip image generation."
    else
        echo "WARNING: could not read the module EEPROM dump; the next flash will regenerate images."
    fi
fi

echo ""
echo "========================================="
echo "  Flash complete!"
echo "========================================="
echo ""
echo "The Jetson will reboot automatically."
echo "Once booted, connect via: ssh jetson@jetson.local"
echo ""
echo "Cached data: $CACHE_DIR"
echo "To free disk space: $0 --clean"
