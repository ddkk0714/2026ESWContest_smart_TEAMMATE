"""CO₂ 창문 환기 타이밍 규칙 — CO₂ 가 높고 사람이 깨어 책상에 있을 때 창문 환기 카드(`vent_suggest`)를 먼저 띄운다. FSM 과 따로 돈다.

life_sim `tools/life_sim/ventilation.py` VentRule(7070786) 의 보드 이식. 설정은 config/co2_window.yaml(보드는 build_payload 가 만든
co2_window.json), 숫자는 전부 시뮬 근거·실측 전.

왜 FSM 밖인가: FSM 의 환경 개입(ACTION_ENV)은 피로가 확정된 뒤에야 나온다. 10-23 v8 시뮬에서 환기 카드는 사람이 이미 졸고 있을 때
떠서 응답이 없었다. CO₂ 는 피로의 결과가 아니라 원인 쪽이라 졸기 전에 내려야 한다. 환기는 학습 정책이 아니라 규칙이 맡는다.

입력(허브가 보는 값만): SCD41 CO₂(env 표본 co2_valid 일 때) · 책상 재실(SensorFrame.present) · 자세 모델 라벨(ingest 와 같은 경로:
자세 서비스 → MLP → 0x03 규칙) · FSM 상태 · 날씨 토픽(deskmate/sensor/weather) · 실내 기온(DHT22, 바깥이 더 더운지).
출력: 카드 요청 {"minutes", "why", "explanation", ...} — live.py 가 deskmate/interaction/request 로 낸다. 기기 명령은 없다.

타이밍 규칙
  켜기 조건  CO₂ 평균(avg_s) ≥ on_ppm 이 hold_s 이어지고, 책상 재실 · 깨어 있음(자세가 sleepy_labels 아님 awake_s) ·
             창문 닫힘 · 날씨가 창문을 허용(비·눈 / 바깥이 outdoor_warmer_c 이상 더움 / 바깥 PM2.5 ≥ exclude_pm25 이면 제외).
  몰입 배려  FOCUS_* 중이면 focus_defer_s 까지 몰입이 끝나기를 기다린다. 평균 ≥ high_ppm 이면 기다리지 않는다.
  재시도     카드를 띄우면 retry_s 동안 다시 묻지 않는다(무응답). 거절이면 거절 시각부터 reject_retry_s.
  FSM 존중   FSM 이 ACTION_* 이거나 다른 카드가 떠 있으면 내지 않는다(blocked). 정숙 시간(personalize bandit.quiet_hours)도.

창문 상태 가정: 보드에 창문 센서가 없다. 카드 수락 = 창문을 열었다고 보고 권유 길이(분) 동안 열림, 그 뒤 닫힘 — 닫힌 뒤에는
hold_s 를 처음부터 다시 잰다. CO₂ 기울기로 열림을 추정하지 않는다(시뮬에 없고 검증 전).
시뮬과 다른 점: SCD41 값이 avg_s 동안 하나도 없으면 높음 지속 시간도 처음부터 다시 잰다(측정 없이 5 분을 채우지 않는다).

제한 Python(보드)에서 돈다 — 표준 라이브러리(collections·json·os·pkgutil·time)와 personalize 의 순수 Python 만 쓴다.
"""
from __future__ import annotations

import json
import os
import pkgutil
import time
from collections import deque
from typing import Any, Callable

try:  # 창문 열 시간은 personalize.yaml `ventilation:` 계산을 그대로 쓴다(중복 금지). 못 불러오면 fallback_minutes.
    from .personalize.features import Situation, season_of
    from .personalize.policy import InterventionPolicy
    from .personalize.weather import weather_from_sample
    _ventilation_plan = InterventionPolicy.ventilation_plan
except ImportError:  # pragma: no cover
    Situation = None  # type: ignore[assignment,misc]
    weather_from_sample = None  # type: ignore[assignment]
    _ventilation_plan = None

    def season_of(month: int) -> str:  # type: ignore[misc]
        return {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
                6: "summer", 7: "summer", 8: "summer"}.get(month, "autumn")

CARD_KIND = "vent_suggest"
PROMPT_CODE = "ventilate_window"
ARM_ID = "ventilate_suggest"          # personalize.yaml arms 의 창문 환기 — 화면이 같은 이름표를 쓰게
ARM_LABEL = "창문 환기"

_CONFIG_DIR = os.path.join(os.path.dirname(__file__), "config")
DEFAULT_PATH = os.path.join(_CONFIG_DIR, "co2_window.yaml")


def load_co2_window_config(path: str | None = None) -> dict[str, Any]:
    """co2_window.yaml → dict. path 미지정 시 패키지 기본값(보드 payload 의 co2_window.json, 없으면 YAML)."""
    if path is None:
        try:
            raw = pkgutil.get_data("deskmate_hub", "config/co2_window.json")
        except OSError:
            raw = None
        if raw is not None:
            return json.loads(raw.decode("utf-8"))
        path = DEFAULT_PATH
    import yaml

    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


class _CfgOnly:
    """InterventionPolicy.ventilation_plan 은 self.cfg 만 본다 — 밴딧 모델을 만들지 않고 계산만 빌린다."""

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg


class Co2WindowRule:
    """observe() 를 tick 마다(보드 10 s) 부른다. 카드를 띄울 때만 dict 를 돌려준다."""

    def __init__(self, cfg: dict[str, Any] | None, *, personalize_cfg: dict[str, Any] | None = None,
                 on_log: Callable[[str], None] | None = None):
        self.c = dict(cfg or {})
        self.enabled = bool(self.c.get("enabled", False))
        self.pcfg = dict(personalize_cfg or {})
        self.wcfg = dict(self.c.get("weather") or {})
        self.on_log = on_log or (lambda m: None)
        self.sleepy = set(self.c.get("sleepy_labels") or ("drowsy", "face_down"))
        self.focus_states = set(self.c.get("focus_states") or ("FOCUS_PC", "FOCUS_MIXED", "FOCUS_NPC"))
        self.samples: deque = deque()
        self._last_received: float | None = None
        self.avg: float | None = None
        self.high_since: float | None = None
        self.awake_since: float | None = None
        self.focus_wait_since: float | None = None
        self.next_allowed: float | None = None          # 첫 observe 시각으로 잡는다
        self.window_open_until: float | None = None
        self.last_card: dict[str, Any] | None = None
        self.excluded_logged = False
        self.last_skip: str | None = None
        self.counts = {"cards": 0, "accept": 0, "reject": 0, "timeout": 0, "excluded": 0}

    def _f(self, key: str, default: float) -> float:
        return float(self.c.get(key, default))

    # ── 시각 ─────────────────────────────────────────────────
    def _local(self, now: float) -> time.struct_time:
        return time.gmtime(now + float(self.pcfg.get("local_utc_offset_hours", 9)) * 3600.0)

    def _quiet(self, now: float) -> bool:
        if not self.c.get("respect_quiet_hours", True):
            return False
        q = (self.pcfg.get("bandit") or {}).get("quiet_hours")
        if not q:
            return False
        lt = self._local(now)
        hour = lt.tm_hour + lt.tm_min / 60.0
        start, end = float(q["start"]), float(q["end"])
        return (start <= hour < end) if start <= end else (hour >= start or hour < end)

    # ── 날씨·창문 시간 ───────────────────────────────────────
    def weather_view(self, sample: Any, now: float) -> tuple[dict[str, Any] | None, float | None]:
        """날씨 표본 → (personalize 정규화 날씨 | None, 바깥 PM2.5 | None). 낡음 기준은 personalize.yaml weather.stale_sec."""
        if sample is None or weather_from_sample is None or "weather" not in self.pcfg:
            return None, None
        try:
            w = weather_from_sample(sample, now, self.pcfg)
        except (KeyError, TypeError, ValueError):
            return None, None
        if w is None:
            return None, None
        pm = sample.data.get("pm25") if isinstance(sample.data, dict) else None
        try:
            pm_f = None if pm is None else float(pm)
        except (TypeError, ValueError):
            pm_f = None
        return w, pm_f

    def window_choice(self, w: dict[str, Any] | None, pm25: float | None, indoor_temp: float | None) -> dict[str, Any]:
        """시뮬 DeviceWorld.ventilation_choice 와 같은 규칙. {"choice": "window"|"excluded", "why": [...]}."""
        if w is None:
            if str(self.wcfg.get("when_unknown", "allow")) == "allow":
                return {"choice": "window", "why": ["weather_unknown"]}
            return {"choice": "excluded", "why": ["weather_unknown"]}
        why: list[str] = []
        if str(w.get("condition")) in tuple(self.wcfg.get("exclude_conditions") or ("rain", "snow")):
            why.append(str(w.get("condition")))
        outdoor = w.get("outdoor_temp_c")
        if outdoor is not None and indoor_temp is not None and \
                float(outdoor) - float(indoor_temp) >= float(self.wcfg.get("outdoor_warmer_c", 2.0)):
            why.append("outdoor_warmer")
        if (pm25 is not None and pm25 >= float(self.wcfg.get("exclude_pm25", 36))) or w.get("pm_bad"):
            why.append("outdoor_pm_bad")
        if why:
            return {"choice": "excluded", "why": why}
        wind = w.get("wind_ms")
        if wind is not None and float(wind) >= float(self.wcfg.get("windy_ms", 4.0)):
            why.append("windy")
        return {"choice": "window", "why": why}

    def minutes(self, now: float, indoor_temp: float | None, w: dict[str, Any] | None) -> tuple[int, list[str]]:
        """창문 열 시간(분)과 그 이유 — personalize.yaml `ventilation:` 계산(InterventionPolicy.ventilation_plan) 그대로."""
        lt = self._local(now)
        if _ventilation_plan is not None and Situation is not None and "ventilation" in self.pcfg:
            try:
                sit = Situation(ts=now, hour_local=lt.tm_hour + lt.tm_min / 60.0, weekday=lt.tm_wday, month=lt.tm_mon,
                                temp_c=indoor_temp, weather=w)
                return _ventilation_plan(_CfgOnly(self.pcfg), sit)
            except (KeyError, TypeError, ValueError):
                pass
        winter = season_of(lt.tm_mon) == "winter"
        return int(self._f("fallback_winter_minutes" if winter else "fallback_minutes", 5 if winter else 15)), []

    # ── 판정 ─────────────────────────────────────────────────
    def observe(self, now: float, co2: float | None, *, received: float | None = None, present: bool,
                posture: str | None, state: str | None, blocked: bool = False,
                weather: dict[str, Any] | None = None, pm25: float | None = None,
                indoor_temp: float | None = None) -> dict[str, Any] | None:
        """tick 마다 부른다. co2 = 이번 tick 의 SCD41 유효값(없으면 None), received = 그 표본 수신 시각(같은 표본 두 번 세지 않게).
        blocked = FSM 이 ACTION_* 이거나 다른 카드가 떠 있음. 카드를 띄울 때만 dict 를 돌려준다."""
        if not self.enabled:
            return None
        c = self.c
        if self.next_allowed is None:
            self.next_allowed = now
        if co2 is not None and (received is None or received != self._last_received):
            self.samples.append((now if received is None else float(received), float(co2)))
            self._last_received = received
        while self.samples and self.samples[0][0] < now - self._f("avg_s", 60):
            self.samples.popleft()
        window_open = self.window_open_until is not None and now < self.window_open_until
        if self.window_open_until is not None and not window_open:
            self.window_open_until = None                  # 권유 시간이 지나면 닫힌 것으로 본다
        awake = bool(present) and (posture not in self.sleepy)
        self.awake_since = (self.awake_since if self.awake_since is not None else now) if awake else None
        if not self.samples:
            self.avg, self.high_since = None, None
            return self._skip("no_co2")
        self.avg = sum(v for _, v in self.samples) / len(self.samples)
        high = self.avg >= self._f("on_ppm", 1000)
        self.high_since = (self.high_since if self.high_since is not None else now) if (high and not window_open) else None
        if window_open:
            self.focus_wait_since = None
            return self._skip("window_open")
        if self.high_since is None:
            self.focus_wait_since = None
            return self._skip("co2_ok")
        if now < self.next_allowed:
            self.focus_wait_since = None
            return self._skip("retry_wait")
        if now - self.high_since < self._f("hold_s", 300):
            return self._skip("hold")
        if self.awake_since is None or now - self.awake_since < self._f("awake_s", 60):
            return self._skip("not_awake" if self.awake_since is None else "awake_hold")
        if blocked:
            return self._skip("blocked")
        if self._quiet(now):
            return self._skip("quiet_hours")
        vent = self.window_choice(weather, pm25, indoor_temp)
        if vent["choice"] != "window":
            if not self.excluded_logged:        # 날씨로 창문 제외 — 카드 없음(선풍기 등은 FSM ACTION_ENV·정책이 맡는다)
                self.counts["excluded"] += 1
                self.on_log(f"[co2_window] CO₂ {self.avg:.0f} ppm 이지만 날씨로 창문 제외({','.join(vent['why'])}) — 카드 없음")
                self.excluded_logged = True
            return self._skip("weather_excluded")
        self.excluded_logged = False
        urgent = self.avg >= self._f("high_ppm", 1400)
        if state in self.focus_states and not urgent:
            if self.focus_wait_since is None:
                self.focus_wait_since = now
            if now - self.focus_wait_since < self._f("focus_defer_s", 600):
                return self._skip("focus_wait")
        waited = 0.0 if self.focus_wait_since is None else now - self.focus_wait_since
        self.focus_wait_since = None
        self.next_allowed = now + self._f("retry_s", 1200)
        minutes, reasons = self.minutes(now, indoor_temp, weather)
        why = ["co2_rule", f"co2_avg={self.avg:.0f}"] + (["urgent"] if urgent else []) + (
            ["after_focus_wait"] if waited > 0 else []) + list(vent["why"])
        held_min = int((now - self.high_since) // 60)
        explanation = (f"CO₂ {self.avg:.0f} ppm 이 {held_min} 분째 높아요 — 창문을 {minutes} 분만 열어 주세요"
                       + (f" ({', '.join(reasons)})" if reasons else ""))
        rec = {"minutes": minutes, "why": why, "explanation": explanation, "co2_avg": round(self.avg),
               "urgent": urgent, "focus_wait_s": round(waited), "minutes_reasons": reasons}
        self.last_card = {"t": now, **rec}
        self.counts["cards"] += 1
        self.last_skip = None
        return rec

    def _skip(self, reason: str) -> None:
        self.last_skip = reason
        return None

    def on_feedback(self, verdict: str, now: float) -> None:
        """이 규칙이 낸 카드의 응답. accept = 창문을 열었다고 보고 권유 시간 동안 열림. reject 면 더 오래 쉰다."""
        if verdict in self.counts:
            self.counts[verdict] += 1
        if verdict == "accept":
            minutes = float((self.last_card or {}).get("minutes") or self._f("fallback_minutes", 15))
            self.window_open_until = now + minutes * 60.0
            self.high_since = None
        elif verdict == "reject":
            self.next_allowed = max(self.next_allowed or now, now + self._f("reject_retry_s", 2400))

    def snapshot(self, now: float) -> dict[str, Any]:
        """state/phase sensor_summary.co2_window — 화면·실기 로그용."""
        return {
            "co2_avg": None if self.avg is None else round(self.avg),
            "high_for_s": None if self.high_since is None else round(now - self.high_since),
            "window_open_s": None if self.window_open_until is None else max(0, round(self.window_open_until - now)),
            "next_in_s": None if self.next_allowed is None else max(0, round(self.next_allowed - now)),
            "waiting": self.last_skip, "counts": dict(self.counts),
        }

    # ── 허브 캐시에서 입력 꺼내기 ─────────────────────────────
    def observe_view(self, view: Any, now: float, ingest_cfg: dict[str, Any], *, present: bool, state: str | None,
                     blocked: bool) -> dict[str, Any] | None:
        """LiveHub 가 tick 마다 부르는 입구. CacheView 에서 CO₂·실내 기온·자세 라벨·날씨를 꺼내 observe() 로."""
        if not self.enabled:
            return None
        from .ingest.mapping import _environment_values, _tof_sample, _tof_with_model

        fresh = ingest_cfg.get("freshness_sec") or {}
        env = view.fresh("env", now, fresh.get("env", 30))
        values = _environment_values(env.data) if env is not None else {}
        posture = None
        tof = _tof_sample(view, now, ingest_cfg) if "tof" in ingest_cfg and fresh else None
        if tof is not None:
            posture = str(_tof_with_model(view, tof.data, now, ingest_cfg).get("posture") or "unknown") \
                if tof.data.get("present", False) else "away"
        weather, pm25 = self.weather_view(getattr(view, "weather", None), now)
        return self.observe(now, values.get("co2_ppm"), received=env.received if env is not None else None,
                            present=present, posture=posture, state=state, blocked=blocked,
                            weather=weather, pm25=pm25, indoor_temp=values.get("temp_c"))
