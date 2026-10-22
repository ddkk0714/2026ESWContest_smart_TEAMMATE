"""자세·졸음 모델과 0x03 규칙 특징 대역.

GruPosture      dataset/tof_sim_v5 PostureNet(CNN→GRU, 9 라벨, drowsy 포함) 체크포인트를 torch 로 돌린다.
                센서별로 은닉 상태를 따로 들고 프레임마다 한 스텝, 두 센서 확률은 log-softmax 합으로 융합(tof_mlp 와 같은 규칙),
                vote_frames 다수결로 확정. ingest 의 `tof_mlp` 표본과 같은 dict 를 돌려주므로 hub 쪽 코드 변경 없이 꽂힌다.
RuleFeatures    ESP32 규칙 추정기(UART 0x03 tof_feature)의 대역. 재실(coverage)·motion_score·끄덕임 검출(nod_rate_hz,
                nods_in_window, drowsy 플래그)을 두 센서 격자에서 계산한다. 펌웨어 코드의 이식이 아니라 같은 뜻의 단순 규칙이다
                (숫자는 config/devices.yaml `rule_features`). posture 는 unknown — 자세 이름은 모델이 낸다.
OraclePosture   시험용: 정답 라벨을 그대로(선택적 지연·잡음) 내는 모델. torch·tofsim 없이 하네스를 돌린다.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Any

import numpy as np

LABELS9 = ["upright", "lean_forward", "recline", "face_down", "chin_rest", "drowsy", "away", "misplaced", "forward_head"]
VALID_STATUS = (5, 6, 9, 10)
MAX_MM = 4000.0


def to_input_np(dist: np.ndarray, status: np.ndarray) -> np.ndarray:
    """tofsim.data.to_input 과 같은 (2,H,W) float32 — tofsim 없이도 쓰려고 복제."""
    valid = np.isin(status, VALID_STATUS) & (dist >= 0)
    x = np.where(valid, dist.astype(np.float32) / MAX_MM, 0.0).astype(np.float32)
    return np.stack([x, valid.astype(np.float32)], axis=0)


def fuse_log_softmax(probs: list[np.ndarray]) -> np.ndarray:
    logs = sum(np.log(np.maximum(p, 1e-12)) for p in probs)
    logs = logs - logs.max()
    e = np.exp(logs)
    return e / e.sum()


class _Voter:
    def __init__(self, labels: list[str], vote_frames: int):
        self.labels = labels
        self.votes: deque[tuple[int, float]] = deque(maxlen=max(1, vote_frames))

    def push(self, fused: np.ndarray) -> tuple[str, float, str]:
        idx = int(fused.argmax())
        self.votes.append((idx, float(fused[idx])))
        counts: dict[int, int] = {}
        for i, _ in self.votes:
            counts[i] = counts.get(i, 0) + 1
        best = max(counts.items(), key=lambda kv: kv[1])[0]
        conf = sum(p for i, p in self.votes if i == best) / len(self.votes)
        return self.labels[best], conf, self.labels[idx]

    def reset(self) -> None:
        self.votes.clear()


class GruPosture:
    """체크포인트 → 센서별 스트리밍 추론 → 융합 → 다수결. `update(sample, now)` 는 ingest_frame 의 posture 훅 계약."""

    def __init__(self, ckpt_path: str, *, vote_frames: int = 10, warmup_frames: int = 45,
                 reset_on_away_s: float = 60.0, reset_every_s: float | None = 900.0, device: str = "cpu"):
        import torch
        from tofsim.model import PostureNet
        self.torch = torch
        try:                                   # 프레임당 한 스텝짜리 작은 망 — 스레드가 많으면 오히려 느리다
            torch.set_num_threads(int(__import__("os").environ.get("LIFESIM_TORCH_THREADS", "1")))
        except RuntimeError:
            pass
        ck = torch.load(ckpt_path, map_location="cpu", weights_only=False)
        state = ck["model"] if isinstance(ck, dict) and "model" in ck else ck
        self.labels = [str(x) for x in ck.get("labels", LABELS9)] if isinstance(ck, dict) else LABELS9
        hidden = int(ck.get("hidden", 64)) if isinstance(ck, dict) else 64
        layers = int(ck.get("gru_layers", 1)) if isinstance(ck, dict) else 1
        sensor = ck.get("sensor") if isinstance(ck, dict) else None
        h = int(sensor.get("height", 8)) if sensor else 8
        w = int(sensor.get("width", 8)) if sensor else 8
        self.net = PostureNet(h, w, len(self.labels), hidden=hidden, layers=layers)
        self.net.load_state_dict(state)
        self.net.eval().to(device)
        self.device = device
        self.step_no = int(ck.get("step", 0)) if isinstance(ck, dict) else 0
        self.info = {"ckpt": ckpt_path, "labels": self.labels, "hidden": hidden, "gru_layers": layers,
                     "params": int(sum(p.numel() for p in self.net.parameters())), "step": self.step_no,
                     "vote_frames": vote_frames, "warmup_frames": warmup_frames,
                     "reset_on_away_s": reset_on_away_s, "reset_every_s": reset_every_s}
        self.h: dict[int, Any] = {}
        self.voter = _Voter(self.labels, vote_frames)
        self.warmup = warmup_frames
        self.frames_since_reset = 0
        self.frames = 0
        self.reset_on_away_s = reset_on_away_s
        self.reset_every_s = reset_every_s
        self._away_since: float | None = None
        self._last_reset_t: float | None = None
        self._last_verdict: dict[str, Any] | None = None
        self.resets = 0
        self.last_probs: np.ndarray | None = None

    def reset(self, now: float | None = None) -> None:
        self.h.clear()
        self.voter.reset()
        self.frames_since_reset = 0
        self._last_reset_t = now
        self.resets += 1

    def update(self, sample: dict[str, Any], now: float, *, present: bool | None = None) -> dict[str, Any]:
        torch = self.torch
        self.frames += 1
        if self._last_reset_t is None:
            self._last_reset_t = now
        if self.reset_every_s and now - self._last_reset_t >= self.reset_every_s:
            self.reset(now)
        frames: dict[int, dict[str, Any]] = sample.get("sensors") or {}
        w, hgt = int(sample.get("grid_width") or 8), int(sample.get("grid_height") or 8)
        probs: list[np.ndarray] = []
        with torch.no_grad():
            for idx in sorted(frames):
                dist = np.asarray(frames[idx]["distance_mm"], dtype=np.int32).reshape(hgt, w)
                status = np.asarray(frames[idx]["status"], dtype=np.int32).reshape(hgt, w)
                x = torch.from_numpy(to_input_np(dist, status))[None, None].to(self.device)   # (1,1,2,H,W)
                logits, self.h[idx] = self.net(x, self.h.get(idx))
                probs.append(torch.softmax(logits[0, -1], dim=-1).cpu().numpy())
        self.frames_since_reset += 1
        if not probs:
            return self._verdict("unknown", 0.0, "unknown", used=0, present=bool(present))
        fused = fuse_log_softmax(probs)
        self.last_probs = fused
        posture, conf, raw = self.voter.push(fused)
        if present is not None:
            if not present:
                if self._away_since is None:
                    self._away_since = now
                elif self.reset_on_away_s and now - self._away_since >= self.reset_on_away_s and self.frames_since_reset > 1:
                    self.reset(now)
                    self._away_since = now
            else:
                self._away_since = None
        if self.frames_since_reset < self.warmup and self._last_verdict is not None:
            v = dict(self._last_verdict, warming_up=True, frames=self.frames)
            return v
        v = self._verdict(posture, conf, raw, used=len(probs), present=bool(present) if present is not None else True)
        self._last_verdict = v
        return v

    def _verdict(self, posture: str, conf: float, raw: str, *, used: int, present: bool) -> dict[str, Any]:
        return {"posture": posture, "posture_confidence": round(float(conf), 4), "present": present,
                "baseline_ready": used > 0, "raw_posture": raw, "source": "gru", "sensors_used": used,
                "frames": self.frames,
                "probs": {self.labels[i]: round(float(p), 4) for i, p in enumerate(self.last_probs)} if self.last_probs is not None else {}}


class OraclePosture:
    """정답 라벨을 그대로 내는 시험용 모델. `truth` 콜백이 현재 라벨을 준다. drowsy 는 끄덕임이 보이는 프레임부터."""

    def __init__(self, truth, *, labels: list[str] | None = None, vote_frames: int = 10, noise_p: float = 0.0, seed: int = 0):
        self.truth = truth
        self.labels = labels or LABELS9
        self.voter = _Voter(self.labels, vote_frames)
        self.rng = np.random.default_rng(seed)
        self.noise_p = noise_p
        self.frames = 0
        self.info = {"ckpt": "oracle", "labels": self.labels}
        self.last_probs = None

    def update(self, sample: dict[str, Any], now: float, *, present: bool | None = None) -> dict[str, Any]:
        self.frames += 1
        label = self.truth()
        if self.noise_p and self.rng.random() < self.noise_p:
            label = str(self.rng.choice(self.labels))
        p = np.full(len(self.labels), 0.02 / max(1, len(self.labels) - 1))
        p[self.labels.index(label) if label in self.labels else 0] = 0.98
        self.last_probs = p
        posture, conf, raw = self.voter.push(p)
        return {"posture": posture, "posture_confidence": round(conf, 4), "present": True if present is None else bool(present),
                "baseline_ready": True, "raw_posture": raw, "source": "oracle", "sensors_used": len(sample.get("sensors") or {}),
                "frames": self.frames}


# ---------------------------------------------------------------- 0x03 규칙 특징 대역

DEFAULT_RULE = {
    "min_person_mm": 150, "max_person_mm": 1300, "present_min_coverage": 0.06,
    "motion_full_mm": 60.0, "motion_ema": 0.3,
    "head_rows": 3,             # 위에서 이만큼 행을 '머리 띠' 로 본다
    "nod_mm": 120.0,            # 머리 띠의 평균 거리가 기준선보다 이만큼 멀어지면(머리가 내려가 띠가 비면) 끄덕임 후보
    "nod_rows": 0.35,           # 또는 사람 zone 의 행 무게중심이 이만큼 내려가면
    "nod_min_s": 0.3, "nod_max_s": 40.0, "baseline_tau_s": 120.0, "seed_after_s": 3.0, "window_s": 60.0,
    "noise_window_s": 60.0, "noise_k": 4.0,   # 문턱 = max(nod_mm, noise_k × 1.4826 × MAD(최근 비-끄덕임 잔차)) — 장면별 흔들림에 적응
    "drowsy_nods": 2,           # 60 s 창 안에 이만큼 끄덕이면 drowsy 플래그
}


class RuleFeatures:
    """두 센서 격자 → 0x03 tof_feature dict. 숫자는 DEFAULT_RULE 또는 설정에서."""

    def __init__(self, cfg: dict[str, Any] | None = None, fps: float = 10.0):
        self.c = dict(DEFAULT_RULE, **(cfg or {}))
        self.fps = fps
        self.prev: dict[int, np.ndarray] = {}
        self.motion = 0.0
        self.top_base: float | None = None      # 머리 띠 평균 거리 기준선 (mm)
        self.row_base: float | None = None      # 사람 zone 행 무게중심 기준선
        self.dip_since: float | None = None
        self.nods: deque[float] = deque()
        self.last_top: float | None = None
        self.frames = 0
        self.debug: dict[str, Any] = {}
        self.present_since: float | None = None
        self.reseeds = 0
        self._resid: deque[tuple[float, float, float]] = deque()   # (t, |Δhead_mm|, |Δrow|) 비-끄덕임 프레임
        self.thr_mm = float(self.c["nod_mm"])
        self.thr_rows = float(self.c["nod_rows"])

    def _person_mask(self, dist: np.ndarray, status: np.ndarray) -> np.ndarray:
        valid = np.isin(status, VALID_STATUS) & (dist >= 0)
        return valid & (dist >= self.c["min_person_mm"]) & (dist <= self.c["max_person_mm"])

    def update(self, frames: dict[int, tuple[np.ndarray, np.ndarray]], now: float) -> dict[str, Any]:
        self.frames += 1
        c = self.c
        coverage = 0.0
        tops: list[float] = []          # 머리 띠(위 head_rows 행)의 평균 거리 (mm, 무효/멀면 max_person_mm 로)
        rowc: list[float] = []          # 사람 zone 행 무게중심
        motions: list[float] = []
        valid_total = 0
        centroid: list[float] = []
        hr = int(c["head_rows"])
        for idx, (dist, status) in frames.items():
            dist = np.asarray(dist)
            status = np.asarray(status)
            valid = np.isin(status, VALID_STATUS) & (dist >= 0)
            valid_total += int(valid.sum())
            person = self._person_mask(dist, status)
            coverage = max(coverage, float(person.mean()))
            if person.any():
                rows = np.where(person.any(axis=1))[0]
                r_idx = np.arange(dist.shape[0])[:, None] * np.ones_like(dist)
                rowc.append(float((r_idx * person).sum() / person.sum()))
                head = dist[:hr].astype(float)
                head = np.where(person[:hr], head, float(c["max_person_mm"]))
                tops.append(float(head.mean()))
                centroid.append(float(dist[person].mean()))
            prev = self.prev.get(idx)
            if prev is not None:
                both = valid & np.isin(prev[1], VALID_STATUS) & (prev[0] >= 0)
                if both.any():
                    motions.append(float(np.abs(dist[both] - prev[0][both]).mean()))
            self.prev[idx] = (dist.copy(), status.copy())
        present = coverage >= c["present_min_coverage"]
        m = min(1.0, (float(np.mean(motions)) / c["motion_full_mm"])) if motions else 0.0
        self.motion = (1 - c["motion_ema"]) * self.motion + c["motion_ema"] * m
        # 끄덕임: 머리 띠가 비거나(평균 거리↑) 사람 무게중심이 내려갔다가(행↑) 느린 기준선으로 돌아오면 1회
        top = float(np.mean(tops)) if tops else None
        rc = float(np.mean(rowc)) if rowc else None
        dip = False
        if present:
            if self.present_since is None:
                self.present_since = now
        else:
            self.present_since = None
        if present and top is not None and rc is not None and now - (self.present_since or now) >= c["seed_after_s"]:
            if self.top_base is None or self.row_base is None:
                self.top_base, self.row_base = top, rc          # 앉은 뒤 조용해진 뒤에야 기준선을 잡는다
            # 머리가 떨어지면 센서 기하에 따라 띠 거리가 멀어지기도(윗행이 빔) 가까워지기도(머리가 아래 행으로 들어옴) 한다 → 양쪽
            d_mm, d_row = abs(top - self.top_base), abs(rc - self.row_base)
            dip = d_mm >= self.thr_mm or d_row >= self.thr_rows
            if dip:
                if self.dip_since is None:
                    self.dip_since = now
                elif now - self.dip_since > c["nod_max_s"]:
                    # 끄덕임이 아니라 자세가 바뀐 것(엎드림·턱괴기·졸음 휴지 자세) → 새 기준선
                    self.top_base, self.row_base, self.dip_since = top, rc, None
                    self.reseeds += 1
            else:
                if self.dip_since is not None:
                    dur = now - self.dip_since
                    if c["nod_min_s"] <= dur <= c["nod_max_s"]:
                        self.nods.append(now)
                    self.dip_since = None
                a = 1.0 / (c["baseline_tau_s"] * self.fps)
                self.top_base = (1 - a) * self.top_base + a * top
                self.row_base = (1 - a) * self.row_base + a * rc
                # 비-끄덕임 잔차로 장면 잡음을 재고 문턱을 올린다(타이핑 흔들림·호흡·자세 떨림이 큰 장면)
                self._resid.append((now, d_mm, d_row))
                while self._resid and now - self._resid[0][0] > c["noise_window_s"]:
                    self._resid.popleft()
                if len(self._resid) >= int(2 * self.fps):
                    mm = sorted(v[1] for v in self._resid)
                    rr = sorted(v[2] for v in self._resid)
                    med_mm, med_rr = mm[len(mm) // 2], rr[len(rr) // 2]
                    mad_mm = sorted(abs(v - med_mm) for v in mm)[len(mm) // 2]
                    mad_rr = sorted(abs(v - med_rr) for v in rr)[len(rr) // 2]
                    self.thr_mm = max(float(c["nod_mm"]), med_mm + c["noise_k"] * 1.4826 * mad_mm)
                    self.thr_rows = max(float(c["nod_rows"]), med_rr + c["noise_k"] * 1.4826 * mad_rr)
        else:
            self.top_base, self.row_base, self.dip_since = None, None, None
            self._resid.clear()
            self.thr_mm, self.thr_rows = float(c["nod_mm"]), float(c["nod_rows"])
        self.debug = {"head_mm": None if top is None else round(top), "head_base_mm": None if self.top_base is None else round(self.top_base),
                      "row_c": None if rc is None else round(rc, 2), "row_base": None if self.row_base is None else round(self.row_base, 2),
                      "dip": dip, "thr_mm": round(self.thr_mm), "thr_rows": round(self.thr_rows, 2)}
        while self.nods and now - self.nods[0] > c["window_s"]:
            self.nods.popleft()
        n = len(self.nods)
        drowsy = n >= int(c["drowsy_nods"])
        return {
            "present": bool(present), "posture": "unknown", "posture_confidence": 0.0,
            "motion_score": round(self.motion, 3), "head_delta_mm": None,
            "nod_rate_hz": round(n / c["window_s"], 4), "valid_zones": valid_total, "grid_width": 8, "grid_height": 8,
            "coverage_ratio": round(coverage, 3), "presence_count": int(round(coverage * 64)),
            "centroid_depth_mm": int(np.mean(centroid)) if centroid else None,
            "head_row_index": None if top is None else round(min(1.0, top / 7.0), 2), "shoulder_tilt_mm": None,
            "motion_indicator_mm_s": int(min(65535, self.motion * c["motion_full_mm"] * self.fps)),
            "posture_change_rate_per_min": 0, "baseline_deviation": 0.0, "baseline_ready": self.top_base is not None,
            "drowsy": bool(drowsy), "geometry_valid": True, "sensors_ok": 3 if len(frames) >= 2 else 1,
            "nods_in_window": n, "_nod_dip": self.dip_since is not None,
        }
