"""tofsim 없이 돌리는 합성 사람 — 시험·빠른 점검용 8×8 격자 생성기.

MuJoCo 장면 대신 아주 단순한 깊이 그림을 만든다: 벽 2.5 m, 사람 몸통 0.65 m, 머리 0.7 m. 끄덕임은 머리 행이 비고
아래 행이 가까워지는 모양으로 넣는다(0x03 규칙 끄덕임 검출기가 보는 것과 같은 현상). 자세 이름의 '정답' 은 라벨 문자열이고,
자세 모델 자리에 OraclePosture 를 두면 모델 없이 hub 폐루프를 검증할 수 있다. ScriptedPerson 과 같은 인터페이스를 가진다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .person import DEFAULT_KEYS, TYPING_KINDS, FrameInfo

FLAG_TRANSITION, FLAG_NOD = 1, 2
WALL, TORSO, HEAD, FAR_HEAD, NEAR_DROP = 2500, 650, 700, 2500, 540


@dataclass
class _Seg:
    kind: str
    seconds: float
    typing: bool
    keys: tuple[float, float, float]
    env: dict = field(default_factory=dict)
    nods: list = field(default_factory=list)      # (start, fall, pause, recover, amp, hold)
    override: str | None = None
    spec: dict = field(default_factory=dict)
    transition_s: float = 1.0


class SyntheticPerson:
    def __init__(self, segments: list[dict], *, seed: int = 0, fps: float = 10.0):
        self.rng = np.random.default_rng(seed)
        self.fps, self.dt = fps, 1.0 / fps
        self.segs = [self._make(s) for s in segments]
        self.i, self.ts, self.t, self.frame_no = 0, 0.0, 0.0, 0
        self.overrides_applied: list[dict] = []
        self.last_label = "away"
        self.meta = {"backend": "synthetic"}

    def _make(self, spec: dict, override: str | None = None) -> _Seg:
        kind = str(spec["kind"])
        sec = float(spec["seconds"])
        nods: list = []
        if kind == "drowsy":
            c = spec.get("nods") or {}
            lo, hi = c.get("interval_s", [4.0, 10.0])
            hold = c.get("hold_s", [3.0, 8.0])
            t = float(c.get("first_s", 5.0)) if not isinstance(c.get("first_s"), list) else float(self.rng.uniform(*c["first_s"]))
            while t < sec - 3.0:
                h = float(self.rng.uniform(*hold)) if self.rng.random() < float(c.get("micro_sleep_p", 0.5)) else 0.0
                nods.append((t, 0.8, 0.2, 0.6, np.radians(40.0), h))
                t += 0.8 + 0.2 + h + 0.6 + float(self.rng.uniform(lo, hi))
        base = DEFAULT_KEYS.get(kind, (0.5, 0.5, 0.08))
        k = spec.get("keys") or {}
        keys = (float(k.get("idle_ratio", base[0])), float(k.get("flight_cv", base[1])), float(k.get("correction_rate", base[2])))
        return _Seg(kind=kind, seconds=sec, typing=bool(spec.get("typing", kind in TYPING_KINDS)), keys=keys,
                    env=dict(spec.get("env") or {}), nods=nods, override=override, spec=dict(spec),
                    transition_s=float(spec.get("transition_s", 1.0)))

    @property
    def current(self) -> _Seg:
        return self.segs[self.i]

    @property
    def done(self) -> bool:
        return self.i >= len(self.segs)

    def total_seconds(self) -> float:
        return float(sum(s.seconds for s in self.segs))

    def remaining_in_segment(self) -> float:
        return max(0.0, self.current.seconds - self.ts)

    def bind(self, cfg) -> None:  # ScriptedPerson 호환
        pass

    def override(self, kind: str, seconds: float, *, transition_s: float = 1.0, name: str = "override",
                 typing: bool | None = None, resume: bool = True, min_resume_s: float = 20.0, spec: dict | None = None) -> dict:
        cur = self.current
        remaining = self.remaining_in_segment() - seconds
        new_spec = dict(spec or {}, kind=kind, seconds=seconds, transition_s=transition_s)
        if typing is not None:
            new_spec["typing"] = typing
        new = self._make(new_spec, override=name)
        tail = [self._make(dict(cur.spec, seconds=remaining), override=cur.override)] \
            if resume and remaining >= min_resume_s and cur.kind != "away" else []
        self.segs[self.i:self.i + 1] = [new] + tail
        self.ts = 0.0
        rec = {"t": round(self.t, 2), "name": name, "kind": kind, "seconds": seconds, "cut": cur.kind,
               "resumed_s": round(remaining, 1) if tail else 0.0}
        self.overrides_applied.append(rec)
        return rec

    def step(self) -> FrameInfo | None:
        if self.done:
            return None
        cur = self.current
        ts = self.ts
        flags = FLAG_TRANSITION if ts < cur.transition_s and self.i > 0 else 0
        label = cur.kind if cur.kind != "turtle" else "forward_head"
        nod_amt = 0.0
        if cur.kind == "drowsy":
            label = "upright" if (not cur.nods or ts < cur.nods[0][0]) else "drowsy"
            for start, fall, pause, recover, amp, hold in cur.nods:
                x = ts - start
                if x < 0:
                    continue
                if x < fall:
                    nod_amt, flags = x / fall, flags | FLAG_NOD
                elif x < fall + pause + hold:
                    nod_amt, flags = 1.0, flags | FLAG_NOD
                elif x < fall + pause + hold + recover:
                    nod_amt, flags = 1 - (x - fall - pause - hold) / recover, flags | FLAG_NOD
        self.last_label = label
        info = FrameInfo(t=self.t, kind=cur.kind, label=label, flags=flags, pose=(cur.kind, nod_amt), walker=None,
                         typing=cur.typing and cur.kind != "away", keys=cur.keys, env=cur.env, segment_index=self.i,
                         override=cur.override)
        self.frame_no += 1
        self.t = self.frame_no * self.dt
        self.ts += self.dt
        if self.ts >= cur.seconds - 1e-9:
            self.i += 1
            self.ts = 0.0
        return info

    def render(self, info: FrameInfo) -> dict[int, tuple[np.ndarray, np.ndarray]]:
        kind, nod = info.pose
        out = {}
        for idx in (0, 1):
            d = np.full((8, 8), WALL, dtype=np.int16)
            s = np.full((8, 8), 5, dtype=np.uint8)
            c0 = 2 + idx            # 두 센서는 시야가 조금 다르다
            if kind != "away":
                d[3:8, c0:c0 + 4] = TORSO
                if kind == "face_down":
                    d[0:4, :] = WALL
                    d[4:8, c0:c0 + 4] = 450
                elif kind in ("chin_rest",):
                    d[1:3, c0:c0 + 3] = HEAD
                    d[3:6, c0 + 3:c0 + 4] = 520       # 괸 팔
                elif kind in ("recline",):
                    d[1:3, c0:c0 + 3] = HEAD + 200
                    d[3:8, c0:c0 + 4] = TORSO + 200
                elif kind in ("lean_forward", "elbows"):
                    d[1:3, c0:c0 + 3] = HEAD - 150
                    d[3:8, c0:c0 + 4] = TORSO - 120
                elif kind == "turtle":
                    d[1:3, c0:c0 + 3] = HEAD - 90
                else:
                    d[1:3, c0:c0 + 3] = HEAD
                if nod > 0.0:                      # 머리가 앞으로 떨어진다: 윗행이 비고 3~4 행이 가까워진다
                    if nod >= 0.5:
                        d[1:3, c0:c0 + 3] = FAR_HEAD
                        d[3:5, c0:c0 + 3] = NEAR_DROP
                    else:
                        d[1:2, c0:c0 + 3] = FAR_HEAD
            noise = self.rng.normal(0.0, 4.0, size=(8, 8)).astype(np.int16)
            d = (d + noise).astype(np.int16)
            drop = self.rng.random((8, 8)) < 0.01
            d[drop], s[drop] = -1, 255
            out[idx] = (d, s)
        return out
