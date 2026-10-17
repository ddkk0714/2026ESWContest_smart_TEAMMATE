#!/usr/bin/env bash
# Pi 4 배포 묶음(pi4/ + pc/ 모델)을 export 두 개로 조립하고, aarch64 자가 시험(qemu)까지 돌린다. 서버(UBAI)용.
#
#   bash assemble_bundle.sh <기본 export 폴더> <대안 export 폴더> <대안 이름> <출력 폴더>
#   예) bash assemble_bundle.sh runs/deploy_fresh_1016/export_..._best ~/tof-sim-v5-random5/runs/ubai_a9_demo/export_..._best \
#          a9_demo ~/pnref_check/bundle_next
#
# 필요한 것: hub/atlas-tof 소스(SRC), Zig(ZIG), qemu-aarch64(QEMU), 시뮬 리플레이 두 파일(REPLAY_S0/S1).
# export 폴더에는 export.py 가 낸 posture_net.bin·golden.txt·model_card.json·report.md·posture_net.onnx 가 있어야 한다.
set -euo pipefail
DEF="${1:?기본 export}"; ALT="${2:?대안 export}"; ALT_NAME="${3:?대안 이름}"; OUT="${4:?출력 폴더}"
SRC="${SRC:-$HOME/pnref_check/atlas-tof}"
ZIG="${ZIG:-$HOME/pnref_check/zigenv/bin/python -m ziglang}"
QEMU="${QEMU:-$HOME/pnref_check/qemuenv/bin/qemu-aarch64}"
REPLAY_S0="${REPLAY_S0:-$HOME/pnref_check/bench/sim/replay/r7004_s0.jsonl}"
REPLAY_S1="${REPLAY_S1:-$HOME/pnref_check/bench/sim/replay/r7004_s1.jsonl}"

for f in posture_net.bin golden.txt model_card.json posture_net.onnx; do
    [ -f "$DEF/$f" ] || { echo "기본 export 에 $f 없음: $DEF" >&2; exit 1; }
    [ -f "$ALT/$f" ] || { echo "대안 export 에 $f 없음: $ALT" >&2; exit 1; }
done

echo "== aarch64 정적 빌드 ($SRC)"
mkdir -p "$SRC/build/aarch64"
FLAGS="-target aarch64-linux-musl -O2 -std=c++17 -static -s"
( cd "$SRC" && $ZIG c++ $FLAGS -o build/aarch64/deskmate_tofposture src/main.cpp src/line_source.cpp src/http_server.cpp src/tof_line.cpp src/device_posture.cpp 2>&1 | grep -E "error" || true
  $ZIG c++ $FLAGS -o build/aarch64/tofposture_host_test src/host_test.cpp src/tof_line.cpp src/device_posture.cpp 2>&1 | grep -E "error" || true )

echo "== 조립 → $OUT"
rm -rf "$OUT"; mkdir -p "$OUT/pi4/models/$ALT_NAME" "$OUT/pi4/test" "$OUT/pc"
cp "$SRC/build/aarch64/deskmate_tofposture" "$OUT/pi4/"
cp "$SRC/build/aarch64/tofposture_host_test" "$OUT/pi4/tofposture_selftest"
cp "$SRC"/board/*.sh "$SRC/board/tofposture.env.example" "$OUT/pi4/"
cp "$DEF"/{posture_net.bin,golden.txt,model_card.json} "$OUT/pi4/models/"; [ -f "$DEF/report.md" ] && cp "$DEF/report.md" "$OUT/pi4/models/"
cp "$ALT"/{posture_net.bin,golden.txt,model_card.json} "$OUT/pi4/models/$ALT_NAME/"; [ -f "$ALT/report.md" ] && cp "$ALT/report.md" "$OUT/pi4/models/$ALT_NAME/"
cp "$REPLAY_S0" "$OUT/pi4/test/replay_s0.jsonl"; cp "$REPLAY_S1" "$OUT/pi4/test/replay_s1.jsonl"
cp "$DEF/posture_net.onnx" "$OUT/pc/posture_net.onnx"; cp "$ALT/posture_net.onnx" "$OUT/pc/posture_net_$ALT_NAME.onnx"
chmod 755 "$OUT/pi4/deskmate_tofposture" "$OUT/pi4/tofposture_selftest" "$OUT"/pi4/*.sh

echo "== aarch64 자가 시험 (qemu, 보드 selftest.sh 와 같은 명령)"
cd "$OUT/pi4"
ok=1
for m in models models/$ALT_NAME; do
    echo "-- $m"
    if ! "$QEMU" ./tofposture_selftest "$m/posture_net.bin" "$m/golden.txt" test/replay_s0.jsonl test/replay_s1.jsonl | grep -E "golden:|replay:|PASS|FAIL"; then ok=0; fi
done
cd "$OUT"; find . -type f | sort | xargs sha256sum | grep -E "deskmate_tofposture|posture_net.bin"
[ $ok = 1 ] && echo "ASSEMBLED OK" || { echo "SELFTEST FAILED" >&2; exit 1; }
