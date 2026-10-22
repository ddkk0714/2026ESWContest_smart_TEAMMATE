"""ToF 원본 8×8 → 자세 MLP 추론 (순수 Python, 표준 라이브러리만). 후보 A.

2026-10-17: 자세 AI 모델은 ESP32 가 아니라 Pi 4 에서 돈다. ESP32 는 VL53L8CX 원본 8×8 을 센서마다 한 프레임씩
(UART 0x05, 같은 frame_id) 넘기고, 여기서 `dataset/tof_sim/train_mlp.py` 가 학습한 MLP(입력 3×zone → 은닉 → 자세)를
돌린다. Pi 4 의 제한 Python(`/restricted/python3`)에는 numpy 가 없으므로 행렬곱을 Python 리스트로 한다
(192→64→10 이면 1.3만 곱셈 — 센서 둘 × 5 Hz 에 충분하다). 가중치는 `export_mlp_json.py` 가 .npz 에서 만든 JSON 이고,
변환 전후 출력 일치는 `dataset/tof_sim/tests/test_export_mlp_json.py` 가 확인한다.

표준(10-17): ToF 의 단위는 "센서 하나의 8×8" 이다. 두 센서는 시야가 대부분 겹치므로 16×8 로 이어 붙이지 않고
**센서마다 따로 추론한 뒤 log-softmax 합으로 융합**한다(`dataset/tof_sim_v5` 의 두 센서 융합과 같은 규칙). 기준선·정지
판정도 센서별이다.

특징(학습 코드 `train_mlp.features` 와 같은 순서·단위, 센서 하나 기준):
    [cur_mm / 1000] × n  ‖  [clip((cur_mm − base_mm) / 300, −3, 3)] × n  ‖  [valid 0/1] × n
    valid = status ∈ {5, 9} (+ {6, 10} accept_semi_valid) 이고 dist ≥ 0.  무효 zone 의 cur·base 는 FAR 2000 mm.
    base = 바른 자세 기준선(zone 별 중앙값). 학습에서는 세션의 '기준 자세' 구간, 제품에서는 앉은 뒤 처음 조용한 구간.

판정(TofMlpPosture): 기준선이 잡힌 센서가 하나도 없으면 unknown. 잡힌 센서들의 확률을 융합해 프레임마다 argmax 를 내고
최근 vote_frames(5 Hz × 1 s)의 다수결로 확정한다. 졸음(drowsy)은 한 프레임으로 정의되지 않는 시간 현상이라 모델 밖이다 —
끄덕임·졸음은 계속 ESP32 규칙 추정기(0x03 flags)가 낸다.
"""
from __future__ import annotations

import json
import math
import os
import pkgutil
from collections import deque
from operator import mul
from typing import Any, Callable

FORMAT = "deskmate-tof-mlp-1"
UNKNOWN = "unknown"
# 학습 코드와 같은 기본값 (train_mlp.py FAR_MM, features / tofl.session VALID_STATUS·SEMI_VALID_STATUS)
DEFAULT_FEATURES = {"far_mm": 2000.0, "dist_scale_mm": 1000.0, "delta_scale_mm": 300.0, "delta_clip": 3.0,
                    "valid_status": [5, 9], "semi_valid_status": [6, 10]}


class TofMlpModel:
    """JSON 가중치 하나 = 모델 하나(센서 한 개의 격자 입력). `features()` → `forward()`."""

    def __init__(self, spec: dict[str, Any]) -> None:
        if spec.get("format") != FORMAT:
            raise ValueError(f"tof_mlp: 모르는 형식 {spec.get('format')!r}")
        self.classes: list[str] = [str(c) for c in spec["classes"]]
        self.n_in, self.n_hidden, self.n_out = int(spec["n_in"]), int(spec["n_hidden"]), int(spec["n_out"])
        self.mu = [float(v) for v in spec["mu"]]
        self.sd = [float(v) for v in spec["sd"]]
        w1 = spec["W1"]                       # n_in × n_hidden (numpy 저장 모양 그대로)
        w2 = spec["W2"]                       # n_hidden × n_out
        if (len(self.mu) != self.n_in or len(self.sd) != self.n_in or len(w1) != self.n_in
                or any(len(r) != self.n_hidden for r in w1) or len(w2) != self.n_hidden
                or any(len(r) != self.n_out for r in w2) or len(self.classes) != self.n_out
                or self.n_in % 3 != 0):
            raise ValueError("tof_mlp: 가중치 모양이 맞지 않는다")
        # 행렬곱은 내적 합으로 한다 — 은닉 j 마다 입력 전체와 곱하도록 전치해 둔다
        self.w1t = [[float(w1[i][j]) for i in range(self.n_in)] for j in range(self.n_hidden)]
        self.b1 = [float(v) for v in spec["b1"]]
        self.w2t = [[float(w2[j][k]) for j in range(self.n_hidden)] for k in range(self.n_out)]
        self.b2 = [float(v) for v in spec["b2"]]
        if len(self.b1) != self.n_hidden or len(self.b2) != self.n_out:
            raise ValueError("tof_mlp: 편향 길이가 맞지 않는다")
        for row in (self.mu, self.sd, self.b1, self.b2, *self.w1t, *self.w2t):
            if any(not math.isfinite(v) for v in row):
                raise ValueError("tof_mlp: 가중치에 유한하지 않은 값")
        f = dict(DEFAULT_FEATURES, **(spec.get("features") or {}))
        self.far_mm = float(f["far_mm"])
        self.dist_scale = float(f["dist_scale_mm"])
        self.delta_scale = float(f["delta_scale_mm"])
        self.delta_clip = float(f["delta_clip"])
        self.valid_status = frozenset(int(s) for s in f["valid_status"])
        self.semi_valid_status = frozenset(int(s) for s in f["semi_valid_status"])
        grid = spec.get("grid") or {}
        self.grid_width = int(grid.get("width") or 0)
        self.grid_height = int(grid.get("height") or 0)
        self.source = str(spec.get("source") or "")

    @property
    def zones(self) -> int:
        return self.n_in // 3

    # ---- 특징 ----
    def valid_mask(self, distance_mm: list[int], status: list[int], accept_semi_valid: bool = True) -> list[bool]:
        ok = self.valid_status | (self.semi_valid_status if accept_semi_valid else frozenset())
        return [s in ok and d >= 0 for d, s in zip(distance_mm, status)]

    def features(self, distance_mm: list[int], status: list[int], baseline_mm: list[float | None] | None,
                 accept_semi_valid: bool = True) -> list[float]:
        """train_mlp.features 와 같은 192(=3×zone) 벡터. baseline 이 None 이면 전부 FAR."""
        n = self.zones
        if len(distance_mm) != n or len(status) != n:
            raise ValueError(f"tof_mlp: zone 수 {len(distance_mm)} ≠ 모델 {n}")
        valid = self.valid_mask(distance_mm, status, accept_semi_valid)
        cur = [float(d) if ok else self.far_mm for d, ok in zip(distance_mm, valid)]
        if baseline_mm is None:
            base = [self.far_mm] * n
        else:
            base = [self.far_mm if b is None else float(b) for b in baseline_mm]
        lo, hi, ds = -self.delta_clip, self.delta_clip, self.delta_scale
        out = [c / self.dist_scale for c in cur]
        out.extend(min(hi, max(lo, (c - b) / ds)) for c, b in zip(cur, base))
        out.extend(1.0 if ok else 0.0 for ok in valid)
        return out

    # ---- 순전파 ----
    def forward(self, x: list[float]) -> list[float]:
        """표준화 → ReLU 은닉 → softmax 확률(클래스 순서 = self.classes)."""
        z = [(v - m) / s for v, m, s in zip(x, self.mu, self.sd)]
        h = [max(0.0, sum(map(mul, row, z)) + b) for row, b in zip(self.w1t, self.b1)]
        logits = [sum(map(mul, row, h)) + b for row, b in zip(self.w2t, self.b2)]
        top = max(logits)
        e = [math.exp(v - top) for v in logits]
        total = sum(e)
        return [v / total for v in e]

    def predict(self, x: list[float]) -> tuple[int, list[float]]:
        p = self.forward(x)
        return max(range(len(p)), key=p.__getitem__), p


def fuse_log_softmax(prob_lists: list[list[float]]) -> list[float]:
    """센서별 확률 → log 합 → 다시 softmax. (tof_sim_v5 의 두 센서 융합 규칙과 같다.)"""
    logs = [sum(math.log(max(p[k], 1e-12)) for p in prob_lists) for k in range(len(prob_lists[0]))]
    top = max(logs)
    e = [math.exp(v - top) for v in logs]
    total = sum(e)
    return [v / total for v in e]


# ---------------------------------------------------------------- 로딩

def load_model(path: str | None = None) -> TofMlpModel:
    """JSON 가중치를 읽는다. path 가 None/상대 파일명이면 패키지 config/ (Atlas zip 안 포함) 에서 찾는다."""
    name = path or "tof_mlp.json"
    raw: bytes | None = None
    if not os.path.isabs(name) and os.sep not in name and "/" not in name:
        try:
            raw = pkgutil.get_data("deskmate_hub", "config/" + name)
        except (OSError, ImportError):
            raw = None
    if raw is None:
        with open(name, "rb") as fh:
            raw = fh.read()
    return TofMlpModel(json.loads(raw.decode("utf-8")))


def _median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else 0.5 * (s[mid - 1] + s[mid])


# ---------------------------------------------------------------- 센서 하나의 상태

class _SensorState:
    """센서 한 개의 기준선 포착·정지 판정. 숫자는 TofMlpPosture 가 ingest.yaml `tof.mlp` 에서 받아 넘긴다."""

    def __init__(self, owner: "TofMlpPosture") -> None:
        self.o = owner
        self.baseline: list[float | None] | None = None
        self.baseline_at: float | None = None
        self._window: deque[tuple[list[float | None], bool]] = deque(maxlen=owner.baseline_frames)
        self._prev: list[float | None] | None = None
        self._away_since: float | None = None
        self.present = False

    def reset(self) -> None:
        self.baseline, self.baseline_at = None, None
        self._window.clear()

    def observe(self, dist: list[int], status: list[int], now: float) -> list[float] | None:
        """프레임 하나 → 기준선이 있으면 클래스 확률, 없으면 None(포착 진행 중)."""
        o = self.o
        valid = o.model.valid_mask(dist, status, o.accept_semi_valid)
        cur: list[float | None] = [float(d) if ok else None for d, ok in zip(dist, valid)]
        person = sum(1 for v in cur if v is not None and o.min_person_mm <= v <= o.max_person_mm)
        self.present = person / len(cur) >= o.present_min_coverage
        still = self._is_still(cur)
        self._prev = cur
        if self.present:
            self._away_since = None
        else:
            if self._away_since is None:
                self._away_since = now
            elif self.baseline is not None and now - self._away_since >= o.away_reset_sec:
                self.reset()
        if self.baseline is None:
            if self.present:
                self._window.append((cur, still))
                if len(self._window) == o.baseline_frames:
                    moving = sum(1 for _, s in self._window if not s)
                    if moving <= o.baseline_max_moving_frac * o.baseline_frames:
                        self.baseline = self._median_grid([c for c, _ in self._window])
                        self.baseline_at = now
                        self._window.clear()
            else:
                self._window.clear()
            if self.baseline is None:
                return None
        return o.model.forward(o.model.features(dist, status, self.baseline, o.accept_semi_valid))

    def mark_absent(self, now: float) -> None:
        """mmWave 가 '사람 없음' 이라고 한 프레임: 격자를 보지 않고 자리비움으로 센다(기준선을 모으지 않는다)."""
        self.present = False
        self._prev = None
        self._window.clear()
        if self._away_since is None:
            self._away_since = now
        elif self.baseline is not None and now - self._away_since >= self.o.away_reset_sec:
            self.reset()

    def _is_still(self, cur: list[float | None]) -> bool:
        if self._prev is None:
            return True
        diffs = [abs(a - b) for a, b in zip(cur, self._prev) if a is not None and b is not None]
        return not diffs or sum(diffs) / len(diffs) <= self.o.still_mm

    @staticmethod
    def _median_grid(frames: list[list[float | None]]) -> list[float | None]:
        out: list[float | None] = []
        for zone in zip(*frames):
            vals = [v for v in zone if v is not None]
            out.append(_median(vals) if vals else None)
        return out


# ---------------------------------------------------------------- 상태 있는 판정기

class TofMlpPosture:
    """센서 프레임 묶음 스트림 → 센서별 기준선·추론 → 융합 → 다수결. 숫자는 전부 ingest.yaml `tof.mlp` 에서 온다.

    기준선(센서별): 사람이 있고(coverage ≥ present_min_coverage) 격자가 조용한(프레임 간 평균 변화 ≤ still_mm) 프레임이
    baseline_frames 만큼 쌓이면 zone 별 중앙값. 창 안에 움직인 프레임이 baseline_max_moving_frac 넘게 섞이면 더 기다린다.
    away_reset_sec 동안 비어 있으면 그 센서의 기준선을 버린다(다른 사람·재착석). `reset_baseline()` 은 전부 버린다.
    """

    def __init__(self, model: TofMlpModel, cfg: dict[str, Any] | None = None) -> None:
        c = cfg or {}
        self.model = model
        self.accept_semi_valid = bool(c.get("accept_semi_valid", True))
        self.present_min_coverage = float(c.get("present_min_coverage", 0.06))
        self.min_person_mm = float(c.get("min_person_mm", 150))
        self.max_person_mm = float(c.get("max_person_mm", 1300))
        self.still_mm = float(c.get("still_mm", 15))
        self.baseline_frames = max(1, int(c.get("baseline_frames", 50)))
        self.baseline_max_moving_frac = float(c.get("baseline_max_moving_frac", 0.2))
        self.away_reset_sec = float(c.get("away_reset_sec", 300))
        self.vote_frames = max(1, int(c.get("vote_frames", 5)))
        self.sensors: dict[int, _SensorState] = {}
        self._votes: deque[tuple[int, float]] = deque(maxlen=self.vote_frames)
        self.frames = 0
        self.last_error: str | None = None

    @property
    def baseline_ready(self) -> bool:
        return any(s.baseline is not None for s in self.sensors.values())

    def reset_baseline(self) -> None:
        for s in self.sensors.values():
            s.reset()
        self._votes.clear()

    # ---- 표본 하나(센서 프레임 묶음) ----
    def update(self, sample: dict[str, Any], now: float, present: bool | None = None) -> dict[str, Any]:
        """`present` 는 mmWave 재실 게이트(ingest `presence.gate_posture`). False 면 모델을 돌리지 않고 unknown,
        None 이면(mmWave 없음·낡음) 격자만으로 판단한다."""
        self.frames += 1
        if present is False:
            for idx in (sample.get("sensors") or {}):
                self.sensors.setdefault(int(idx), _SensorState(self)).mark_absent(now)
            self._votes.clear()
            return self._verdict(UNKNOWN, 0.0, present=False, raw=UNKNOWN, used=0, gated="mmwave_absent")
        w, h = int(sample.get("grid_width") or 0), int(sample.get("grid_height") or 0)
        frames: dict[int, dict[str, Any]] = sample.get("sensors") or {}
        if w * h != self.model.zones or not frames:
            self.last_error = f"grid {w}x{h} ≠ model {self.model.zones} zones" if frames else "no sensor frames"
            return self._verdict(UNKNOWN, 0.0, present=False, raw=UNKNOWN, used=0, error=self.last_error)
        probs: list[list[float]] = []
        present = False
        for idx in sorted(frames):
            dist, status = frames[idx]["distance_mm"], frames[idx]["status"]
            if len(dist) != self.model.zones:
                self.last_error = f"sensor {idx}: {len(dist)} zones ≠ model {self.model.zones}"
                continue
            state = self.sensors.setdefault(int(idx), _SensorState(self))
            p = state.observe(dist, status, now)
            present = present or state.present
            if p is not None:
                probs.append(p)
        if not probs:
            return self._verdict(UNKNOWN, 0.0, present=present, raw=UNKNOWN, used=0)
        fused = fuse_log_softmax(probs)
        idx = max(range(len(fused)), key=fused.__getitem__)
        self._votes.append((idx, fused[idx]))
        counts: dict[int, int] = {}            # 삽입 순서 = 오래된 표부터
        for i, _ in self._votes:
            counts[i] = counts.get(i, 0) + 1
        # 동률이면 창에서 먼저 나온 클래스 — 학습 평가 train_mlp.smooth(Counter.most_common) 와 같은 규칙
        best = max(counts.items(), key=lambda kv: kv[1])[0]
        conf = sum(p for i, p in self._votes if i == best) / len(self._votes)
        return self._verdict(self.model.classes[best], conf, present=present, raw=self.model.classes[idx],
                             used=len(probs))

    def _verdict(self, posture: str, confidence: float, *, present: bool, raw: str, used: int,
                 error: str | None = None, gated: str | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {
            "posture": posture, "posture_confidence": round(float(confidence), 4), "present": bool(present),
            "baseline_ready": used > 0, "raw_posture": raw, "source": "mlp", "sensors_used": used,
            "frames": self.frames,
        }
        if error:
            out["error"] = error
        if gated:
            out["gated"] = gated
        return out


def load_posture_from_config(tof_cfg: dict[str, Any] | None, log: Callable[[str], None] | None = None,
                             ) -> TofMlpPosture | None:
    """ingest.yaml `tof.mlp` → TofMlpPosture. 꺼져 있거나 가중치가 없으면 None (허브는 0x03 규칙 판정으로 돈다)."""
    mcfg = dict((tof_cfg or {}).get("mlp") or {})
    if not mcfg or not mcfg.get("enabled", True):
        return None
    try:
        model = load_model(mcfg.get("weights"))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        if log:
            log(f"[tof_mlp] 가중치를 못 읽어 규칙 판정으로 폴백: {exc}")
        return None
    if log:
        log(f"[tof_mlp] {model.n_in}→{model.n_hidden}→{model.n_out} ({model.grid_width}x{model.grid_height}, "
            f"{model.source or 'source 미기록'})")
    return TofMlpPosture(model, mcfg)
