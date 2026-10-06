"""화면 표시: 랜드마크, 깊이 미리보기, 한글 상태 패널."""
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .labeler import FLAGS, LABEL_KO

_FONTS = {}
PANEL_W = 340
SKELETON = (("sh_l", "sh_r"), ("ear_l", "ear_r"), ("forehead", "chin"),
            ("sh_l", "elbow_l"), ("elbow_l", "wrist_l"), ("wrist_l", "index_l"),
            ("sh_r", "elbow_r"), ("elbow_r", "wrist_r"), ("wrist_r", "index_r"))


def _font(size):
    if size not in _FONTS:
        for path in ("C:/Windows/Fonts/malgun.ttf", "C:/Windows/Fonts/gulim.ttc"):
            try:
                _FONTS[size] = ImageFont.truetype(path, size)
                break
            except OSError:
                continue
        else:
            _FONTS[size] = ImageFont.load_default()
    return _FONTS[size]


def _put_texts(img, items):
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(pil)
    for text, (x, y), size, bgr in items:
        d.text((x, y), text, font=_font(size), fill=tuple(int(c) for c in bgr[::-1]))
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


def render(color, depth_m, pts2d, feats, labels, status, banner=None):
    img = color.copy()
    for a, b in SKELETON:
        if a in pts2d and b in pts2d:
            col = (0, 165, 255) if a.startswith(("elbow", "wrist")) or b.startswith("elbow") else (255, 200, 0)
            cv2.line(img, tuple(map(int, pts2d[a])), tuple(map(int, pts2d[b])), col, 2)
    for name, (u, v) in pts2d.items():
        cv2.circle(img, (int(u), int(v)), 4, (0, 255, 255), -1)

    h = img.shape[0]
    panel = np.full((h, PANEL_W, 3), 30, np.uint8)
    dvis = cv2.applyColorMap(cv2.convertScaleAbs(np.clip(depth_m, 0, 2.0), alpha=255 / 2.0),
                             cv2.COLORMAP_JET)
    dvis[depth_m == 0] = 0
    small = cv2.resize(dvis, (150, int(150 * h / img.shape[1])))  # 오른쪽 아래 깊이 미리보기
    panel[h - small.shape[0] - 8:h - 8, PANEL_W - 158:PANEL_W - 8] = small

    texts = [(status, (10, 8), 18, (200, 200, 200))]
    y = 40
    if labels:
        posture = labels.get("posture", "invalid")
        col = (80, 220, 80) if posture == "normal" else (60, 60, 240)
        texts.append((f"자세: {LABEL_KO.get(posture, posture)}", (10, y), 26, col))
        y += 36
        if labels.get("drowsy") is not None:
            dcol = (60, 60, 240) if labels["drowsy"] else (80, 220, 80)
            texts.append((f"졸음: {'예' if labels['drowsy'] else '아니오'}  "
                          f"(점수 {labels['drowsy_score']:.2f}, 끄덕임 {labels['nods_window']})",
                          (10, y), 18, dcol))
        y += 30
        for i, k in enumerate(FLAGS):  # 두 열
            on = labels.get(k)
            texts.append((("● " if on else "○ ") + LABEL_KO[k], (10 + 165 * (i % 2), y + 22 * (i // 2)),
                          16, (60, 60, 240) if on else (150, 150, 150)))
        y += 22 * ((len(FLAGS) + 1) // 2)
    if feats:
        y += 6
        for k, fmt in (("neck_angle", "목 각도 {:.1f}°"), ("head_fwd", "머리 전방 {:.3f} m"),
                       ("head_pitch", "고개 pitch {:.1f}°"), ("head_dist", "거리 {:.2f} m"),
                       ("sh_tilt", "어깨 기울기 {:.1f}°"), ("hand_face_dist", "손-턱 {:.2f} m")):
            v = feats.get(k)
            if v is not None and np.isfinite(v):
                texts.append((fmt.format(v), (10, y), 15, (200, 200, 200)))
                y += 19
    canvas = np.hstack([img, panel])
    if banner:
        cv2.rectangle(canvas, (0, 0), (img.shape[1], 46), (0, 0, 0), -1)
        texts.append((banner, (12, 8), 24, (0, 255, 255)))
    return _put_texts(canvas, texts)
