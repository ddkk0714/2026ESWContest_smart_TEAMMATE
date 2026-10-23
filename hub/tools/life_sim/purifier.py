"""공기청정기 타이밍 규칙(life_sim 제안) — PM2.5 로 켜고·세기를 바꾸고·끈다. FSM 과 따로 돈다.

왜 FSM 밖인가: PM2.5 는 피로 원인이 아니고, 청정은 CO₂ 를 못 내린다(feat/device-control-platform 의 environment_control.yaml 도
CO₂ → outside_air 만 허용하고 air_cleaning 규칙은 "PM 설계 검증 뒤"로 막아 두었다). 그래서 이 모듈이 그 검증용 설계다.
설정은 config/devices.yaml `purifier_control`, 숫자는 전부 가정(환경부 PM2.5 등급 좋음 ≤15·보통 ≤35·나쁨 ≤75·매우나쁨 76+).

입력은 공기청정기 자체 PM2.5 표시값(LG ThinQ 상태 조회, device_gateway 읽기 전용)을 모사한다: `sample_s` 마다 잡음을 섞어 읽고
`avg_s` 이동평균으로 판정한다. 출력은 hub control/cmd 와 같은 꼴 {"target_id": "air_purifier", "operation": "set_level", ...}.

타이밍 규칙
  켜기      평균 ≥ on_pm 이 on_hold_s 이어지면 등급 단계로 켠다.
  올리기    더 높은 등급이 up_hold_s 이어지면 바로 올린다.
  내리기    낮은 등급이 down_hold_s 이어져야 내린다(오르내림 떨림 방지, 등급 경계에 hysteresis 를 둔다).
  끄기      평균 ≤ off_pm 이 off_hold_s 이어지고 켠 지 min_on_s 가 지나야 끈다.
  조용히    정숙 시간(quiet_hours) 또는 몰입(FOCUS_*) 중엔 quiet_max_level 까지만 — 단 매우나쁨(≥ bypass_pm)이면 무시.
  자리비움  away_boost_after_s 이상 비어 있으면 한 단계 더(소음 걱정 없음), 돌아오면 다음 판정에서 원래 상한으로.
  최소 간격 명령 사이 min_cmd_gap_s.
"""
from __future__ import annotations

import datetime as _dt
import random
from collections import deque
from typing import Any

FOCUS_STATES = {"FOCUS_PC", "FOCUS_MIXED", "FOCUS_NPC"}


class PurifierController:
    def __init__(self, cfg: dict[str, Any], *, seed: int, t0: float):
        self.c = dict(cfg or {})
        self.enabled = bool(self.c.get("enabled", True))
        self.rnd = random.Random(seed + 29)
        self.samples: deque[tuple[float, float]] = deque()
        self.next_sample = t0
        self.level = 0
        self.on_since: float | None = None
        self.last_cmd = -1e18
        self.want_since: dict[int, float] = {}        # 원하는 단계 → 그 단계가 처음 나온 시각
        self.away_since: float | None = None
        self.avg: float | None = None
        self.log: list[dict[str, Any]] = []

    # ---------------------------------------------------------------- 판정
    def band_level(self, pm: float) -> int:
        """등급 → 단계. bands = [[하한, 단계], ...] 오름차순."""
        lvl = 0
        for lo, l in self.c.get("bands") or [[16, 1], [36, 2], [76, 3]]:
            if pm >= float(lo):
                lvl = int(l)
        return lvl

    def _cap(self, now: float, state: str | None, present: bool) -> int:
        cap = 3
        if self.avg is not None and self.avg >= float(self.c.get("bypass_pm", 76)):
            return 3
        hour = _dt.datetime.fromtimestamp(now).hour
        q0, q1 = self.c.get("quiet_hours") or [23, 7]
        quiet = (hour >= q0 or hour < q1) if q0 > q1 else (q0 <= hour < q1)
        if present and (quiet or (state in FOCUS_STATES)):
            cap = int(self.c.get("quiet_max_level", 2))
        return cap

    def observe(self, now: float, pm_true: float, *, present: bool, state: str | None) -> dict[str, Any] | None:
        """1 초마다 부른다. 명령이 필요하면 control/cmd 꼴 dict 를 돌려준다."""
        if not self.enabled:
            return None
        c = self.c
        if now >= self.next_sample:
            noise = float(c.get("sensor_noise", 2.0))
            self.samples.append((now, max(0.0, pm_true + self.rnd.gauss(0.0, noise))))
            self.next_sample = now + float(c.get("sample_s", 10))
        while self.samples and self.samples[0][0] < now - float(c.get("avg_s", 60)):
            self.samples.popleft()
        if not self.samples:
            return None
        self.avg = sum(v for _, v in self.samples) / len(self.samples)
        self.away_since = None if present else (self.away_since if self.away_since is not None else now)

        want = self.band_level(self.avg)
        if self.level == 0 and self.avg < float(c.get("on_pm", 35)):
            want = 0                                      # 꺼져 있으면 on_pm 전에는 켜지 않는다(보통 등급은 그냥 둔다)
        if self.level > 0 and want < self.level:
            # 내릴 때는 경계 아래로 hysteresis 만큼 더 내려가야 그 등급으로 본다
            want = self.band_level(self.avg + float(c.get("hysteresis", 5)))
            if want == 0 and self.avg > float(c.get("off_pm", 15)):
                want = 1                                  # off_pm 위면 최소 1 단계 유지
        boost = (self.away_since is not None and now - self.away_since >= float(c.get("away_boost_after_s", 300))
                 and want > 0)
        if boost:
            want = min(3, want + 1)
        want = min(want, self._cap(now, state, present))
        for k in list(self.want_since):
            if k != want:
                self.want_since.pop(k)
        self.want_since.setdefault(want, now)
        held = now - self.want_since[want]
        if want == self.level:
            return None
        if now - self.last_cmd < float(c.get("min_cmd_gap_s", 60)):
            return None
        if self.level == 0:
            need, why = float(c.get("on_hold_s", 60)), "on"
        elif want == 0:
            need, why = float(c.get("off_hold_s", 300)), "off"
            if self.on_since is not None and now - self.on_since < float(c.get("min_on_s", 600)):
                return None
        elif want > self.level:
            need, why = float(c.get("up_hold_s", 60)), "up"
        else:
            need, why = float(c.get("down_hold_s", 300)), "down"
        if held < need:
            return None
        prev = self.level
        self.level = want
        self.last_cmd = now
        if prev == 0:
            self.on_since = now
        if want == 0:
            self.on_since = None
        reason = [why, f"pm_avg={self.avg:.0f}"] + (["away_boost"] if boost else []) + (
            ["quiet_cap"] if want < self.band_level(self.avg) and not boost else [])
        rec = {"target_id": "air_purifier", "operation": "set_level", "value": want, "gate": "pm_rule",
               "undo_value": prev, "why": reason}
        self.log.append({"t": now, **rec})
        return rec


def purifier_metrics(rows: list[dict[str, Any]], commands: list[dict[str, Any]], t0: float, cfg: dict[str, Any]) -> dict[str, Any]:
    """타이밍 지표 — 나쁨 노출(재실 중), 켜기 지연, 명령 수(떨림), 켜진 시간, 몰입 중 강풍 시간."""
    on_pm = float((cfg or {}).get("on_pm", 35))
    bad = [r for r in rows if r["env"]["pm25"] >= on_pm]
    bad_present = [r for r in bad if r.get("present")]
    on_rows = [r for r in rows if (r["devices"].get("purifier") or 0) > 0]
    loud_focus = [r for r in rows if (r["devices"].get("purifier") or 0) >= 3 and r.get("present")
                  and r.get("state") in FOCUS_STATES]
    # 켜기 지연: 꺼진 상태에서 PM 이 on_pm 을 넘은 시점 → 다음 켜짐 명령
    lat: list[float] = []
    cmd_on = sorted(c["t"] for c in commands if c.get("target_id") == "air_purifier" and int(c.get("value") or 0) > 0)
    prev_bad = False
    for r in rows:
        is_bad = r["env"]["pm25"] >= on_pm and (r["devices"].get("purifier") or 0) == 0
        if is_bad and not prev_bad:
            nxt = next((t for t in cmd_on if t >= r["t"]), None)
            if nxt is not None:
                lat.append(nxt - r["t"])
        prev_bad = is_bad
    pm = [r["env"]["pm25"] for r in rows]
    return {
        "pm_max": round(max(pm), 1) if pm else None,
        "pm_mean": round(sum(pm) / len(pm), 1) if pm else None,
        "bad_min": round(len(bad) / 60, 1), "bad_present_min": round(len(bad_present) / 60, 1),
        "on_min": round(len(on_rows) / 60, 1),
        "commands": sum(1 for c in commands if c.get("target_id") == "air_purifier"),
        "on_latency_s": [round(x) for x in lat], "loud_in_focus_min": round(len(loud_focus) / 60, 1),
        "level_min": {str(l): round(sum(1 for r in rows if (r["devices"].get("purifier") or 0) == l) / 60, 1) for l in range(4)},
    }
