"""빠른 시연의 깨우기 규칙 — 같은 자세에도 사람마다 다른 기기가 나간다(11-03 결선 대본, control.yaml `demo_wake:`).

왜: 결선 대본은 A·B 두 사람에게 같은 행동(뒤로 젖히기·졸기·거북목·턱 괴기)을 시키고, 사람마다 다른 반응을 보여 준다.
밴딧(personalize/)은 쿨다운·탐색·누적 피드백으로 순서가 바뀌어 8분 대본을 매번 같게 재현하지 못하고, 음악·선풍기 깨우기 arm 도 없다.
그래서 빠른 시연(demo_mode.py speed fast)일 때만 ACTION_POSTURE 의 기기를 이 규칙으로 고른다 — 운영(normal)은 그대로.

  지금 자세(FSM 이 ACTION_POSTURE 에 들어온 tick 의 자세 라벨)가
    notice_labels(거북목·턱 괴기)  → 기기 없이 바른 자세 안내 카드(arm posture_card)
    mild_labels(뒤로 젖히기)       → 첫 설정 '도움 순서'(focus_help) 1순위 기기 하나
    deep_labels(엎드림·졸음)       → 1·2순위 기기를 함께 ("하나로는 부족했으니 좋아하는 것과 함께" — 사용자)
  도움 순서 항목 → 기기 명령은 helps 표. 표에 없는 항목(break·voice)은 건너뛴다. 프로필이 없으면 default_order.
  warm_only(11-05 사용자 "온도 영향 받아서 선풍기를 고른다" → "24도 말고 관측하다가 온도 상승하면 그때"): 이 항목(wind)은
    방 온도가 최근 warm_window_sec 동안의 가장 낮은 값보다 warm_rise_c 이상 올랐을 때만 고른다(observe_temp 로 tick 마다 기록).
    안 올랐으면 건너뛰고 다음 순위로 간다. B(조명 › 선풍기 › 알림)는 더워지면 조명+선풍기, 그대로면 조명+음악. 온도를 모르면 건너뛴다.
  모든 명령은 ignore_cooldown — 같은 사람이 같은 기기로 두 번 깨워지는 장면(B 조명 → 조명)이 디스패처 300 s 쿨다운에 막히지 않게.

제한 Python(보드)에서 돈다 — 표준 라이브러리만.
"""
from __future__ import annotations

from collections import deque
from typing import Any, Iterable


class DemoWake:
    def __init__(self, cfg: dict[str, Any] | None) -> None:
        cfg = cfg or {}
        self.enabled = bool(cfg.get("enabled", False))
        self.notice_labels = tuple(cfg.get("notice_labels") or ())
        self.mild_labels = tuple(cfg.get("mild_labels") or ())
        self.deep_labels = tuple(cfg.get("deep_labels") or ())
        self.helps: dict[str, list[dict[str, Any]]] = {
            str(k): [dict(s, ignore_cooldown=True) for s in (v or [])] for k, v in (cfg.get("helps") or {}).items()}
        self.default_order = [str(h) for h in (cfg.get("default_order") or list(self.helps))]
        self.warm_only = tuple(str(h) for h in (cfg.get("warm_only") or ()))
        self.warm_rise_c = float(cfg.get("warm_rise_c", 0.5))
        self.warm_window_sec = float(cfg.get("warm_window_sec", 600))
        self._temps: deque[tuple[float, float]] = deque()
        self.rule_labels = self.notice_labels + self.mild_labels + self.deep_labels
        self.last: dict[str, Any] | None = None

    def observe_temp(self, temp_c: float | None, now: float) -> None:
        """tick 마다 방 온도를 남긴다. warm_window_sec 보다 오래된 값은 버린다."""
        if temp_c is not None:
            self._temps.append((now, float(temp_c)))
        while self._temps and now - self._temps[0][0] > self.warm_window_sec:
            self._temps.popleft()

    def temp_rise(self) -> float | None:
        """최근 창의 가장 낮은 온도 대비 지금 온도가 오른 만큼(°C). 값이 없으면 None."""
        if not self._temps:
            return None
        return round(self._temps[-1][1] - min(t for _, t in self._temps), 2)

    def decide(self, label: str, focus_help: Iterable[str] | None) -> dict[str, Any] | None:
        """이 자세에 낼 것. {"kind": "notice"|"wake", "arm", "helps", "specs"} — 규칙에 없는 자세면 None(원래 경로)."""
        if not self.enabled:
            return None
        rise = self.temp_rise()
        warm = rise is not None and rise >= self.warm_rise_c
        # 11-05 사용자 "원인이 자세 + 환경일 수 있잖아 — 자세 + 환경이면 조명과 선풍기 둘 다": 자세 몫은 도움 순서로 고르고,
        # 방 온도가 올랐으면(환경 몫) 선풍기를 더한다. 사람·자세와 상관없이 "더위 때문에 피로하면 선풍기".
        heat = [h for h in self.warm_only if self.helps.get(h)] if warm else []
        if label in self.notice_labels:
            plan = {"kind": "notice", "arm": "posture_card", "label": label, "helps": [], "specs": []}
        elif label in self.mild_labels or label in self.deep_labels:
            n = 2 if label in self.deep_labels else 1
            order = [str(h) for h in (focus_help or [])] or self.default_order
            helps = [h for h in order if self.helps.get(h) and (warm or h not in self.warm_only)][:n]
            plan = {"kind": "wake", "label": label, "helps": helps}
        elif heat:
            plan = {"kind": "wake", "label": "heat", "helps": []}       # 자세는 규칙 밖이지만 더위 — 선풍기만
        else:
            return None
        plan["helps"] = plan["helps"] + [h for h in heat if h not in plan["helps"]]
        if plan["kind"] == "wake" and not plan["helps"]:
            return None
        plan["specs"] = [dict(s) for h in plan["helps"] for s in self.helps[h]]
        plan["arm"] = "posture_card" if plan["kind"] == "notice" else "demo_wake:" + "+".join(plan["helps"])
        plan["causes"] = ([] if plan["label"] == "heat" else ["posture"]) + (["environment"] if heat else [])
        plan["temp_rise_c"] = rise
        self.last = {k: plan[k] for k in ("kind", "arm", "label", "helps", "causes", "temp_rise_c")}
        return plan

    def snapshot(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "temp_rise_c": self.temp_rise(),
                **({"last": self.last} if self.last else {})}
