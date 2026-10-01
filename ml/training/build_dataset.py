"""세션 단위 분할 뒤 학습 창만 증강해 재현 가능한 NPZ와 manifest를 만든다."""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from ml.training import augment, frames, synth  # noqa: E402


def number_list(text: str) -> list[int]:
    values = []
    for part in text.split(","):
        if "-" in part:
            low, high = map(int, part.split("-", 1))
            values.extend(range(low, high + 1))
        else:
            values.append(int(part))
    return values


def split_sessions(ids: list[str], *, eval_fraction: float, seed: int) -> tuple[set[str], set[str]]:
    if not 0 <= eval_fraction < 1:
        raise ValueError("eval_fraction 은 [0,1)이어야 합니다")
    order = np.random.default_rng(seed).permutation(sorted(ids))
    count = (min(len(order) - 1, max(1, round(len(order) * eval_fraction)))
             if len(order) > 1 and eval_fraction > 0 else 0)
    return set(order[count:]), set(order[:count])


def _counts(labels: np.ndarray) -> dict[str, int]:
    return {name: int(np.sum(labels == index)) for index, name in enumerate(frames.CLASSES)}


def build_dataset(*, name: str, synthetic: str = "default,short,demo", seeds: str = "1-20",
                  offsets: str = "0,3,7", logs: str | None = None, esm: str | None = None,
                  window: int = 6, stride: int = 1, augment_count: int = 4,
                  eval_fraction: float = 0.25, seed: int = 42,
                  output_dir: Path | None = None) -> dict:
    if not name or not all(char.isalnum() or char in "-_" for char in name):
        raise ValueError("name 은 영문·숫자·-·_만 허용합니다")
    if augment_count < 0:
        raise ValueError("augment 는 음수일 수 없습니다")
    output_dir = output_dir or ROOT / "ml" / "datasets" / name
    output_dir.mkdir(parents=True, exist_ok=True)
    esm_files = sorted(glob.glob(esm, recursive=True)) if esm else []
    esm_records = [record for path in esm_files for record in frames.read_jsonl(path)]
    sessions = []
    material = []
    unknown = Counter()
    for scenario in filter(None, (part.strip() for part in synthetic.split(","))):
        for item_seed in number_list(seeds):
            for offset in number_list(offsets):
                session_id = f"synth-{scenario}-s{item_seed}-o{offset}"
                ticks, phases, raw = synth.generate_session(scenario, item_seed, offset)
                sessions.append((session_id, ticks, phases))
                material.append({"session_id": session_id, "source": "synthetic", "scenario": scenario,
                                 "seed": item_seed, "offset": offset, "sha256": hashlib.sha256(raw).hexdigest(),
                                 "ticks": len(ticks)})
    if logs:
        for frame_path in sorted(Path(logs).rglob("frames-*.jsonl")):
            state_path = frame_path.with_name(frame_path.name.replace("frames-", "state-", 1))
            if not state_path.exists():
                raise FileNotFoundError(f"state 로그 없음: {state_path}")
            raw = frame_path.read_bytes()
            ticks = frames.read_jsonl(frame_path)
            phases = frames.state_phases(frames.read_jsonl(state_path), len(ticks))
            session_id = f"log-{hashlib.sha256(str(frame_path).encode()).hexdigest()[:12]}"
            sessions.append((session_id, ticks, phases))
            material.append({"session_id": session_id, "source": "log", "frames": str(frame_path),
                             "state": str(state_path), "sha256": hashlib.sha256(raw).hexdigest(),
                             "state_sha256": hashlib.sha256(state_path.read_bytes()).hexdigest(), "ticks": len(ticks)})
    if not sessions:
        raise ValueError("합성 시나리오 또는 로그가 하나 이상 필요합니다")
    train_ids, test_ids = split_sessions([session[0] for session in sessions],
                                         eval_fraction=eval_fraction, seed=seed)
    train_x, train_y, test_x, test_y, train_meta, test_meta = [], [], [], [], [], []
    for session_id, ticks, phases in sessions:
        labels, sources, missing = frames.label_ticks(ticks, phases, esm_records)
        unknown.update(missing)
        x, y, meta = frames.windows(ticks, labels, sources, session_id=session_id,
                                    width=window, stride=stride)
        if session_id in train_ids:
            train_x.extend(x); train_y.extend(y); train_meta.extend(meta)
        else:
            test_x.extend(x); test_y.extend(y); test_meta.extend(meta)

    original_train = len(train_y)
    rng = np.random.default_rng(seed)
    augmentation_counts = Counter()
    for index in range(original_train):
        for _ in range(augment_count):
            kinds = [str(kind) for kind in rng.choice(augment.KINDS,
                                                      size=int(rng.integers(1, 4)), replace=False)]
            changed = train_x[index].copy()
            for kind in kinds:
                changed = augment.OPERATIONS[kind](changed, rng)
                augmentation_counts[kind] += 1
            train_x.append(changed)
            train_y.append(train_y[index])
            train_meta.append({**train_meta[index], "augmentation": "+".join(kinds)})

    def array_x(items):
        return np.stack(items).astype(np.float32) if items else np.empty((0, window, len(frames.FEATURES)), np.float32)

    x_train, y_train = array_x(train_x), np.asarray(train_y, dtype=np.int64)
    x_test, y_test = array_x(test_x), np.asarray(test_y, dtype=np.int64)
    meta = [{**item, "split": split} for split, items in (("train", train_meta), ("test", test_meta))
            for item in items]
    np.savez_compressed(output_dir / "windows.npz", X_train=x_train, y_train=y_train,
                        X_test=x_test, y_test=y_test,
                        meta=np.asarray([json.dumps(item, ensure_ascii=False) for item in meta]))
    manifest = {
        "name": name, "created_at": datetime.now(timezone.utc).isoformat(),
        "features": list(frames.FEATURES), "classes": list(frames.CLASSES),
        "window": window, "stride": stride, "seed": seed, "eval_fraction": eval_fraction,
        "sessions": {"total": len(sessions), "train": sorted(train_ids), "test": sorted(test_ids)},
        "materials": material + [{"source": "esm", "path": path,
                                   "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()} for path in esm_files],
        "unknown_phases": dict(unknown),
        "counts": {"train_original": _counts(y_train[:original_train]),
                   "train_augmented": _counts(y_train[original_train:]), "test": _counts(y_test),
                   "label_source": dict(Counter(item["label_source"] for item in meta))},
        "augmentation": {"per_train_window": augment_count, "counts": dict(augmentation_counts),
                         "parameters": augment.PARAMETERS, "exempt_from_noise": list(augment.EXEMPT)},
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                               encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--synthetic", default="default,short,demo")
    parser.add_argument("--seeds", default="1-20")
    parser.add_argument("--offsets", default="0,3,7")
    parser.add_argument("--logs")
    parser.add_argument("--esm")
    parser.add_argument("--window", type=int, default=6)
    parser.add_argument("--stride", type=int, default=1)
    parser.add_argument("--augment", type=int, default=4)
    parser.add_argument("--eval-fraction", type=float, default=0.25)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)
    manifest = build_dataset(name=args.name, synthetic=args.synthetic, seeds=args.seeds,
                             offsets=args.offsets, logs=args.logs, esm=args.esm,
                             window=args.window, stride=args.stride, augment_count=args.augment,
                             eval_fraction=args.eval_fraction, seed=args.seed)
    print(json.dumps({"sessions": manifest["sessions"]["total"], "counts": manifest["counts"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
