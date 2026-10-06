"""Intel RealSense D435if 입력: RGB + 정렬된 Depth(m), IMU 중력 방향, .db3(rosbag2) 녹화/재생."""
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyrealsense2 as rs


@dataclass
class Frame:
    index: int
    t_host: float      # 호스트 epoch(s) - L9 데이터와 시간 동기화 기준
    t_dev: float       # RealSense 프레임 타임스탬프(ms)
    color: np.ndarray  # BGR uint8 (H, W, 3)
    depth_m: np.ndarray  # color에 정렬된 depth, float32 (H, W), 0 = 측정 실패


def measure_up_vector(seconds=1.0):
    """IMU 가속도계 평균으로 카메라 좌표계(x 오른쪽, y 아래, z 앞)에서의 '위' 방향을 구한다.

    정지 상태 가속도계는 중력 반대(위쪽)를 가리킨다. 수평으로 놓인 D435if는 약 (0, -9.8, 0).
    실패하거나 값이 이상하면 None.
    """
    pipe, cfg = rs.pipeline(), rs.config()
    cfg.enable_stream(rs.stream.accel, rs.format.motion_xyz32f, 100)
    try:
        pipe.start(cfg)
    except RuntimeError as e:
        print(f"[camera] IMU 시작 실패: {e}")
        return None
    # IMU는 켜지는 데 1초 이상 걸릴 수 있어, 첫 샘플 이후 seconds 동안 수집 (최대 5초 대기)
    samples, t0, t_first = [], time.time(), None
    try:
        while time.time() - t0 < 5.0 and (t_first is None or time.time() - t_first < seconds):
            ok, frames = pipe.try_wait_for_frames(1000)
            if not ok:
                continue
            for f in frames:
                if f.is_motion_frame():
                    d = f.as_motion_frame().get_motion_data()
                    samples.append((d.x, d.y, d.z))
                    t_first = t_first or time.time()
    finally:
        pipe.stop()
    if len(samples) < 10:
        print(f"[camera] IMU 샘플 부족({len(samples)})")
        return None
    up = np.median(np.asarray(samples), axis=0)
    norm = np.linalg.norm(up)
    if not 8.0 < norm < 11.5:
        print(f"[camera] 가속도 크기 이상({norm:.2f} m/s^2) - 카메라가 움직이는 중?")
        return None
    up = up / norm
    if abs(up[1]) < 0.5:  # 정방향/뒤집힘 모두 아니고 60도 이상 기울어짐 (엎어짐, 천장 향함 등)
        print(f"[camera] 카메라가 60도 이상 기울어짐 {up.round(2)} - 렌즈가 사용자를 향하게 세워 주세요")
        return None
    return up.tolist()


def measure_up_vector_isolated(seconds=1.0):
    """별도 프로세스에서 measure_up_vector 실행.

    Windows에서 mediapipe를 import한 프로세스는 IMU(HID) 프레임을 받지 못하는 현상이 있어 분리한다.
    """
    root = Path(__file__).resolve().parent.parent
    code = ("import json; from posture.camera import measure_up_vector as m; "
            f"print('UP=' + json.dumps(m({float(seconds)})))")
    try:
        r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True,
                           text=True, encoding="utf-8", timeout=30)
    except subprocess.TimeoutExpired:
        print("[camera] IMU 측정 시간 초과")
        return None
    for line in r.stdout.splitlines():
        if line.startswith("UP="):
            return json.loads(line[3:])
        print(line)
    return None


def orientation_from_up(up):
    """(rotate180, 회전 보정된 up). 카메라를 위아래 뒤집어 설치하면 up의 y가 +가 된다."""
    if up is not None and up[1] > 0:
        return True, [-up[0], -up[1], up[2]]
    return False, up


class RealSenseCamera:
    def __init__(self, width=640, height=480, fps=30, bag_in=None, bag_out=None, rotate180=False):
        self.live = bag_in is None
        self.rotate180 = rotate180  # 뒤집힌 설치: 영상/깊이를 180도 돌려 MediaPipe에 정방향으로 입력
        self.pipeline = rs.pipeline()
        cfg = rs.config()
        if bag_in:
            cfg.enable_device_from_file(str(bag_in), False)
        else:
            cfg.enable_stream(rs.stream.color, width, height, rs.format.bgr8, fps)
            cfg.enable_stream(rs.stream.depth, width, height, rs.format.z16, fps)
            if bag_out:
                cfg.enable_record_to_file(str(bag_out))
        self.profile = self.pipeline.start(cfg)
        dev = self.profile.get_device()
        if bag_in:
            dev.as_playback().set_real_time(False)  # 모든 프레임 처리
        self.depth_scale = dev.first_depth_sensor().get_depth_scale()
        self.align = rs.align(rs.stream.color)
        self.spatial = rs.spatial_filter()
        self.temporal = rs.temporal_filter()
        cs = self.profile.get_stream(rs.stream.color).as_video_stream_profile()
        self.fps = cs.fps()
        i = cs.get_intrinsics()
        self.intrinsics = {"width": i.width, "height": i.height, "fx": i.fx, "fy": i.fy,
                           "ppx": i.ppx, "ppy": i.ppy, "model": str(i.model), "coeffs": list(i.coeffs)}
        if rotate180:
            self.intrinsics.update(ppx=i.width - 1 - i.ppx, ppy=i.height - 1 - i.ppy)
        self._index = 0

    def read(self, timeout_ms=5000):
        ok, frames = self.pipeline.try_wait_for_frames(timeout_ms)
        if not ok:
            return None  # 재생 종료 또는 카메라 응답 없음
        t_host = time.time()
        frames = self.align.process(frames)
        color, depth = frames.get_color_frame(), frames.get_depth_frame()
        if not color or not depth:
            return self.read(timeout_ms)
        depth = self.spatial.process(depth)
        depth = self.temporal.process(depth).as_depth_frame()
        depth_m = np.asanyarray(depth.get_data()).astype(np.float32) * self.depth_scale
        color_img = np.asanyarray(color.get_data())
        if self.rotate180:
            color_img, depth_m = color_img[::-1, ::-1], depth_m[::-1, ::-1]
        fr = Frame(self._index, t_host, color.get_timestamp(),
                   np.ascontiguousarray(color_img), np.ascontiguousarray(depth_m))
        self._index += 1
        return fr

    def stop(self):
        self.pipeline.stop()
