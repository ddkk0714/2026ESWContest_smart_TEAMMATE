"""MediaPipe 2D 랜드마크 -> depth로 3D 복원.

L9(ToF)로 측정 가능한 얼굴 미세 신호(눈 깜빡임/감김/시선/하품)는 쓰지 않는다:
  - FaceLandmarker blendshape 출력 비활성화
  - Pose의 눈(1~6)/입(9,10) 랜드마크, Face mesh의 눈/입 영역 랜드마크를 추출하지 않음
머리 각도 계산용으로 이마(10)와 턱 끝(152)만 사용한다.
"""
import urllib.request
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_tasks
from mediapipe.tasks.python import vision

MODEL_URLS = {
    "pose": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
            "pose_landmarker_full/float16/latest/pose_landmarker_full.task",
    "face": "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
            "face_landmarker/float16/latest/face_landmarker.task",
}

# 사용하는 랜드마크 (이 목록 외에는 추출하지 않음)
# 팔(팔꿈치·손목·손가락 끝)은 턱괴기 판정용 - 팀 camsvc/posture_pose 와 같은 정의를 3D로 확장
POSE_POINTS = {"nose": 0, "ear_l": 7, "ear_r": 8, "sh_l": 11, "sh_r": 12,
               "elbow_l": 13, "elbow_r": 14, "wrist_l": 15, "wrist_r": 16,
               "pinky_l": 17, "pinky_r": 18, "index_l": 19, "index_r": 20,
               "thumb_l": 21, "thumb_r": 22}
# 얼굴은 피부 위 점만: 이마·턱 끝·광대(116 = 사용자 오른쪽, 345 = 왼쪽).
# 귀는 머리카락에 덮이면 가시성이 떨어지고 깊이도 머리카락 표면이 잡혀 예비용으로만 쓴다.
FACE_POINTS = {"forehead": 10, "chin": 152, "cheek_r": 116, "cheek_l": 345}
# torso: 랜드마크가 아니라 어깨선 아래 가슴 중앙에서 잰 몸통 깊이 (lift_to_3d 에서 만든다)
POINT_NAMES = list(POSE_POINTS) + list(FACE_POINTS) + ["torso"]


def ensure_model(path, url):
    path = Path(path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"[landmarks] 모델 다운로드: {url}")
        urllib.request.urlretrieve(url, path)
    return str(path)


class LandmarkDetector:
    def __init__(self, cfg, root="."):
        mcfg = cfg["models"]
        mdir = Path(root) / mcfg["dir"]
        pose_path = ensure_model(mdir / mcfg["pose"], MODEL_URLS["pose"])
        face_path = ensure_model(mdir / mcfg["face"], MODEL_URLS["face"])
        self.pose = vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(
            base_options=mp_tasks.BaseOptions(model_asset_path=pose_path),
            running_mode=vision.RunningMode.VIDEO, num_poses=1,
            min_pose_detection_confidence=0.5, min_pose_presence_confidence=0.5,
            min_tracking_confidence=0.5))
        self.face = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
            base_options=mp_tasks.BaseOptions(model_asset_path=face_path),
            running_mode=vision.RunningMode.VIDEO, num_faces=1,
            output_face_blendshapes=False,  # 눈 깜빡임/입 벌림 계수 -> 사용 금지
            output_facial_transformation_matrixes=False))
        self.min_vis = cfg["landmarks"]["min_visibility"]
        self._t0, self._last_ts = None, -1

    def detect(self, bgr, t_dev_ms):
        """{name: (u, v) 픽셀 좌표} - 검출 안 되거나 가시성이 낮은 점은 제외.

        t_dev_ms: 카메라 프레임 시각(ms). 첫 프레임 기준 경과 시간으로 바꿔 MediaPipe 에 준다
        (VIDEO 모드는 정수 ms, 엄격히 증가해야 한다).
        """
        if self._t0 is None:
            self._t0 = t_dev_ms
        ts_ms = max(int(round(t_dev_ms - self._t0)), self._last_ts + 1)
        self._last_ts = ts_ms
        h, w = bgr.shape[:2]
        img = mp.Image(image_format=mp.ImageFormat.SRGB,
                       data=np.ascontiguousarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)))
        out = {}
        pr = self.pose.detect_for_video(img, ts_ms)
        if pr.pose_landmarks:
            lms = pr.pose_landmarks[0]
            for name, idx in POSE_POINTS.items():
                lm = lms[idx]
                if lm.visibility >= self.min_vis:
                    out[name] = (lm.x * w, lm.y * h)
        fr = self.face.detect_for_video(img, ts_ms)
        if fr.face_landmarks:
            lms = fr.face_landmarks[0]
            for name, idx in FACE_POINTS.items():
                out[name] = (lms[idx].x * w, lms[idx].y * h)
        return out


def sample_depth(depth_m, u, v, r, dmin, dmax):
    """랜드마크 주변 패치에서 '가장 앞쪽 표면'의 깊이. 몸 가장자리의 배경 혼입을 막는다."""
    h, w = depth_m.shape
    u, v = int(round(u)), int(round(v))
    if not (0 <= u < w and 0 <= v < h):
        return np.nan
    patch = depth_m[max(0, v - r):v + r + 1, max(0, u - r):u + r + 1]
    vals = patch[(patch > dmin) & (patch < dmax)]
    if vals.size < 0.3 * patch.size:
        return np.nan
    near = np.percentile(vals, 10)
    return float(np.median(vals[vals < near + 0.08]))


def lift_to_3d(points2d, depth_m, intr, cfg):
    """픽셀 좌표 + depth -> 카메라 좌표계 3D (m). 깊이 측정 실패한 점은 제외."""
    lc = cfg["landmarks"]
    r, dmin, dmax = lc["depth_patch_px"], lc["depth_min_m"], lc["depth_max_m"]

    def point(u, v, z):
        return np.array([(u - intr["ppx"]) / intr["fx"] * z, (v - intr["ppy"]) / intr["fy"] * z, z])

    pts = dict(points2d)
    out = {}
    if "sh_l" in pts and "sh_r" in pts:
        L, R = np.array(pts.pop("sh_l")), np.array(pts.pop("sh_r"))
        mid, w = (L + R) / 2, float(np.linalg.norm(L - R))
        # 몸통 깊이: 어깨선 아래 가슴 높이. 사무용 의자 등받이와 맞닿는 몸 가장자리에서 멀어
        # 의자가 섞이지 않는다. 가슴 앞의 손·컵·캔은 몸보다 앞에 있으므로, 가운데와 좌우 세 곳을
        # 재서 가장 뒤(몸)에 있는 값들만 쓴다 (10-04 2차 녹화: 두 손으로 든 캔이 가슴 중앙에 잡혀
        # '상체 앞으로'·'구부정' 오탐).
        tv = mid[1] + lc["torso_down_ratio"] * w
        across = (L - R) / 2
        samples = []
        for k in (0.0, -lc["torso_side_ratio"], lc["torso_side_ratio"]):
            tu = mid[0] + k * 2 * across[0]
            z = sample_depth(depth_m, tu, tv, max(r, int(0.06 * w)), dmin, dmax)
            if np.isfinite(z):
                samples.append((z, tu))
        torso_z = np.nan
        if samples:
            far = max(z for z, _ in samples)
            body = [(z, u) for z, u in samples if z >= far - lc["torso_occluder_m"]]
            torso_z = float(np.median([z for z, _ in body]))
            out["torso"] = point(float(np.mean([u for _, u in body])), tv, torso_z)
        # 어깨 깊이: 검은 옷과 검은 의자의 경계가 애매해 랜드마크 자리에 등받이가 잡힐 수 있다.
        # 목 쪽으로 조금씩 옮겨 가며, 몸통 깊이와 맞는(등받이처럼 뒤에 있지 않은) 첫 값을 쓴다.
        # x, y 는 원래 랜드마크 픽셀에서 계산해 어깨 폭·기울기가 샘플 위치에 따라 흔들리지 않게 한다.
        # 어느 샘플도 맞지 않으면 몸통 깊이로 대신한다 - 어깨의 높이·폭·기울기는 2D 위치가
        # 정하므로, 깊이 하나 때문에 프레임 전체를 '측정 불가'로 버리지 않는다.
        for name, P0 in (("sh_l", L), ("sh_r", R)):
            for frac in lc["shoulder_inward_steps"]:
                u, v = P0 + frac * (mid - P0)
                z = sample_depth(depth_m, u, v, r, dmin, dmax)
                if np.isfinite(z) and (not np.isfinite(torso_z)
                                       or abs(z - torso_z) <= lc["shoulder_torso_max_m"]):
                    out[name] = point(P0[0], P0[1], z)
                    break
            else:
                if np.isfinite(torso_z):
                    out[name] = point(P0[0], P0[1], torso_z)
    for name, (u, v) in pts.items():
        z = sample_depth(depth_m, u, v, r, dmin, dmax)
        if np.isfinite(z):
            out[name] = point(u, v, z)
    return out
