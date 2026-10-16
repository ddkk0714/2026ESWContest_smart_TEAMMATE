"""Score the firmware's rule-based posture estimator on a validation set.

Same frames and the same scoring mask as train.py's evaluate(), so the two
numbers are directly comparable.

    python eval_rules.py runs/valset_vl53l8cx_24x300.npz
    python eval_rules.py data/real.npz --exe tools/rule_replay.exe
"""

import argparse
import json
import os
import subprocess
import sys

import numpy as np

from tofsim.session import FLAG_TRANSITION, LABELS

HERE = os.path.dirname(os.path.abspath(__file__))
# 펌웨어 규칙 판정 소스. 기본은 rules/ 의 사본(팀 레포 firmware/esp32_sensor_node/src/preprocess/tof).
# 팀 레포의 최신 펌웨어로 채점하려면 TOFPOSTURE_SRC 에 그 폴더(TofPosture.h·.cpp 가 있는 곳)를 준다.
RULES_SRC = os.environ.get("TOFPOSTURE_SRC", os.path.join(HERE, "rules"))
# firmware TofPosture enum -> label index (Unknown -> -1)
ENUM_TO_LABEL = {0: -1, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6}


def build_exe(path):
    src = os.path.join(HERE, "tools", "rule_replay.cpp")
    est = os.path.join(RULES_SRC, "TofPosture.cpp")
    cmd = ["g++", "-std=gnu++14", "-O2", f"-I{RULES_SRC}", src, est, "-o", path]
    print("building", " ".join(cmd))
    subprocess.check_call(cmd)


def run_rules(exe, data):
    dist, status = data["dist"], data["status"]
    T, H, W = dist.shape
    starts = list(data.get("starts", [0]))
    chunks = [bytes([W, H])]
    reset = np.array([0xFFFFFFFF], dtype="<u4").tobytes()
    frame_ms = 67
    for i in range(T):
        if i in starts:
            chunks.append(reset)
            t0 = i
        chunks.append(np.array([(i - t0) * frame_ms], dtype="<u4").tobytes())
        chunks.append(dist[i].astype("<i2").tobytes())
        chunks.append(status[i].astype(np.uint8).tobytes())
    out = subprocess.run([exe], input=b"".join(chunks), capture_output=True, check=True).stdout
    enum = np.frombuffer(out, dtype=np.uint8)
    assert len(enum) == T, (len(enum), T)
    return np.array([ENUM_TO_LABEL[int(e)] for e in enum])


def score(pred, data, warmup_frames=45):
    label = data["label"].astype(int)
    flags = data.get("flags", np.zeros(len(label), dtype=np.uint8))
    keep = (flags & FLAG_TRANSITION) == 0
    for s in data.get("starts", [0]):
        keep[s:s + warmup_frames] = False
    C = len(LABELS)
    conf = np.zeros((C, C + 1), dtype=int)          # last column = unknown
    for y, p in zip(label[keep], pred[keep]):
        conf[y, p if p >= 0 else C] += 1
    tp = np.array([conf[i, i] for i in range(C)], dtype=float)
    recall = tp / np.maximum(conf.sum(1), 1)
    precision = tp / np.maximum(conf[:, :C].sum(0), 1)
    f1 = 2 * recall * precision / np.maximum(recall + precision, 1e-9)
    present = conf.sum(1) > 0
    return {
        "accuracy": float(tp.sum() / max(conf.sum(), 1)),
        "macro_f1": float(f1[present].mean()),
        "unknown_rate": float(conf[:, C].sum() / max(conf.sum(), 1)),
        "recall": {LABELS[i]: round(float(recall[i]), 3) for i in range(C) if present[i]},
        "precision": {LABELS[i]: round(float(precision[i]), 3) for i in range(C) if present[i]},
        "confusion_rows_true_cols_pred+unknown": conf.tolist(),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("npz")
    ap.add_argument("--exe", default=os.path.join(HERE, "tools", "rule_replay.exe" if os.name == "nt" else "rule_replay"))
    args = ap.parse_args()
    if not os.path.exists(args.exe):
        build_exe(args.exe)
    with np.load(args.npz) as z:
        data = {k: z[k] for k in z.files}
    pred = run_rules(args.exe, data)
    res = score(pred, data)
    rec = " ".join(f"{k}:{v:.2f}" for k, v in res["recall"].items())
    print(f"rules: acc {res['accuracy']:.3f} macroF1 {res['macro_f1']:.3f} unknown {res['unknown_rate']:.3f} | recall {rec}")
    print("precision", res["precision"])
    print(json.dumps(res["confusion_rows_true_cols_pred+unknown"]))


if __name__ == "__main__":
    sys.exit(main())
