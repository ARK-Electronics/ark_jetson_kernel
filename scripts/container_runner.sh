#!/bin/bash
# Host-detection + container handoff, sourced by setup.sh and build.sh. NVIDIA's
# documented build host is Ubuntu 22.04; on anything else we re-exec inside a 22.04
# container with release-matched initrd tools. See docs/build_host.md.

ARK_BUILDER_IMAGE="ark-jetson-builder:22.04"

# Returns 0 if the calling script should re-exec inside the build container.
# False when already inside (IN_BUILD_CONTAINER=1) or when the host is 22.04.
needs_container() {
    [ -z "$IN_BUILD_CONTAINER" ] || return 1
    local host_id
    host_id=$(. /etc/os-release && echo "$VERSION_ID")
    [ "$host_id" = "22.04" ] || return 0
    # R39's target uses kmod 31. A native Jammy host normally has kmod 29;
    # use the container's pinned 31 tools unless the native tools match.
    if [ "${EXPECTED_BSP_RELEASE:-}" = R39 ] && \
       [ ! -x /opt/ark-kmod31/bin/depmod ] && \
       ! depmod --version 2>/dev/null | grep -qx 'kmod version 31'; then
        return 0
    fi
    return 1
}

# Install docker if missing, then set DOCKER_CMD to (docker) or (sudo docker) by
# probing daemon access. The sudo fallback avoids forcing a docker-group re-login.
ensure_docker() {
    if ! command -v docker >/dev/null 2>&1; then
        if ! command -v apt-get >/dev/null 2>&1; then
            echo "ERROR: docker is not installed and apt-get is not available." >&2
            echo "       Install docker manually, or run on an Ubuntu 22.04 host." >&2
            echo "       See docs/build_host.md." >&2
            exit 1
        fi
        echo "Docker not installed — installing via apt (requires sudo)..."
        sudo apt-get update
        sudo apt-get install -y docker.io
    fi

    if docker info >/dev/null 2>&1; then
        DOCKER_CMD=(docker)
        return
    fi

    # Probe failed: daemon stopped or user not in docker group. Try starting it, then
    # re-probe and fall back to sudo docker if only group membership remains.
    if command -v systemctl >/dev/null 2>&1; then
        sudo systemctl is-active docker >/dev/null 2>&1 || sudo systemctl start docker
    fi

    if docker info >/dev/null 2>&1; then
        DOCKER_CMD=(docker)
    else
        DOCKER_CMD=(sudo docker)
    fi
}

# The CI workspace keeps staging on a separate disk. Bind that source only at
# a fixed container path; never use an arbitrary symlink target as a mount point.
prepare_container_staging_mounts() {
    local repo_dir="$1" staging_source staging_link staging_resolved
    STAGING_CONTAINER_MOUNTS=()
    if [ -n "${ARK_STAGING_MOUNT:-}" ]; then
        staging_link=$(readlink -- "$repo_dir/staging") || {
            echo "ERROR: ARK_STAGING_MOUNT requires staging -> /mnt/staging." >&2
            return 1
        }
        if [ "$staging_link" != /mnt/staging ]; then
            echo "ERROR: ARK_STAGING_MOUNT only supports staging -> /mnt/staging." >&2
            return 1
        fi
        staging_source=$(realpath -e -- "$ARK_STAGING_MOUNT") || return 1
        # Require a dedicated staging directory, not a filesystem or home root.
        if [ ! -d "$staging_source" ] || [ "${staging_source##*/}" != staging ]; then
            echo "ERROR: ARK_STAGING_MOUNT must resolve to an existing directory named staging." >&2
            return 1
        fi
        case "$staging_source" in
            *:*) echo "ERROR: staging mount path cannot contain a colon." >&2; return 1 ;;
        esac
        STAGING_CONTAINER_MOUNTS=(-v "$staging_source:/mnt/staging")
    elif [ -L "$repo_dir/staging" ]; then
        staging_link=$(readlink -- "$repo_dir/staging")
        staging_resolved=$(realpath -m -- "$repo_dir/staging") || return 1
        # A relative link staying inside the repo survives its /workspace mount.
        if [[ "$staging_link" = /* ]] || [[ "$staging_resolved" != "$repo_dir/"* ]]; then
            echo "ERROR: external staging symlink is not mounted in the build container." >&2
            echo "       CI uses staging -> /mnt/staging with ARK_STAGING_MOUNT=/mnt/staging." >&2
            return 1
        fi
    fi
}

# Re-exec the calling script in the 22.04 build container (does not return on
# success; builds the image on first use).
#   $1     path to the calling script ($0)
#   $2..   args passed through to the in-container run
run_in_container() {
    local script_path="$1"; shift
    local repo_dir="$ARK_JETSON_KERNEL_DIR"

    if [ -z "$repo_dir" ]; then
        echo "ERROR: ARK_JETSON_KERNEL_DIR is not set; cannot bind-mount repo into container." >&2
        exit 1
    fi

    prepare_container_staging_mounts "$repo_dir" || exit 1
    ensure_docker

    # Rebuild when the image is missing OR the Dockerfile changed since it was built.
    # An existence-only check silently keeps a stale image after a Dockerfile edit (e.g.
    # missing a newly-added tool, failing later with "command not found"). Stamp the build
    # with a content-hash label and compare it back — the :22.04 tag stays stable and the
    # check needs no network.
    local want_sha have_sha
    want_sha=$(sha256sum "$repo_dir/docker/Dockerfile" | cut -d' ' -f1)
    have_sha=$("${DOCKER_CMD[@]}" image inspect \
        --format '{{ if .Config.Labels }}{{ index .Config.Labels "dockerfile_sha" }}{{ end }}' \
        "$ARK_BUILDER_IMAGE" 2>/dev/null) || have_sha=""

    if [ "$want_sha" != "$have_sha" ]; then
        if [ -n "$have_sha" ]; then
            echo "Dockerfile changed — rebuilding $ARK_BUILDER_IMAGE..."
        else
            echo "Building $ARK_BUILDER_IMAGE (one-time, ~30-60s)..."
        fi
        "${DOCKER_CMD[@]}" build --label "dockerfile_sha=$want_sha" \
            -t "$ARK_BUILDER_IMAGE" "$repo_dir/docker/"
    fi

    # Persistent ccache for container builds: the container is --rm, so without a host
    # mount the cache vanishes each run. Dedicated dir (not a shared host ccache) to
    # avoid cross-project churn; override via ARK_CCACHE_DIR.
    local ccache_dir="${ARK_CCACHE_DIR:-${CCACHE_DIR:-$HOME/.cache/ark_jetson_ccache}}"
    mkdir -p "$HOME/l4t-gcc" "$ccache_dir"
    echo "Re-executing $(basename "$script_path") in 22.04 build container..."

    # Only add -t when stdin/stdout are both TTYs; `docker run -t` against a non-TTY
    # (CI, pipe, redirection) errors out. -i stays so the interactive prompt still works.
    local tty_flags=(-i)
    if [ -t 0 ] && [ -t 1 ]; then
        tty_flags+=(-t)
    fi

    # Translate an explicit local application artifact through the repository
    # mount. Relative paths are resolved on the caller's host before handoff.
    local ark_os_deb_container="" ark_os_deb_host
    if [ -n "${ARK_OS_DEB_PATH:-}" ]; then
        ark_os_deb_host=$(realpath -e -- "$ARK_OS_DEB_PATH") || {
            echo "ERROR: ARK_OS_DEB_PATH does not name an existing file." >&2
            exit 1
        }
        if [ ! -f "$ark_os_deb_host" ]; then
            echo "ERROR: ARK_OS_DEB_PATH must be a regular package file." >&2
            exit 1
        fi
        case "$ark_os_deb_host" in
            "$repo_dir"/*) ark_os_deb_container="/workspace/${ark_os_deb_host#"$repo_dir"/}" ;;
            *)
                echo "ERROR: for container builds, copy ARK_OS_DEB_PATH into this repository's downloads/ first." >&2
                exit 1
                ;;
        esac
    fi

    # SYS_ADMIN + apparmor=unconfined: NVIDIA's rootfs helpers chroot in and bind-mount
    # /proc,/sys,/dev, which Docker's default profiles block. The container is ephemeral
    # (--rm) and runs only the build, so the wider privilege is acceptable.
    exec "${DOCKER_CMD[@]}" run --rm "${tty_flags[@]}" \
        --cap-add=SYS_ADMIN \
        --security-opt apparmor=unconfined \
        -v "$repo_dir:/workspace" \
        "${STAGING_CONTAINER_MOUNTS[@]}" \
        -v "$HOME/l4t-gcc:/root/l4t-gcc" \
        -v "$ccache_dir:/root/.ccache" \
        -w /workspace \
        -e IN_BUILD_CONTAINER=1 \
        -e CCACHE_DIR=/root/.ccache \
        -e CCACHE_MAXSIZE="${CCACHE_MAXSIZE:-20G}" \
        -e CCACHE_COMPILERCHECK="${CCACHE_COMPILERCHECK:-content}" \
        -e CCACHE_SLOPPINESS=time_macros,include_file_ctime,include_file_mtime \
        -e ARK_BUILD_OS="$ARK_BUILD_OS" \
        -e ARK_OS_DEB_PATH="$ark_os_deb_container" \
        -e ARK_BUILD_COMMIT="$ARK_BUILD_COMMIT" \
        "$ARK_BUILDER_IMAGE" \
        bash "/workspace/$(basename "$script_path")" "$@"
}
