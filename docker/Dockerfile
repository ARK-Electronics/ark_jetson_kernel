# Build environment matching NVIDIA's documented host for L4T R36.4.4 / JP 6.2.1.
# See docs/build_host.md for why we pin to 22.04 (kmod 29 in the Jetson sample
# rootfs cannot parse kmod 31's modules.builtin index format).

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        flex \
        bison \
        libssl-dev \
        bc \
        kmod \
        wget \
        tar \
        bzip2 \
        cpio \
        sudo \
        git \
        python3 \
        ca-certificates \
        rsync \
        xxd \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
