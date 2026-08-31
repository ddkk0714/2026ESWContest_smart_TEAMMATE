# Atlas build assets are provisioned into ./vendor by scripts/import-atlas-vendor.ps1.
FROM ubuntu:noble

ENV DEBIAN_FRONTEND=noninteractive
ENV FLUTTER_ATLAS_PLUGINS_PATH=/opt/flutter-atlas-plugins

RUN apt-get update && apt-get install -y \
    curl unzip xz-utils zstd lz4 gawk diffstat texinfo build-essential chrpath \
    socat cpio file rsync iputils-ping debianutils locales xterm libegl1 \
    libsdl2-dev python3 python3-pip python3-pexpect python3-git python3-jinja2 \
    python-is-python3 ca-certificates gnupg git openssh-client \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update && apt-get install -y nodejs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY vendor/atlas_engine /opt/atlas_engine
COPY vendor/flutter-atlas-plugins /opt/flutter-atlas-plugins
COPY vendor/flutter-elinux-atlas /opt/flutter-elinux-atlas
COPY vendor/atlas-sdk-x86_64-armv8a-generic_arm64-toolchain-26.06.0-unofficial.scarthgap.s6-6-ai.sh /tmp/atlas-sdk.sh
COPY vendor/arc-0.5.0.tgz /tmp/arc.tgz

RUN chmod +x /opt/flutter-elinux-atlas/bin/flutter-atlas \
    && mkdir -p /opt/atlas-sdk-x86_64 \
    && chmod +x /tmp/atlas-sdk.sh \
    && /tmp/atlas-sdk.sh -y -d /opt/atlas-sdk-x86_64 \
    && rm /tmp/atlas-sdk.sh \
    && npm install -g /tmp/arc.tgz \
    && rm /tmp/arc.tgz

ENV PATH="/opt/flutter-elinux-atlas/bin:/opt/flutter-elinux-atlas/flutter/bin:${PATH}"
ENV ATLAS_FLUTTER_NDK_ENV=/opt/atlas-sdk-x86_64/environment-setup-armv8a-atlas-linux
ENV ATLAS_ENGINE_BASE_LOCAL_DIRECTORY=/opt/atlas_engine/

RUN flutter-atlas --version

WORKDIR /workspace/display
