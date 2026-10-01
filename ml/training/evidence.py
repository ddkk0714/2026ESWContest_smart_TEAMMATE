"""합성 창과 증강본의 분포·기준 분류기 결과를 Markdown 증거로 남긴다."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from ml.training.frames import CLASSES, FEATURES  # noqa: E402


def ks_statistic(first: np.ndarray, second: np.ndarray) -> float | None:
    if first.size == 0 or second.size == 0:
        return None
    a, b = np.sort(first.ravel()), np.sort(second.ravel())
    points = np.unique(np.concatenate((a, b)))
    return float(np.max(np.abs(np.searchsorted(a, points, side="right") / len(a) -
                               np.searchsorted(b, points, side="right") / len(b))))


def summarize_windows(windows: np.ndarray) -> np.ndarray:
    return np.concatenate((windows.mean(axis=1), windows.std(axis=1)), axis=1)


def _standardize(train: np.ndarray, test: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mean, std = train.mean(axis=0), train.std(axis=0)
    std = np.where(std < 1e-6, 1, std)
    return (train - mean) / std, (test - mean) / std


def predict_centroid(train: np.ndarray, labels: np.ndarray, test: np.ndarray) -> np.ndarray:
    train, test = _standardize(train, test)
    present = np.unique(labels)
    centers = np.stack([train[labels == label].mean(axis=0) for label in present])
    distances = ((test[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
    return present[distances.argmin(axis=1)]


def predict_logistic(train: np.ndarray, labels: np.ndarray, test: np.ndarray,
                     iterations: int = 120) -> np.ndarray:
    train, test = _standardize(train, test)
    present = np.unique(labels)
    weights = np.zeros((train.shape[1], len(present)), dtype=np.float64)
    bias = np.zeros(len(present), dtype=np.float64)
    targets = (labels[:, None] == present[None, :]).astype(np.float64)
    for _ in range(iterations):
        logits = train @ weights + bias
        logits -= logits.max(axis=1, keepdims=True)
        probabilities = np.exp(logits)
        probabilities /= probabilities.sum(axis=1, keepdims=True)
        error = probabilities - targets
        weights -= 0.1 * (train.T @ error / len(train))
        bias -= 0.1 * error.mean(axis=0)
    return present[(test @ weights + bias).argmax(axis=1)]


def classification_metrics(truth: np.ndarray, prediction: np.ndarray) -> dict:
    confusion = np.zeros((len(CLASSES), len(CLASSES)), dtype=int)
    for actual, predicted in zip(truth, prediction):
        confusion[int(actual), int(predicted)] += 1
    recalls = {name: float(confusion[i, i] / confusion[i].sum()) if confusion[i].sum() else None
               for i, name in enumerate(CLASSES)}
    return {"accuracy": float(np.mean(truth == prediction)) if len(truth) else None,
            "recall": recalls, "confusion": confusion.tolist()}


def evaluate(dataset_dir: str | Path) -> tuple[dict, dict]:
    directory = Path(dataset_dir)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    with np.load(directory / "windows.npz", allow_pickle=False) as data:
        x_train, y_train = data["X_train"], data["y_train"]
        x_test, y_test = data["X_test"], data["y_test"]
        meta = [json.loads(item) for item in data["meta"]]
    originals = sum(manifest["counts"]["train_original"].values())
    if originals == 0 or len(x_test) == 0:
        raise ValueError("기준 분류기에는 학습·평가 창이 모두 필요합니다")
    original, augmented = x_train[:originals], x_train[originals:]
    distribution = []
    for index, name in enumerate(FEATURES):
        first, second = original[:, :, index], augmented[:, :, index]
        distribution.append((name, float(first.mean()), float(first.std()),
                             float(second.mean()) if second.size else None,
                             float(second.std()) if second.size else None,
                             ks_statistic(first, second)))
    test_summary = summarize_windows(x_test)
    classifiers = {}
    for method, predictor in (("nearest_centroid", predict_centroid), ("softmax_logistic", predict_logistic)):
        for variant, count in (("original", originals), ("augmented", len(x_train))):
            prediction = predictor(summarize_windows(x_train[:count]), y_train[:count], test_summary)
            classifiers[f"{method}_{variant}"] = classification_metrics(y_test, prediction)
    return manifest, {"distribution": distribution, "classifiers": classifiers,
                      "label_source": {source: sum(item["label_source"] == source for item in meta)
                                       for source in ("fsm", "esm")}}


def generate_evidence(dataset_dir: str | Path, out: str | Path) -> dict:
    manifest, analysis = evaluate(dataset_dir)
    counts = manifest["counts"]
    lines = ["# DESKMATE 2단계 학습 창 증강 증거", "",
             "**합성 데이터 기준, 실사용 성능 아님.** FSM phase의 약한 라벨로 만든 개념 실증이다.", "",
             "## 데이터 규모", "",
             "| 항목 | 학습 | 평가 | 전체 |", "|---|---:|---:|---:|",
             f"| 세션 | {len(manifest['sessions']['train'])} | {len(manifest['sessions']['test'])} | {manifest['sessions']['total']} |",
             f"| 원본 창 | {sum(counts['train_original'].values())} | {sum(counts['test'].values())} | {sum(counts['train_original'].values()) + sum(counts['test'].values())} |",
             f"| 증강 창 | {sum(counts['train_augmented'].values())} | 0 | {sum(counts['train_augmented'].values())} |", "",
             "| 라벨 출처 | 창 수 |", "|---|---:|",
             f"| FSM | {analysis['label_source']['fsm']} |",
             f"| ESM | {analysis['label_source']['esm']} |", "",
             "| 클래스 | 원본 학습 | 증강 학습 | 평가 |", "|---|---:|---:|---:|"]
    for name in CLASSES:
        lines.append(f"| {name} | {counts['train_original'][name]} | {counts['train_augmented'][name]} | {counts['test'][name]} |")
    lines += ["", "## 증강 방법·파라미터", "", "| 방법 | 적용 수 | 파라미터 |", "|---|---:|---|"]
    for name, params in manifest["augmentation"]["parameters"].items():
        lines.append(f"| {name} | {manifest['augmentation']['counts'].get(name, 0)} | "
                     f"{json.dumps(params, ensure_ascii=False)} |")
    lines += ["", "## 분포 비교", "",
              "원본 학습 창과 증강 창의 모든 tick을 비교했다. KS는 numpy 경험적 누적분포의 최대 차이다.", "",
              "| 특징 | 원본 평균 | 원본 표준편차 | 증강 평균 | 증강 표준편차 | 2표본 KS |",
              "|---|---:|---:|---:|---:|---:|"]
    for name, am, ast, bm, bst, ks in analysis["distribution"]:
        fmt = lambda x: "—" if x is None else f"{x:.4f}"  # noqa: E731
        lines.append(f"| {name} | {fmt(am)} | {fmt(ast)} | {fmt(bm)} | {fmt(bst)} | {fmt(ks)} |")
    lines += ["", "## 기준 분류기 sanity check", "",
              "창별 평균·표준편차(34차원)를 입력으로 쓴다. 최근접 중심과 소프트맥스 로지스틱 회귀(경사하강)를 원본만/원본+증강으로 학습하고 동일한 세션 분리 평가 창에서 비교했다.", "",
              "| 방법 | 학습 | 정확도 | focus 재현율 | fatigue 재현율 | rest 재현율 | idle 재현율 |",
              "|---|---|---:|---:|---:|---:|---:|"]
    for key, result in analysis["classifiers"].items():
        method, variant = key.rsplit("_", 1)
        fmt = lambda x: "—" if x is None else f"{x:.3f}"  # noqa: E731
        lines.append(f"| {method} | {variant} | {fmt(result['accuracy'])} | " +
                     " | ".join(fmt(result["recall"][name]) for name in CLASSES) + " |")
    lines += ["", "혼동행렬(행=실제, 열=예측; 순서: focus, fatigue, rest, idle):", ""]
    for key, result in analysis["classifiers"].items():
        lines += [f"- `{key}`: `{result['confusion']}`"]
    original_accuracy = analysis["classifiers"]["softmax_logistic_original"]["accuracy"]
    augmented_accuracy = analysis["classifiers"]["softmax_logistic_augmented"]["accuracy"]
    rest_recall = analysis["classifiers"]["softmax_logistic_augmented"]["recall"]["rest"]
    rest_text = "평가 표본 없음" if rest_recall is None else f"{rest_recall:.3f}"
    lines += ["", "## 한계·다음 단계", "",
              "시나리오와 FSM에서 유래한 약한 라벨이므로 결과가 높아도 실사용 성능을 뜻하지 않는다. 시드·offset 변형은 독립적인 사람이나 환경을 대체하지 못한다."
              f" 이번 기준에서 소프트맥스 정확도는 원본 {original_accuracy:.3f}, 증강 {augmented_accuracy:.3f}이고"
              f" 증강 후 rest 재현율은 {rest_text}이다."
              " ESM 정정 라벨을 실제 세션에서 축적하고 사람·시간 단위로 재평가해야 한다. 1D CNN 학습과 TFLite 변환은 조명희 님의 후속 범위다.", "",
              f"재현: `python ml/training/build_dataset.py --name {manifest['name']} --synthetic default,short,demo --seeds 1-20 --offsets 0,3,7 --augment 4`", "",
              f"특징 순서: `{', '.join(FEATURES)}`", ""]
    Path(out).write_text("\n".join(lines), encoding="utf-8")
    return analysis


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    analysis = generate_evidence(args.dataset_dir, args.out)
    print(json.dumps({key: value["accuracy"] for key, value in analysis["classifiers"].items()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
