"""Dump one session seen by all 19 arc sensors (MuJoCo geoms + every sensor's 8x8) for the web viewer.

usage: python export_arc.py <tof-sim dir> <out.json> <seed> [seconds] [record_fps] [checkpoint|-] [placement]
placement: arc (default) | random | mixed (tofsim.session.PLACEMENT_MODES); random shows the one device drawn.
Each sensor re-runs the same seeded session, so the person's motion is identical across views.
"""
import json
import sys
from dataclasses import replace

import numpy as np

root, out, seed = sys.argv[1], sys.argv[2], int(sys.argv[3])
seconds = float(sys.argv[4]) if len(sys.argv) > 4 else 240
rec_fps = float(sys.argv[5]) if len(sys.argv) > 5 else 5
ckpt_path = sys.argv[6] if len(sys.argv) > 6 and sys.argv[6] != "-" else None
placement = sys.argv[7] if len(sys.argv) > 7 else "arc"
sys.path.insert(0, root)

import mujoco  # noqa: E402

from tofsim import LABELS, generate_frames, sample_session  # noqa: E402
from tofsim.mj_scene import MjDesk, sensor_frame  # noqa: E402
from tofsim.sensor import Sensor  # noqa: E402

cfg0 = sample_session(np.random.default_rng(seed), placement=placement)
arc = cfg0.meta["arc"]
every = max(1, int(round(cfg0.fps / rec_fps)))
desk = MjDesk(cfg0.room, cfg0.person)
m, d = desk.model, desk.data

net = None
if ckpt_path:
    import torch

    from tofsim.data import to_input
    from tofsim.model import PostureNet

    ck = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    ck_labels = list(ck.get("labels", LABELS))                   # the checkpoint's own class list (7 or 8 labels)
    hidden = ck["model"]["gru.weight_hh_l0"].shape[1]            # GRU width from the weights (train.py --hidden)
    net = PostureNet(cfg0.spec.height, cfg0.spec.width, len(ck_labels), hidden=hidden)
    net.load_state_dict(ck["model"])
    net.eval()

frames, poses, sensors = [], [], []
for s_i, (az, si, pose) in enumerate(arc):
    rng = np.random.default_rng(seed)
    cfg = replace(sample_session(rng, placement=placement), sensor_pose=pose)   # same draws as cfg0, then this sensor
    dists, stats, labels = [], [], []
    for i, (t_ms, dist, status, label, flags) in enumerate(generate_frames(cfg, seconds, rng, backend="mujoco", desk=desk)):
        dists.append(dist.copy()); stats.append(status.copy()); labels.append(label)
        if i % every:
            continue
        r = i // every
        if s_i == 0:
            poses.append(np.concatenate([d.geom_xpos, d.geom_xmat], axis=1).copy())
            frames.append({"t": int(t_ms), "label": int(label), "flags": int(flags), "d": [], "pred": [], "p": []})
        f = frames[r]
        assert f["label"] == label, f"sensor {az}: motion differs from sensor 0 at frame {i}"
        ok = np.isin(status, (5, 6, 9, 10)) & (dist >= 0)
        f["d"].append([int(v) if o else -1 for v, o in zip(dist.ravel(), ok.ravel())])
    if net is not None:
        import torch
        x = torch.from_numpy(to_input(np.stack(dists), np.stack(stats)))[None]
        probs, h = [], None
        with torch.no_grad():
            for s in range(0, x.shape[1], 600):
                logits, h = net(x[:, s:s + 600], h)
                probs.append(torch.softmax(logits[0], -1).numpy())
        probs = np.concatenate(probs)
        for r, f in enumerate(frames):
            pr = probs[r * every]
            f["pred"].append(int(pr.argmax()))
            f["p"].append([round(float(v), 2) for v in pr])
    origin, R = sensor_frame(pose)
    sensors.append({"az": az, "si": si, "height_m": round(float(pose.position[1] - cfg0.room.desk_height), 4), "origin": [round(float(v), 4) for v in origin], "R": [round(float(v), 5) for v in R.ravel()],
                    "pitch_down_deg": round(pose.pitch_down_deg, 2), "yaw_deg": round(pose.yaw_deg, 2),
                    "dirs": [[round(float(v), 5) for v in c] for c in Sensor(cfg.spec, cfg.model, np.random.default_rng(0)).zone_centers(R)]})
    print(f"sensor {az:+.0f}/{si}: {len(dists)} frames", flush=True)

poses = np.stack(poses)
moving = np.abs(poses - poses[:1]).max(axis=(0, 2)) > 1e-5
dyn = [int(g) for g in np.nonzero(moving)[0]]
for f, p in zip(frames, poses):
    f["pose"] = [[round(float(v), 4) for v in p[g]] for g in dyn]
geoms = [{"name": mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_GEOM, g) or f"g{g}", "type": int(m.geom_type[g]),
          "size": [round(float(v), 4) for v in m.geom_size[g]], "rgba": [round(float(v), 3) for v in m.geom_rgba[g]],
          "group": int(m.geom_group[g])} for g in range(m.ngeom)]
for g, ge in enumerate(geoms):                                      # mesh geoms carry their triangles
    if ge["type"] == 7:
        mid = int(m.geom_dataid[g])
        va, vn = int(m.mesh_vertadr[mid]), int(m.mesh_vertnum[mid])
        fa_, fn = int(m.mesh_faceadr[mid]), int(m.mesh_facenum[mid])
        ge["mesh"] = {"v": [round(float(x), 4) for x in m.mesh_vert[va:va + vn].ravel()],
                      "f": [int(x) for x in m.mesh_face[fa_:fa_ + fn].ravel()]}
model_info = None
if net is not None:
    st = ck["state"]
    model_info = {"step": int(st["step"]), "hours": round(st["train_seconds"] / 3600, 2), "best_f1": round(float(st["best_f1"]), 3)}
data = {"seed": seed, "fps": cfg0.fps / every, "labels": LABELS, "model": model_info,
        "placement": {"mode": cfg0.meta.get("placement_mode", "arc"), "misplaced": bool(cfg0.meta.get("misplaced", False)),
                      "why": cfg0.meta.get("placement", {}).get("why", "")},
        "arc": {"radius_m": round(cfg0.meta["radius_m"], 3)},
        "devices": [{"az": az, "c": [round(float(c[0]), 4), round(float(c[2]), 4), round(float(c[1]), 4)], "yaw_deg": round(psi, 3)}
                    for az, c, psi in cfg0.meta["devices"]],   # c in MuJoCo axes (x, toward the user, up)
        "geoms": geoms, "static": {g: [round(float(v), 4) for v in poses[0, g]] for g in range(m.ngeom) if not moving[g]},
        "dynamic": dyn, "sensors": sensors, "frames": frames}
with open(out, "w", encoding="utf-8") as fp:
    json.dump(data, fp, separators=(",", ":"))
print(f"{len(frames)} frames x {len(sensors)} sensors -> {out}; labels:", sorted({LABELS[f['label']] for f in frames}))
