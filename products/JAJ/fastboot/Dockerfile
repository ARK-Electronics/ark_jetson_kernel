FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc-aarch64-linux-gnu g++-aarch64-linux-gnu \
    device-tree-compiler acpica-tools nasm python3-venv python3-virtualenv \
    python3-pip uuid-dev libssl-dev liblzma-dev git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build/src
