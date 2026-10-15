"""지시 자세 프로토콜: 경과 시간 → 지금 안내 중인 단계와 정답 라벨.

dataset/depth_teacher 의 Schedule 과 같은 규칙이다. 각 단계 앞에 transition_s 의 전환 구간이
있고, 그 동안은 정답이 없다(prompt=None).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

LABELS = ("upright", "lean_forward", "forward_head", "head_down", "recline", "face_down",
          "chin_rest", "lean_left", "lean_right", "drowsy", "away")

# 지시 자세 → 규칙 추정기 posture enum (data-spec §6.1). 규칙에 해당 범주가 없으면 None 이고,
# 그 구간은 규칙 정확도 계산에서 뺀다.
RULE_CLASS = {
    "upright": "upright",
    "lean_forward": "lean_forward",
    "recline": "recline",
    "face_down": "face_down",
    "chin_rest": "chin_rest",
    "drowsy": "drowsy",
    "away": "away",
    "forward_head": None,
    "head_down": None,
    "lean_left": None,
    "lean_right": None,
}


@dataclass(frozen=True)
class Step:
    label: str
    seconds: float
    text: str
    cmd: str | None = None


@dataclass(frozen=True)
class Now:
    index: int            # 단계 번호(0부터)
    phase: str            # "transition" | "step"
    prompt: str | None    # 정답 라벨. 전환 구간이면 None
    text: str
    remain_s: float
    since_s: float        # 이 구간이 시작된 뒤 지난 시간


def load_config(path: str | Path | None = None) -> dict:
    with open(path or ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


class Schedule:
    def __init__(self, steps: list[Step], transition_s: float):
        for s in steps:
            if s.label not in LABELS:
                raise ValueError(f"모르는 라벨: {s.label}")
            if s.cmd is not None and s.cmd not in ("b", "g", "r"):
                raise ValueError(f"모르는 명령: {s.cmd}")
        self.steps = list(steps)
        self.transition_s = float(transition_s)

    @classmethod
    def from_config(cls, cfg: dict) -> "Schedule":
        p = cfg["protocol"]
        steps = [Step(s["label"], float(s["seconds"]), s["text"], s.get("cmd")) for s in p["steps"]]
        return cls(steps, p["transition_s"])

    @property
    def total_s(self) -> float:
        return sum(self.transition_s + s.seconds for s in self.steps)

    def at(self, elapsed: float) -> Now | None:
        """프로토콜이 끝났으면 None."""
        t = 0.0
        n = len(self.steps)
        for i, s in enumerate(self.steps):
            if elapsed < t + self.transition_s:
                return Now(i, "transition", None, f"[{i + 1}/{n}] 준비: {s.text}",
                           t + self.transition_s - elapsed, elapsed - t)
            t += self.transition_s
            if elapsed < t + s.seconds:
                return Now(i, "step", s.label, f"[{i + 1}/{n}] {s.text}",
                           t + s.seconds - elapsed, elapsed - t)
            t += s.seconds
        return None

    def snapshot(self) -> dict:
        return {"transition_s": self.transition_s,
                "steps": [{"label": s.label, "seconds": s.seconds, "text": s.text, "cmd": s.cmd}
                          for s in self.steps]}
