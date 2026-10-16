"""기준 MLP: 8×8 한 프레임(+바른 자세 기준선 대비 변화) → 지시 자세. 규칙과 같은 프레임에서 비교한다.

  python train_mlp.py data/sessions/sim* --folds 4

- 입력 192 = 현재 거리(m) 64 + 기준선 대비 변화(÷300 mm) 64 + 유효 칸 64.
  기준선은 세션의 '기준 자세' 단계(cmd b) 중앙값 — 제품에서 앉자마자 잡는 바른 자세와 같은 역할.
- 정답 = 지시 자세(규칙 판정은 쓰지 않는다). 꾸벅 졸음은 한 프레임으로 정의되지 않는 시간 현상이라 뺀다
  (끄덕임 사이 프레임은 바른 자세와 같다). 졸음은 따로 시간축 판정이 필요하다.
- 검증은 세션(=사람·방·설치) 단위 k-겹. 학습에 쓴 사람은 평가에 안 들어간다.
- 1초(15 프레임) 다수결 평활 결과도 같이 낸다(규칙의 1.5 s 유지와 비슷한 역할).

주의: 시뮬레이션으로 학습·평가한 점수다. 실제 L8 에서의 정확도가 아니다.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tof_logger"))
from tofl.protocol import LABELS, RULE_CLASS  # noqa: E402
from tofl.session import load_session  # noqa: E402

from evaluate import step_elapsed  # noqa: E402

CLASSES = [c for c in LABELS if c != "drowsy"]
FAR_MM = 2000.0


def features(dist: np.ndarray, base: np.ndarray) -> np.ndarray:
    valid = np.isfinite(dist)
    cur = np.where(valid, dist, FAR_MM)
    b = np.where(np.isfinite(base), base, FAR_MM)
    n = len(dist)
    return np.concatenate([cur.reshape(n, -1) / 1000.0,
                           np.clip((cur - b).reshape(n, -1) / 300.0, -3, 3),
                           valid.reshape(n, -1).astype(np.float64)], axis=1).astype(np.float32)


def load(folder: Path, skip: float) -> dict | None:
    meta, data = load_session(folder)
    steps = (meta.get("protocol") or {}).get("steps") or []
    calib = [i for i, s in enumerate(steps) if s.get("cmd") == "b"]
    since = step_elapsed(data)
    if not calib:
        return None
    sel = (data["step"] == calib[0]) & np.isfinite(since) & (since >= skip)
    with np.errstate(all="ignore"):
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            base = np.nanmedian(data["dist"][sel], axis=0)
    X = features(data["dist"], base)
    lab = data["prompt"]
    m = np.array([p in CLASSES for p in lab]) & np.isfinite(since) & (since >= skip)
    y = np.array([CLASSES.index(p) if p in CLASSES else -1 for p in lab])
    return {"name": folder.name, "X": X, "y": y, "mask": m, "rule": data["rule_posture"],
            "prompt": lab}


class MLP:
    def __init__(self, n_in: int, n_hidden: int, n_out: int, seed: int = 0):
        r = np.random.default_rng(seed)
        self.p = {"W1": r.normal(0, np.sqrt(2 / n_in), (n_in, n_hidden)), "b1": np.zeros(n_hidden),
                  "W2": r.normal(0, np.sqrt(1 / n_hidden), (n_hidden, n_out)), "b2": np.zeros(n_out)}

    def forward(self, X):
        h = np.maximum(X @ self.p["W1"] + self.p["b1"], 0)
        z = h @ self.p["W2"] + self.p["b2"]
        z -= z.max(axis=1, keepdims=True)
        e = np.exp(z)
        return h, e / e.sum(axis=1, keepdims=True)

    def fit(self, X, y, epochs=40, lr=2e-3, l2=1e-4, batch=256, seed=0):
        r = np.random.default_rng(seed)
        w = 1.0 / np.maximum(np.bincount(y, minlength=self.p["b2"].size), 1)
        w = w / w[y].mean()
        m = {k: np.zeros_like(v) for k, v in self.p.items()}
        v = {k: np.zeros_like(x) for k, x in self.p.items()}
        t = 0
        for _ in range(epochs):
            order = r.permutation(len(X))
            for s in range(0, len(X), batch):
                idx = order[s:s + batch]
                xb, yb = X[idx], y[idx]
                h, p = self.forward(xb)
                g = p.copy()
                g[np.arange(len(yb)), yb] -= 1
                g *= w[yb][:, None] / len(yb)
                grads = {"W2": h.T @ g + l2 * self.p["W2"], "b2": g.sum(0)}
                dh = (g @ self.p["W2"].T) * (h > 0)
                grads["W1"] = xb.T @ dh + l2 * self.p["W1"]
                grads["b1"] = dh.sum(0)
                t += 1
                for k in self.p:
                    m[k] = 0.9 * m[k] + 0.1 * grads[k]
                    v[k] = 0.999 * v[k] + 0.001 * grads[k] ** 2
                    self.p[k] -= lr * (m[k] / (1 - 0.9 ** t)) / (np.sqrt(v[k] / (1 - 0.999 ** t)) + 1e-8)
        return self

    def predict(self, X):
        return self.forward(X)[1].argmax(axis=1)

    @property
    def n_params(self) -> int:
        return sum(x.size for x in self.p.values())


def smooth(pred: np.ndarray, k: int = 15) -> np.ndarray:
    """과거 k 프레임 다수결(인과적)."""
    out = pred.copy()
    for i in range(len(pred)):
        out[i] = Counter(pred[max(0, i - k + 1):i + 1].tolist()).most_common(1)[0][0]
    return out


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("sessions", nargs="+", type=Path)
    ap.add_argument("--folds", type=int, default=4)
    ap.add_argument("--hidden", type=int, default=32)
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--skip", type=float, default=2.0)
    ap.add_argument("--save", type=Path, help="전체 세션으로 다시 학습해 가중치 저장(.npz)")
    a = ap.parse_args()

    ss = [s for s in (load(p, a.skip) for p in sorted(a.sessions) if (p / "frames.jsonl").exists()) if s]
    if len(ss) < 2:
        print("세션이 2개 이상 필요하다")
        return 1
    folds = np.array_split(np.random.default_rng(0).permutation(len(ss)), min(a.folds, len(ss)))
    conf = {c: Counter() for c in CLASSES}
    cmp = Counter()
    n_params = 0
    for f in folds:
        test = set(f.tolist())
        tr = [ss[i] for i in range(len(ss)) if i not in test]
        X = np.concatenate([s["X"][s["mask"]] for s in tr])
        y = np.concatenate([s["y"][s["mask"]] for s in tr])
        mu, sd = X.mean(0), X.std(0) + 1e-6
        net = MLP(X.shape[1], a.hidden, len(CLASSES)).fit((X - mu) / sd, y, epochs=a.epochs)
        n_params = net.n_params
        for i in test:
            s = ss[i]
            raw = net.predict((s["X"] - mu) / sd)  # 세션 전체를 시간 순서로(평활 위해)
            sm = smooth(raw)
            m = s["mask"]
            for t, p in zip(s["y"][m], sm[m]):
                conf[CLASSES[t]][CLASSES[p]] += 1
            # 규칙과 같은 프레임·같은 범주(규칙 enum 이 있는 자세)에서 비교
            for t, p, rp, ok in zip(s["prompt"], sm, s["rule"], m):
                if not ok or RULE_CLASS.get(t) is None:
                    continue
                cmp["n"] += 1
                cmp["mlp"] += int(CLASSES[p] == t)
                cmp["rule"] += int(rp == RULE_CLASS[t])

    total = sum(sum(c.values()) for c in conf.values())
    right = sum(conf[c][c] for c in CLASSES)
    print(f"MLP 192→{a.hidden}→{len(CLASSES)} (가중치 {n_params}개), 세션 {len(ss)}개 {len(folds)}겹, "
          f"1 s 다수결 평활")
    print(f"  전체 정확도 {right / total * 100:.1f}%  ({total} 프레임, 졸음 제외 {len(CLASSES)}자세)")
    print(f"  규칙과 같은 프레임(규칙 enum 자세 {cmp['n']}프레임): MLP {cmp['mlp'] / cmp['n'] * 100:.1f}% "
          f"vs 규칙 {cmp['rule'] / cmp['n'] * 100:.1f}%")
    print("  지시\\예측       " + "".join(c[:8].rjust(9) for c in CLASSES) + "   재현율")
    for c in CLASSES:
        n = sum(conf[c].values())
        if not n:
            continue
        print("  " + c.ljust(15) + "".join(
            (f"{conf[c][p] / n * 100:8.0f}%" if conf[c][p] else "        .") for p in CLASSES)
            + f"   {conf[c][c] / n * 100:5.1f}%")
    print("\n  ※ 시뮬레이션으로 학습·평가한 점수다. 실제 L8 정확도가 아니다.")

    if a.save:
        X = np.concatenate([s["X"][s["mask"]] for s in ss])
        y = np.concatenate([s["y"][s["mask"]] for s in ss])
        mu, sd = X.mean(0), X.std(0) + 1e-6
        net = MLP(X.shape[1], a.hidden, len(CLASSES)).fit((X - mu) / sd, y, epochs=a.epochs)
        a.save.parent.mkdir(parents=True, exist_ok=True)
        np.savez(a.save, mu=mu, sd=sd, classes=np.array(CLASSES), **net.p)
        print(f"  저장: {a.save}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
