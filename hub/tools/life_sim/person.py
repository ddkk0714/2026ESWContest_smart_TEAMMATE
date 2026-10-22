"""각본대로 움직이는 사람 — tofsim 의 자세·끄덕임 헬퍼를 프레임 단위 루프에서 재사용하고, 개입이 오면 각본을 바꾼다.

tofsim.session.generate_frames 는 plan_timeline() 으로 구간을 무작위로 뽑는 고정 생성기라 "정해진 각본 + 동적 전환" 을
못 한다. 여기서는 같은 헬퍼(_target_pose, _plan_nods 의 매개변수 판, _lerp_pose, _nod_offset, _away_frame)를 써서
프레임마다 Pose 를 만들고, `override()` 로 지금 구간을 잘라 새 구간(깨어남 → upright 등)을 끼워 넣은 뒤 남은 구간을 잇는다.

구간(yaml `segments[]`) 필드
  kind        upright | lean_forward | recline | face_down | chin_rest | drowsy | away | turtle | writing | reading | ...
              (tofsim SEGMENTS 의 종류; 라벨은 SEGMENT_LABEL 로 접힌다. drowsy 는 첫 끄덕임 전까지 upright 라벨)
  seconds     길이
  typing      키스트로크 활성(기본: upright/turtle/mouse_work 만 true)
  arms        손 위치 강제(없으면 tofsim 기본 추첨)
  nods        drowsy: {interval_s: [lo, hi], amp_deg: [lo, hi], fall_s, recover_s, micro_sleep_p, hold_s: [lo, hi], first_s}
  nod_side    drowsy: 0 = 앞으로, ±1 = 옆으로 꺾임
  lookalikes  true 면 tofsim 의 헷갈리는 동작(힐끗·컵·고개돌림)도 섞는다 (기본 false)
  transition_s  앞 구간에서 이 자세로 바뀌는 시간(기본 0.5~2 s 추첨)
  keys        키스트로크 품질 {idle_ratio, flight_cv, correction_rate} (기본은 kind 별)
  env         이 구간 동안 환경 강제 {co2_to, lux, temp_c} (없으면 DeviceWorld 물리대로)
라벨 문자열은 tofsim.LABELS 그대로(upright, lean_forward, recline, face_down, chin_rest, drowsy, away, misplaced, forward_head).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

from . import tofsim_bridge

TYPING_KINDS = {"upright", "turtle", "mouse_work", "elbows"}
DEFAULT_KEYS = {  # kind → (idle_ratio, flight_cv, correction_rate): 피곤해질수록 idle·분산·정정이 는다
    "upright": (0.15, 0.30, 0.04), "turtle": (0.20, 0.35, 0.05), "mouse_work": (0.30, 0.30, 0.03),
    "elbows": (0.35, 0.45, 0.08), "lean_forward": (0.30, 0.40, 0.06),
}


@dataclass
class FrameInfo:
    t: float                  # 각본 시작 기준 초
    kind: str                 # 구간 종류
    label: str                # 관측 가능한 정답 라벨(tofsim 규칙)
    flags: int
    pose: Any                 # Pose | None(빈 의자)
    walker: Any               # (x, z) | None
    typing: bool
    keys: tuple[float, float, float]
    env: dict
    segment_index: int
    override: str | None      # 개입 반응으로 끼워 넣은 구간이면 그 이름


@dataclass
class ScriptSegment:
    kind: str
    seconds: float
    typing: bool
    seg: Any                                   # tofsim Segment (pose·nods·leave·ret)
    keys: tuple[float, float, float]
    env: dict = field(default_factory=dict)
    transition_s: float | None = None
    lookalikes: bool = False
    override: str | None = None
    spec: dict = field(default_factory=dict)


class ScriptedPerson:
    def __init__(self, segments: list[dict], *, seed: int, limits: dict | None = None, fps: float = 10.0):
        self.tofsim = tofsim_bridge.load()
        if self.tofsim is None:
            raise RuntimeError("tofsim 을 찾지 못했다")
        from tofsim import session as S
        from tofsim.body import Pose
        self.S, self.Pose = S, Pose
        self.rng = np.random.default_rng(seed)
        self.limits = limits or {}
        self.fps = fps
        self.dt = 1.0 / fps
        self.segs: list[ScriptSegment] = [self._make(spec) for spec in segments]
        self.i = 0
        self.ts = 0.0                       # 지금 구간 안의 시각
        self.t = 0.0
        self.prev_pose = None               # 앞 구간의 마지막 자세(away 뒤면 None)
        self.start_pose = None
        self.trans = 0.0
        self.breath_f = float(self.rng.uniform(0.2, 0.33))
        self.sway_f = float(self.rng.uniform(0.03, 0.08))
        self.frame_no = 0
        self.overrides_applied: list[dict] = []
        self._enter(0)

    # ------------------------------------------------------------------ 구간 만들기
    def _make(self, spec: dict, *, override: str | None = None) -> ScriptSegment:
        S = self.S
        kind = str(spec["kind"])
        seconds = float(spec["seconds"])
        if kind == "away":
            seg = S.Segment("away", seconds, self.Pose(), leave=S._plan_leave(self.rng), ret=S._plan_return(self.rng))
            if spec.get("no_return"):
                seg.ret = None
            if spec.get("no_leave"):
                seg.leave = None
        else:
            pose = S._target_pose(kind, self.rng, self.limits)
            if spec.get("arms"):
                pose.arms = str(spec["arms"])
            seg = S.Segment(kind, seconds, pose)
            if kind == "drowsy":
                seg.nods = self._plan_nods(seconds, spec.get("nods") or {})
                seg.nod_side = float(spec.get("nod_side", 0.0))
            if spec.get("lookalikes"):
                seg.lookalikes = S._plan_lookalikes(kind, seconds, self.rng)
        typing = bool(spec.get("typing", kind in TYPING_KINDS))
        k = spec.get("keys") or {}
        base = DEFAULT_KEYS.get(kind, (0.5, 0.5, 0.08))
        keys = (float(k.get("idle_ratio", base[0])), float(k.get("flight_cv", base[1])),
                float(k.get("correction_rate", base[2])))
        return ScriptSegment(kind=kind, seconds=seconds, typing=typing, seg=seg, keys=keys, env=dict(spec.get("env") or {}),
                             transition_s=spec.get("transition_s"), lookalikes=bool(spec.get("lookalikes")),
                             override=override, spec=dict(spec))

    def _plan_nods(self, duration: float, cfg: dict) -> list:
        """tofsim._plan_nods 와 같은 튜플 (start, fall, pause, recover, amp, hold) 을 각본 매개변수로 만든다."""
        u = lambda a, b: float(self.rng.uniform(a, b))  # noqa: E731
        lo, hi = cfg.get("interval_s", [3.0, 15.0])
        amp_lo, amp_hi = cfg.get("amp_deg", [25.0, 55.0])
        fall = cfg.get("fall_s", [0.5, 1.5])
        rec = cfg.get("recover_s", [0.3, 1.0])
        hold = cfg.get("hold_s", [4.0, 20.0])
        p_sleep = float(cfg.get("micro_sleep_p", 0.45))
        first = cfg.get("first_s", [1.0, 8.0])
        nods, t = [], u(*first) if isinstance(first, (list, tuple)) else float(first)
        while t < duration - 3.0:
            f = u(*fall) if isinstance(fall, (list, tuple)) else float(fall)
            pause = u(0.0, 0.4)
            r = u(*rec) if isinstance(rec, (list, tuple)) else float(rec)
            amp = float(np.radians(u(amp_lo, amp_hi)))
            h = (u(*hold) if isinstance(hold, (list, tuple)) else float(hold)) if self.rng.random() < p_sleep else 0.0
            nods.append((t, f, pause, r, amp, h))
            t += f + pause + h + r + u(lo, hi)
        return nods

    def _enter(self, i: int) -> None:
        self.i = i
        self.ts = 0.0
        cur = self.segs[i]
        self.start_pose = self.prev_pose
        if i == 0:
            self.trans = 0.0
        elif cur.transition_s is not None:
            self.trans = float(cur.transition_s)
        else:
            self.trans = float(self.rng.uniform(0.5, 2.0))

    # ------------------------------------------------------------------ 동적 전환
    @property
    def current(self) -> ScriptSegment:
        return self.segs[self.i]

    @property
    def done(self) -> bool:
        return self.i >= len(self.segs)

    def remaining_in_segment(self) -> float:
        return max(0.0, self.current.seconds - self.ts)

    def override(self, kind: str, seconds: float, *, transition_s: float = 1.0, name: str = "override",
                 typing: bool | None = None, resume: bool = True, min_resume_s: float = 20.0, spec: dict | None = None) -> dict:
        """지금 구간을 자르고 `kind` 구간을 `seconds` 동안 끼워 넣는다. resume 면 남은 원래 구간(끄덕임 다시 계획)을 뒤에 잇는다."""
        cur = self.current
        remaining = self.remaining_in_segment() - seconds
        new_spec = dict(spec or {}, kind=kind, seconds=seconds, transition_s=transition_s)
        if typing is not None:
            new_spec["typing"] = typing
        new_seg = self._make(new_spec, override=name)
        tail: list[ScriptSegment] = []
        if resume and remaining >= min_resume_s and cur.kind != "away":
            tail_spec = dict(cur.spec, seconds=remaining, transition_s=cur.transition_s)
            tail.append(self._make(tail_spec, override=cur.override))
        self.segs[self.i:self.i + 1] = [new_seg] + tail
        self.prev_pose = self._last_pose if self._last_pose is not None else self.prev_pose
        self._enter(self.i)
        rec = {"t": round(self.t, 2), "name": name, "kind": kind, "seconds": seconds, "cut": cur.kind,
               "resumed_s": round(remaining, 1) if tail else 0.0}
        self.overrides_applied.append(rec)
        return rec

    # ------------------------------------------------------------------ 프레임
    _last_pose = None

    def step(self) -> FrameInfo | None:
        """다음 프레임(1/fps 초). 각본이 끝나면 None."""
        if self.done:
            return None
        S = self.S
        cur = self.segs[self.i]
        seg = cur.seg
        ts = self.ts
        flags = 0
        label = S.SEGMENT_LABEL.get(cur.kind, cur.kind)
        if cur.kind == "drowsy" and (not seg.nods or ts < seg.nods[0][0]):
            label = "upright"
        walker = None
        if cur.kind == "away":
            pose, walker, moving = S._away_frame(seg, ts, cur.seconds, self.start_pose, self._cfg)
            if moving:
                flags |= S.FLAG_TRANSITION
        else:
            w = 1.0 if self.trans <= 0 else min(1.0, ts / self.trans)
            if w < 1.0:
                flags |= S.FLAG_TRANSITION
            base = self.start_pose if self.start_pose is not None else seg.pose
            pose = S._lerp_pose(base, seg.pose, w)
            if self.i > 0 and self.segs[self.i - 1].kind == "away" and self.start_pose is None:
                pose.hip_shift = seg.pose.hip_shift + 0.08 * (1 - w)
                pose.trunk_pitch += np.radians(12.0) * (1 - w)
            tg = self.t
            pose.breath = 0.004 * np.sin(2 * np.pi * self.breath_f * tg)
            pose.trunk_roll += np.radians(1.5) * np.sin(2 * np.pi * self.sway_f * tg)
            pose.head_yaw += np.radians(self.rng.normal(0, 0.6))
            if pose.arms == "typing" and cur.typing:
                pose.arm_jitter = 0.01 * np.sin(2 * np.pi * 3.0 * tg + self.rng.normal(0, 0.3))
            elif pose.arms == "writing":
                pose.arm_jitter = 0.015 * np.sin(2 * np.pi * 1.5 * tg + self.rng.normal(0, 0.3))
            for nod in seg.nods:
                off, active = S._nod_offset(nod, ts)
                if active:
                    if seg.nod_side:
                        pose.head_roll += off * 0.9 * seg.nod_side
                        pose.neck_pitch += off * 0.25
                        pose.trunk_roll += off * 0.3 * seg.nod_side
                    else:
                        pose.neck_pitch += off
                        pose.trunk_pitch += off * 0.25
                    flags |= S.FLAG_NOD
            for start, what, length, amount in seg.lookalikes:
                if start <= ts < start + length:
                    flags |= S.FLAG_LOOKALIKE
                    k = np.sin(np.pi * (ts - start) / length)
                    if what == "glance":
                        pose.neck_pitch += amount * min(1.0, 2 * k)
                    elif what == "turn":
                        pose.head_yaw += amount * k
                    elif what == "cup" and pose.arms in S.TWO_HANDED_FREE:
                        pose.other_arms, pose.arms = pose.arms, "cup_right"
            self._last_pose = pose
        info = FrameInfo(t=self.t, kind=cur.kind, label=label, flags=flags, pose=pose, walker=walker,
                         typing=cur.typing and cur.kind != "away", keys=cur.keys, env=cur.env, segment_index=self.i,
                         override=cur.override)
        # 다음 프레임으로
        self.frame_no += 1
        self.t = self.frame_no * self.dt
        self.ts += self.dt
        if self.ts >= cur.seconds - 1e-9:
            self.prev_pose = None if cur.kind == "away" else self._last_pose
            if cur.kind == "away":
                self._last_pose = None
            if self.i + 1 < len(self.segs):
                self._enter(self.i + 1)
            else:
                self.i = len(self.segs)
        return info

    # _away_frame 은 cfg.person/room 만 쓴다 — Scene 이 bind() 로 넣어 준다
    _cfg = None

    def bind(self, cfg) -> None:
        self._cfg = cfg
        if not self.limits:
            self.limits = dict(cfg.meta.get("limits") or {})
            for s in self.segs:                       # face_down 의 머리 높이 제한은 장면에 따라 다르다
                if s.kind == "face_down":
                    s.seg.pose = self.S._target_pose("face_down", self.rng, self.limits)

    def total_seconds(self) -> float:
        return float(sum(s.seconds for s in self.segs))
