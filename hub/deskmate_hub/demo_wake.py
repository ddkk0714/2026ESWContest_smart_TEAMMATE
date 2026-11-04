"""빠른 시연의 깨우기 규칙 — 같은 자세에도 사람마다 다른 기기가 나간다(11-03 결선 대본, control.yaml `demo_wake:`).

왜: 결선 대본은 A·B 두 사람에게 같은 행동(뒤로 젖히기·졸기·거북목·턱 괴기)을 시키고, 사람마다 다른 반응을 보여 준다.
밴딧(personalize/)은 쿨다운·탐색·누적 피드백으로 순서가 바뀌어 8분 대본을 매번 같게 재현하지 못하고, 음악·선풍기 깨우기 arm 도 없다.
그래서 빠른 시연(demo_mode.py speed fast)일 때만 ACTION_POSTURE 의 기기를 이 규칙으로 고른다 — 운영(normal)은 그대로.

  지금 자세(FSM 이 ACTION_POSTURE 에 들어온 tick 의 자세 라벨)가
    notice_labels(거북목·턱 괴기)  → 기기 없이 바른 자세 안내 카드(arm posture_card)
    mild_labels(뒤로 젖히기)       → 첫 설정 '도움 순서'(focus_help) 1순위 기기 하나
    deep_labels(엎드림·졸음)       → 1·2순위 기기를 함께 ("하나로는 부족했으니 좋아하는 것과 함께" — 사용자)
  도움 순서 항목 → 기기 명령은 helps 표. 표에 없는 항목(break·voice)은 건너뛴다. 프로필이 없으면 default_order.
  모든 명령은 ignore_cooldown — 같은 사람이 같은 기기로 두 번 깨워지는 장면(B 조명 → 조명)이 디스패처 300 s 쿨다운에 막히지 않게.

제한 Python(보드)에서 돈다 — 표준 라이브러리만.
"""
from __future__ import annotations

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
        self.last: dict[str, Any] | None = None

    def decide(self, label: str, focus_help: Iterable[str] | None) -> dict[str, Any] | None:
        """이 자세에 낼 것. {"kind": "notice"|"wake", "arm", "helps", "specs"} — 규칙에 없는 자세면 None(원래 경로)."""
        if not self.enabled:
            return None
        if label in self.notice_labels:
            plan = {"kind": "notice", "arm": "posture_card", "label": label, "helps": [], "specs": []}
        elif label in self.mild_labels or label in self.deep_labels:
            n = 2 if label in self.deep_labels else 1
            order = [str(h) for h in (focus_help or [])] or self.default_order
            helps = [h for h in order if self.helps.get(h)][:n]
            if not helps:
                return None
            specs = [dict(s) for h in helps for s in self.helps[h]]
            plan = {"kind": "wake", "arm": "demo_wake:" + "+".join(helps), "label": label, "helps": helps, "specs": specs}
        else:
            return None
        self.last = {k: plan[k] for k in ("kind", "arm", "label", "helps")}
        return plan

    def snapshot(self) -> dict[str, Any]:
        return {"enabled": self.enabled, **({"last": self.last} if self.last else {})}
