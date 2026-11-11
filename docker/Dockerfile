# Build environment for L4T R39.2.1 / JP 7.2.1: Ubuntu 24.04, the same release as
# the Jetson sample rootfs, so the host's kmod matches the device's. See
# docs/build_host.md.

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ccache \
        flex \
        bison \
        libssl-dev \
        bc \
        kmod \
        wget \
        curl \
        tar \
        bzip2 \
        zstd \
        cpio \
        sudo \
        git \
        python3 \
        ca-certificates \
        rsync \
        xxd \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
