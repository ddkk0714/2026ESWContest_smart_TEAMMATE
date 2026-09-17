#!/usr/bin/env bash
# Cross-build a static aarch64 mosquitto broker + pub/sub clients with the Atlas SDK
# toolchain inside the deskmate-atlas-dev:local container (see display/atlas/README.md).
# opkg on the boards cannot reach the LG feed (kairos-art.lge.com), so this is the
# only practical way to get a broker onto the Pi 4.
#
# Usage (host, Git Bash / PowerShell with docker):  tools/atlas-hotspot-broker/build_mosquitto.sh
# Output: tools/atlas-hotspot-broker/dist/{mosquitto,mosquitto_pub,mosquitto_sub}
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VER="${MOSQUITTO_VERSION:-2.0.20}"
IMAGE="${ATLAS_DEV_IMAGE:-deskmate-atlas-dev:local}"
DIST="$HERE/dist"
mkdir -p "$DIST"

TARBALL="$DIST/mosquitto-$VER.tar.gz"
if [ ! -f "$TARBALL" ]; then
    curl -sSL -o "$TARBALL" "https://mosquitto.org/files/source/mosquitto-$VER.tar.gz"
fi

# Windows docker needs a native path for the bind mount.
if command -v cygpath >/dev/null 2>&1; then MOUNT="$(cygpath -w "$DIST")"; else MOUNT="$DIST"; fi

docker run --rm -v "$MOUNT:/dist" "$IMAGE" bash -c '
set -e
. /opt/atlas-sdk-x86_64/environment-setup-armv8a-atlas-linux
cd /tmp && rm -rf mosquitto-'"$VER"' && tar -xzf /dist/mosquitto-'"$VER"'.tar.gz && cd mosquitto-'"$VER"'
OPTS="WITH_TLS=no WITH_TLS_PSK=no WITH_CJSON=no WITH_DOCS=no WITH_WEBSOCKETS=no WITH_SRV=no WITH_DLT=no \
      WITH_BRIDGE=no WITH_PERSISTENCE=no WITH_MEMORY_TRACKING=no WITH_SYS_TREE=yes \
      WITH_STATIC_LIBRARIES=yes WITH_SHARED_LIBRARIES=no WITH_APPS=no WITH_PLUGINS=no \
      WITH_UNIX_SOCKETS=yes WITH_EPOLL=yes CROSS_COMPILE="
# plugins/ does not build for this target, so build lib, broker and clients directly.
make -C lib    -j"$(nproc)" $OPTS CC="$CC" LDFLAGS="$LDFLAGS -static" >/dev/null
make -C src    -j"$(nproc)" $OPTS CC="$CC" LDFLAGS="$LDFLAGS -static" >/dev/null
make -C client -j"$(nproc)" $OPTS CC="$CC" LDFLAGS="$LDFLAGS -static" >/dev/null
cp src/mosquitto client/mosquitto_pub client/mosquitto_sub /dist/
file /dist/mosquitto /dist/mosquitto_pub /dist/mosquitto_sub
'
echo "built: $DIST"
