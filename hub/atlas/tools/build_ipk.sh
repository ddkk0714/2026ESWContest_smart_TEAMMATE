#!/usr/bin/env bash
# Build the Pi 4 hub IPK inside the Atlas dev container (image deskmate-atlas-dev:local, see display/atlas/compose.yaml).
#
#   PC (repo root):  docker run --rm -v "$PWD:/workspace" -w /workspace deskmate-atlas-dev:local bash hub/atlas/tools/build_ipk.sh
#   Windows Git Bash: prefix with MSYS_NO_PATHCONV=1 and use "$(pwd -W):/workspace".
#
# Output: hub/atlas/build/arm64/ipk/com.deskmate.hub1.ipk
# Install (what `arc install` does under the hood, usable from any shell that can ssh to the board):
#   scp hub/atlas/build/arm64/ipk/com.deskmate.hub1.ipk atlas:/tmp/arc/
#   ssh atlas 'abusctl call com.atlas.PackageManager1 Remove com.deskmate.hub1;
#              abusctl call com.atlas.PackageManager1 Install com.deskmate.hub1 /tmp/arc/com.deskmate.hub1.ipk'
#   curl http://<pi4-ip>:8765/health      # D-Bus activation starts the service on first use
set -euo pipefail
cd /workspace

# The SDK env script puts cmake, ar and the aarch64-atlas-linux compilers on PATH; `arc doctor` fails without it.
# shellcheck disable=SC1091
source /opt/atlas-sdk-x86_64/environment-setup-armv8a-atlas-linux

# CMakeLists.txt uses the container's /usr/bin/python3 (3.12, same minor as the board's restricted Python) to convert
# YAML -> JSON and to bundle the pure-python stdlib + paho into the executable payload.
if ! /usr/bin/python3 -c "import yaml, paho.mqtt" 2>/dev/null; then
    /usr/bin/python3 -m pip --version >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y -qq python3-pip; }
    /usr/bin/python3 -m pip install -q --break-system-packages "pyyaml" "paho-mqtt>=2,<3"
fi

arc build hub/atlas
ls -la hub/atlas/build/arm64/ipk/*.ipk
