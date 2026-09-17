FROM ubuntu:24.04
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git xz-utils sudo cmake meson ninja-build pkg-config \
    build-essential astyle file patchelf python3-pip python3-venv libssl-dev \
    libsqlite3-dev libunwind-dev libgflags-dev libgoogle-glog-dev libboost-all-dev \
    libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev libgstrtspserver-1.0-dev \
    libbluetooth-dev lintian && rm -rf /var/lib/apt/lists/*
ARG NODE_VERSION=20.20.2
RUN cd /tmp \
    && curl -fsSLO "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-arm64.tar.xz" \
    && curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt" -o SHASUMS256.txt \
    && grep "  node-v${NODE_VERSION}-linux-arm64.tar.xz$" SHASUMS256.txt | sha256sum -c - \
    && tar -xJf "node-v${NODE_VERSION}-linux-arm64.tar.xz" -C /usr/local --strip-components=1 \
    && rm "node-v${NODE_VERSION}-linux-arm64.tar.xz" SHASUMS256.txt
