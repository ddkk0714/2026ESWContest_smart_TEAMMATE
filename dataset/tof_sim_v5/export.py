"""학습 중이든 멈춘 뒤든, 지금까지의 모델을 꺼내 한 폴더(+zip)로 정리한다.

    python export.py --run mj_trial                 # 최고 모델(ckpt_best) + 검증셋 채점
    python export.py --run mj_trial --which last    # 가장 최근 저장본(ckpt_last)
    python export.py --run mj_trial --no-eval       # 채점 없이 가중치만
    python export.py --run mj_trial --real data/real.npz   # 실측 데이터도 채점

학습을 멈추지 않아도 된다. 체크포인트는 원자적으로 저장되므로 학습 중에 읽어도 안전하다.
최신 상태가 필요하면 먼저 `runs/<RUN>/SNAPSHOT` 파일을 만들면 학습이 몇 초 안에 저장한다.

출력: runs/<RUN>/export_<시각>_<best|last>/ 와 같은 이름의 .zip
    posture_net.pt      가중치(state_dict)와 라벨·센서·입력 형식·학습 정보
    weights.npz         같은 가중치를 numpy 배열로 (펌웨어 이식용)
    posture_net.onnx    한 프레임 스트리밍 추론 그래프 (입력 x, h → 출력 logits, h)
    posture_net.bin     보드 이식용 평탄 float32 가중치 (헤더 "TOFPN001" + H,W,classes,hidden; 순서는 BIN_ORDER)
    golden.txt          PyTorch 로 만든 입력(거리·상태값)과 logits — tools/posture_net_ref.cpp 로 C++ 구현을 맞춰 봄
    report.md           학습 정보, 클래스별 점수, 혼동행렬, 학습 중 점수 변화
    model_card.json     입력·출력 형식 요약
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sys
import time

import numpy as np
import torch
from torch import nn

from tofsim.data import load_valset, to_input
from tofsim.evaluate import evaluate
from tofsim.model import PostureNet, count_params
from tofsim.session import LABELS, SessionConfig

HERE = os.path.dirname(os.path.abspath(__file__))
KO = {"upright": "바른자세", "lean_forward": "상체 앞으로", "recline": "뒤로 젖힘", "face_down": "엎드림",
      "chin_rest": "턱괴기", "drowsy": "꾸벅 졸음", "away": "자리비움", "misplaced": "위치 재조정", "forward_head": "거북목"}

INPUT_SPEC = {
    "frame": "센서 프레임 하나 = (2, H, W) float32",
    "channel_0": "거리 mm / 4000 (유효한 zone 만, 아니면 0)",
    "channel_1": "유효 zone 이면 1, 아니면 0. 유효 = 상태값 5·6·9·10 이고 거리 >= 0",
    "layout": "row 0 = 위쪽, col 0 = 센서에서 볼 때 왼쪽 (row-major, 펌웨어 TofPosture 와 같음)",
    "state": "GRU 은닉 상태 h (1, 1, hidden). 세션 시작 때 0 으로, 프레임마다 이어서 넘긴다",
    "output": "logits (classes,) — argmax 가 자세. 라벨 순서는 labels",
    "rate_hz": SessionConfig.fps,      # the simulated ranging rate: the board must step the model at this rate
}


BIN_MAGIC = b"TOFPN001"
BIN_ORDER = ["enc.0.weight", "enc.0.bias", "enc.2.weight", "enc.2.bias", "enc.4.weight", "enc.4.bias",
             "enc.8.weight", "enc.8.bias", "gru.weight_ih_l0", "gru.weight_hh_l0", "gru.bias_ih_l0",
             "gru.bias_hh_l0", "head.weight", "head.bias"]


def write_bin(net: PostureNet, path: str, sensor: dict, classes: int, hidden: int) -> list[dict]:
    """보드용 평탄 바이너리: 8바이트 매직, int32 ×4 (H, W, classes, hidden), 이어서 BIN_ORDER 순서의 float32(LE).
    헤더만 읽으면 모든 텐서 크기가 정해지므로 C++ 쪽에 파서가 필요 없다 (tools/posture_net_ref.cpp)."""
    sd = {k: np.ascontiguousarray(v.detach().cpu().numpy().astype("<f4")) for k, v in net.state_dict().items()}
    manifest, off = [], 0
    with open(path, "wb") as fp:
        fp.write(BIN_MAGIC)
        fp.write(np.array([sensor["height"], sensor["width"], classes, hidden], dtype="<i4").tobytes())
        for k in BIN_ORDER:
            fp.write(sd[k].tobytes())
            manifest.append({"name": k, "shape": list(sd[k].shape), "offset_floats": off, "count": int(sd[k].size)})
            off += int(sd[k].size)
    return manifest


def write_golden(net: PostureNet, path: str, sensor: dict, classes: int, hidden: int, frames: int = 240,
                 seed: int = 20261014) -> None:
    """C++ 구현 검증용 입출력. 무작위 거리·상태값(가까운 덩어리 + 먼 배경 + 결측·음수·빈 프레임)을 넣고
    PyTorch 가 낸 logits 를 프레임마다 적는다. 중간에 한 번 reset 해 상태 초기화도 같이 검사한다."""
    rng = np.random.default_rng(seed)
    H, W = sensor["height"], sensor["width"]
    pool = np.array([5, 5, 5, 5, 6, 9, 10, 4, 255, 0], dtype=np.uint8)
    dist = rng.integers(150, 4000, size=(frames, H, W)).astype(np.int16)
    for t in range(frames):                       # 사람처럼 가까운 3×3 덩어리
        r0, c0 = int(rng.integers(0, max(1, H - 2))), int(rng.integers(0, max(1, W - 2)))
        dist[t, r0:r0 + 3, c0:c0 + 3] = rng.integers(300, 1200)
    status = pool[rng.integers(0, len(pool), size=(frames, H, W))]
    dist[rng.random((frames, H, W)) < 0.02] = -1   # 음수 거리 = 무효
    dist[frames // 3] = 0
    status[frames // 3] = 255                      # 아무것도 못 본 프레임
    resets = {0, (2 * frames) // 3}
    x = torch.from_numpy(to_input(dist, status))   # (T,2,H,W)
    lines = [f"golden {frames} {H} {W} {classes} {hidden}"]
    h = None
    with torch.no_grad():
        for t in range(frames):
            if t in resets:
                h = None
            logits, h = net(x[t][None, None], h)
            lines += [f"frame {t} reset {int(t in resets)}",
                      "dist " + " ".join(str(int(v)) for v in dist[t].ravel()),
                      "status " + " ".join(str(int(v)) for v in status[t].ravel()),
                      "logits " + " ".join(f"{float(v):.7g}" for v in logits[0, 0].numpy())]
    with open(path, "w", encoding="ascii") as fp:
        fp.write("\n".join(lines) + "\n")


class StepModel(nn.Module):
    """ONNX 용: 한 프레임 입력 -> 한 프레임 출력 (보드처럼 매 프레임 한 번씩 부른다)."""

    def __init__(self, net: PostureNet):
        super().__init__()
        self.net = net

    def forward(self, x, h):            # x (1,2,H,W), h (1,1,hidden)
        logits, h2 = self.net(x[:, None], h)
        return logits[:, 0], h2


def load_checkpoint(path):
    try:
        return torch.load(path, map_location="cpu", weights_only=True)
    except Exception:
        return torch.load(path, map_location="cpu", weights_only=False)


def find_valset(sensor_name):
    cands = sorted(glob.glob(os.path.join(HERE, "runs", f"valset_mujoco_{sensor_name}_*.npz")))
    cands += sorted(glob.glob(os.path.join(HERE, "runs", f"valset_*{sensor_name}*.npz")))
    return cands[0] if cands else None


def eval_table(ev):
    rows = ["| 자세 | 재현율 | 정밀도 | F1 |", "|---|---|---|---|"]
    for name in LABELS:
        if name in ev["recall"]:
            rows.append(f"| {KO[name]} ({name}) | {ev['recall'][name]:.3f} | {ev['precision'][name]:.3f} | "
                        f"{ev['f1'][name]:.3f} |")
    conf = np.array(ev["confusion"])
    head = "| 실제 \\ 예측 | " + " | ".join(KO[n] for n in LABELS) + " |"
    sep = "|---" * (len(LABELS) + 1) + "|"
    body = [f"| {KO[LABELS[i]]} | " + " | ".join(str(v) for v in conf[i]) + " |" for i in range(len(LABELS))]
    return "\n".join(rows) + "\n\n혼동행렬 (프레임 수)\n\n" + "\n".join([head, sep] + body)


def history(run_dir):
    path = os.path.join(run_dir, "metrics.jsonl")
    if not os.path.exists(path):
        return ""
    lines = ["| 시각 | step | 학습 프레임 | 정확도 | macro-F1 |", "|---|---|---|---|---|"]
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("kind") == "eval":
                v = rec["val"]
                lines.append(f"| {rec['time']} | {rec['step']} | {rec['frames']:,} | {v['accuracy']:.3f} | {v['macro_f1']:.3f} |")
    return "\n".join(lines) if len(lines) > 2 else ""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True, help="runs/<RUN> 폴더 이름")
    ap.add_argument("--which", choices=["best", "last"], default="best")
    ap.add_argument("--no-eval", action="store_true", help="검증셋 채점을 건너뜀")
    ap.add_argument("--valset", default=None, help="채점할 검증셋 (기본: runs/ 에서 찾음)")
    ap.add_argument("--real", nargs="*", default=[], help="실측 라벨 데이터 npz")
    ap.add_argument("--no-zip", action="store_true")
    args = ap.parse_args()

    run_dir = os.path.join(HERE, "runs", args.run)
    ckpt_path = os.path.join(run_dir, f"ckpt_{args.which}.pt")
    if not os.path.exists(ckpt_path):
        other = os.path.join(run_dir, "ckpt_last.pt")
        if args.which == "best" and os.path.exists(other):
            print(f"{ckpt_path} 가 없어 ckpt_last.pt 를 씀 (아직 평가 전)")
            ckpt_path, args.which = other, "last"
        else:
            sys.exit(f"체크포인트가 없음: {ckpt_path}")

    ck = load_checkpoint(ckpt_path)
    sensor = ck.get("sensor", {"name": "vl53l8cx", "width": 8, "height": 8})
    labels = list(ck.get("labels", LABELS))
    hidden = int(ck["model"]["gru.weight_hh_l0"].shape[1])          # the width the checkpoint was trained with
    net = PostureNet(sensor["height"], sensor["width"], len(labels), hidden=hidden)
    net.load_state_dict(ck["model"])
    net.eval()
    st = ck.get("state", {})

    stamp = time.strftime("%Y%m%d_%H%M")
    out = os.path.join(run_dir, f"export_{stamp}_{args.which}")
    os.makedirs(out, exist_ok=True)
    hidden = net.gru.hidden_size

    info = {
        "run": args.run, "checkpoint": os.path.basename(ckpt_path),
        "step": st.get("step"), "frames": st.get("frames"),
        "train_hours": round(st.get("train_seconds", 0.0) / 3600, 2), "best_macro_f1": st.get("best_f1"),
        "parameters": count_params(net), "labels": labels, "sensor": sensor, "hidden": hidden, "input": INPUT_SPEC,
        "exported": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    # 1) PyTorch / numpy 가중치
    torch.save({"state_dict": net.state_dict(), **info}, os.path.join(out, "posture_net.pt"))
    np.savez(os.path.join(out, "weights.npz"), **{k: v.detach().numpy() for k, v in net.state_dict().items()})

    # 2) ONNX (한 프레임 스트리밍)
    onnx_note = "posture_net.onnx"
    try:
        x = torch.zeros(1, 2, sensor["height"], sensor["width"])
        h = torch.zeros(1, 1, hidden)
        torch.onnx.export(StepModel(net), (x, h), os.path.join(out, "posture_net.onnx"),
                          input_names=["x", "h"], output_names=["logits", "h_out"], opset_version=17, dynamo=False)
    except Exception as e:  # onnx 패키지가 없거나 내보내기 미지원
        onnx_note = f"ONNX 내보내기 실패: {type(e).__name__}: {e}"[:300]
        print(onnx_note)

    # 2b) 보드 이식용 평탄 가중치 + 골든 입출력 (C++ 구현은 tools/posture_net_ref.cpp 로 맞춰 본다)
    info["bin"] = {"file": "posture_net.bin", "magic": BIN_MAGIC.decode(), "header": "int32 H, W, classes, hidden",
                   "dtype": "float32 little-endian", "tensors": write_bin(net, os.path.join(out, "posture_net.bin"),
                                                                           sensor, len(labels), hidden)}
    write_golden(net, os.path.join(out, "golden.txt"), sensor, len(labels), hidden)

    with open(os.path.join(out, "model_card.json"), "w", encoding="utf-8") as fp:
        json.dump(info, fp, ensure_ascii=False, indent=1)

    # 3) 채점과 보고서
    report = [f"# 자세 분류 모델 — {args.run} ({args.which})", "",
              f"- 내보낸 시각: {info['exported']}",
              f"- 체크포인트: `{info['checkpoint']}` · step {info['step']} · 학습 프레임 {info['frames']:,} · "
              f"학습 {info['train_hours']} 시간",
              f"- 파라미터 {info['parameters']:,}개 · 센서 {sensor.get('name')} {sensor['width']}×{sensor['height']} · "
              f"GRU 은닉 {hidden}",
              f"- 학습 중 최고 검증 macro-F1: {info['best_macro_f1']}",
              f"- ONNX: {onnx_note}",
              "- 보드 이식: `posture_net.bin`(평탄 가중치) + `golden.txt`(검증 입출력) → `tools/posture_net_ref.cpp`", "",
              "> 시뮬 검증셋 점수는 실제 성능이 아니다. 실측 데이터로 다시 확인해야 한다.", ""]
    if not args.no_eval:
        val_path = args.valset or find_valset(sensor.get("name", "vl53l8cx"))
        if val_path and os.path.exists(val_path):
            ev = evaluate(net, load_valset(val_path))
            report += [f"## 시뮬 검증셋 `{os.path.basename(val_path)}`", "",
                       f"정확도 **{ev['accuracy']:.3f}** · macro-F1 **{ev['macro_f1']:.3f}** · 채점 프레임 {ev['frames']:,}", "",
                       eval_table(ev), ""]
            with open(os.path.join(out, "eval_sim.json"), "w", encoding="utf-8") as fp:
                json.dump(ev, fp, ensure_ascii=False, indent=1)
            print(f"시뮬 검증셋: 정확도 {ev['accuracy']:.3f}, macro-F1 {ev['macro_f1']:.3f}")
        else:
            report += ["## 시뮬 검증셋", "", "검증셋 파일을 찾지 못해 채점하지 않음 (`--valset` 로 지정).", ""]
        for p in args.real:
            ev = evaluate(net, load_valset(p))
            report += [f"## 실측 `{os.path.basename(p)}`", "",
                       f"정확도 **{ev['accuracy']:.3f}** · macro-F1 **{ev['macro_f1']:.3f}**", "", eval_table(ev), ""]
            print(f"실측 {os.path.basename(p)}: 정확도 {ev['accuracy']:.3f}, macro-F1 {ev['macro_f1']:.3f}")
    hist = history(run_dir)
    if hist:
        report += ["## 학습 중 검증 점수 변화", "", hist, ""]
    report += ["## 입력·출력 형식", "", "\n".join(f"- **{k}**: {v}" for k, v in INPUT_SPEC.items()), "",
               "라벨 순서: " + ", ".join(f"{i} {n}({KO.get(n, n)})" for i, n in enumerate(labels)), ""]
    with open(os.path.join(out, "report.md"), "w", encoding="utf-8") as fp:
        fp.write("\n".join(report))

    if not args.no_zip:
        shutil.make_archive(out, "zip", root_dir=run_dir, base_dir=os.path.basename(out))
        print(f"내보냄: {out}.zip")
    print(f"폴더: {out}")


if __name__ == "__main__":
    main()
