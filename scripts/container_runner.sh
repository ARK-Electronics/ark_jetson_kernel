#!/bin/bash
# Shared host-detection + container-handoff helpers. Sourced by setup.sh and
# build_kernel.sh. NVIDIA's documented build host for L4T R36.4.4 / JP 6.2.1
# is Ubuntu 22.04; on anything else we re-exec the calling script inside a
# 22.04 docker container so that host-tooling differences (notably kmod 31
# vs kmod 29 in the on-device sample rootfs) cannot silently corrupt the
# build. See docs/build_host.md for the full background.

ARK_BUILDER_IMAGE="ark-jetson-builder:22.04"

# Returns 0 if the calling script should re-exec inside the build container.
# False when already inside (IN_BUILD_CONTAINER=1) or when the host is 22.04.
needs_container() {
    [ -z "$IN_BUILD_CONTAINER" ] || return 1
    local host_id
    host_id=$(. /etc/os-release && echo "$VERSION_ID")
    [ "$host_id" != "22.04" ]
}

# Installs docker via apt if it isn't on PATH, then sets DOCKER_CMD to either
# (docker) or (sudo docker) depending on whether the current user can talk to
# the docker daemon without sudo. The sudo fallback avoids forcing the user
# to add themselves to the docker group + re-login the first time they run.
# Exits on failure.
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
        # apt should start dockerd via systemd post-install, but be defensive.
        sudo systemctl is-active docker >/dev/null 2>&1 || sudo systemctl start docker
    fi

    if docker info >/dev/null 2>&1; then
        DOCKER_CMD=(docker)
    else
        DOCKER_CMD=(sudo docker)
    fi
}

# Re-exec the calling script inside the 22.04 build container. Does not
# return on success. Builds the image on first use. Bind-mounts the repo
# at /workspace and the bootlin toolchain at /root/l4t-gcc.
#
# Args:
#   $1     path to the calling script ($0)
#   $2..   args to pass through to the in-container invocation
run_in_container() {
    local script_path="$1"; shift
    local repo_dir="$ARK_JETSON_KERNEL_DIR"

    if [ -z "$repo_dir" ]; then
        echo "ERROR: ARK_JETSON_KERNEL_DIR is not set; cannot bind-mount repo into container." >&2
        exit 1
    fi

    ensure_docker

    if ! "${DOCKER_CMD[@]}" image inspect "$ARK_BUILDER_IMAGE" >/dev/null 2>&1; then
        echo "Building $ARK_BUILDER_IMAGE (one-time, ~30-60s)..."
        "${DOCKER_CMD[@]}" build -t "$ARK_BUILDER_IMAGE" "$repo_dir/docker/"
    fi

    mkdir -p "$HOME/l4t-gcc"
    echo "Re-executing $(basename "$script_path") in 22.04 build container..."
    # SYS_ADMIN + apparmor=unconfined: NVIDIA's l4t_create_default_user.sh and
    # other rootfs-customization helpers chroot into the staged rootfs and
    # mount-bind /proc, /sys, /dev. Docker's default seccomp/AppArmor profile
    # blocks mount(2), so we relax both for the build container. The container
    # is ephemeral (--rm) and runs only the kernel build, so the broader
    # privilege scope is acceptable.
    exec "${DOCKER_CMD[@]}" run --rm -it \
        --cap-add=SYS_ADMIN \
        --security-opt apparmor=unconfined \
        -v "$repo_dir:/workspace" \
        -v "$HOME/l4t-gcc:/root/l4t-gcc" \
        -w /workspace \
        -e IN_BUILD_CONTAINER=1 \
        "$ARK_BUILDER_IMAGE" \
        bash "/workspace/$(basename "$script_path")" "$@"
}
