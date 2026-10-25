#!/usr/bin/env bash
set -euo pipefail
poc_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ $# != 2 || $1 != --output-dir ]]; then
  echo 'usage: scripts/build.sh --output-dir NEW_ABSOLUTE_DIRECTORY' >&2; exit 64
fi
poc_output="$2"
# Refuse an existing output directory, including the initial IPK location.
python3 - "$poc_output" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]);assert p.is_absolute() and not p.exists(), 'new absolute output required'
p.mkdir(mode=0o700,parents=True)
PY
poc_container=deskmate-atlas-atlas-dev-1
poc_destination="$("$poc_root/scripts/prepare_workspace.sh")"
[[ $poc_destination == /tmp/deskmate-thinq-pat-* ]]
printf '%s\n' "$poc_destination" > "$poc_output/container-workspace.txt"
# A review-bound policy requires a separately reviewed future build. This build
# intentionally ships no account snapshot, no credential listener and no Live.
tar -C "$poc_root" --exclude=.git --exclude=artifacts --exclude=app/build --exclude=app/.dart_tool --exclude=native/build --exclude=__pycache__ --exclude='*.conf' -cf - . |
  docker exec -i "$poc_container" tar -xf - -C "$poc_destination"
docker exec -e POC_BUILD_ROOT="$poc_destination" "$poc_container" bash -lc 'set -eo pipefail; source /opt/atlas-sdk-x86_64/environment-setup-armv8a-atlas-linux; unset POC_BINDINGS_FILE; cd "$POC_BUILD_ROOT/native"; arc build .; test -s build/arm64/ipk/com.deskmate.thinqpoc1.ipk'
docker exec -e POC_BUILD_ROOT="$poc_destination" "$poc_container" bash -lc 'set -e; source "$ATLAS_FLUTTER_NDK_ENV"; cd "$POC_BUILD_ROOT/app"; flutter pub get --offline; dart format lib test; flutter test --no-pub; flutter analyze --no-pub; FLUTTER_INSTALL_DIR=/data/share/usr/atlas/apps/com.atlas.app.deskmate_thinq_poc flutter-atlas build atlas --release --target-arch arm64 --ipk; test -s build/atlas/arm64/release/ipk/com.atlas.app.deskmate_thinq_poc.ipk'
docker cp "$poc_container:$poc_destination/native/build/arm64/ipk/com.deskmate.thinqpoc1.ipk" "$poc_output/"
docker cp "$poc_container:$poc_destination/app/build/atlas/arm64/release/ipk/com.atlas.app.deskmate_thinq_poc.ipk" "$poc_output/"
python3 "$poc_root/scripts/inspect_packages.py" --directory "$poc_output"
