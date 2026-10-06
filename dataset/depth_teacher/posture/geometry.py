"""카메라 좌표 -> 중력 기준 월드 좌표, 자세 특징값 계산.

월드 좌표 (m):
  X = 옆 (카메라 기준 오른쪽 = 마주 앉은 사용자의 왼쪽)
  Y = 높이 (중력 반대 방향)
  Z = 카메라로부터의 수평 거리
"""
import math

import numpy as np

FEATURES = ["head_x", "head_y", "head_z", "sh_x", "sh_y", "sh_z", "head_dist", "head_fwd",
            "neck_angle", "nose_drop", "head_pitch", "head_roll", "sh_tilt", "sh_width", "head_lat",
            "hand_face_dist", "hand_face_ratio", "arm_folded"]
HANDS = {"l": ("wrist_l", "pinky_l", "index_l", "thumb_l"),
         "r": ("wrist_r", "pinky_r", "index_r", "thumb_r")}


class WorldFrame:
    def __init__(self, up_cam=(0.0, -1.0, 0.0)):
        u = np.asarray(up_cam, float)
        u /= np.linalg.norm(u)
        z = np.array([0.0, 0.0, 1.0])
        f = z - (z @ u) * u
        f /= np.linalg.norm(f)
        r = np.cross(f, u)
        self.R = np.stack([r, u, f])
        self.tilt_deg = math.degrees(math.acos(np.clip(u @ np.array([0.0, -1.0, 0.0]), -1, 1)))

    def to_world(self, p_cam):
        return self.R @ np.asarray(p_cam, float)


def _deg(y, x):
    return math.degrees(math.atan2(y, x))


def compute_features(P):
    """P: {name: world xyz}. 어깨 두 점이 없으면 None(측정 불가)."""
    sl, sr = P.get("sh_l"), P.get("sh_r")
    if sl is None or sr is None:
        return None
    nose = P.get("nose")
    # 머리 위치: 얼굴 피부 점(이마·턱·양 광대) 우선. 귀는 머리카락에 덮이기 쉬워 예비용.
    face_pts = [P[k] for k in ("forehead", "chin", "cheek_l", "cheek_r") if k in P]
    ears = [P[k] for k in ("ear_l", "ear_r") if k in P]
    if len(face_pts) >= 3:
        head = np.mean(face_pts, axis=0)
    elif ears:
        head = np.mean(ears, axis=0)
    elif nose is not None:
        head = nose
    else:
        return None
    # 좌우 기준점(roll, 코 높이 비교용): 광대 우선, 없으면 귀
    if "cheek_l" in P and "cheek_r" in P:
        side_l, side_r = P["cheek_l"], P["cheek_r"]
    elif "ear_l" in P and "ear_r" in P:
        side_l, side_r = P["ear_l"], P["ear_r"]
    else:
        side_l = side_r = None
    sh = (sl + sr) / 2
    if "torso" in P:  # 몸통 깊이는 의자와 섞이지 않는 가슴 중앙 값으로
        sh = np.array([sh[0], sh[1], P["torso"][2]])
    nan = float("nan")
    f = dict.fromkeys(FEATURES, nan)
    f.update(head_x=head[0], head_y=head[1], head_z=head[2], sh_x=sh[0], sh_y=sh[1], sh_z=sh[2])
    f["head_dist"] = (nose if nose is not None else head)[2]
    f["head_fwd"] = sh[2] - head[2]                          # +: 머리가 어깨보다 카메라 쪽
    # 어깨->머리 벡터의 전방 기울기. 옆 기울임에 부풀지 않도록 분모에 좌우 성분 포함
    f["neck_angle"] = _deg(sh[2] - head[2], math.hypot(head[1] - sh[1], head[0] - sh[0]))
    if side_l is not None and nose is not None:
        f["nose_drop"] = (side_l[1] + side_r[1]) / 2 - nose[1]  # +: 코가 광대(귀)선보다 아래로 (숙임)
    fh, ch = P.get("forehead"), P.get("chin")
    if fh is not None and ch is not None:
        f["head_pitch"] = _deg(ch[2] - fh[2], fh[1] - ch[1])  # +: 이마가 카메라 쪽 (숙임)
    if side_l is not None:
        f["head_roll"] = _deg(side_l[1] - side_r[1], side_l[0] - side_r[0])  # +: 왼쪽이 위
    f["sh_tilt"] = _deg(sl[1] - sr[1], sl[0] - sr[0])         # +: 왼쪽 어깨가 위
    f["sh_width"] = float(np.linalg.norm(sl - sr))
    f["head_lat"] = head[0] - sh[0]

    # 턱괴기 (팀 camsvc/posture_pose 정의의 3D판): 얼굴(턱, 없으면 코)에 가장 가까운 손과
    # 그 팔의 팔꿈치가 어깨보다 아래인지(책상에 괸 팔). 손을 들어 머리를 만질 때는 팔꿈치가 위.
    face = ch if ch is not None else nose
    if face is not None:
        best = None
        for side, names in HANDS.items():
            pts = [P[n] for n in names if n in P]
            if pts:
                d = min(float(np.linalg.norm(p - face)) for p in pts)
                if best is None or d < best[0]:
                    best = (d, side)
        if best is not None:
            d, side = best
            f["hand_face_dist"] = d
            f["hand_face_ratio"] = d / f["sh_width"]
            elbow, shoulder = P.get(f"elbow_{side}"), P.get(f"sh_{side}")
            if elbow is not None and shoulder is not None:
                f["arm_folded"] = float(elbow[1] < shoulder[1])
    return {k: float(v) for k, v in f.items()}
