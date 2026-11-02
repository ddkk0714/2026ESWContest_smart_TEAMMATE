#!/usr/bin/env bash
# Offline-only test runner. Uses pre-existing host dependencies; installs nothing.
set -euo pipefail
poc_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ $# != 6 || $1 != --python || $3 != --include-root || $5 != --output-dir ]]; then
  echo 'usage: test_host.sh --python PYTHON312 --include-root HOST_HEADERS --output-dir NEW_DIRECTORY' >&2; exit 64
fi
poc_python="$2"
poc_headers="$4"
poc_output="$6"
"$poc_python" - "$poc_output" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]);assert p.is_absolute() and not p.exists(), 'new absolute output required'
p.mkdir(mode=0o700,parents=True)
PY
cd "$poc_root"
"$poc_python" -B native/configure_bindings.py '' "$poc_output/bindings.h" "$poc_output/default.conf"
"$poc_python" -B native/embed.py "$poc_output/embedded.h"
poc_flags=(-std=c++17 -Wall -Wextra -Werror -pthread -I"$poc_headers" -Inative/src)
poc_libs=(-Wl,-l:libcurl.so.4 -Wl,-l:libcrypto.so.3)
"${CXX:-g++}" -DPOC_HOST_TEST "${poc_flags[@]}" -I"$poc_output" native/src/main.cpp native/src/https.cpp native/src/auth.cpp native/src/credential_helper.cpp "${poc_libs[@]}" -o "$poc_output/service"
"${CXX:-g++}" -DPOC_HOST_TEST "${poc_flags[@]}" native/src/auth_host_test.cpp native/src/auth.cpp native/src/https.cpp "${poc_libs[@]}" -o "$poc_output/auth-host"
"${CXX:-g++}" "${poc_flags[@]}" native/src/transport_test.cpp native/src/https.cpp "${poc_libs[@]}" -o "$poc_output/transport-test"
"$poc_output/transport-test"
POC_HOST_BINARY="$poc_output/service" POC_AUTH_HOST_BINARY="$poc_output/auth-host" POC_SECURITY_SERVICE_BINARY="$poc_output/service" PYTHONPATH="$poc_root" PYTHONDONTWRITEBYTECODE=1 "$poc_python" -B -m pytest -p no:cacheprovider -q tests tools/thinq_connect/test_manual_trial.py tools/thinq_connect/test_air_quality.py
"$poc_python" -m ruff check --select E4,E7,E9,F,I --exclude tests/legacy poc native/configure_bindings.py tools/pat_session.py scripts tests
"$poc_python" -m ruff format --check --exclude tests/legacy poc native/configure_bindings.py tools/pat_session.py scripts tests
