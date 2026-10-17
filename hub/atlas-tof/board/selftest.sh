#!/bin/sh
# 보드에서 (센서 없이) 모델·파서·융합이 PC 와 같은 답을 내는지 확인한다. 1분 안쪽.
#   golden: PyTorch 가 낸 logits 와 C++ 순전파 비교 (PASS 기준: 최대 오차 < 1e-4, argmax 불일치 0)
#   replay: 시뮬 세션 156 초를 두 센서로 넣어 10 Hz 경로 전체를 돌린다
D=$(cd "$(dirname "$0")" && pwd)
cd "$D" || exit 1
chmod +x ./tofposture_selftest ./deskmate_tofposture 2>/dev/null
./tofposture_selftest models/posture_net.bin models/golden.txt test/replay_s0.jsonl test/replay_s1.jsonl
