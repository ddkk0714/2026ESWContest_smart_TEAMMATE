"""2층 사용자 프로필 — 개인화 로그를 하루 단위 생활습관 통계로 접고(JSONL 한 줄 = 하루), 최근 N일을 요약한다.

docs/personalization-spec.md §3. 입력은 personalization-log.md 의 두 줄 종류다:
  personalize_episode  — 개입 결정 한 번(수락·효과)
  personalize_session  — 세션 한 번(첫 슬럼프·슬럼프 수·휴식·시간대별 피로·피로 시 CO₂)
집계값만 남기고 원시 센서·키 내용은 받지도 않는다.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from ..mathutil import median

EPISODE_SCHEMA = "personalize_episode"
SESSION_SCHEMA = "personalize_session"


def _median(values: list[float]) -> float | None:
    vals = [float(v) for v in values if v is not None]
    return median(vals) if vals else None


def _percentile(values: list[float], pct: float) -> float | None:
    vals = sorted(float(v) for v in values if v is not None)
    if not vals:
        return None
    k = (len(vals) - 1) * pct / 100.0
    lo, hi = int(k), min(int(k) + 1, len(vals) - 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)


def _hhmm(minutes_of_day: float) -> str:
    m = int(round(minutes_of_day)) % (24 * 60)
    return f"{m // 60:02d}:{m % 60:02d}"


def _empty_arm() -> dict[str, float]:
    return {"n": 0, "accept": 0, "reject": 0, "timeout": 0, "undo": 0, "auto_kept": 0, "effect_sum": 0.0, "effect_n": 0}


@dataclass
class DailyStats:
    """하루치 집계. daily.jsonl 한 줄."""
    date: str                                   # YYYY-MM-DD (지역시)
    sessions: int = 0
    work_min: float = 0.0
    first_slump_min: list[float] = field(default_factory=list)       # 세션별
    slump_count: int = 0
    recovery_after_alert_min: list[float] = field(default_factory=list)
    fatigue_by_hour: dict[str, list[float]] = field(default_factory=dict)  # "15": [sum, n]
    session_len_min: list[float] = field(default_factory=list)
    break_interval_min: list[float] = field(default_factory=list)
    late_night_sessions: int = 0
    bedtime_samples: list[float] = field(default_factory=list)       # 분 단위(자정 넘기면 +1440)
    co2_at_fatigue: list[float] = field(default_factory=list)
    arm_stats: dict[str, dict[str, float]] = field(default_factory=dict)
    simulated: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["schema_name"] = "personalize_daily"
        d["schema_version"] = "1.0"
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DailyStats":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class ProfileSummary:
    """최근 window_days 요약. 설명 문구·취침 규칙·prior 에 쓴다."""
    days: int = 0
    first_slump_min: float | None = None
    slumps_per_hour: float | None = None
    recovery_after_alert_min: float | None = None
    fatigue_by_hour: dict[str, float] = field(default_factory=dict)
    session_len_min: float | None = None
    break_interval_min: float | None = None
    late_night_ratio: float | None = None
    usual_bedtime: str | None = None
    co2_sensitivity_ppm: float | None = None
    arm_stats: dict[str, dict[str, float | None]] = field(default_factory=dict)
    simulated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def peak_fatigue_hour(self) -> int | None:
        if not self.fatigue_by_hour:
            return None
        return int(max(self.fatigue_by_hour, key=self.fatigue_by_hour.get))


# ── 집계 ─────────────────────────────────────────────────────
def _date_of(record: dict[str, Any]) -> str:
    lt = record.get("local_time") or record.get("start_local_time") or ""
    return lt[:10]


def aggregate_daily(records: Iterable[dict[str, Any]], cfg: dict[str, Any]) -> dict[str, DailyStats]:
    """에피소드·세션 줄을 날짜별 DailyStats 로."""
    pcfg = cfg["profile"]
    late_hour = float(pcfg["late_night_hour"])
    bed_after = float(pcfg["bedtime_sample_after_hour"])
    days: dict[str, DailyStats] = {}

    def day(record: dict[str, Any]) -> DailyStats:
        key = _date_of(record)
        if key not in days:
            days[key] = DailyStats(date=key, simulated=bool(record.get("simulated", False)))
        return days[key]

    for rec in records:
        schema = rec.get("schema_name")
        if schema == SESSION_SCHEMA:
            d = day(rec)
            d.sessions += 1
            length = float(rec.get("length_min", 0.0))
            d.work_min += length
            d.session_len_min.append(length)
            if rec.get("first_slump_min") is not None:
                d.first_slump_min.append(float(rec["first_slump_min"]))
            d.slump_count += int(rec.get("slump_count", 0))
            d.recovery_after_alert_min.extend(float(v) for v in rec.get("recovery_after_alert_min", []))
            d.break_interval_min.extend(float(v) for v in rec.get("break_interval_min", []))
            for hour, (s, n) in (rec.get("fatigue_by_hour") or {}).items():
                acc = d.fatigue_by_hour.setdefault(str(hour), [0.0, 0])
                acc[0] += float(s)
                acc[1] += int(n)
            d.co2_at_fatigue.extend(float(v) for v in rec.get("co2_at_fatigue", []))
            start_h = float(rec.get("start_hour_local", 0.0))
            end_h = float(rec.get("end_hour_local", start_h))
            end_abs = end_h if end_h >= start_h else end_h + 24.0
            if end_abs >= late_hour:
                d.late_night_sessions += 1
            if end_abs >= bed_after:
                d.bedtime_samples.append(end_abs * 60.0)
        elif schema == EPISODE_SCHEMA:
            d = day(rec)
            arm = (rec.get("decision") or {}).get("arm")
            if not arm:
                continue
            st = d.arm_stats.setdefault(arm, _empty_arm())
            fb = rec.get("feedback") or {}
            verdict = fb.get("verdict")
            if verdict in st:
                st[verdict] += 1
                st["n"] += 1
            oc = rec.get("outcome") or {}
            if oc.get("reward_effect") is not None:
                st["effect_sum"] += float(oc["reward_effect"])
                st["effect_n"] += 1
    return days


def summarize(daily: Iterable[DailyStats], cfg: dict[str, Any]) -> ProfileSummary:
    pcfg = cfg["profile"]
    rows = sorted(daily, key=lambda d: d.date)[-int(pcfg["window_days"]):]
    if not rows:
        return ProfileSummary()
    first, recovery, lens, breaks, bed, co2 = [], [], [], [], [], []
    slumps, hours = 0, 0.0
    sessions, late = 0, 0
    fat: dict[str, list[float]] = defaultdict(lambda: [0.0, 0])
    arms: dict[str, dict[str, float]] = defaultdict(_empty_arm)
    for d in rows:
        first += d.first_slump_min
        recovery += d.recovery_after_alert_min
        lens += d.session_len_min
        breaks += d.break_interval_min
        bed += d.bedtime_samples
        co2 += d.co2_at_fatigue
        slumps += d.slump_count
        hours += d.work_min / 60.0
        sessions += d.sessions
        late += d.late_night_sessions
        for h, (s, n) in d.fatigue_by_hour.items():
            fat[h][0] += s
            fat[h][1] += n
        for arm, st in d.arm_stats.items():
            for k, v in st.items():
                arms[arm][k] += v
    arm_out: dict[str, dict[str, float | None]] = {}
    for arm, st in arms.items():
        answered = st["accept"] + st["reject"] + st["timeout"]
        autos = st["undo"] + st["auto_kept"]
        arm_out[arm] = {
            **st,
            "accept_rate": st["accept"] / answered if answered else None,
            "timeout_rate": st["timeout"] / answered if answered else None,
            "undo_rate": st["undo"] / autos if autos else None,
            "effect_mean": st["effect_sum"] / st["effect_n"] if st["effect_n"] else None,
        }
    bedtime = _median(bed)
    return ProfileSummary(
        days=len(rows),
        first_slump_min=_median(first),
        slumps_per_hour=(slumps / hours) if hours > 0 else None,
        recovery_after_alert_min=_median(recovery),
        fatigue_by_hour={h: s / n for h, (s, n) in fat.items() if n},
        session_len_min=_median(lens),
        break_interval_min=_median(breaks),
        late_night_ratio=(late / sessions) if sessions else None,
        usual_bedtime=_hhmm(bedtime) if bedtime is not None else None,
        co2_sensitivity_ppm=_percentile(co2, float(pcfg["co2_sensitivity_percentile"])),
        arm_stats=arm_out,
        simulated=any(d.simulated for d in rows),
    )


def bedtime_minutes(summary: ProfileSummary, cfg: dict[str, Any]) -> float:
    """취침 시각을 '그날 0시 기준 분' 으로. 자정 넘긴 취침(01:30)은 1530 으로 돌려준다."""
    text = summary.usual_bedtime or cfg["sleep"]["default_bedtime"]
    h, m = text.split(":")
    minutes = int(h) * 60 + int(m)
    after = float(cfg["profile"]["bedtime_sample_after_hour"]) * 60.0
    return minutes + 1440.0 if minutes < after else minutes


def is_after_bedtime(hour_local: float, summary: ProfileSummary | None, cfg: dict[str, Any]) -> bool:
    bed = bedtime_minutes(summary or ProfileSummary(), cfg)
    now_min = hour_local * 60.0
    if hour_local < float(cfg["profile"]["night_end_hour"]):     # 새벽은 전날 밤의 연장
        now_min += 1440.0
    return now_min >= bed


# ── JSONL 입출력 ─────────────────────────────────────────────
def write_daily(path: str, daily: Iterable[DailyStats]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for d in sorted(daily, key=lambda d: d.date):
            fh.write(json.dumps(d.to_dict(), ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def read_daily(path: str) -> list[DailyStats]:
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(DailyStats.from_dict(json.loads(line)))
    return out


def build_profile(records: Iterable[dict[str, Any]], cfg: dict[str, Any],
                  daily_path: str | None = None) -> ProfileSummary:
    """로그 → 일 단위 집계(선택: daily.jsonl 저장) → 요약."""
    daily = aggregate_daily(records, cfg)
    if daily_path is not None:
        existing = {d.date: d for d in read_daily(daily_path)}
        existing.update(daily)           # 같은 날짜는 새 집계로 교체
        write_daily(daily_path, existing.values())
        return summarize(existing.values(), cfg)
    return summarize(daily.values(), cfg)
