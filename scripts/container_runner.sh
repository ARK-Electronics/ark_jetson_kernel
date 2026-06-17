#!/bin/bash
# Host-detection + container handoff, sourced by setup.sh and build.sh. NVIDIA's
# documented build host is Ubuntu 22.04; on anything else we re-exec inside a 22.04
# container so host-tool differences (kmod 31 vs the rootfs's kmod 29) can't corrupt
# the build. See docs/build_host.md.

ARK_BUILDER_IMAGE="ark-jetson-builder:22.04"

# Returns 0 if the calling script should re-exec inside the build container.
# False when already inside (IN_BUILD_CONTAINER=1) or when the host is 22.04.
needs_container() {
    [ -z "$IN_BUILD_CONTAINER" ] || return 1
    local host_id
    host_id=$(. /etc/os-release && echo "$VERSION_ID")
    [ "$host_id" != "22.04" ]
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
    local ccache_dir="${ARK_CCACHE_DIR:-$HOME/.cache/ark_jetson_ccache}"
    mkdir -p "$HOME/l4t-gcc" "$ccache_dir"
    echo "Re-executing $(basename "$script_path") in 22.04 build container..."

    # Only add -t when stdin/stdout are both TTYs; `docker run -t` against a non-TTY
    # (CI, pipe, redirection) errors out. -i stays so the interactive prompt still works.
    local tty_flags=(-i)
    if [ -t 0 ] && [ -t 1 ]; then
        tty_flags+=(-t)
    fi

    # SYS_ADMIN + apparmor=unconfined: NVIDIA's rootfs helpers chroot in and bind-mount
    # /proc,/sys,/dev, which Docker's default profiles block. The container is ephemeral
    # (--rm) and runs only the build, so the wider privilege is acceptable.
    exec "${DOCKER_CMD[@]}" run --rm "${tty_flags[@]}" \
        --cap-add=SYS_ADMIN \
        --security-opt apparmor=unconfined \
        -v "$repo_dir:/workspace" \
        -v "$HOME/l4t-gcc:/root/l4t-gcc" \
        -v "$ccache_dir:/root/.ccache" \
        -w /workspace \
        -e IN_BUILD_CONTAINER=1 \
        -e CCACHE_DIR=/root/.ccache \
        -e CCACHE_MAXSIZE="${CCACHE_MAXSIZE:-20G}" \
        -e CCACHE_SLOPPINESS=time_macros,include_file_ctime,include_file_mtime \
        -e ARK_BUILD_OS="$ARK_BUILD_OS" \
        -e ARK_BUILD_COMMIT="$ARK_BUILD_COMMIT" \
        "$ARK_BUILDER_IMAGE" \
        bash "/workspace/$(basename "$script_path")" "$@"
}
