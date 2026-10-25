#!/usr/bin/env bash
set -euo pipefail
poc_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
poc_container=deskmate-atlas-atlas-dev-1
poc_destination=/tmp/deskmate-team-thinq-poc-initial-20261025
# Reuse the existing container; never start/recreate it or use /workspace.
docker exec "$poc_container" test -f "$poc_destination/.poc-only"
tar -C "$poc_root" --exclude=.git --exclude=artifacts --exclude=app/build --exclude=app/.dart_tool --exclude=native/build --exclude=__pycache__ -cf - . |
  docker exec -i "$poc_container" tar -xf - -C "$poc_destination"
docker exec "$poc_container" bash -lc 'set -e; source /opt/atlas-sdk-x86_64/environment-setup-armv8a-atlas-linux; cd /tmp/deskmate-team-thinq-poc-initial-20261025/native; rm -f build/arm64/ipk/com.deskmate.thinqpoc1.ipk; arc build .; test -s build/arm64/ipk/com.deskmate.thinqpoc1.ipk'
docker exec "$poc_container" bash -lc 'set -e; source "$ATLAS_FLUTTER_NDK_ENV"; cd /tmp/deskmate-team-thinq-poc-initial-20261025/app; flutter pub get --offline; flutter test --no-pub; flutter analyze --no-pub; FLUTTER_INSTALL_DIR=/data/share/usr/atlas/apps/com.atlas.app.deskmate_thinq_poc flutter-atlas build atlas --release --target-arch arm64 --ipk'
mkdir -p "$poc_root/artifacts"
docker cp "$poc_container:$poc_destination/native/build/arm64/ipk/com.deskmate.thinqpoc1.ipk" "$poc_root/artifacts/"
docker cp "$poc_container:$poc_destination/app/build/atlas/arm64/release/ipk/com.atlas.app.deskmate_thinq_poc.ipk" "$poc_root/artifacts/"
python3 "$poc_root/scripts/inspect_packages.py"
