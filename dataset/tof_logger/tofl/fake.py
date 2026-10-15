"""가짜 L8 — 수집 펌웨어와 같은 USB 줄을 만들어 수집기를 하드웨어 없이 돌려 본다.

장면은 firmware test_tof_posture 와 같은 방식(뒤 벽 앞에 머리 원·상체 사각형·팔 기둥, zone
중심에 걸리는 가장 가까운 도형의 거리)으로 그린다. 자세별 숫자는 그럴듯한 값일 뿐 실측이
아니다. 배관(줄 형식·세션 저장·라벨 정렬·결측 처리)을 시험하는 용도이고 학습 데이터가 아니다.

rule 은 진짜 TofPosture 를 돌리지 않고 지시 자세를 그대로 돌려준다(규칙 없는 자세는 unknown).
"""
from __future__ import annotations

import json
import math
import time
from typing import Callable, Iterator

import numpy as np

from .protocol import RULE_CLASS

WALL_MM = 1600.0

# label → (머리 중심 x, 머리 중심 y, 머리 거리, 상체 위 y, 상체 거리, 상체 x 이동)
# x·y 는 장면 방향 정규화 좌표(0,0 = 왼쪽 위)
_POSE = {
    "upright":      (0.50, 0.28, 600, 0.45, 680, 0.00),
    "lean_forward": (0.50, 0.32, 480, 0.48, 560, 0.00),
    "forward_head": (0.50, 0.32, 520, 0.45, 680, 0.00),
    "head_down":    (0.50, 0.36, 570, 0.45, 680, 0.00),
    "recline":      (0.50, 0.26, 720, 0.43, 760, 0.00),
    "face_down":    (0.50, 0.62, 520, 0.62, 600, 0.00),
    "chin_rest":    (0.50, 0.32, 600, 0.47, 680, 0.00),
    "lean_left":    (0.38, 0.30, 610, 0.46, 680, -0.10),
    "lean_right":   (0.62, 0.30, 610, 0.46, 680, 0.10),
    "drowsy":       (0.50, 0.30, 600, 0.45, 680, 0.00),
}
_HEAD_R = 0.15


def render(label: str | None, w: int = 8, h: int = 8, t: float = 0.0, chin_side: int = 1,
           rng: np.random.Generator | None = None) -> tuple[np.ndarray, np.ndarray]:
    """(d, st) 장면 방향 (h, w). 사람이 없으면 벽만."""
    rng = rng or np.random.default_rng(0)
    ys, xs = (np.arange(h) + 0.5) / h, (np.arange(w) + 0.5) / w
    X, Y = np.meshgrid(xs, ys)
    d = np.full((h, w), WALL_MM)
    if label in _POSE:
        hx, hy, hd, ty, td, tx = _POSE[label]
        if label == "drowsy":  # 6 s 마다 0.8 s 끄덕임
            phase = t % 6.0
            dip = math.sin(math.pi * phase / 0.8) if phase < 0.8 else 0.0
            hy += 0.08 * dip
            hd -= 40 * dip
        torso = (Y >= ty) & (np.abs(X - 0.5 - tx) <= 0.25)
        d = np.where(torso, np.minimum(d, td), d)
        head = (X - hx) ** 2 + (Y - hy) ** 2 <= _HEAD_R ** 2
        d = np.where(head, np.minimum(d, hd), d)
        if label == "chin_rest":
            ax = 0.5 + 0.18 * chin_side
            arm = (np.abs(X - ax) <= 0.07) & (Y >= hy + 0.08)
            d = np.where(arm, np.minimum(d, td - 120), d)
    d = d + rng.normal(0, 6, size=d.shape)
    st = np.full((h, w), 5, dtype=np.int64)
    st[rng.random(size=d.shape) < 0.02] = 6
    missing = rng.random(size=d.shape) < 0.02
    d = np.round(d).astype(np.int64)
    d[missing] = -1
    st[missing] = 255
    return d, st


def scene_to_sensor(a: np.ndarray, flip_rows: bool, flip_cols: bool) -> np.ndarray:
    """장면 방향 → 센서 zone 순서. 펌웨어 remapZones 의 역(뒤집기라 자기 자신이 역)."""
    if flip_rows:
        a = a[::-1, :]
    if flip_cols:
        a = a[:, ::-1]
    return a


class FakeSource:
    """(줄 bytes, t_host) 를 내는 반복자. posture_at(t_host) 가 지금 취할 자세를 준다.

    realtime=False 면 가상 시계로 즉시 돈다(테스트용). drop_every=n 이면 n 번째마다 프레임을
    빠뜨려 seq 갭을 만든다.
    """

    def __init__(self, posture_at: Callable[[float], str | None], hz: int = 15,
                 realtime: bool = True, t0: float | None = None, seed: int = 0,
                 flip_rows: bool = False, flip_cols: bool = True, drop_every: int = 0,
                 max_frames: int | None = None):
        self.posture_at = posture_at
        self.hz = hz
        self.realtime = realtime
        self.t0 = time.time() if t0 is None else t0
        self.rng = np.random.default_rng(seed)
        self.flip_rows, self.flip_cols = flip_rows, flip_cols
        self.drop_every = drop_every
        self.max_frames = max_frames
        self.sent: list[str] = []
        self._label: str | None = None
        self._chin_side = 1
        self._baseline = False

    def send(self, cmd: str) -> None:
        self.sent.append(cmd)
        if cmd == "b":
            self._baseline = False

    def close(self) -> None:
        pass

    def _hello(self, ms: int) -> bytes:
        return (json.dumps({"t": "tof_hello", "ms": ms, "sensor": "FAKE-L8", "w": 8, "h": 8,
                            "hz": self.hz, "flip_rows": self.flip_rows,
                            "flip_cols": self.flip_cols, "target_order": "closest", "fw": 0},
                           separators=(",", ":")) + "\n").encode()

    def __iter__(self) -> Iterator[tuple[bytes, float]]:
        period = 1.0 / self.hz
        yield b"ets Jun  8 2016 00:22:57\r\n", self.t0  # 부팅 로그는 버려져야 한다
        yield self._hello(0), self.t0
        seq = 0
        while self.max_frames is None or seq < self.max_frames:
            t_host = self.t0 + 0.5 + seq * period
            if self.realtime:
                wait = t_host - time.time()
                if wait > 0:
                    time.sleep(wait)
            want = self.posture_at(t_host)
            if want is not None and want != self._label:
                if want == "chin_rest":
                    self._chin_side = -self._chin_side
                self._label = want
            label = self._label
            if label == "upright":
                self._baseline = True
            d, st = render(label, t=t_host - self.t0, chin_side=self._chin_side, rng=self.rng)
            d = scene_to_sensor(d, self.flip_rows, self.flip_cols)
            st = scene_to_sensor(st, self.flip_rows, self.flip_cols)
            this_seq = seq
            seq += 1
            if self.drop_every and this_seq % self.drop_every == self.drop_every - 1:
                continue
            present = label not in (None, "away")
            rule = {"present": present,
                    "posture": (RULE_CLASS.get(label) or "unknown") if present else "away",
                    "conf": 0.9, "baseline": self._baseline and present,
                    "head_mm": None, "torso_mm": None, "drop_mm": None, "chin": 0.0,
                    "motion": 0.0, "nods": 0}
            obj = {"t": "tof", "seq": this_seq, "ms": int((t_host - self.t0) * 1000), "w": 8,
                   "h": 8, "d": d.ravel().tolist(), "st": st.ravel().tolist(), "rule": rule}
            yield (json.dumps(obj, separators=(",", ":")) + "\n").encode(), t_host
