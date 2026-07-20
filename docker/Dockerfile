# Build environment matching NVIDIA's documented host for L4T R39.2 / JP 7.2.
# Host stays on 22.04 (NVIDIA's flash/build host recommendation); the Jetson
# sample rootfs is Ubuntu 24.04. See docs/build_host.md.

FROM ubuntu:22.04

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
