"""세션 폴더 쓰기·읽기.

data/sessions/<user>_<시각>_<tag>/
  meta.json      장비(hello)·프로토콜·통계
  frames.jsonl   프레임마다 한 줄: t_host·seq·ms·d·st(센서 순서 원본)·rule·prompt·phase·step
  events.jsonl   hello·err·보낸 명령

d·st 는 센서 zone 순서 그대로 저장한다. 장면 방향(row 0 = 위)으로 돌리는 것은 읽을 때
meta 의 flip_rows·flip_cols 로 한다 — 방향을 잘못 잡았어도 다시 돌려 쓸 수 있다.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from .protocol import ROOT

VALID_STATUS = (5, 9)        # VL53 target status: 확실히 유효
SEMI_VALID_STATUS = (6, 10)  # 대체로 유효. TofPostureConfig::accept_semi_valid 와 같은 기본값


class SessionWriter:
    def __init__(self, user: str, tag: str, meta: dict, base: Path | None = None,
                 stamp: str | None = None):
        if not user or not all(c.isalnum() or c in "-_" for c in user):
            raise ValueError("user 는 영문·숫자·-·_ 만")
        stamp = stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.folder = (base or ROOT / "data" / "sessions") / f"{user}_{stamp}_{tag}"
        self.folder.mkdir(parents=True, exist_ok=False)
        self.meta = {"user": user, "tag": tag, "created": stamp, "format": 1, **meta}
        self._frames = open(self.folder / "frames.jsonl", "w", encoding="utf-8", newline="\n")
        self._events = open(self.folder / "events.jsonl", "w", encoding="utf-8", newline="\n")
        self.stats = {"frames": 0, "seq_gaps": 0, "missing_frames": 0, "errors": 0,
                      "seq_resets": 0}
        self._last_seq: int | None = None
        self.write_meta()

    def frame(self, obj: dict, t_host: float, prompt: str | None, phase: str | None,
              step: int | None) -> None:
        seq = int(obj["seq"])
        if self._last_seq is not None:
            if seq == self._last_seq + 1:
                pass
            elif seq > self._last_seq:
                self.stats["seq_gaps"] += 1
                self.stats["missing_frames"] += seq - self._last_seq - 1
            else:  # ESP32 재부팅
                self.stats["seq_resets"] += 1
        self._last_seq = seq
        rec = {"t_host": round(t_host, 4), "seq": seq, "ms": obj["ms"], "w": obj["w"],
               "h": obj["h"], "d": obj["d"], "st": obj["st"], "rule": obj["rule"],
               "prompt": prompt, "phase": phase, "step": step}
        self._frames.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.stats["frames"] += 1

    def event(self, kind: str, t_host: float, **data) -> None:
        if kind == "tof_hello":
            self.meta["device"] = {k: v for k, v in data.items() if k not in ("t", "ms")}
            self.write_meta()
        elif kind == "tof_err":
            self.stats["errors"] += 1
        rec = {"t_host": round(t_host, 4), "kind": kind, **data}
        self._events.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
        self._events.flush()

    def flush(self) -> None:
        self._frames.flush()

    def write_meta(self) -> None:
        (self.folder / "meta.json").write_text(
            json.dumps({**self.meta, "stats": self.stats}, ensure_ascii=False, indent=2),
            encoding="utf-8")

    def close(self, **extra) -> None:
        self.meta.update(extra)
        self.meta["closed"] = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._frames.close()
        self._events.close()
        self.write_meta()


def load_session(folder: str | Path, flip_rows: bool | None = None, flip_cols: bool | None = None,
                 accept_semi_valid: bool = True) -> tuple[dict, dict]:
    """(meta, data). data 배열은 프레임 순서이고 거리맵은 장면 방향(row 0 = 위)이다.

    dist     (N, h, w) float32 mm, 무효 zone = nan
    raw_d    (N, h, w) int16 센서 값(-1 = 타깃 없음), 장면 방향
    status   (N, h, w) uint8
    t_host, seq, ms, prompt(object), phase(object), step(int, 없으면 -1),
    rule_posture(object), rule_conf, rule_present, rule_baseline
    """
    folder = Path(folder)
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    device = meta.get("device") or {}
    fr = device.get("flip_rows", False) if flip_rows is None else flip_rows
    fc = device.get("flip_cols", False) if flip_cols is None else flip_cols
    recs = [json.loads(line) for line in open(folder / "frames.jsonl", encoding="utf-8")
            if line.strip()]
    if not recs:
        raise ValueError(f"프레임 없음: {folder}")
    h, w = recs[0]["h"], recs[0]["w"]
    recs = [r for r in recs if r["h"] == h and r["w"] == w]
    raw = np.array([r["d"] for r in recs], dtype=np.int16).reshape(-1, h, w)
    st = np.array([r["st"] for r in recs], dtype=np.uint8).reshape(-1, h, w)
    if fr:
        raw, st = raw[:, ::-1, :], st[:, ::-1, :]
    if fc:
        raw, st = raw[:, :, ::-1], st[:, :, ::-1]
    ok_status = VALID_STATUS + (SEMI_VALID_STATUS if accept_semi_valid else ())
    valid = np.isin(st, ok_status) & (raw >= 0)
    dist = np.where(valid, raw.astype(np.float32), np.nan).astype(np.float32)
    data = {
        "dist": dist,
        "raw_d": np.ascontiguousarray(raw),
        "status": np.ascontiguousarray(st),
        "t_host": np.array([r["t_host"] for r in recs], dtype=np.float64),
        "seq": np.array([r["seq"] for r in recs], dtype=np.int64),
        "ms": np.array([r["ms"] for r in recs], dtype=np.int64),
        "prompt": np.array([r.get("prompt") for r in recs], dtype=object),
        "phase": np.array([r.get("phase") for r in recs], dtype=object),
        "step": np.array([-1 if r.get("step") is None else r["step"] for r in recs],
                         dtype=np.int32),
        "rule_posture": np.array([r["rule"].get("posture") for r in recs], dtype=object),
        "rule_conf": np.array([r["rule"].get("conf") or 0.0 for r in recs], dtype=np.float32),
        "rule_present": np.array([bool(r["rule"].get("present")) for r in recs]),
        "rule_baseline": np.array([bool(r["rule"].get("baseline")) for r in recs]),
    }
    meta["orientation_used"] = {"flip_rows": bool(fr), "flip_cols": bool(fc)}
    return meta, data


def export_npz(folder: str | Path, out: str | Path, **kw) -> Path:
    meta, data = load_session(folder, **kw)
    arrays = {k: (v.astype(str) if v.dtype == object else v) for k, v in data.items()}
    out = Path(out)
    np.savez_compressed(out, meta=json.dumps(meta, ensure_ascii=False), **arrays)
    return out


def now() -> float:
    return time.time()
