# Build environment for L4T R39.2.1 / JP 7.2.1: NVIDIA's documented 22.04 host, plus
# kmod 31 to match the Ubuntu 24.04 sample rootfs. See docs/build_host.md.

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

# 22.04 ships kmod 29. Build kmod 31 for depmod; build.sh puts it first on PATH.
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
