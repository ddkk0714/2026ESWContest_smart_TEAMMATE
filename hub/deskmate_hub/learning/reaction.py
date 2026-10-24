"""사용자 반응으로 배우는 피로 판단(ReactionLearner) — 처음엔 규칙 FSM 으로 시작하고, 사람마다 쌓인 반응으로 배운 모델이
판단을 넘겨받는다. FSM 은 지나친 개인화(모델이 FSM 과 너무 다르게 보는 것)만 막는 울타리로 남는다.
기본 꺼짐(config/learning.yaml enabled: false). 꺼져 있으면 hub 동작은 그대로다.

10 s tick 마다
  1. 특징 x: SensorFrame 신호(δ·φ·가용) · 재실 · PC 비율 · FSM 의 C_fatigue·C_focus · 하루 중 시각 · 마지막 개입 뒤 경과 ·
     재실 기준 심박 하락(hr_drop_bpm)·mmWave 체동 — 키 입력은 keystroke 신호(공백·리듬·정정)로 들어간다.
  2. 모델 p = P(지금 피로·졸음이라 개입이 맞다) — 순수 Python 온라인 로지스틱 회귀(보드 제한 Python 에서도 돈다).
     residual(기본): p = σ(logit(c_fsm) + w·x) — 가중치 0 이면 p = c_fsm 이라 첫날부터 FSM 만큼은 맞히고,
     라벨이 FSM 과 다르게 말하는 곳만 배운다(10-23 learn14: 0 에서 배우면 0.5 근처라 FSM 을 못 넘어 α 가 0 에 머물렀다).
  3. 판단 c = c_fsm + clip(α·(p − c_fsm), −down_max, +up_max). FSM 의 임계값·유지 시간은 이 c 에 그대로 걸린다.
     α 는 0(라벨 없음 = FSM 그대로)에서 시작해 라벨이 쌓이고 모델이 FSM 보다 잘 맞힐수록 alpha_max 까지 오른다.
     clip 폭이 울타리다. 그리고 FSM 이 강한 증거(c_fsm ≥ keep_floor)를 보면 모델이 낮추지 못한다.
  4. 라벨(사람의 반응):
       피로 기록 앱 KSS 응답(deskmate/fatigue/label, 15 분마다): KSS ≥ kss_label_at 이면 1, 아니면 0 — FSM 상태와 상관없이
         고르게 들어오는 정답이라 FSM 이 틀리는 곳(놓침·헛개입)을 배울 수 있다. 앱은 수집 기간에만 있을 수 있다.
       개입(ACTION_* 진입) 뒤: 카드 수락 1 · 카드 거절 0 · 자동 실행 되돌리기 0.
         (effect·no_effect_awake: 개입 뒤 C_fsm 이 떨어졌나로 정하는 라벨 — FSM 점수로 만든 정답이라 FSM 이 맞힐 수밖에 없다.
          effect_weight 0(기본)이면 쓰지 않는다.)
       앱 무응답(prompt_missed): 자리에 있고 타이핑하지 않는데 답이 없으면 1(missed_weight, 기본 0.5) — 졸 때는 답을 못 해서
         KSS 응답만 쓰면 라벨이 깨어 있을 때로 치우치고, 모델이 '자세 증거가 크면 덜 졸리다'를 배웠다(10-23 learnfix).
       확인 카드(checkin_suggest "지금 피곤하세요?"): 모델이 애매할 때(|p − 0.5| < probe_band) probe_gap_s 마다 하나,
         세션당 probe_max 까지 — 예 1 · 아니요 0 · 무응답 버림. 최근 probe_gap_s 안에 앱 KSS 응답이 있으면 묻지 않는다.
  5. 상태 저장: state_path(JSON: 가중치·라벨 수·최근 맞힘 기록) — 세션·날을 넘어 이어 배운다.
  6. 공통모델(life_sim common, "common": true 인 상태 파일)에서 출발하면, 개인 라벨이 쌓이기 전에도 common_alpha 만큼 모델을 쓴다
     (여러 사람에게서 이미 FSM 보다 나았던 모델이라서). 개인 라벨이 쌓이면 원래 α 규칙이 이기면 그쪽을 따른다.
"""
from __future__ import annotations

import json
import math
import os
from collections import deque
from typing import Any, Callable

SIGNALS = ("keystroke", "posture", "respiration", "environment", "elapsed")
FOCUS = {"FOCUS_PC", "FOCUS_MIXED", "FOCUS_NPC"}
ACTION = {"ACTION_ENV", "ACTION_POSTURE", "ACTION_BREAK"}


def _clip(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


class OnlineLogistic:
    """AdaGrad + L2 온라인 로지스틱 회귀. 가중치 0 이면 p = σ(offset) — offset 0 이면 0.5(아무것도 모름),
    offset = logit(c_fsm) 이면 FSM 그대로(잔차 학습)."""

    def __init__(self, n: int, *, lr: float = 0.3, l2: float = 1e-3):
        self.n, self.lr, self.l2 = n, lr, l2
        self.w = [0.0] * (n + 1)          # 마지막이 bias
        self.g2 = [1e-6] * (n + 1)

    def predict(self, x: list[float], offset: float = 0.0) -> float:
        z = offset + self.w[-1] + sum(wi * xi for wi, xi in zip(self.w, x))
        return _sigmoid(z)

    def update(self, x: list[float], y: float, weight: float = 1.0, offset: float = 0.0) -> None:
        err = (self.predict(x, offset) - y) * weight
        for i in range(self.n + 1):
            xi = x[i] if i < self.n else 1.0
            g = err * xi + (self.l2 * self.w[i] if i < self.n else 0.0)
            self.g2[i] += g * g
            self.w[i] -= self.lr * g / math.sqrt(self.g2[i])

    def to_dict(self) -> dict[str, Any]:
        return {"n": self.n, "w": self.w, "g2": self.g2}

    def load(self, d: dict[str, Any]) -> None:
        if int(d.get("n", -1)) == self.n:
            self.w, self.g2 = list(d["w"]), list(d["g2"])


class ReactionLearner:
    def __init__(self, cfg: dict[str, Any], fsm_cfg: dict[str, Any] | None = None, *, state_path: str | None = None):
        self.c = dict(cfg or {})
        self.enabled = bool(self.c.get("enabled", False))
        self.state_path = state_path or self.c.get("state_path")
        self.model = OnlineLogistic(len(self.feature_names()), lr=float(self.c.get("lr", 0.3)), l2=float(self.c.get("l2", 1e-3)))
        self.n_labels = 0
        self.hist: deque[tuple[int, int, int]] = deque(maxlen=int(self.c.get("acc_window", 40)))  # (y, ai_hat, fsm_hat)
        self.pending: list[dict[str, Any]] = []      # 결과를 기다리는 개입
        self.probe: dict[str, Any] | None = None     # 답을 기다리는 확인 카드
        self.last_x: list[float] | None = None
        self.last_p = 0.5
        self.last_c_fsm = 0.0
        self.last_c_adj = 0.0
        self.last_iv_t: float | None = None
        self.last_probe_t: float | None = None
        self.last_kss_ts: float | None = None        # 마지막으로 라벨로 쓴 앱 KSS 응답 시각(같은 응답을 두 번 쓰지 않게)
        self.last_kss_t: float | None = None         # 그 응답을 받은 hub 시각(확인 카드를 겹쳐 묻지 않게)
        self.last_missed_prompt: float | None = None # 마지막으로 라벨로 쓴 무응답 질문 시각
        self.common = False                           # 공통모델(life_sim common)에서 출발했나
        self.session_probes = 0
        self.counts = {"labels_iv": 0, "labels_probe": 0, "labels_kss": 0, "labels_missed": 0, "probes": 0, "probe_yes": 0,
                       "probe_no": 0, "probe_none": 0, "pos": 0, "neg": 0}
        self.log: list[dict[str, Any]] = []
        self._load()

    # ---------------------------------------------------------------- 상태 저장
    def _load(self) -> None:
        if not self.state_path or not os.path.exists(self.state_path):
            return
        try:
            with open(self.state_path, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            return
        names = d.get("features")
        if names is not None and list(names) != self.feature_names():
            return                                      # 특징 이름·순서가 다른 상태(옛 버전·다른 기기) — 틀린 가중치를 쓰느니 FSM 에서 다시 시작
        self.model.load(d.get("model") or {})
        self.n_labels = int(d.get("n_labels", 0))
        self.common = bool(d.get("common", False))
        for row in d.get("hist") or []:
            self.hist.append(tuple(row))

    def save(self) -> None:
        if not self.state_path:
            return
        os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"model": self.model.to_dict(), "n_labels": self.n_labels, "hist": list(self.hist),
                       "features": self.feature_names(), "common": self.common}, fh)
        os.replace(tmp, self.state_path)

    # ---------------------------------------------------------------- 특징
    @staticmethod
    def feature_names() -> list[str]:
        names = [f"{k}_{a}" for k in SIGNALS for a in ("delta", "phi", "avail")]
        return names + ["present", "pc_ratio", "c_fsm", "c_focus", "hour_sin", "hour_cos", "since_iv",
                        "hr_drop", "hr_avail", "mm_motion", "mm_avail"]

    def features(self, frame, c_fsm: float, c_focus: float) -> list[float]:
        x: list[float] = []
        for k in SIGNALS:
            s = frame.get(k)
            x += [float(s.delta) if s.available else 0.0, float(s.phi) if s.available else 0.0, 1.0 if s.available else 0.0]
        import time as _t
        lt = _t.localtime(frame.now)
        h = (lt.tm_hour + lt.tm_min / 60.0) / 24.0 * 2.0 * math.pi
        since = 1.0 if self.last_iv_t is None else _clip((frame.now - self.last_iv_t) / 3600.0, 0.0, 1.0)
        hr = getattr(frame, "hr_drop_bpm", None)
        mm = getattr(frame, "mm_motion", None)
        return x + [1.0 if frame.present else 0.0, float(frame.pc_ratio), float(c_fsm), float(c_focus),
                    math.sin(h), math.cos(h), since,
                    _clip(float(hr) / 10.0, -1.0, 2.0) if hr is not None else 0.0, 1.0 if hr is not None else 0.0,
                    _clip(float(mm) / 100.0, 0.0, 1.0) if mm is not None else 0.0, 1.0 if mm is not None else 0.0]

    # ---------------------------------------------------------------- 권한(α)
    def accuracy(self) -> tuple[float | None, float | None]:
        if not self.hist:
            return None, None
        n = len(self.hist)
        return (sum(1 for y, a, _ in self.hist if y == a) / n, sum(1 for y, _, f in self.hist if y == f) / n)

    def alpha(self) -> float:
        c = self.c
        n_min, n_full = int(c.get("n_min", 8)), int(c.get("n_full", 40))
        ramp = _clip((self.n_labels - n_min) / max(1, n_full - n_min), 0.0, 1.0)
        floor = float(c.get("common_alpha", 0.3)) if self.common else 0.0
        acc_ai, acc_fsm = self.accuracy()
        if acc_ai is None:
            return floor
        q = _clip(0.5 + (acc_ai - acc_fsm) / float(c.get("acc_scale", 0.2)), 0.0, 1.0)
        personal = float(c.get("alpha_max", 0.8)) * ramp * q
        if self.common and len(self.hist) >= int(c.get("n_min", 8)) and acc_ai < acc_fsm:
            return personal                           # 이 사람에겐 공통모델이 FSM 보다 못하다 — 바닥을 걷는다
        return max(floor, personal)

    def _offset(self, c_fsm: float) -> float:
        """잔차 학습의 출발점 logit(c_fsm). c_fsm 이 0·1 에 붙어도 무한대가 되지 않게 깎는다."""
        if not self.c.get("residual", True):
            return 0.0
        q = _clip(float(c_fsm), 0.02, 0.98)
        return math.log(q / (1.0 - q))

    # ---------------------------------------------------------------- FSM 점수 보정(engine.score_adjust)
    def adjust(self, scores, frame):
        """FSMEngine 이 compute_scores 직후 부른다. c_fatigue 만 바꾸고 나머지는 그대로 둔다."""
        c_fsm = float(scores.c_fatigue)
        x = self.features(frame, c_fsm, float(scores.c_focus))
        p = self.model.predict(x, self._offset(c_fsm))
        a = self.alpha()
        c = self.c
        delta = _clip(a * (p - c_fsm), -float(c.get("down_max", 0.25)), float(c.get("up_max", 0.25)))
        if c_fsm >= float(c.get("keep_floor", 0.85)) and delta < 0:
            delta = 0.0                               # 울타리: FSM 이 강하게 보면 모델이 낮추지 못한다
        if not frame.present:
            delta = 0.0
        self.last_x, self.last_p, self.last_c_fsm = x, p, c_fsm
        self.last_c_adj = _clip(c_fsm + delta, 0.0, 1.0)
        scores.c_fatigue = self.last_c_adj
        return scores

    # ---------------------------------------------------------------- 라벨
    def _weight(self, source: str) -> float:
        c = self.c
        if source == "probe":
            return float(c.get("probe_weight", 1.0))
        if source == "kss":
            return float(c.get("kss_weight", 1.0))
        if source == "missed":
            return float(c.get("missed_weight", 0.5))
        if source in ("effect", "no_effect_awake"):
            return float(c.get("effect_weight", 0.0))   # FSM 점수로 만든 라벨 — 기본은 안 쓴다
        if source == "undo":
            return float(c.get("undo_weight", 1.0))     # 기기가 싫어 끈 것일 수도 있다 — 기기 선택을 따로 배우면 0(learn --learn-outputs)
        return 1.0

    def _label(self, x: list[float], y: int, c_fsm: float, now: float, source: str) -> None:
        w = self._weight(source)
        if w <= 0.0:
            return
        if y:
            w *= float(self.c.get("pos_weight", 1.0))   # 졸림 라벨 무게 — 라벨 대부분이 '괜찮음'이라 덜 피곤한 쪽으로 치우친다(10-24 v10·v11)
        off = self._offset(c_fsm)
        p = self.model.predict(x, off)                # 갱신 전에 맞혔는지(앞으로 보는 정확도)
        fsm_hat = 1 if c_fsm >= float(self.c.get("fsm_hat_at", 0.5)) else 0
        self.hist.append((int(y), 1 if p >= 0.5 else 0, fsm_hat))
        self.model.update(x, float(y), w, off)
        self.n_labels += 1
        self.counts[{"probe": "labels_probe", "kss": "labels_kss", "missed": "labels_missed"}.get(source, "labels_iv")] += 1
        self.counts["pos" if y else "neg"] += 1
        self.log.append({"t": now, "y": int(y), "p": round(p, 3), "c_fsm": round(c_fsm, 3), "src": source,
                         "alpha": round(self.alpha(), 3)})
        self.save()

    def after_tick(self, result, prev_state, frame, now: float) -> None:
        state = result.state.value
        entered = result.state is not prev_state
        typing = bool(frame.get("keystroke").available)
        # 개입 시작: ACTION_* 에 새로 들어온 tick 의 x 를 라벨 대기에 올린다
        if entered and state in ACTION and self.last_x is not None:
            self.pending.append({"t": now, "x": list(self.last_x), "c0": self.last_c_fsm, "typing0": typing,
                                 "gate": result.gate.value if hasattr(result.gate, "value") else str(result.gate),
                                 "request_id": None, "state": state})
            self.last_iv_t = now
        if state in ("START", "IDLE", "END") and entered:
            self.session_probes = 0
        keep = []
        for ep in self.pending:
            age = now - ep["t"]
            if age <= float(self.c.get("effect_s", 180)) and self.last_c_fsm <= ep["c0"] - float(self.c.get("recover_drop", 0.25)):
                self._label(ep["x"], 1, ep["c0"], now, "effect")
                continue
            if age > float(self.c.get("resolve_s", 600)):
                if ep["typing0"] and ep["c0"] < float(self.c.get("awake_c_max", 0.75)):
                    self._label(ep["x"], 0, ep["c0"], now, "no_effect_awake")
                continue
            keep.append(ep)
        self.pending = keep
        if self.probe is not None and now - self.probe["t"] > self.probe["expires"]:
            self.counts["probe_none"] += 1
            self.probe = None

    def on_self_report(self, kss: float, answer_ts: float, now: float) -> None:
        """피로 기록 앱의 KSS 응답(cache.fatigue_label) — 새 응답이면 이번 tick 의 특징에 라벨을 붙인다."""
        if not self.c.get("kss_label", True) or self.last_x is None:
            return
        if self.last_kss_ts is not None and answer_ts <= self.last_kss_ts:
            return
        self.last_kss_ts, self.last_kss_t = answer_ts, now
        y = 1 if float(kss) >= float(self.c.get("kss_label_at", 7)) else 0
        self._label(self.last_x, y, self.last_c_fsm, now, "kss")

    def on_prompt_missed(self, prompt_ts: float, now: float, *, present: bool, typing: bool) -> None:
        """앱 질문에 답이 없었다(prompt_missed). 자리에 있고 타이핑하지 않을 때만 졸림 라벨 — 자리를 비웠거나 일하느라
        안 본 것이면 버린다."""
        if not self.c.get("missed_label", True) or self.last_x is None:
            return
        if self.last_missed_prompt is not None and prompt_ts <= self.last_missed_prompt:
            return
        self.last_missed_prompt, self.last_kss_t = prompt_ts, now
        if present and not typing:
            self._label(self.last_x, 1, self.last_c_fsm, now, "missed")

    def link_request(self, request_id: str) -> None:
        """FSM 이 낸 카드(request)를 방금 연 개입에 묶는다 — 수락·거절이 그 개입의 라벨이 된다."""
        if self.pending and self.pending[-1]["request_id"] is None:
            self.pending[-1]["request_id"] = request_id

    def owns(self, request_id: str | None) -> bool:
        return bool(request_id) and self.probe is not None and self.probe["request_id"] == request_id

    def on_feedback(self, verdict: str, request_id: str | None, now: float) -> None:
        if self.owns(request_id):
            pr, self.probe = self.probe, None
            if verdict == "accept":
                self.counts["probe_yes"] += 1
                self._label(pr["x"], 1, pr["c0"], now, "probe")
            elif verdict == "reject":
                self.counts["probe_no"] += 1
                self._label(pr["x"], 0, pr["c0"], now, "probe")
            else:
                self.counts["probe_none"] += 1
            return
        for i, ep in enumerate(self.pending):
            hit = (request_id and ep["request_id"] == request_id) or (
                not request_id and ep["gate"] == "auto" and now - ep["t"] <= float(self.c.get("undo_s", 120)))
            if not hit:
                continue
            if verdict == "accept":
                self._label(ep["x"], 1, ep["c0"], now, "accept")
            elif verdict == "reject":
                self._label(ep["x"], 0, ep["c0"], now, "reject" if request_id else "undo")
            else:
                continue
            del self.pending[i]
            return

    # ---------------------------------------------------------------- 확인 카드
    def maybe_checkin(self, result, frame, now: float, *, busy: bool, request_id: str) -> dict[str, Any] | None:
        c = self.c
        if not c.get("probe", True) or busy or self.probe is not None or self.last_x is None or not frame.present:
            return None
        if result.state.value not in FOCUS | {"FATIGUE_SUSPECT"}:
            return None
        if self.session_probes >= int(c.get("probe_max", 6)):
            return None
        gap = float(c.get("probe_gap_s", 900))
        if self.last_probe_t is not None and now - self.last_probe_t < gap:
            return None
        if self.last_iv_t is not None and now - self.last_iv_t < gap:
            return None
        if self.last_kss_t is not None and now - self.last_kss_t < gap:
            return None                               # 앱이 방금 물었다 — 겹쳐 묻지 않는다
        if abs(self.last_p - 0.5) >= float(c.get("probe_band", 0.25)):
            return None
        expires = float(c.get("probe_expires_s", 120))
        self.probe = {"t": now, "x": list(self.last_x), "c0": self.last_c_fsm, "request_id": request_id, "expires": expires}
        self.last_probe_t = now
        self.session_probes += 1
        self.counts["probes"] += 1
        return {"request_id": request_id, "kind": "checkin_suggest", "prompt_code": "checkin_tired",
                "options": ["accept", "reject"], "expires_in_s": expires, "evidence": ["learning:checkin"],
                "cause": "learning", "c_fatigue": round(self.last_c_fsm, 4), "p_model": round(self.last_p, 4)}

    # ---------------------------------------------------------------- 요약
    def snapshot(self) -> dict[str, Any]:
        acc_ai, acc_fsm = self.accuracy()
        return {"enabled": self.enabled, "alpha": round(self.alpha(), 3), "n_labels": self.n_labels,
                "acc_ai": None if acc_ai is None else round(acc_ai, 3), "acc_fsm": None if acc_fsm is None else round(acc_fsm, 3),
                "p": round(self.last_p, 3), "c_fsm": round(self.last_c_fsm, 3), "c_adj": round(self.last_c_adj, 3),
                "pending": len(self.pending), "probe_open": self.probe is not None, **self.counts}


def load_learning_config(path: str | None = None) -> dict[str, Any]:
    """config/learning.yaml(없으면 꺼진 기본값). 보드 제한 Python 에 yaml 이 없으면 꺼진 채로 돈다."""
    path = path or os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "learning.yaml")
    try:
        import yaml
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception:  # noqa: BLE001
        return {"enabled": False}


ScoreAdjust = Callable[[Any, Any], Any]
