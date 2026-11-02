"""시뮬 세션 하나를 두 센서(한 장치의 좌·우 VL53L8CX)가 본 대로 렌더해, ESP32 tof_logger 펌웨어가
USB 로 보내는 것과 같은 줄 형식(JSON lines)으로 저장한다. Pi 4 서비스(hub/atlas-tof)의 리플레이·호스트
테스트 입력이다. 각 줄에 정답 "label" 을 덧붙인다(펌웨어 줄에는 없음; 서비스는 무시하고 테스트는 채점에 씀).

    python tools/sim_to_toflines.py --seed 7001 --placement random --seconds 120 --out ../2026ESWContest_smart_TEAMMATE/hub/atlas-tof/test
    -> test/replay_s0.jsonl, test/replay_s1.jsonl  (센서 0 = 사용자 기준 왼쪽, 1 = 오른쪽)

줄 형식은 firmware/esp32_sensor_node/src/tof_logger/TofLine.cpp 와 같다: tof_hello 한 줄 뒤
{"t":"tof","seq":N,"ms":N,"w":8,"h":8,"d":[...],"st":[...],"rule":{},"label":"..."}. ms 는 시뮬 프레임률(10 Hz)
그대로다 — 실제 펌웨어는 15 Hz 이므로 서비스가 10 Hz 로 솎아 넣는 쪽은 실기에서 확인한다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from eval_pair import render_pair  # noqa: E402
from tofsim.session import FLAG_TRANSITION, LABELS  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=7001)
    ap.add_argument("--placement", default="random", choices=["arc", "random", "mixed"])
    ap.add_argument("--seconds", type=float, default=120.0)
    ap.add_argument("--clutter", type=int, default=None)
    ap.add_argument("--out", required=True, help="폴더. replay_s0.jsonl, replay_s1.jsonl 을 쓴다")
    ap.add_argument("--prefix", default="replay")
    args = ap.parse_args()

    dist, status, label, flags, meta = render_pair((args.seed, args.placement, args.seconds, args.clutter))
    os.makedirs(args.out, exist_ok=True)
    T, H, W = dist.shape[1:]
    for s in range(dist.shape[0]):
        path = os.path.join(args.out, f"{args.prefix}_s{s}.jsonl")
        with open(path, "w", encoding="ascii") as fp:
            fp.write(json.dumps({"t": "tof_hello", "ms": 0, "sensor": "VL53L8CX-sim", "w": W, "h": H, "hz": 10,
                                 "flip_rows": False, "flip_cols": False, "target_order": "closest", "fw": 0,
                                 "seed": args.seed, "placement": args.placement,
                                 "azimuth_deg": meta["azimuth_deg"], "misplaced": meta["misplaced"]},
                                separators=(",", ":")) + "\n")
            for t in range(T):
                lab = "transition" if (flags[t] & FLAG_TRANSITION) else LABELS[int(label[t])]
                fp.write(json.dumps({"t": "tof", "seq": t + 1, "ms": 100 + t * 100, "w": W, "h": H,
                                     "d": [int(v) for v in dist[s, t].ravel()],
                                     "st": [int(v) for v in status[s, t].ravel()],
                                     "rule": {}, "label": lab}, separators=(",", ":")) + "\n")
        print(f"-> {path}  ({T} frames)")
    counts = {LABELS[int(k)]: int(v) for k, v in zip(*np.unique(label, return_counts=True))}
    print(f"seed {args.seed} az {meta['azimuth_deg']:+.0f} misplaced={meta['misplaced']} labels {counts}")


if __name__ == "__main__":
    main()
