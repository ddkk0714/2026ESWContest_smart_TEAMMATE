"""hub 라이브 루프의 피로 점수 — 관찰 전용(FSM 입력 아님). 표준 라이브러리만(Pi 4 제한 Python).

LiveHub 가 tick 마다 state/phase 와 같은 모양(`ts`·`c_fatigue`·`sensor_summary`)을 넘기면 period_sec(60 s)마다
그 시각으로 끝나는 5분 창 특징(features.window_features — 학습과 같은 함수) → FatigueModel → sensor_summary.fatigue.

세션 문맥(시작 KSS·어젯밤 수면·프로필)은 Pi 5 피로 기록 앱의 `deskmate/fatigue/label` session_start 에서 온다.
앱 세션이 없으면 hub FSM 세션 시작 시각만 쓴다 — 문맥 답변 특징은 비고 모델이 학습 평균으로 대신한다.
KSS 응답이 오면 그 질문 시각의 공용 예측과 비교해 프로필 보정(PersonalBias)을 갱신한다(동의가 있을 때만).
"""
from __future__ import annotations

import json
import os
import pkgutil
from collections import deque
from typing import Any, Callable

from .features import window_features
from .model import FatigueModel, PersonalBias

BIAS_FORMAT = "deskmate-fatigue-bias-1"


def load_fatigue_config(path: str | None = None) -> dict[str, Any]:
    """fatigue.yaml 을 dict 로. path 미지정 시 패키지 기본값(빌드가 만든 JSON, 없으면 YAML)."""
    if path is None:
        try:
            raw = pkgutil.get_data("deskmate_hub", "config/fatigue.json")
        except OSError:
            raw = None
        if raw is not None:
            return json.loads(raw.decode("utf-8"))
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "fatigue.yaml")
        if not os.path.exists(path):
            return {}
    import yaml

    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _fnv1a(data: bytes) -> int:
    """모델 파일 지문(model_id). 제한 Python 에 hashlib 이 있다고 가정하지 않는다."""
    h = 0x811C9DC5
    for b in data:
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h


def load_fatigue_model(cfg: dict[str, Any], *, env=os.environ,
                       on_log: Callable[[str], None] | None = None) -> tuple[FatigueModel | None, str | None]:
    """(모델, model_id). 환경변수 DESKMATE_FATIGUE_MODEL(파일) → cfg.model(패키지 안) 순. 없거나 깨졌으면 (None, None)."""
    log = on_log or (lambda m: None)
    path = env.get("DESKMATE_FATIGUE_MODEL")
    name = path or str(cfg.get("model") or "")
    try:
        if path:
            with open(path, "rb") as fh:
                raw = fh.read()
        else:
            raw = pkgutil.get_data("deskmate_hub", name) if name else None
        if raw is None:
            log("[fatigue] 모델 JSON 없음 — 피로 점수 끔")
            return None, None
        spec = json.loads(raw.decode("utf-8"))
        model = FatigueModel(spec)
    except FileNotFoundError:
        log(f"[fatigue] 모델 JSON 없음({name}) — 피로 점수 끔")
        return None, None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log(f"[fatigue] 모델을 못 읽어 피로 점수 끔: {name} ({exc})")
        return None, None
    stem = os.path.splitext(os.path.basename(name))[0] or "fatigue"
    model_id = str(spec.get("model_id") or f"{stem}-{_fnv1a(raw):08x}")
    log(f"[fatigue] 모델 {model_id} — 특징 {len(model.features)}개, 창 {model.window_sec:.0f} s")
    return model, model_id


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _profile_id(v: Any) -> str | None:
    return v if isinstance(v, str) and 0 < len(v) <= 64 else None


class FatigueRuntime:
    def __init__(self, cfg: dict[str, Any], model: FatigueModel, *, model_id: str, tick_period: float,
                 personal_allowed: Callable[[], bool] = lambda: False,
                 on_log: Callable[[str], None] | None = None) -> None:
        self.cfg = cfg
        self.model, self.model_id = model, model_id
        self.tick_period = float(tick_period)
        self.period = float(cfg.get("period_sec", 60))
        self.min_tick_ratio = float(cfg.get("min_tick_ratio", 0.8))
        self.window_sec, self.baseline_sec = model.window_sec, model.baseline_sec
        self.retain_sec = self.window_sec + float(cfg.get("label_slack_sec", 600))
        pcfg = cfg.get("personal") or {}
        self.prior_n = float(pcfg.get("prior_n", 3.0))
        self.bias_path: str | None = pcfg.get("path") or None
        self.personal_allowed = personal_allowed
        self._log = on_log or (lambda m: None)
        self.recent: deque = deque()          # 최근 retain_sec 의 tick
        self.early: list[dict[str, Any]] = []  # 지금 세션 처음 baseline_sec 의 tick (RELATIVE 특징 기준)
        self.session: dict[str, Any] | None = None   # 피로 기록 앱 session_start data
        self.hub_start: float | None = None          # hub FSM 세션 시작 (앱 세션이 없을 때 문맥)
        self._session_key: tuple | None = None
        self.profile_id: str | None = None
        self.biases: dict[str, PersonalBias] = {}
        self._seen: deque = deque(maxlen=256)  # QoS 1 재전송·앱 재발행 중복 (session_id, event, ts)
        self.view: dict[str, Any] | None = None
        self._next_at: float | None = None
        self.load_personal()

    @property
    def expected_ticks(self) -> float:
        return self.window_sec / self.tick_period if self.tick_period > 0 else 0.0

    # ---- 세션 문맥 ----
    def _context(self) -> dict[str, Any]:
        if self.session is not None:
            return self.session
        return {"ts": self.hub_start} if self.hub_start is not None else {}

    def _sync_session(self) -> None:
        ctx = self._context()
        key = (ctx.get("session_id"), ctx.get("ts"))
        if key == self._session_key:
            return
        self._session_key = key
        start = _num(ctx.get("ts"))
        self.early = [] if start is None else [
            t for t in self.recent if start <= t["ts"] < start + self.baseline_sec]

    def set_hub_session(self, started: float | None) -> None:
        self.hub_start = started
        self._sync_session()

    # ---- tick ----
    def observe(self, tick: dict[str, Any]) -> dict[str, Any] | None:
        """tick = {ts, c_fatigue, sensor_summary, ...}(state/phase data + ts). 최근 점수(없으면 None)를 돌려준다."""
        ts = float(tick["ts"])
        self.recent.append(tick)
        while self.recent and self.recent[0]["ts"] <= ts - self.retain_sec:
            self.recent.popleft()
        start = _num(self._context().get("ts"))
        if start is not None and start <= ts < start + self.baseline_sec:
            self.early.append(tick)
        if self._next_at is None or ts >= self._next_at:
            self.view = self._predict_view(ts)
            self._next_at = ts + self.period
        return self.view

    def _window(self, end_ts: float) -> tuple[dict[str, float | None], int]:
        ids = {id(t) for t in self.early}
        ticks = sorted(self.early + [t for t in self.recent if id(t) not in ids], key=lambda t: t["ts"])
        feats = window_features(ticks, end_ts, self._context(), window_sec=self.window_sec,
                                baseline_sec=self.baseline_sec)
        n = sum(1 for t in self.recent if end_ts - self.window_sec < t["ts"] <= end_ts)
        return feats, n

    def _predict_view(self, end_ts: float) -> dict[str, Any]:
        feats, n = self._window(end_ts)
        kss = self.model.predict(feats)
        bias = self.biases.get(self.profile_id) if self.profile_id is not None and self.personal_allowed() else None
        return {
            "kss": round(kss, 2),
            "kss_personal": round(bias.apply(kss), 2) if bias is not None else None,
            "window_s": int(round(self.window_sec)),
            "n_ticks": n,
            "model_id": self.model_id,
            "valid": self.expected_ticks > 0 and n >= self.min_tick_ratio * self.expected_ticks,
            "ts": round(end_ts, 3),
        }

    # ---- deskmate/fatigue/label ----
    def on_label(self, data: dict[str, Any]) -> str | None:
        """앱 응답 하나를 반영하고 event 이름을 돌려준다. 중복·모르는 기록은 None."""
        event = data.get("event")
        key = (data.get("session_id"), event, data.get("ts"))
        if key in self._seen:
            return None
        self._seen.append(key)
        if event == "session_start":
            if _num(data.get("ts")) is None:
                return None
            self.session = dict(data)
            self.profile_id = _profile_id(data.get("profile_id")) or self.profile_id
            self._sync_session()
            return event
        if event in ("kss", "session_end") and isinstance(data.get("kss"), int) and not isinstance(data["kss"], bool):
            self._update_bias(data)
        if event == "session_end" and self.session is not None \
                and data.get("session_id") == self.session.get("session_id"):
            self.session = None
            self._sync_session()
        return event if event in ("kss", "session_end", "sleepy", "prompt_missed") else None

    def _update_bias(self, data: dict[str, Any]) -> None:
        pid = _profile_id(data.get("profile_id"))
        # 학습 라벨과 같은 시각(질문이 뜬 시각, 없으면 응답 시각)의 창으로 공용 예측을 다시 만든다
        at = _num(data.get("prompt_ts"))
        at = _num(data.get("ts")) if at is None else at
        if pid is None or at is None or not self.personal_allowed():
            return
        feats, n = self._window(at)
        if n < self.min_tick_ratio * self.expected_ticks:
            self._log(f"[fatigue] 보정 건너뜀 — 질문 시각 창의 tick {n}개")
            return
        bias = self.biases.setdefault(pid, PersonalBias(self.prior_n))
        bias.update(float(data["kss"]), self.model.predict(feats))
        self._save_personal()

    # ---- 보정 저장 (privacy) ----
    def load_personal(self) -> None:
        self.biases = {}
        if not self.bias_path or not self.personal_allowed():
            return
        try:
            with open(self.bias_path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict) or raw.get("format") != BIAS_FORMAT:
            return
        if raw.get("model_id") != self.model_id:
            # 보정값은 그 모델의 빗나감이다 — 모델이 바뀌면 새로 배운다
            self._log(f"[fatigue] 보정 파일의 모델({raw.get('model_id')})이 지금 모델과 달라 새로 시작")
            return
        for pid, d in (raw.get("profiles") or {}).items():
            if _profile_id(pid) and isinstance(d, dict):
                self.biases[pid] = PersonalBias.from_json(d)

    def _save_personal(self) -> None:
        if not self.bias_path:
            return
        body = {"format": BIAS_FORMAT, "model_id": self.model_id,
                "profiles": {pid: b.to_json() for pid, b in self.biases.items()}}
        tmp = self.bias_path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self.bias_path) or ".", exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(body, fh, ensure_ascii=False)
            os.replace(tmp, self.bias_path)
        except OSError as exc:
            self._log(f"[fatigue] 보정 저장 실패: {exc}")

    def reset_personal(self) -> None:
        """동의 철회·삭제 — 메모리와 파일의 보정값을 지운다."""
        self.biases = {}
        if self.bias_path:
            try:
                if os.path.lexists(self.bias_path):
                    os.unlink(self.bias_path)
            except OSError as exc:
                self._log(f"[fatigue] 보정 파일 삭제 실패: {exc}")


def build_fatigue_runtime(cfg: dict[str, Any], *, tick_period: float,
                          personal_allowed: Callable[[], bool] = lambda: False,
                          on_log: Callable[[str], None] | None = None, env=os.environ,
                          model: FatigueModel | None = None, model_id: str | None = None) -> FatigueRuntime | None:
    """설정이 꺼져 있거나 모델이 없으면 None — 허브는 피로 점수 없이 돈다."""
    if not cfg.get("enabled"):
        return None
    if model is None:
        model, model_id = load_fatigue_model(cfg, env=env, on_log=on_log)
        if model is None:
            return None
    return FatigueRuntime(cfg, model, model_id=model_id or "custom", tick_period=tick_period,
                          personal_allowed=personal_allowed, on_log=on_log)
