"""시간축: 프로토콜 단계 → 프레임별 관절 각도·재실·정답.

- 단계마다 자세 하나를 뽑는다(pose.sample_pose). 전환 구간(준비 3 s) 안에서 0.2~1.2 s 뒤에 움직이기
  시작해 0.6~1.6 s 동안 부드럽게(smoothstep) 바뀐다.
- 숨·몸 흔들림·고개 돌림: 느린 무작위(OU 과정) 흔들림을 모든 프레임에 더한다.
- 꾸벅 졸음: 0.3~1.0 s 에 고개가 18~35° 떨어지고, 0.2~1.5 s 머문 뒤 0.3~0.8 s 에 돌아온다. 4~12 s 간격.
  30% 확률로 단계 끝을 떨군 채 정지로 마친다(depth_teacher '떨군 채 5 s').
- 키보드 힐끗(바른 자세 단계의 sim: glance): 0.6~1.2 s 고개 숙였다 복귀, 2~3번. 끄덕임 오탐 시험용.
- 자리비움: 사람 geom 을 광선에서 뺀다. 전환 중간에 사라지고 나타난다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .pose import chair_tilt_for, sample_pose
from .scene import Scene


@dataclass
class Frame:
    t: float
    q: dict[str, float]
    present: bool
    prompt: str | None
    phase: str | None
    step: int | None
    chair_tilt: float
    nodding: bool = False


@dataclass
class StepSpec:
    label: str
    seconds: float
    sim: str | None = None   # "left"/"right"(턱괴기 손), "glance"(힐끗)


def _smooth(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def _lerp(a: dict, b: dict, w: float) -> dict:
    return {k: a.get(k, 0.0) + (b.get(k, 0.0) - a.get(k, 0.0)) * w for k in set(a) | set(b)}


class _OU:
    """평균 0 으로 돌아오는 느린 흔들림."""

    def __init__(self, sigma: float, tau: float, rng: np.random.Generator):
        self.sigma, self.tau, self.rng, self.x = sigma, tau, rng, 0.0

    def step(self, dt: float) -> float:
        a = np.exp(-dt / self.tau)
        self.x = a * self.x + np.sqrt(1 - a * a) * self.sigma * self.rng.normal()
        return self.x


def _head_events(kind: str, seconds: float, rng: np.random.Generator) -> list[tuple]:
    """(시작, 떨어짐, 머묾, 복귀, 진폭°). 복귀=None 이면 끝까지 떨군 채."""
    ev = []
    if kind == "glance":
        for t in sorted(rng.uniform(1.0, max(seconds - 2.0, 1.5), size=rng.integers(2, 4))):
            ev.append((t, 0.35, rng.uniform(0.3, 0.7), 0.35, rng.uniform(15, 25)))
    elif kind == "nod":
        t = rng.uniform(1.0, 4.0)
        while t < seconds - 2.5:
            fall, hold, rec = rng.uniform(0.3, 1.0), rng.uniform(0.2, 1.5), rng.uniform(0.3, 0.8)
            ev.append((t, fall, hold, rec, rng.uniform(18, 35)))
            t += fall + hold + rec + rng.uniform(4, 12)
        if rng.random() < 0.3 and seconds > 8:
            ev.append((seconds - rng.uniform(6, 7.5), rng.uniform(0.4, 1.0), 99.0, None, rng.uniform(25, 35)))
    return ev


def _head_offset(ev: list[tuple], ts: float) -> tuple[float, bool]:
    for start, fall, hold, rec, amp in ev:
        x = ts - start
        if x < 0:
            continue
        if x < fall:
            return amp * _smooth(x / fall), True
        if rec is None or x < fall + hold:
            return amp, True
        if x < fall + hold + rec:
            return amp * (1 - _smooth((x - fall - hold) / rec)), True
    return 0.0, False


def timeline(sc: Scene, steps: list[StepSpec], transition_s: float, rng: np.random.Generator,
             fps: float = 15.0, start_present: bool = False) -> list[Frame]:
    poses, events = [], []
    last_q = sample_pose(sc, "upright", rng)
    for s in steps:
        if s.label == "away":
            q = last_q
        else:
            side = s.sim if s.sim in ("left", "right") else rng.choice(["left", "right"])
            q = sample_pose(sc, s.label, rng, chin_side=side)
            last_q = q
        poses.append(q)
        kind = "nod" if s.label == "drowsy" else "glance" if s.sim == "glance" else None
        events.append(_head_events(kind, s.seconds, rng) if kind else [])

    sway = {"lumbar_pitch": _OU(np.radians(1.5), 3.0, rng), "lumbar_roll": _OU(np.radians(1.0), 3.0, rng),
            "head_yaw": _OU(np.radians(5.0), 2.0, rng), "head_pitch": _OU(np.radians(2.0), 1.5, rng)}
    breath_f = rng.uniform(0.2, 0.3)
    base_tilt = sc.room.chair_tilt_deg
    frames: list[Frame] = []
    dt = 1.0 / fps
    t = 0.0
    prev_q, prev_present = poses[0], start_present
    for i, s in enumerate(steps):
        move_at = rng.uniform(0.2, 1.2)
        move_len = min(rng.uniform(0.6, 1.6), transition_s - move_at)
        present = s.label != "away"
        seg_end = t + transition_s + s.seconds
        while t < seg_end - 1e-9:
            local = t - (seg_end - transition_s - s.seconds)
            if local < transition_s:
                phase, prompt = "transition", None
                w = _smooth((local - move_at) / max(move_len, 1e-3))
                q = _lerp(prev_q, poses[i], w)
                now_present = present if w >= 0.5 else prev_present
                nod = False
            else:
                phase, prompt = "step", s.label
                ts = local - transition_s
                q = dict(poses[i])
                off, nod = _head_offset(events[i], ts)
                if off:
                    q["head_pitch"] = q.get("head_pitch", 0.0) - np.radians(off)
                    q["neck_pitch"] = q.get("neck_pitch", 0.0) - np.radians(0.3 * off)
                now_present = present
            for k, ou in sway.items():
                q[k] = q.get(k, 0.0) + ou.step(dt)
            q["lumbar_pitch"] += np.radians(0.6) * np.sin(2 * np.pi * breath_f * t)
            frames.append(Frame(t, q, now_present, prompt, phase, i if phase else None,
                                chair_tilt_for(q, base_tilt), nod and s.label == "drowsy"))
            t += dt
        prev_q, prev_present = poses[i], present
    return frames
