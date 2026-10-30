"""공용 피로 모델 학습 — 사람(프로필) 단위로 빼고 검증(LOSO)한 뒤 전체로 학습해 JSON 으로 내보낸다.

    python -m ml.training.fatigue_train --raw ml/datasets/fatigue/raw --out ml/models/fatigue/fatigue_linear.json
    python -m ml.training.fatigue_train --synthetic --out ml/models/fatigue/synth.json   # 파이프라인 확인용

모델은 표준화 + 릿지 회귀(KSS 1~9). 데이터가 수백 개라 이보다 큰 모델은 사람 단위 검증에서 이기기 어렵다.
같은 사람이 학습·평가에 함께 들어가면 점수가 부풀려지므로 평가는 항상 '처음 보는 사람' 기준이다.

리포트에 함께 싣는 비교:
  mean      학습 데이터 KSS 평균 하나로 찍기
  start     세션 시작 때 답한 KSS 를 그대로 유지
  model     공용 모델
  personal  공용 모델 + 프로필 보정(그 사람의 앞선 응답만 사용, Pi 4 개인 head 1단계와 같은 규칙)
모델이 mean·start 를 못 이기면 센서가 쓸모 있다는 근거가 없다는 뜻이다.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import warnings
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hub"))
from deskmate_hub.fatigue.features import BASELINE_SEC, FEATURES, WINDOW_SEC  # noqa: E402
from deskmate_hub.fatigue.model import FORMAT, FatigueModel, PersonalBias  # noqa: E402

from .fatigue_dataset import build  # noqa: E402

ALPHAS = (0.3, 1.0, 3.0, 10.0, 30.0, 100.0)
FATIGUED_KSS = 7  # KSS 7 이상 = '졸림' (AUC 기준)
KSS_START = FEATURES.index("kss_start")


def fit(x: np.ndarray, y: np.ndarray, alpha: float) -> dict:
    with warnings.catch_warnings():  # 한 번도 측정 안 된 특징(전부 nan)은 아래에서 0·1 로 둔다
        warnings.simplefilter("ignore", RuntimeWarning)
        mu = np.nanmean(x, axis=0)
        sd = np.nanstd(x, axis=0)
    mu = np.where(np.isfinite(mu), mu, 0.0)
    sd = np.where(np.isfinite(sd) & (sd > 1e-9), sd, 1.0)
    z = np.nan_to_num((x - mu) / sd, nan=0.0)
    ym = float(y.mean())
    coef = np.linalg.solve(z.T @ z + alpha * np.eye(z.shape[1]), z.T @ (y - ym))
    return {"mu": mu, "sd": sd, "coef": coef, "intercept": ym, "alpha": alpha}


def predict(m: dict, x: np.ndarray) -> np.ndarray:
    z = np.nan_to_num((x - m["mu"]) / m["sd"], nan=0.0)
    return np.clip(z @ m["coef"] + m["intercept"], 1.0, 9.0)


def spearman(a: np.ndarray, b: np.ndarray) -> float | None:
    if len(a) < 3:
        return None
    ra, rb = _rank(a), _rank(b)
    if ra.std() == 0 or rb.std() == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def _rank(v: np.ndarray) -> np.ndarray:
    order = np.argsort(v, kind="mergesort")
    ranks = np.empty(len(v))
    ranks[order] = np.arange(len(v))
    for value in np.unique(v):  # 동점은 평균 순위
        idx = v == value
        ranks[idx] = ranks[idx].mean()
    return ranks


def auc(score: np.ndarray, positive: np.ndarray) -> float | None:
    pos, neg = score[positive], score[~positive]
    if len(pos) == 0 or len(neg) == 0:
        return None
    greater = (pos[:, None] > neg[None, :]).sum() + 0.5 * (pos[:, None] == neg[None, :]).sum()
    return float(greater / (len(pos) * len(neg)))


def _pick_alpha(x, y, groups, train_mask) -> float:
    profiles = sorted(set(groups[train_mask]))
    if len(profiles) < 3:
        return 10.0
    best, best_err = 10.0, np.inf
    for alpha in ALPHAS:
        errs = []
        for p in profiles:
            tr = train_mask & (groups != p)
            te = train_mask & (groups == p)
            errs.append(np.abs(predict(fit(x[tr], y[tr], alpha), x[te]) - y[te]))
        err = float(np.mean(np.concatenate(errs)))
        if err < best_err:
            best, best_err = alpha, err
    return best


def loso(x: np.ndarray, y: np.ndarray, meta: list[dict]) -> dict:
    groups = np.array([m["profile"] for m in meta])
    is_label = np.array([m["kind"] == "label" for m in meta])
    ts = np.array([m["ts"] for m in meta])
    preds = {k: np.full(len(y), np.nan) for k in ("mean", "start", "model", "personal")}
    alphas = {}
    for p in sorted(set(groups)):
        train = groups != p
        test = (groups == p) & is_label
        if not test.any() or not (train & is_label).any():
            continue
        alpha = _pick_alpha(x, y, groups, train)
        alphas[p] = alpha
        m = fit(x[train], y[train], alpha)
        preds["mean"][test] = y[train & is_label].mean()
        start = x[test, KSS_START]
        preds["start"][test] = np.where(np.isfinite(start), start, y[train & is_label].mean())
        preds["model"][test] = predict(m, x[test])
        bias = PersonalBias()
        for i in np.where(test)[0][np.argsort(ts[test], kind="mergesort")]:
            preds["personal"][i] = bias.apply(preds["model"][i])
            bias.update(y[i], preds["model"][i])
    used = is_label & np.isfinite(preds["model"])
    truth = y[used]
    report = {"labels": int(used.sum()), "profiles": int(len(alphas)), "alpha_by_profile": alphas, "methods": {}}
    for name, pred in preds.items():
        p = pred[used]
        report["methods"][name] = {
            "mae": float(np.mean(np.abs(p - truth))) if len(p) else None,
            "spearman": spearman(p, truth),
            "auc_kss7": auc(p, truth >= FATIGUED_KSS),
        }
    return report


def export(x: np.ndarray, y: np.ndarray, meta: list[dict], report: dict, path: Path) -> dict:
    alpha = _pick_alpha(x, y, np.array([m["profile"] for m in meta]), np.ones(len(y), bool))
    m = fit(x, y, alpha)
    spec = {
        "format": FORMAT, "features": list(FEATURES), "window_sec": WINDOW_SEC, "baseline_sec": BASELINE_SEC,
        "mu": m["mu"].tolist(), "sd": m["sd"].tolist(), "coef": m["coef"].tolist(),
        "intercept": m["intercept"], "alpha": alpha,
        "trained_on": {"windows": len(y), "labels": sum(mm["kind"] == "label" for mm in meta),
                       "profiles": len({mm["profile"] for mm in meta}),
                       "sessions": len({mm["session"] for mm in meta})},
        "loso": report,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    # 내보낸 JSON 을 기기 쪽 코드로 다시 읽어 numpy 예측과 같은지 확인한다.
    runtime = FatigueModel.load(str(path))
    for row, expect in zip(x[:50], predict(m, x[:50])):
        got = runtime.predict({k: None if np.isnan(v) else float(v) for k, v in zip(FEATURES, row)})
        if abs(got - expect) > 1e-6:
            raise RuntimeError(f"export mismatch {got} != {expect}")
    return spec


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--raw", help="fatigue_logger 저장 폴더(<profile>/<session>.jsonl)")
    src.add_argument("--synthetic", action="store_true", help="합성 세션으로 파이프라인만 확인")
    ap.add_argument("--out", required=True, help="내보낼 모델 JSON")
    ap.add_argument("--interpolate-sec", type=float, default=60.0, help="응답 사이 보간 창 간격(0 = 끔)")
    a = ap.parse_args(argv)
    if a.synthetic:
        from .fatigue_synth import write_synthetic
        raw = write_synthetic(tempfile.mkdtemp(prefix="fatigue-synth-"))
    else:
        raw = Path(a.raw)
    x, y, meta = build(raw, interpolate_sec=a.interpolate_sec or None)
    if not any(m["kind"] == "label" for m in meta):
        print("응답이 달린 창이 없습니다 — 수집 세션을 확인하세요.", file=sys.stderr)
        return 1
    report = loso(x, y, meta)
    spec = export(x, y, meta, report, Path(a.out))
    print(json.dumps({"trained_on": spec["trained_on"], "alpha": spec["alpha"], "loso": report["methods"]},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
