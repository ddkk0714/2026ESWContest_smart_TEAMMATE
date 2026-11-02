"""묻지 않고 얻는 개인화 신호 두 가지 — 순수 Python(Pi 4). docs/personalization-spec.md §5.3.

사용자 설계(2026-10-25):
  "무드등을 밝혔는데 사용자가 그 반응을 보고 다시 어둡게 하면 그 사용자는 밝음을 싫어하는 것"
      → ImplicitDimTracker: 허브가 무드등을 밝힌 뒤 `implicit.dim_window_min` 안에 사용자가 직접 어둡게 하면 음의 보상.
        증거 두 갈래 — (a) 앱 보고 `feedback/user kind=manual_adjust`(강), (b) 책상 조도(BH1750) 휴리스틱(약).
        조도는 "우리 밝힘이 조도를 눈에 띄게 올렸을 때"만 믿는다(방 조명 변화와 섞이지 않게).
  "잠에서 잘 깨면 그걸 좋은 신호로 본다"
      → WakeTracker: 졸음·엎드림에서 시작한 자세 개입 뒤 `wake.window_sec` 안에 깨어 있는 자세로 `wake.hold_sec` 이어지고
        (활동 재개 또는 C_fatigue 하락) 이면 성공. 효과 축 보상으로 들어간다.

두 추적기는 판정만 하고(정책·로그는 record.EpisodeRecorder 가 붙인다), 숫자는 전부 personalize.yaml 에서 온다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

POSTURE_KO = {"drowsy": "졸음", "face_down": "엎드림", "upright": "바른 자세", "lean_forward": "앞으로 기댐",
              "forward_head": "거북목", "recline": "뒤로 기댐", "chin_rest": "턱 괴기", "unknown": "자세 모름"}


def _num(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _percent(value: Any) -> float | None:
    """adjust_brightness 값 {"percent": n} 또는 숫자 n."""
    if isinstance(value, dict):
        return _num(value.get("percent"))
    return _num(value)


# ── 밝힘 → 직접 어둡게 ──────────────────────────────────────────
@dataclass
class LampEvent:
    """허브가 낸 무드등 명령 하나(마지막 것만 추적한다 — 그 뒤 다른 허브 명령이 오면 새 사건으로 바뀐다)."""
    command_id: str
    ts: float
    operation: str
    value: Any
    gate: str
    episode_id: str | None
    decision: Any = None
    brighten: bool | None = None        # None = 아직 모름(set_brightness 인데 previous 를 모름)
    previous: float | None = None
    actual: float | None = None
    lux_before: float | None = None
    lux_peak: float | None = None
    rise_checked: bool = False
    drop_since: float | None = None
    resolved: str | None = None         # app | lux | no_lux_rise | room_light | expired | failed


class ImplicitDimTracker:
    """personalize.yaml `implicit:` 절. on_command·on_result·on_lux·on_manual_adjust 가 신호(dict)를 돌려주면 기록한다.

    신호 dict: {kind: "manual_dim_app"|"manual_dim_lux"|"ignored", reward, episode_id, decision, text_ko, detail}.
    kind "ignored" 는 로그 전용(정책에 넣지 않는다)."""

    def __init__(self, cfg: dict[str, Any] | None):
        c = cfg or {}
        self.enabled = bool(c.get("enabled", True))
        self.target = str(c.get("lamp_target", "desk_lamp"))
        self.window = float(c.get("dim_window_min", 10)) * 60.0
        self.use_app = bool(c.get("app_report", True))
        self.use_lux = bool(c.get("lux_heuristic", True))
        self.app_reward = float(c.get("app_reward", -1.0))
        self.lux_reward = float(c.get("lux_reward", -0.5))
        self.unknown_min = c.get("brighten_unknown_min")
        self.lux_fresh = float(c.get("lux_fresh_sec", 30))
        self.settle = float(c.get("lux_settle_sec", 20))
        self.rise_ratio = float(c.get("lux_rise_min_ratio", 0.15))
        self.rise_abs = float(c.get("lux_rise_min_lx", 15))
        self.drop_ratio = float(c.get("lux_drop_ratio", 0.4))
        self.hold = float(c.get("lux_drop_hold_sec", 30))
        self.drop_max_factor = c.get("lux_drop_max_factor", 1.5)
        self.last: LampEvent | None = None
        self.level: float | None = None          # 마지막으로 안 원시 밝기(결과 actual·직접 조절 to)
        self._lux: tuple[float, float] | None = None   # (ts, lux) 마지막 조도

    # ── 허브 명령·결과 ──
    def on_command(self, payload: dict[str, Any], ts: float, *, episode_id: str | None = None,
                   decision: Any = None) -> None:
        if not self.enabled or payload.get("target_id") != self.target:
            return
        op = str(payload.get("operation") or "")
        ev = LampEvent(command_id=str(payload.get("command_id") or ""), ts=ts, operation=op,
                       value=payload.get("value"), gate=str(payload.get("gate") or ""),
                       episode_id=episode_id, decision=decision)
        if ev.gate == "undo":
            ev.brighten = False          # 되돌리기는 이미 명시적 undo 로 처리된다
        elif op == "adjust_brightness":
            pct = _percent(ev.value)
            ev.brighten = pct is not None and pct > 0
        elif op == "set_brightness":
            ev.brighten = self._set_is_brighten(_num(ev.value), self.level)
        else:
            ev.brighten = False
        if self._lux is not None and ts - self._lux[0] <= self.lux_fresh:
            ev.lux_before = self._lux[1]
        self.last = ev

    def _set_is_brighten(self, value: float | None, previous: float | None) -> bool | None:
        if value is None:
            return False
        if previous is not None:
            return value > previous
        return None

    def on_result(self, payload: dict[str, Any], ts: float) -> None:
        """control/result. 마지막 허브 무드등 명령의 결과면 previous·actual(원시 0~255)로 '정말 밝아졌는지'를 다시 본다."""
        ev = self.last
        if ev is None or payload.get("command_id") != ev.command_id:
            return
        if str(payload.get("status", "")).lower() not in ("succeeded", "executing"):
            ev.brighten, ev.resolved = False, ev.resolved or "failed"
            return
        actual = _num(payload.get("actual"))
        if actual is None and ev.operation == "set_brightness":
            actual = _num(payload.get("actual_value"))
        ev.previous, ev.actual = _num(payload.get("previous")), actual
        if actual is not None:
            self.level = actual
        if ev.gate == "undo":
            return
        if ev.operation == "set_brightness" and ev.previous is not None:
            ev.brighten = (_num(ev.value) or 0.0) > ev.previous
        elif ev.operation == "adjust_brightness" and ev.brighten and ev.previous is not None and actual is not None:
            ev.brighten = actual > ev.previous     # 이미 최대 밝기였으면 실제로는 안 밝아졌다

    def _brightened(self, ev: LampEvent) -> bool:
        if ev.brighten is None and ev.operation == "set_brightness" and self.unknown_min is not None:
            # previous 를 끝내 모를 때(옛 앱 결과): 값이 이 이상이면 밝힘으로 본다
            return (_num(ev.value) or 0.0) >= float(self.unknown_min)
        return bool(ev.brighten)

    def _open(self, ts: float) -> LampEvent | None:
        ev = self.last
        if ev is None or ev.resolved is not None or not self._brightened(ev):
            return None
        if ts - ev.ts > self.window:
            ev.resolved = "expired"
            return None
        return ev

    # ── 증거 (a) 앱 보고 ──
    def on_manual_adjust(self, data: dict[str, Any], ts: float) -> dict[str, Any] | None:
        if not self.enabled or data.get("target_id", self.target) != self.target:
            return None
        before, after = _num(data.get("from")), _num(data.get("to"))
        pct = _percent(data.get("percent"))
        if after is not None:
            self.level = after
        dimmed = (after < before) if (before is not None and after is not None) else (pct is not None and pct < 0)
        if not self.use_app or not dimmed:
            return None
        ev = self.last
        if ev is None:
            return {"kind": "ignored", "reason": "no_hub_command", "text_ko": "직접 어둡게 함 · 허브 밝힘 없음 → 무시"}
        if not self._brightened(ev):
            return {"kind": "ignored", "reason": "unrelated_command", "episode_id": ev.episode_id,
                    "text_ko": "직접 어둡게 함 · 직전 허브 명령이 밝힘 아님 → 무시"}
        if ev.resolved in ("app", "lux"):
            return None        # 이미 센 밝힘(조도 신호가 무시된 경우 no_lux_rise·room_light 는 앱 보고를 그대로 받는다)
        if ts - ev.ts > self.window:
            ev.resolved = "expired"
            return {"kind": "ignored", "reason": "outside_window", "episode_id": ev.episode_id,
                    "text_ko": f"직접 어둡게 함 · 밝힘 {(ts - ev.ts) / 60:.0f}분 뒤라 창 밖 → 무시"}
        ev.resolved = "app"
        return {"kind": "manual_dim_app", "reward": self.app_reward, "episode_id": ev.episode_id,
                "decision": ev.decision, "text_ko": "밝힘 뒤 직접 어둡게 함 → 밝은 조명 덜 고름",
                "detail": {"from": before, "to": after, "after_sec": round(ts - ev.ts, 1),
                           "hub_previous": ev.previous, "hub_actual": ev.actual}}

    # ── 증거 (b) 조도 휴리스틱 ──
    def on_lux(self, lux: float | None, ts: float) -> dict[str, Any] | None:
        if lux is None:
            return None
        self._lux = (ts, float(lux))
        if not (self.enabled and self.use_lux):
            return None
        ev = self._open(ts)
        if ev is None or ts <= ev.ts:
            return None
        if not ev.rise_checked:
            ev.lux_peak = float(lux) if ev.lux_peak is None else max(ev.lux_peak, float(lux))
            if ts - ev.ts < self.settle:
                return None
            ev.rise_checked = True
            before = ev.lux_before
            if before is None or ev.lux_peak < before * (1.0 + self.rise_ratio) or ev.lux_peak - before < self.rise_abs:
                ev.resolved = "no_lux_rise"
                return {"kind": "ignored", "reason": "no_lux_rise", "episode_id": ev.episode_id,
                        "text_ko": "밝힘 뒤 조도 상승이 안 보여 조도 신호는 쓰지 않음",
                        "detail": {"lux_before": before, "lux_peak": ev.lux_peak}}
            return None
        peak, before = float(ev.lux_peak or 0.0), float(ev.lux_before or 0.0)
        if float(lux) >= peak * (1.0 - self.drop_ratio):
            ev.drop_since = None
            return None
        if self.drop_max_factor is not None and peak - float(lux) > float(self.drop_max_factor) * (peak - before):
            # 우리 밝힘보다 훨씬 크게 떨어졌다 → 방 조명이 꺼진 것에 가깝다
            ev.resolved = "room_light"
            return {"kind": "ignored", "reason": "room_light", "episode_id": ev.episode_id,
                    "text_ko": "조도가 밝힘보다 크게 떨어짐(방 조명 변화로 봄) → 무시",
                    "detail": {"lux_before": before, "lux_peak": peak, "lux": float(lux)}}
        if ev.drop_since is None:
            ev.drop_since = ts
        if ts - ev.drop_since < self.hold:
            return None
        ev.resolved = "lux"
        return {"kind": "manual_dim_lux", "reward": self.lux_reward, "episode_id": ev.episode_id,
                "decision": ev.decision, "text_ko": "밝힘 뒤 조도가 다시 떨어짐(직접 어둡게 한 듯) → 밝은 조명 조금 덜 고름",
                "detail": {"lux_before": before, "lux_peak": peak, "lux": float(lux),
                           "after_sec": round(ts - ev.ts, 1), "hold_sec": round(ts - ev.drop_since, 1)}}


# ── 잘 깸 ──────────────────────────────────────────────────────
@dataclass
class WakeWatch:
    episode_id: str
    started: float
    trigger: str
    c_before: float
    awake_since: float | None = None
    awake_label: str | None = None
    activity: bool = False
    result: str | None = None           # success | fail
    detail: dict[str, Any] = field(default_factory=dict)


class WakeTracker:
    """personalize.yaml `wake:` 절. start() 로 감시를 열고 tick 마다 observe(). 판정이 나면 (watch, reward) 목록."""

    def __init__(self, cfg: dict[str, Any] | None):
        c = cfg or {}
        self.enabled = bool(c.get("enabled", True))
        self.window = float(c.get("window_sec", 120))
        self.hold = float(c.get("hold_sec", 20))
        self.trigger_labels = tuple(c.get("trigger_labels") or ("drowsy", "face_down"))
        self.awake_labels = tuple(c.get("awake_labels") or ("upright", "lean_forward", "forward_head", "recline", "chin_rest"))
        self.fatigue_min = c.get("fatigue_min", 0.70)
        self.fatigue_drop = float(c.get("fatigue_drop", 0.10))
        self.reward_success = float(c.get("reward_success", 1.0))
        self.reward_fail = float(c.get("reward_fail", -0.5))
        self.watches: dict[str, WakeWatch] = {}

    def wants(self, cause: str | None, label: str, drowsy: bool, c_fatigue: float) -> str | None:
        """이 결정이 깨우기 에피소드면 그 계기(라벨)를, 아니면 None."""
        if not self.enabled or cause != "posture":
            return None
        if label in self.trigger_labels:
            return label
        if drowsy:
            return "drowsy"
        if self.fatigue_min is not None and float(c_fatigue) >= float(self.fatigue_min):
            return "c_fatigue"
        return None

    def start(self, episode_id: str, now: float, trigger: str, c_fatigue: float) -> WakeWatch:
        w = WakeWatch(episode_id=episode_id, started=now, trigger=trigger, c_before=float(c_fatigue))
        self.watches[episode_id] = w
        return w

    def observe(self, now: float, label: str, c_fatigue: float, active: bool) -> list[tuple[WakeWatch, float]]:
        done: list[tuple[WakeWatch, float]] = []
        for eid, w in list(self.watches.items()):
            if now <= w.started:
                continue
            if active:
                w.activity = True
            if label in self.awake_labels:
                if w.awake_since is None:
                    w.awake_since, w.awake_label = now, label
            else:
                w.awake_since, w.awake_label = None, None
            drop = w.c_before - float(c_fatigue)
            held = w.awake_since is not None and now - w.awake_since >= self.hold
            if held and (w.activity or drop >= self.fatigue_drop):
                w.result = "success"
                w.detail = {"to_awake_sec": round(w.awake_since - w.started, 1), "hold_sec": round(now - w.awake_since, 1),
                            "awake_label": w.awake_label, "activity": w.activity, "fatigue_drop": round(drop, 4)}
                done.append((w, self.reward_success))
            elif now - w.started >= self.window:
                w.result = "fail"
                w.detail = {"last_label": label, "activity": w.activity, "fatigue_drop": round(drop, 4)}
                done.append((w, self.reward_fail))
            if w.result is not None:
                del self.watches[eid]
        return done

    def drop(self, episode_id: str) -> None:
        self.watches.pop(episode_id, None)

    @staticmethod
    def text_ko(w: WakeWatch) -> str:
        frm = POSTURE_KO.get(w.trigger, "피로 높음" if w.trigger == "c_fatigue" else w.trigger)
        if w.result == "success":
            to = POSTURE_KO.get(str(w.awake_label), str(w.awake_label))
            return f"깨우기 성공 ({frm}→{to} {w.detail.get('to_awake_sec', 0):.0f} s)"
        return f"깨우기 실패 ({frm}, {w.detail.get('last_label', '-')} 그대로)"
