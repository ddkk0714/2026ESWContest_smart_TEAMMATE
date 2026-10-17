"""train_mlp.py 가 저장한 .npz → Pi 4 허브용 JSON 가중치 (hub/deskmate_hub/posture/tof_mlp.py 형식).

  python export_mlp_json.py models/mlp_sim60.npz ../../hub/deskmate_hub/config/tof_mlp.json
  python export_mlp_json.py models/mlp_real.npz out.json --grid 16x8      # 실측 16×8 모델이면 격자를 적는다

2026-10-17 결정: 자세 MLP 추론은 ESP32 가 아니라 Pi 4 에서 한다. Pi 4 제한 Python 에는 numpy 가 없어 가중치를
JSON 리스트로 넘기고 순수 Python 으로 곱한다. 변환이 끝나면 같은 입력에 대해 numpy 순전파와 허브 순전파가
일치하는지 바로 비교해 최대 차이를 출력한다(tests/test_export_mlp_json.py 도 같은 검사를 한다).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
HUB = HERE.parent.parent / "hub"
FORMAT = "deskmate-tof-mlp-1"
FEATURES = {"far_mm": 2000.0, "dist_scale_mm": 1000.0, "delta_scale_mm": 300.0, "delta_clip": 3.0,
            "valid_status": [5, 9], "semi_valid_status": [6, 10]}  # train_mlp.features · tofl.session 과 같다


def _round(v: float, digits: int) -> float:
    return float(f"{float(v):.{digits}g}")


def npz_to_spec(npz, *, source: str, grid: tuple[int, int] | None, digits: int = 7) -> dict:
    W1, b1, W2, b2 = npz["W1"], npz["b1"], npz["W2"], npz["b2"]
    mu, sd = npz["mu"], npz["sd"]
    n_in, n_hidden = W1.shape
    n_out = W2.shape[1]
    if n_in % 3:
        raise ValueError(f"n_in {n_in} 은 3×zone 이어야 한다")
    zones = n_in // 3
    if grid is None:
        side = int(math.isqrt(zones))
        if side * side != zones:
            raise ValueError(f"zone {zones} 가 정사각이 아니다. --grid WxH 를 주어야 한다")
        grid = (side, side)
    if grid[0] * grid[1] != zones:
        raise ValueError(f"--grid {grid} ≠ zone {zones}")
    rows = lambda a: [[_round(v, digits) for v in r] for r in np.asarray(a, dtype=np.float64)]  # noqa: E731
    vec = lambda a: [_round(v, digits) for v in np.asarray(a, dtype=np.float64).ravel()]  # noqa: E731
    return {
        "format": FORMAT, "source": source,
        "grid": {"width": grid[0], "height": grid[1]},
        "features": FEATURES,
        "classes": [str(c) for c in npz["classes"]],
        "n_in": int(n_in), "n_hidden": int(n_hidden), "n_out": int(n_out),
        "mu": vec(mu), "sd": vec(sd),
        "W1": rows(W1), "b1": vec(b1), "W2": rows(W2), "b2": vec(b2),
    }


def numpy_forward(npz, X: np.ndarray) -> np.ndarray:
    """train_mlp.MLP.forward 와 같은 식(표준화 포함)."""
    Z = (X - npz["mu"]) / npz["sd"]
    h = np.maximum(Z @ npz["W1"] + npz["b1"], 0)
    z = h @ npz["W2"] + npz["b2"]
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def compare(npz, spec: dict, n: int = 64, seed: int = 0) -> float:
    """무작위 표준화-전 입력 n 개에서 numpy ↔ 허브 순전파 확률의 최대 절대 차이."""
    sys.path.insert(0, str(HUB))
    from deskmate_hub.posture.tof_mlp import TofMlpModel  # noqa: E402

    model = TofMlpModel(spec)
    r = np.random.default_rng(seed)
    X = (npz["mu"] + npz["sd"] * r.normal(size=(n, spec["n_in"]))).astype(np.float32)
    ref = numpy_forward(npz, X)
    worst = 0.0
    for i in range(n):
        p = model.forward([float(v) for v in X[i]])
        worst = max(worst, float(np.abs(np.asarray(p) - ref[i]).max()))
        if int(np.argmax(p)) != int(ref[i].argmax()):
            raise AssertionError(f"argmax 불일치 (표본 {i})")
    return worst


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("npz", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--grid", help="WxH (zone 수가 정사각이 아닐 때 필수, 예: 16x8)")
    ap.add_argument("--digits", type=int, default=7, help="유효 숫자(기본 7 = float32 정밀도)")
    a = ap.parse_args()
    grid = tuple(int(v) for v in a.grid.lower().split("x")) if a.grid else None
    npz = np.load(a.npz)
    try:
        source = str(a.npz.resolve().relative_to(HERE.parent.parent)).replace("\\", "/")
    except ValueError:
        source = a.npz.name
    spec = npz_to_spec(npz, source=source, grid=grid, digits=a.digits)
    worst = compare(npz, spec)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(spec, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{a.out}: {spec['n_in']}→{spec['n_hidden']}→{spec['n_out']}, 격자 {spec['grid']['width']}x"
          f"{spec['grid']['height']}, {a.out.stat().st_size / 1024:.0f} KB, numpy 대비 최대 차이 {worst:.2e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
