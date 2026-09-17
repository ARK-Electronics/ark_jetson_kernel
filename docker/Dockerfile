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
        liblzma-dev \
        libzstd-dev \
        zlib1g-dev \
        pkg-config \
        bc \
        kmod \
        qemu-user-static \
        binfmt-support \
        device-tree-compiler \
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

# R39 initramfs uses kmod 31. Keep Ubuntu 22.04's kmod 29 available for
# historical R36 profiles, and select this pinned tool only for R39 builds.
RUN curl -fL --retry 3 -o /tmp/kmod.tar.xz \
        https://mirrors.edge.kernel.org/pub/linux/utils/kernel/kmod/kmod-31.tar.xz \
    && echo "f5a6949043cc72c001b728d8c218609c5a15f3c33d75614b78c79418fcf00d80  /tmp/kmod.tar.xz" | sha256sum -c - \
    && tar -xJf /tmp/kmod.tar.xz -C /tmp \
    && cd /tmp/kmod-31 \
    && ./configure --prefix=/opt/ark-kmod31 --with-rootlibdir=/opt/ark-kmod31/lib \
        LDFLAGS=-Wl,-rpath,/opt/ark-kmod31/lib \
        --disable-manpages --with-xz --with-zstd --with-zlib \
    && make -j"$(nproc)" \
    && make install \
    && ln -sf kmod /opt/ark-kmod31/bin/depmod \
    && ln -sf kmod /opt/ark-kmod31/bin/modprobe \
    && /opt/ark-kmod31/bin/depmod --version \
    && rm -rf /tmp/kmod-31 /tmp/kmod.tar.xz

WORKDIR /workspace
