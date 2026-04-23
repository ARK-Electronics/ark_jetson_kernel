#!/bin/bash

# Flash a Jetson from a prebuilt ARK flash package.
# Downloads the package from GitHub Releases, reassembles if split, and flashes.
# No build tools or kernel source needed — just a Linux host with USB.
#
# Releases that contain multiple carrier targets (PAB, PAB_V3, JAJ) prompt
# interactively to pick which one to flash. Each (version, target) is cached
# independently in ~/.ark-jetson-cache/<version>/<target>/.
#
# Usage: ./flash_from_package.sh [version]
#        ./flash_from_package.sh              # uses latest release
#        ./flash_from_package.sh 0.0.6        # specific version
#        ./flash_from_package.sh package.tar.gz  # local file
#        ./flash_from_package.sh --clean       # remove all cached data

set -euo pipefail

REPO="ARK-Electronics/ark_jetson_kernel"
API_URL="https://api.github.com/repos/$REPO/releases"
CACHE_BASE="$HOME/.ark-jetson-cache"

# --- Handle --help / --clean ---

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    sed -n '2,/^$/{ s/^# \?//; p }' "$0"
    exit 0
fi

if [ "${1:-}" = "--clean" ]; then
    if [ -d "$CACHE_BASE" ]; then
        echo "Removing cached data in $CACHE_BASE ..."
        sudo rm -rf "$CACHE_BASE"
        echo "Done."
    else
        echo "No cached data found."
    fi
    exit 0
fi

# --- Check Ubuntu version ---

if [ -f /etc/lsb-release ]; then
    DISTRO_VER=$(grep "DISTRIB_RELEASE" /etc/lsb-release | cut -d= -f2)
    DISTRO_VER_NUM=$(echo "$DISTRO_VER" | sed 's/\.//')
    if [ "$DISTRO_VER_NUM" -lt 2204 ] 2>/dev/null; then
        echo "ERROR: Ubuntu 22.04 or newer is required (found $DISTRO_VER)."
        exit 1
    fi
else
    echo "WARNING: Could not detect Ubuntu version. Ubuntu 22.04+ is required."
fi

# --- Install prerequisites (skip if already installed) ---
# These packages are required by l4t_initrd_flash.sh (from NVIDIA's l4t_flash_prerequisites.sh)

FLASH_PREREQS=(abootimg binfmt-support binutils cpio cpp curl
    device-tree-compiler dosfstools
    iproute2 iputils-ping lbzip2 libxml2-utils lz4
    netcat-openbsd nfs-kernel-server openssl
    python3-yaml qemu-user-static rsync sshpass
    udev usbutils uuid-runtime whois xmlstarlet zstd)

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

# --- Determine input and cache directory ---

INPUT="${1:-}"
TARBALL=""
CACHE_DIR=""
NEED_DOWNLOAD=false

if [ -n "$INPUT" ] && [ -f "$INPUT" ]; then
    # Local tarball
    TARBALL="$(realpath "$INPUT")"
    CACHE_DIR="$CACHE_BASE/local-$(basename "$INPUT" .tar.gz)"
    echo "Using local tarball: $TARBALL"

elif [ -n "$INPUT" ] && [ -d "$INPUT" ]; then
    # Local directory with split parts
    local_dir="$(realpath "$INPUT")"
    CACHE_DIR="$CACHE_BASE/local-$(basename "$INPUT")"
    TARBALL="$CACHE_DIR/package.tar.gz"

    if [ ! -f "$TARBALL" ]; then
        if ls "$local_dir"/*.part.* &>/dev/null; then
            mkdir -p "$CACHE_DIR"
            echo "Reassembling split parts from $local_dir..."
            cat "$local_dir"/*.part.* > "$TARBALL.tmp"
            mv "$TARBALL.tmp" "$TARBALL"
            echo "Reassembled: $TARBALL"
        else
            echo "ERROR: No split parts found in $local_dir"
            exit 1
        fi
    else
        echo "Using cached reassembled tarball."
    fi

else
    # GitHub release
    NEED_DOWNLOAD=true

    if [ -z "$INPUT" ]; then
        echo "Fetching latest release..."
        release_url="$API_URL/latest"
    else
        echo "Fetching release $INPUT..."
        release_url="$API_URL/tags/$INPUT"
    fi

    release_json=$(curl -sL "$release_url")

    if echo "$release_json" | grep -q '"message"'; then
        msg=$(echo "$release_json" | grep -o '"message": *"[^"]*"' | head -1)
        echo "ERROR: Release not found ($msg)"
        echo ""
        echo "Available releases:"
        curl -sL "$API_URL" | grep -o '"tag_name": *"[^"]*"' | sed 's/"tag_name": *"//;s/"//' | head -10
        exit 1
    fi

    RELEASE_TAG=$(echo "$release_json" | grep -o '"tag_name": *"[^"]*"' | sed 's/"tag_name": *"//' | sed 's/"//')
    if [ -z "$RELEASE_TAG" ]; then
        echo "ERROR: Could not parse release tag from GitHub API response"
        exit 1
    fi
    echo "Release: $RELEASE_TAG"

    # --- Detect carrier targets in the release ---
    # Asset filenames are ark-<target>-<storage>[-super].{tar.gz,part.*}. The
    # target slug follows the same convention as build_kernel.sh's TARGET,
    # lowercased with underscores replaced by dashes.
    KNOWN_TARGETS=("pab" "jaj" "pab-v3")
    TARGETS_IN_RELEASE=()
    for t in "${KNOWN_TARGETS[@]}"; do
        if echo "$release_json" | grep -qE "\"name\": *\"ark-$t-(nvme|sdcard)"; then
            TARGETS_IN_RELEASE+=("$t")
        fi
    done

    if [ ${#TARGETS_IN_RELEASE[@]} -eq 0 ]; then
        echo "ERROR: No ark-<target>-* assets found in release $RELEASE_TAG."
        echo "Expected at least one of: ${KNOWN_TARGETS[*]}"
        exit 1
    fi

    # --- Select target ---
    if [ ${#TARGETS_IN_RELEASE[@]} -eq 1 ]; then
        TARGET="${TARGETS_IN_RELEASE[0]}"
        echo "Target: $TARGET (only target in release)"
    else
        echo ""
        echo "Please select the target platform:"
        echo -e "\033[3mNote: PAB Rev3 is not the same as PAB_V3. PAB_V3 is a separate product.\033[0m"
        i=1
        for t in "${TARGETS_IN_RELEASE[@]}"; do
            case "$t" in
                pab)    label="PAB" ;;
                pab-v3) label="PAB_V3" ;;
                jaj)    label="JAJ" ;;
                *)      label="$t" ;;
            esac
            echo "$i) $label"
            i=$((i+1))
        done
        read -rp "Enter your choice (1-${#TARGETS_IN_RELEASE[@]}): " choice
        if ! [[ "$choice" =~ ^[0-9]+$ ]] || \
           [ "$choice" -lt 1 ] || [ "$choice" -gt "${#TARGETS_IN_RELEASE[@]}" ]; then
            echo "Invalid choice. Exiting."
            exit 1
        fi
        TARGET="${TARGETS_IN_RELEASE[$((choice-1))]}"
        echo "Target: $TARGET"
    fi

    CACHE_DIR="$CACHE_BASE/$RELEASE_TAG/$TARGET"
fi

EXTRACT_DIR="$CACHE_DIR/extracted"

# --- Log output to file while keeping terminal output ---

mkdir -p "$CACHE_DIR/logs"
LOG_FILE="$CACHE_DIR/logs/flash-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee "$LOG_FILE") 2>&1

# --- Helper: find the tarball in cache dir ---

find_tarball() {
    # Check for reassembled tarball first, then any .tar.gz (excluding .tmp)
    if [ -f "$CACHE_DIR/package.tar.gz" ]; then
        echo "$CACHE_DIR/package.tar.gz"
    else
        ls "$CACHE_DIR"/*.tar.gz 2>/dev/null | grep -v '\.tmp$' | head -1 || true
    fi
}

# --- Helper: find the flash script in extracted dir ---

find_flash_script() {
    sudo find "$EXTRACT_DIR" -name "l4t_initrd_flash.sh" -path "*/tools/kernel_flash/*" 2>/dev/null | head -1 || true
}

# --- Check if already fully extracted (skip everything) ---

FLASH_SCRIPT=""
if [ -f "$CACHE_DIR/.extract-done" ] && [ -d "$EXTRACT_DIR" ]; then
    FLASH_SCRIPT=$(find_flash_script)
fi

if [ -n "$FLASH_SCRIPT" ]; then
    echo "Using cached flash package."
else
    # --- Step 1: Obtain tarball ---

    if [ "$NEED_DOWNLOAD" = true ]; then
        TARBALL=$(find_tarball)

        if [ -n "$TARBALL" ]; then
            echo "Using cached tarball: $(basename "$TARBALL")"
        else
            # Parse download URLs from release JSON
            assets=$(echo "$release_json" | grep -o '"browser_download_url": *"[^"]*"' | sed 's/"browser_download_url": *"//;s/"//' | grep -v '/archive/')

            if [ -z "$assets" ]; then
                echo "ERROR: No downloadable assets found in release $RELEASE_TAG"
                exit 1
            fi

            mkdir -p "$CACHE_DIR"

            # Clean up partial downloads from any previous failed run
            rm -f "$CACHE_DIR"/*.tmp

            echo ""
            echo "Downloading to $CACHE_DIR ..."

            while IFS= read -r url; do
                filename=$(basename "$url")

                # Skip the script itself if attached to the release
                if [ "$filename" = "flash_from_package.sh" ]; then
                    continue
                fi

                # Only download assets matching the selected target. The
                # storage token (nvme|sdcard) follows the target slug so
                # pab doesn't spuriously match pab-v3-*.
                if ! [[ "$filename" =~ ^ark-${TARGET}-(nvme|sdcard) ]]; then
                    continue
                fi

                if [ -f "$CACHE_DIR/$filename" ]; then
                    echo "  Already downloaded: $filename"
                    continue
                fi

                echo "  Downloading $filename..."
                curl -L --progress-bar -o "$CACHE_DIR/$filename.tmp" "$url"
                mv "$CACHE_DIR/$filename.tmp" "$CACHE_DIR/$filename"
            done <<< "$assets"
            echo ""

            # --- Step 2: Reassemble split parts if needed ---

            if ls "$CACHE_DIR"/*.part.* &>/dev/null; then
                echo "Reassembling split parts..."
                cat "$CACHE_DIR"/*.part.* > "$CACHE_DIR/package.tar.gz.tmp"
                mv "$CACHE_DIR/package.tar.gz.tmp" "$CACHE_DIR/package.tar.gz"
                echo "Removing split parts..."
                rm -f "$CACHE_DIR"/*.part.*
            fi

            TARBALL=$(find_tarball)
            if [ -z "$TARBALL" ]; then
                echo "ERROR: No .tar.gz found in $CACHE_DIR after download"
                exit 1
            fi
        fi
    fi

    # --- Step 3: Extract ---

    # Clean up any incomplete extraction from a previous failed run
    if [ -d "$EXTRACT_DIR" ] && [ ! -f "$CACHE_DIR/.extract-done" ]; then
        echo "Cleaning up incomplete extraction..."
        sudo rm -rf "$EXTRACT_DIR"
    fi

    mkdir -p "$EXTRACT_DIR"
    echo "Extracting $(basename "$TARBALL") ..."
    sudo tar xf "$TARBALL" -C "$EXTRACT_DIR"
    touch "$CACHE_DIR/.extract-done"

    FLASH_SCRIPT=$(find_flash_script)
    if [ -z "$FLASH_SCRIPT" ]; then
        rm -f "$CACHE_DIR/.extract-done"
        echo "ERROR: l4t_initrd_flash.sh not found in extracted package."
        echo "This doesn't appear to be a valid massflash package."
        exit 1
    fi
fi

FLASH_DIR=$(dirname "$FLASH_SCRIPT")

# --- Display package build info if present ---
# generate_flash_package.sh embeds BUILD_INFO.txt at the top of the tarball;
# printing it here records the source commit in the per-run flash log.

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

echo ""
echo "========================================="
echo "  Ready to flash"
echo "========================================="
echo ""
echo "Waiting for Jetson in recovery mode..."
echo "  Connect USB and hold Force Recovery button while powering on."
echo "  Looking for NVIDIA recovery device..."
echo ""

while true; do
    for pid in 7323 7423 7523 7623; do
        if lsusb -d 0955:${pid} > /dev/null 2>&1; then
            break 2
        fi
    done
    sleep 1
done

echo "Jetson detected!"
echo ""
echo "Starting flash..."
echo ""

cd "$FLASH_DIR"
sudo ./l4t_initrd_flash.sh --flash-only --massflash 1 --network usb0

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
