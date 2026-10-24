"""사람 소스 — 각본대로 움직이는 사람(ScriptedPerson) + CAD04 두 센서 장면(Scene)을 한 인터페이스로.

    src = TofsimSource(scenario, backend="mujoco", tofsim_root=None)
    info = src.step()            # FrameInfo | None(끝)
    frames = src.render(info)    # {센서 번호: (dist_mm[8,8], status[8,8])}

전체 시뮬(life_sim)도 같은 소스를 쓴다. 시험용 합성 소스는 synthetic.SyntheticPerson(같은 인터페이스).
"""
from __future__ import annotations

from typing import Any


class TofsimSource:
    def __init__(self, scenario: dict[str, Any], *, backend: str, tofsim_root: str | None):
        from . import tofsim_bridge
        from .person import ScriptedPerson
        from .scene import build_scene
        if tofsim_bridge.load(tofsim_root) is None:
            raise RuntimeError("tofsim 을 찾지 못했다 (TOFSIM_ROOT 또는 --tofsim)")
        self.scene = build_scene(int(scenario["seed"]), backend=backend, placement=scenario["placement"],
                                 clutter=int(scenario["clutter"]), scene=scenario["scene"],
                                 require_ok=bool(scenario["require_ok_placement"]), l8_params=scenario.get("l8_params"))
        self.person = ScriptedPerson(scenario["segments"], seed=int(scenario["seed"]) + 1, fps=float(scenario["fps"]))
        self.person.bind(self.scene.cfg)
        self.meta = dict(self.scene.meta)

    def __getattr__(self, name):           # step/override/current/done/total_seconds/overrides_applied/t
        return getattr(self.person, name)

    def render(self, info):
        return self.scene.render(info.pose, info.walker)


def where_now(src: Any, *, default: str = "out") -> str:
    """사람이 어디 있는지: desk | room(같은 방 다른 곳 — 침대·주방, mmWave 는 먼 거리로 계속 잡는다) | out.
    자리비움 구간의 `where`, 없으면 취침(override bedtime)=room, 그 밖은 각본 away_where(기본 out)."""
    if src.done:
        return default
    cur = src.current
    if cur.kind != "away":
        return "desk"
    w = (getattr(cur, "spec", None) or {}).get("where")
    if w is None:
        w = "room" if getattr(cur, "override", None) == "bedtime" else default
    return str(w)
