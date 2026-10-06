"""세션 실행 루프: 카메라 -> 랜드마크 -> 3D -> 특징 -> 캘리브레이션/라벨 -> CSV 저장 + 화면."""
import csv
import json
import math
import time
from datetime import datetime
from pathlib import Path

import cv2
import yaml

from .camera import RealSenseCamera, measure_up_vector_isolated, orientation_from_up
from .geometry import FEATURES, WorldFrame, compute_features
from .labeler import FLAGS, LABEL_KO, Calibrator, PostureLabeler, baseline_usable
from .landmarks import POINT_NAMES, LandmarkDetector, lift_to_3d
from .viz import render

ROOT = Path(__file__).resolve().parent.parent
LABEL_COLS = FLAGS + ["posture", "drowsy", "drowsy_score", "nods_window", "head_sway"]
COLUMNS = (["frame", "t_host", "t_dev", "phase", "gt_posture", "gt_drowsy", "valid"]
           + [f"{n}_{a}" for n in POINT_NAMES for a in "xyz"] + FEATURES + LABEL_COLS)


def load_config(path=None):
    with open(path or ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def baseline_path(user):
    return ROOT / "data" / "baselines" / f"{user}.json"


class Schedule:
    """검증 프로토콜: 단계별 안내 문구와 정답 라벨."""

    def __init__(self, cfg):
        p = cfg["protocol"]
        self.steps, self.trans = p["steps"], p["transition_s"]

    def at(self, elapsed):
        t = 0.0
        for i, s in enumerate(self.steps):
            if elapsed < t + self.trans:
                return {"phase": "transition", "gt_posture": "", "gt_drowsy": "",
                        "text": f"[{i + 1}/{len(self.steps)}] 준비: {s['text']}",
                        "remain": t + self.trans - elapsed}
            t += self.trans
            if elapsed < t + s["seconds"]:
                drowsy = s["label"] == "drowsy"
                return {"phase": "step", "gt_posture": "" if drowsy else s["label"],
                        "gt_drowsy": int(drowsy),
                        "text": f"[{i + 1}/{len(self.steps)}] {s['text']}",
                        "remain": t + s["seconds"] - elapsed}
            t += s["seconds"]
        return None


def _fmt(v):
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return ""
    return round(v, 5) if isinstance(v, float) else v


def run_session(cfg, user, bag_in=None, record_bag=False, save_video=False, recalibrate=False,
                schedule=None, display=True, tag="live", max_seconds=None):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    sdir = ROOT / "data" / "sessions" / f"{user}_{stamp}_{tag}"
    sdir.mkdir(parents=True, exist_ok=True)
    cc = cfg["camera"]

    up_raw = None  # IMU가 잰 원본 카메라 좌표계의 '위' (녹화본 재생 시 meta.json에서 복원)
    if bag_in:
        meta_in = Path(bag_in).with_name("meta.json")
        if meta_in.exists():
            up_raw = json.loads(meta_in.read_text(encoding="utf-8")).get("up_raw")
    else:
        up_raw = measure_up_vector_isolated(cc["imu_seconds"])
    rotate180, up = orientation_from_up(up_raw)
    if up is None:
        print("[app] IMU 중력 방향 없음 -> 카메라가 수평이라고 가정")
        up = [0.0, -1.0, 0.0]
    wf = WorldFrame(up)
    print(f"[app] 카메라 기울기 {wf.tilt_deg:.1f}°" + (" (뒤집힌 설치 -> 영상 180° 회전)" if rotate180 else ""))

    cam = RealSenseCamera(cc["width"], cc["height"], cc["fps"], bag_in=bag_in,
                          bag_out=sdir / "raw.db3" if record_bag else None, rotate180=rotate180)
    det = LandmarkDetector(cfg, ROOT)

    baseline = None
    bpath = baseline_path(user)
    if not (recalibrate or schedule) and bpath.exists():
        baseline = json.loads(bpath.read_text(encoding="utf-8"))
        if baseline_usable(baseline):
            print(f"[app] 저장된 기준 자세 사용: {bpath}")
        else:
            print("[app] 저장된 고개 기준 없음 -> 다시 측정")
            baseline = None
    calib = None if baseline else Calibrator(cfg)
    labeler = PostureLabeler(cfg, baseline) if baseline else None

    meta = {"user": user, "created": stamp, "bag_in": str(bag_in) if bag_in else None,
            "up_raw": up_raw, "rotate180": rotate180, "up_vector": up,
            "camera_tilt_deg": wf.tilt_deg, "intrinsics": cam.intrinsics,
            "fps": cam.fps, "excluded_signals": cfg["excluded_signals"], "config": cfg}
    fcsv = open(sdir / "frames.csv", "w", newline="", encoding="utf-8")
    writer = csv.DictWriter(fcsv, fieldnames=COLUMNS)
    writer.writeheader()
    video = None
    if save_video:
        video = cv2.VideoWriter(str(sdir / "color.mp4"), cv2.VideoWriter_fourcc(*"mp4v"),
                                cam.fps, (cam.intrinsics["width"], cam.intrinsics["height"]))
    sched_t0 = None
    fps_t, fps_n, fps = time.time(), 0, 0.0
    print(f"[app] 세션 저장 위치: {sdir}")
    start = time.time()
    try:
        while max_seconds is None or time.time() - start < max_seconds:
            fr = cam.read()
            if fr is None:
                break
            t = fr.t_host if cam.live else fr.t_dev / 1000.0
            # MediaPipe VIDEO 추적에는 실제 프레임 시각을 준다. 처리가 느려 프레임을 건너뛸 때
            # '번호 x 33ms' 를 주면 추적기가 움직임을 실제보다 빠르게 보고 놓친다.
            pts2d = det.detect(fr.color, fr.t_dev)
            P = {k: wf.to_world(v) for k, v in lift_to_3d(pts2d, fr.depth_m, cam.intrinsics, cfg).items()}
            feats = compute_features(P)
            row = {"frame": fr.index, "t_host": fr.t_host, "t_dev": fr.t_dev,
                   "valid": int(feats is not None)}
            for n in POINT_NAMES:
                for i, a in enumerate("xyz"):
                    row[f"{n}_{a}"] = float(P[n][i]) if n in P else None
            row.update(feats or {})
            labels, banner = None, None

            if calib is not None:
                baseline = calib.add(t, feats)
                if calib.in_prep(t):
                    row["phase"] = "prep"
                    banner = (f"준비: 의자에 깊숙이 앉아 허리를 펴고 평소처럼 화면을 보세요 "
                              f"({calib.prep_remaining(t):.0f}s)")
                else:
                    row["phase"] = "calib"
                    banner = f"기준 자세 측정 중: 그대로 유지하세요 ({calib.progress(t) * 100:.0f}%)"
                if baseline is not None:
                    bpath.parent.mkdir(parents=True, exist_ok=True)
                    bpath.write_text(json.dumps(baseline, indent=2), encoding="utf-8")
                    meta["baseline"] = baseline
                    labeler, calib, sched_t0 = PostureLabeler(cfg, baseline), None, t
                    print("[app] 기준 자세 저장 완료")
            else:
                labels = labeler.update(t, feats)
                row.update(labels)
                row["phase"] = "run"
                if schedule:
                    st = schedule.at(t - sched_t0)
                    if st is None:
                        break
                    row.update(phase=st["phase"], gt_posture=st["gt_posture"], gt_drowsy=st["gt_drowsy"])
                    banner = f"{st['text']}  ({st['remain']:.0f}s)"
            writer.writerow({k: _fmt(row.get(k)) for k in COLUMNS})
            if video is not None:
                video.write(fr.color)

            fps_n += 1
            if time.time() - fps_t >= 1.0:
                fps, fps_n, fps_t = fps_n / (time.time() - fps_t), 0, time.time()
            if display:
                status = f"{fps:.0f} fps | {'재생' if bag_in else '실시간'} | q 종료, c 재캘리브레이션"
                cv2.imshow("posture", render(fr.color, fr.depth_m, pts2d, feats, labels, status, banner))
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                if key == ord("c") and not schedule:
                    calib, labeler = Calibrator(cfg), None
            elif fr.index % 300 == 0 and labels:
                print(f"[app] frame {fr.index}: {LABEL_KO.get(labels['posture'])} drowsy={labels['drowsy']}")
    finally:
        cam.stop()
        fcsv.close()
        if video is not None:
            video.release()
        cv2.destroyAllWindows()
        (sdir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False, default=str),
                                        encoding="utf-8")
    return sdir
