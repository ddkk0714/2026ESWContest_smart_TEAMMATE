"""캘리브레이션 기준 대비 규칙 기반 자세/졸음 라벨러 (스트리밍 + 오프라인 재라벨링 공용)."""
import math
import warnings
from collections import deque

import numpy as np

from .geometry import FEATURES

FLAGS = ["chin_rest", "forward_head", "head_down", "slouch", "lean_forward", "lean_left",
         "lean_right", "head_tilt", "too_close"]
LABEL_KO = {"normal": "정상", "chin_rest": "턱 괴기", "forward_head": "거북목",
            "head_down": "고개 숙임", "slouch": "구부정",
            "lean_forward": "상체 앞으로", "lean_left": "왼쪽 기울임", "lean_right": "오른쪽 기울임",
            "head_tilt": "고개 갸웃", "too_close": "화면 너무 가까움", "invalid": "측정 불가",
            "drowsy": "졸음"}


def _finite(x):
    return x is not None and math.isfinite(x)


def _any(*checks):
    """checks: (값, 임계값) - 값 >= 임계값 중 하나라도 참이면 True. 모든 값이 NaN이면 None."""
    vals = [(v, t) for v, t in checks if _finite(v)]
    if not vals:
        return None
    return any(v >= t for v, t in vals)


def baseline_usable(baseline):
    """숙임·졸음에 쓸 고개 기준이 하나도 없으면 재측정한다."""
    return any(_finite(baseline.get(k)) for k in ("head_pitch", "nose_drop"))


def build_baseline(cfg, rows):
    """프레임 가용성과 고개 특징별 가용성을 함께 확인한다."""
    valid = [r for r in rows if r is not None]
    ratio = cfg["calibration"]["min_valid_ratio"]
    if not rows or len(valid) < ratio * len(rows):
        return None
    usable = {k: sum(_finite(r[k]) for r in valid) >= ratio * len(rows)
              for k in ("head_pitch", "nose_drop")}
    if not any(usable.values()):
        return None
    arr = np.array([[r[k] for k in FEATURES] for r in valid], float)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        med = np.nanmedian(arr, axis=0)
    baseline = {k: float(v) for k, v in zip(FEATURES, med)}
    for k, ok in usable.items():
        if not ok:
            baseline[k] = float("nan")
    return baseline


class Hysteresis:
    def __init__(self, on_s, off_s):
        self.on_s, self.off_s = on_s, off_s
        self.state, self.since = False, None

    def update(self, t, cond):
        if cond is None:
            return self.state
        if cond != self.state:
            if self.since is None:
                self.since = t
            if t - self.since >= (self.on_s if cond else self.off_s):
                self.state, self.since = cond, None
        else:
            self.since = None
        return self.state


class Calibrator:
    """준비 시간(prep_s) 뒤, 바른 자세로 앉은 N초 동안의 특징값 중앙값 = 기준(baseline).

    준비 시간이 없으면 창이 뜨자마자 안내문을 읽으려 화면 쪽으로 다가간 자세가 기준이 된다
    (2026-10-04 프로토콜에서 실제로 정상 구간보다 8~19cm 앞이 기준으로 잡혔다).
    """

    def __init__(self, cfg):
        self.cfg = cfg
        c = cfg["calibration"]
        self.seconds, self.min_ratio = c["seconds"], c["min_valid_ratio"]
        self.prep_s = c.get("prep_s", 0)
        self.t_start = None
        self.reset()

    def reset(self):
        self.t0, self.rows, self.n_total = None, [], 0

    def in_prep(self, t):
        return self.t_start is None or t - self.t_start < self.prep_s

    def add(self, t, feats):
        """기준이 확정되면 baseline dict 반환, 아니면 None."""
        if self.t_start is None:
            self.t_start = t
        if self.in_prep(t):
            return None
        if self.t0 is None:
            self.t0 = t
        self.n_total += 1
        self.rows.append(feats)
        if t - self.t0 < self.seconds:
            return None
        baseline = build_baseline(self.cfg, self.rows)
        if baseline is None:
            print("[calib] 유효 프레임 또는 고개 기준 부족 - 다시 측정")
            self.reset()
            return None
        return baseline

    def progress(self, t):
        return 0.0 if self.t0 is None else min(1.0, (t - self.t0) / self.seconds)

    def prep_remaining(self, t):
        return self.prep_s if self.t_start is None else max(0.0, self.prep_s - (t - self.t_start))


class DrowsinessDetector:
    """눈/입 신호 없이 머리 동역학만으로 졸음 판단.

    - 끄덕임(nod): 직전 nod_lookback_s 의 가장 든 각도에서 nod_amp_deg 이상 떨어지고(정점은
      기준 대비 nod_min_peak_deg 이상), 정점에서 nod_recover_s 안에 떨어진 폭의
      nod_recover_frac 만큼 되돌아오면 1회. 원래 각도까지 다 돌아올 필요는 없다 - 조는 사람은
      반쯤 들었다가 다시 떨군다 (10-04 2차 프로토콜 실측: 0.5~1.5초에 15~44° 낙하, 정점 후 0.5~1초 복귀).
    - 졸음 판정 뒤 고개를 떨군 채 있으면(깊이 조는 상태) 졸음을 유지한다. 떨군 자세만으로는
      책상 보기와 각도·흔들림이 같아 구분이 안 되므로, 앞선 끄덕임이 있을 때만 이어 붙인다.
    - 지속 떨굼: 고개를 떨군 채 머리 위치가 거의 움직이지 않음 (sustained_s 이상)
    - 흔들림(sway): 머리 위치 표준편차 (점수에만 반영)
    """

    def __init__(self, cfg):
        self.c = cfg["drowsiness"]
        self.nods = deque()
        self.pos = deque()
        self.body = deque()
        self.pitch_hist = deque()
        self.in_drop = False
        self.drop_t0 = self.peak_t = None
        self.start = self.peak = 0.0
        self.last_trigger = -1e9
        self.active = False

    def _std(self, window_s, t):
        pts = np.array([(x, z) for tt, x, z in self.pos if t - tt <= window_s])
        if len(pts) < 5:
            return float("nan")
        return float(np.sqrt(pts.var(axis=0).sum()))

    def _body_moved(self, t0, t):
        """t0~t 동안 몸통(어깨 높이·몸통 깊이)이 움직였나. 끄덕임은 고개만 움직인다.

        10-04 1·2차 프로토콜: 진짜 끄덕임 높이 변화 ≤ 2.0cm·깊이 ≤ 4.7cm,
        상체 숙였다 들기는 높이 2.4~5.7cm 또는 깊이 15~20cm.
        """
        pts = np.array([(y, z) for tt, y, z in self.body if t0 <= tt <= t])
        if len(pts) < 3:
            return False
        rng = np.nanmax(pts, axis=0) - np.nanmin(pts, axis=0)
        return rng[0] > self.c["nod_body_y_m"] or rng[1] > self.c["nod_body_z_m"]

    def update(self, t, pitch, head_x, head_z, sh_y=float("nan"), sh_z=float("nan"), chin_rest=False,
               leaning=False):
        c = self.c
        if _finite(head_x) and _finite(head_z):
            self.pos.append((t, head_x, head_z))
        while self.pos and t - self.pos[0][0] > max(c["sway_window_s"], c["still_window_s"]):
            self.pos.popleft()
        if _finite(sh_y) and _finite(sh_z):
            self.body.append((t, sh_y, sh_z))
        while self.body and t - self.body[0][0] > c["nod_max_s"] + c["nod_lookback_s"]:
            self.body.popleft()
        while self.nods and t - self.nods[0] > c["nod_window_s"]:
            self.nods.popleft()

        sustained = False
        # 얼굴 랜드마크가 무너진 프레임(10-04 3차: -67°)은 끄덕임 판단에서 뺀다
        if _finite(pitch) and abs(pitch) > c["pitch_valid_deg"]:
            pitch = float("nan")
        if _finite(pitch):
            self.pitch_hist.append((t, pitch))
            while t - self.pitch_hist[0][0] > c["nod_lookback_s"]:
                self.pitch_hist.popleft()
            if not self.in_drop:
                low = min(p for _, p in self.pitch_hist)
                if pitch - low >= c["nod_amp_deg"]:
                    self.in_drop, self.drop_t0, self.start = True, t, low
                    self.peak, self.peak_t = pitch, t
            else:
                if pitch > self.peak:
                    self.peak, self.peak_t = pitch, t
                back = self.peak - c["nod_recover_frac"] * (self.peak - self.start)
                if pitch <= back:
                    # 턱 괴기 중에는 손이 얼굴을 가려 고개 각도가 튀므로 세지 않는다.
                    # 상체를 숙인 채 고개가 흔들리는 것도 세지 않는다 (10-04 3차 상체 앞으로 구간 3회)
                    if (t - self.peak_t <= c["nod_recover_s"] and t - self.drop_t0 <= c["nod_max_s"]
                            and self.peak >= c["nod_min_peak_deg"] and not chin_rest and not leaning
                            and not self._body_moved(self.drop_t0 - c["nod_lookback_s"], t)):
                        self.nods.append(t)
                    self.in_drop = False
                    # 낙하 시작점을 복귀 지점부터 다시 잡는다. 이전 최저점이 남아 있으면 반쯤 돌아온
                    # 같은 동작이 바로 다음 프레임에 또 낙하로 잡혀 두 번 세어진다 (10-04 0.1초 간격 2회)
                    self.pitch_hist.clear()
                    self.pitch_hist.append((t, pitch))
                elif t - self.peak_t > c["nod_max_s"]:
                    # 끄덕임으로 이미 켜진 졸음은 깊이 떨군 동안 유지한다.
                    # 끄덕임 검출 제한 시간으로 이 유지 상태까지 끊지 않는다.
                    if not (self.active and pitch >= c["nod_min_peak_deg"]
                            and not chin_rest and not leaning
                            and not self._body_moved(self.drop_t0 - c["nod_lookback_s"], t)):
                        self.in_drop = False
            if self.in_drop and t - self.drop_t0 >= c["sustained_s"]:
                still = self._std(c["still_window_s"], t)
                sustained = _finite(still) and still <= c["still_std_m"]

        sway = self._std(c["sway_window_s"], t)
        # 졸음은 끄덕임으로만 켠다. 숙인 채 정지(sustained)는 책상 보기와 구분이 안 돼
        # (10-04 1·2차 프로토콜에서 고개 숙임 구간마다 켜짐) 점수에만 반영한다.
        # 끄덕임이 일어난 그 순간에만 켠다 (창 안에 2회가 남아 있다고 1분 내내 켜지 않게)
        if len(self.nods) >= c["nod_count"] and self.nods[-1] == t:
            self.last_trigger, self.active = t, True
        # 졸음이 켜진 상태에서 시작된 떨굼이 이어지는 동안(깊이 조는 중)만 유지.
        # 몸통이 움직였으면 상체를 숙인 것이지 조는 것이 아니다 (10-04 상체 앞으로 구간 전체가 이어 붙음)
        hanging = (self.active and self.in_drop and self.drop_t0 <= self.last_trigger + c["hold_s"]
                   and _finite(pitch) and pitch >= c["nod_min_peak_deg"]
                   and not chin_rest and not leaning
                   and not self._body_moved(self.drop_t0 - c["nod_lookback_s"], t))
        if hanging:
            self.last_trigger = t
        drowsy = t - self.last_trigger <= c["hold_s"]
        if not drowsy:
            self.active = False
        score = (0.5 * min(len(self.nods) / c["nod_count"], 1.0) + 0.3 * float(sustained or hanging)
                 + 0.2 * (min(sway / c["sway_ref_m"], 1.0) if _finite(sway) else 0.0))
        return {"drowsy": int(drowsy), "drowsy_score": round(score, 3),
                "nods_window": len(self.nods), "head_sway": sway}


class PostureLabeler:
    def __init__(self, cfg, baseline):
        if not baseline_usable(baseline):
            raise ValueError("고개 기준이 없는 캘리브레이션입니다. 다시 측정하세요.")
        self.cfg, self.base = cfg, baseline
        s = cfg["smoothing"]
        self.buf = deque(maxlen=s["median_frames"])
        self.hyst = {k: Hysteresis(s["on_s"], s["off_s"]) for k in FLAGS}
        self.drowsy = DrowsinessDetector(cfg)
        self.last_t = None

    def _reset_tracking(self):
        self.buf.clear()
        for h in self.hyst.values():
            h.state, h.since = False, None
        self.drowsy = DrowsinessDetector(self.cfg)

    def _smooth(self, feats):
        self.buf.append([feats[k] for k in FEATURES])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            med = np.nanmedian(np.array(self.buf, float), axis=0)
        return dict(zip(FEATURES, med))

    def _chin_rest(self, f, c):
        """손이 턱(없으면 코) 근처 + 그 팔의 팔꿈치가 어깨 아래. 기준 자세와 무관한 절대 판정.

        켜진 뒤에는 release_m 까지 멀어져야 꺼진다 (문턱에 걸친 손목이 라벨을 뒤집지 않게).
        팔꿈치가 화면 밖(책상 아래)이라 안 보이면 손 거리만으로 판단한다.
        """
        near = c["release_m"] if self.hyst["chin_rest"].state else c["near_m"]
        d = f["hand_face_dist"]
        if not _finite(d):
            return False
        return d <= near and f["arm_folded"] != 0  # NaN(팔꿈치 안 보임) 허용

    def update(self, t, feats):
        if self.last_t is not None and (t <= self.last_t
                                       or t - self.last_t > self.cfg["smoothing"]["max_gap_s"]):
            self._reset_tracking()
        self.last_t = t
        if feats is None:
            self._reset_tracking()
            out = {k: None for k in FLAGS}
            out.update(posture="invalid", drowsy=None, drowsy_score=None, nods_window=None,
                       head_sway=None)
            return out
        f = self._smooth(feats)
        d = {k: f[k] - self.base[k] for k in FEATURES}
        p = self.cfg["posture"]
        lat_thr, tilt_thr = p["lean_side"]["lat_m"], p["lean_side"]["tilt_deg"]
        cond = {
            "forward_head": _any((d["neck_angle"], p["forward_head"]["neck_angle_deg"]),
                                 (d["head_fwd"], p["forward_head"]["head_fwd_m"])),
            "head_down": _any((d["head_pitch"], p["head_down"]["pitch_deg"]),
                              (d["nose_drop"], p["head_down"]["nose_drop_m"])),
            "slouch": _any((-d["sh_y"], p["slouch"]["sh_drop_m"])),
            "lean_forward": _any((-d["sh_z"], p["lean_forward"]["sh_fwd_m"])),
            # 사용자 왼쪽 = 월드 +X. 왼쪽으로 기울면 왼쪽 어깨가 내려감(sh_tilt 감소)
            "lean_left": _any((d["sh_x"], lat_thr), (-d["sh_tilt"], tilt_thr)),
            "lean_right": _any((-d["sh_x"], lat_thr), (d["sh_tilt"], tilt_thr)),
            "head_tilt": _any((abs(d["head_roll"]), p["head_tilt"]["roll_deg"])),
            "too_close": _any((-f["head_dist"], -p["too_close"]["dist_m"])),
            "chin_rest": self._chin_rest(f, p["chin_rest"]),
        }
        out = {k: int(self.hyst[k].update(t, cond[k])) for k in FLAGS}
        out["posture"] = next((k for k in p["priority"] if out.get(k)), "normal")

        # 졸음용 고개 각도: 얼굴 기반 pitch 우선, 얼굴을 놓치면(깊이 숙임) 코-귀 높이차로 근사
        pitch = d["head_pitch"]
        if not _finite(pitch) and _finite(d["nose_drop"]):
            pitch = math.degrees(math.asin(np.clip(d["nose_drop"] / 0.10, -1, 1)))
        out.update(self.drowsy.update(t, pitch, f["head_x"], f["head_z"], f["sh_y"], f["sh_z"],
                                      chin_rest=bool(out["chin_rest"]),
                                      leaning=bool(out["lean_forward"])))
        return out
