#!/usr/bin/env bash
# Atlas SDK 컨테이너 없이 Pi 4(ATLAS, aarch64)용 정적 실행 파일을 만든다 (Zig 의 clang + musl).
#   python -m venv zigenv && zigenv/bin/pip install ziglang==0.13.0
#   ZIG="zigenv/bin/python -m ziglang" bash hub/atlas-tof/tools/build_static_aarch64.sh
# 결과: hub/atlas-tof/build/aarch64/{deskmate_tofposture,tofposture_selftest} — 정적 링크라 보드 라이브러리와 무관.
# 2026-10-15 UBAI 에서 이렇게 빌드해 qemu-aarch64 로 selftest PASS(골든 오차 4.1e-5) 확인.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$HERE"
ZIG="${ZIG:-zig}"; OUT=build/aarch64; mkdir -p "$OUT"
FLAGS="-target aarch64-linux-musl -O2 -std=c++17 -static -s"
CORE="src/tof_line.cpp src/device_posture.cpp src/placement.cpp src/baseline.cpp src/facedown_rule.cpp"
$ZIG c++ $FLAGS -o $OUT/deskmate_tofposture src/main.cpp src/line_source.cpp src/http_server.cpp $CORE
$ZIG c++ $FLAGS -o $OUT/tofposture_selftest src/host_test.cpp src/http_server.cpp $CORE
ls -l $OUT
