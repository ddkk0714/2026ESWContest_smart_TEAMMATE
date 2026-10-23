"""자세판정 시뮬 본체 — 각본 → 사람 → 두 ToF 센서 + mmWave C1001 → 책상 재실 판정 → 자세 모델(Pi 4 경로) → 평가.

hub FSM 은 돌리지 않는다(그건 전체 시뮬 life_sim). 여기서 보는 것은 "자세 판정 단계" 하나:
  ToF   센서별 8×8 → UART 0x05 payload(encode_tof_sensor_frames) → decode_payload → TofGridAssembler → 표본 → 모델
        (실기 Pi 4 와 같은 디코더라 단위·범위도 같게 깎인다). 0x03 규칙 특징(RuleFeatures) = ESP32 규칙 추정기 대역.
  mmWave C1001 대역 1 Hz(mmwave.py). 책상 재실 = presence.priority(mmwave: 부재→부재, 재실이면 distance_cm ≤ desk_max_cm, 거리 없으면 ToF,
        끊기면 ToF / any: OR). gate_posture 면 모델에 present=책상 재실을 넘긴다(TofMlpPosture.update(sample, now, present) 계약).

    res = run_posture(load_scenario(path), out_dir="runs/posture_sim", model_path="models/opt_l8v5_c256_best.pt")

출력(out_dir/<name>/): frames.jsonl(1 s: 정답·구간·where·판정·재실 셋·mmWave), summary.json/md.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import numpy as np

from .mmwave import MmwaveSensor, presence_decision
from .posture_model import OraclePosture, RuleFeatures
from .source import TofsimSource, where_now


def _jsonable(x: Any) -> Any:
    if isinstance(x, (np.floating, np.integer)):
        return x.item()
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    return x


class WirePath:
    """센서별 격자 → UART 0x05 → 디코더 → 표본(Pi 4 의 ingest_frame 과 같은 조립)."""

    def __init__(self) -> None:
        from deskmate_hub.ingest.uart_frame import (TYPE_TOF_GRID, TofGridAssembler, decode_payload,
                                                    encode_tof_sensor_frames)
        self._type, self._dec, self._enc = TYPE_TOF_GRID, decode_payload, encode_tof_sensor_frames
        self.asm = TofGridAssembler()
        self.frame_id = 0

    def push(self, frames: dict[int, tuple[np.ndarray, np.ndarray]]) -> dict[str, Any] | None:
        self.frame_id = (self.frame_id + 1) & 0xFF
        packed = {i: (np.asarray(d).reshape(-1).astype(int).tolist(), np.asarray(s).reshape(-1).astype(int).tolist())
                  for i, (d, s) in frames.items()}
        sample = None
        for payload in self._enc(packed, frame_id=self.frame_id):
            got = self.asm.push(self._dec(self._type, payload))
            if got is not None:
                sample = got
        return sample


def make_model(scenario: dict[str, Any], *, posture: str, model_path: str | None, truth):
    mcfg = scenario["sensors_resolved"].get("posture_model") or {}
    if posture == "oracle" or model_path is None:
        return OraclePosture(truth, seed=int(scenario["seed"]))
    from .posture_model import GruPosture
    return GruPosture(model_path, vote_frames=int(mcfg.get("vote_frames", 10)), warmup_frames=int(mcfg.get("warmup_frames", 45)),
                      reset_on_away_s=float(mcfg.get("reset_on_away_s", 60)), reset_every_s=mcfg.get("reset_every_s", 900))


def run_posture(scenario: dict[str, Any], *, out_dir: str, model_path: str | None = None, posture: str = "gru",
                source: Any = None, backend: str | None = None, tofsim_root: str | None = None, label: str | None = None,
                progress: bool = True) -> dict[str, Any]:
    name = label or scenario["name"]
    run_dir = os.path.join(out_dir, name)
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "scenario.json"), "w", encoding="utf-8") as fh:
        json.dump(_jsonable({k: v for k, v in scenario.items() if not k.endswith("_resolved")}), fh, ensure_ascii=False, indent=1)
    sens = scenario["sensors_resolved"]
    pres_cfg = sens.get("presence") or {}
    fps = float(scenario["fps"])
    seed = int(scenario["seed"])
    t0 = float(scenario["t0"])
    started = time.time()
    src = source if source is not None else TofsimSource(scenario, backend=backend or scenario["backend"], tofsim_root=tofsim_root)
    truth: dict[str, str] = {}
    model = make_model(scenario, posture=posture, model_path=model_path,
                       truth=lambda: getattr(src, "last_label", None) or truth.get("label", "upright"))
    rules = RuleFeatures(sens.get("rule_features") or {}, fps=fps)
    mmw = MmwaveSensor(sens.get("mmwave") or {}, seed=seed)
    wire = WirePath()
    away_default = str(scenario.get("away_where", "out"))
    rows: list[dict[str, Any]] = []
    mm: dict[str, Any] | None = None
    verdict: dict[str, Any] | None = None
    gated_since: float | None = None
    gated_reset = False
    reset_after = float((sens.get("posture_model") or {}).get("reset_on_away_s", 60))
    k = 0
    prof = {"render": 0.0, "model": 0.0}
    next_report = time.time() + 15.0
    fh_frames = open(os.path.join(run_dir, "frames.jsonl"), "w", encoding="utf-8")
    while True:
        info = src.step()
        if info is None:
            break
        truth["label"] = info.label
        t = t0 + info.t
        where = where_now(src, default=away_default)
        p0 = time.perf_counter()
        frames = src.render(info)
        p1 = time.perf_counter()
        feats = rules.update(frames, t)
        if k % int(fps) == 0:
            mm = mmw.step(t, kind=info.kind, typing=bool(info.typing), where=where)
        present, src_p = presence_decision(mm, bool(feats["present"]), desk_max_cm=float(pres_cfg.get("desk_max_cm", 130)),
                                           priority=str(pres_cfg.get("priority", "mmwave")))
        sample = wire.push(frames)
        if sample is not None:
            if pres_cfg.get("gate_posture", True) and not present:
                # hub 계약(TofMlpPosture.update(present=False)): 책상 부재면 모델·기준선을 건너뛰고 away. 오래 비면 은닉 상태를 비운다
                gated_since = t if gated_since is None else gated_since
                if t - gated_since >= reset_after and not gated_reset and hasattr(model, "reset"):
                    model.reset(t)
                    gated_reset = True
                verdict = {"posture": "away", "posture_confidence": 1.0, "raw_posture": "away", "source": "presence_gate"}
            else:
                gated_since, gated_reset = None, False
                verdict = model.update(sample, t, present=present if pres_cfg.get("gate_posture", True) else bool(feats["present"]))
        prof["render"] += p1 - p0
        prof["model"] += time.perf_counter() - p1
        if k % int(fps) == 0:
            row = {"t": t, "sec": int(round(info.t)), "kind": info.kind, "label": info.label, "where": where,
                   "transition": bool(info.flags & 1), "nodding": bool(info.flags & 2), "override": info.override,
                   "pred": (verdict or {}).get("posture"), "conf": (verdict or {}).get("posture_confidence"),
                   "raw": (verdict or {}).get("raw_posture"),
                   "truth_present": info.kind != "away", "present": present, "present_src": src_p,
                   "tof_present": bool(feats["present"]), "mm_present": None if mm is None else mm["present"],
                   "mm_dist": None if mm is None else mm.get("distance_cm"), "mm_level": None if mm is None else mm["motion_level"],
                   "mm_drowsy": None if mm is None else mm["drowsy_state"],
                   "drowsy_flag": bool(feats["drowsy"]), "nods": feats["nods_in_window"], "coverage": feats["coverage_ratio"]}
            rows.append(row)
            fh_frames.write(json.dumps(_jsonable(row), ensure_ascii=False) + "\n")
            if progress and time.time() >= next_report:
                print(f"[posture_sim] {name}: {row['sec']}/{src.total_seconds():.0f} s  truth={info.label} pred={row['pred']} "
                      f"present={present}({src_p}) ({time.time() - started:.0f} s; render {prof['render']:.0f} s, "
                      f"model {prof['model']:.0f} s)", flush=True)
                next_report = time.time() + 15.0
        k += 1
    fh_frames.close()
    summary = summarize(scenario, rows, model, src, elapsed=time.time() - started, run_dir=run_dir)
    summary["profile_s"] = {k2: round(v2, 1) for k2, v2 in prof.items()}
    with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(_jsonable(summary), fh, ensure_ascii=False, indent=1)
    with open(os.path.join(run_dir, "summary.md"), "w", encoding="utf-8") as fh:
        fh.write(format_summary(summary))
    return summary


# ---------------------------------------------------------------- 평가

def posture_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """초 단위 자세 정확도(전이 제외, 판정이 있는 초만) · 라벨별 재현율/정밀도 · 혼동 상위 · misplaced 오탐 · 0x03 drowsy 플래그."""
    scored = [(r["label"], r["pred"]) for r in rows if r["pred"] is not None and not r["transition"]]
    acc = sum(1 for l, p in scored if l == p) / max(1, len(scored))
    per: dict[str, dict[str, Any]] = {}
    for lab in sorted({r["label"] for r in rows}):
        tp = sum(1 for l, p in scored if l == lab and p == lab)
        n = sum(1 for l, _ in scored if l == lab)
        pp = sum(1 for _, p in scored if p == lab)
        per[lab] = {"n_sec": n, "recall": round(tp / n, 3) if n else None, "precision": round(tp / pp, 3) if pp else None}
    conf: dict[str, int] = {}
    for l, p in scored:
        if l != p:
            conf[f"{l}->{p}"] = conf.get(f"{l}->{p}", 0) + 1
    drowsy = [r for r in rows if r["label"] == "drowsy"]
    return {"acc_sec": round(acc, 3), "per_label": per,
            "confusions_top": dict(sorted(conf.items(), key=lambda kv: -kv[1])[:8]),
            "misplaced_fp": round(sum(1 for _, p in scored if p == "misplaced") / max(1, len(scored)), 4),
            "drowsy_flag_recall_0x03": round(sum(1 for r in drowsy if r["drowsy_flag"]) / max(1, len(drowsy)), 3),
            "drowsy_flag_fp_0x03": round(sum(1 for r in rows if r["drowsy_flag"] and r["kind"] != "drowsy") / max(1, len(rows)), 4)}


def presence_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """책상 재실 판정(정답 = 책상에 앉아 있는지): ToF(0x03) · mmWave present 만 · OR(이전 hub) · 결정(설정된 규칙).
    자리비움 구간(≥ 30 s)마다 떠난 뒤 몇 초 만에 부재로, 돌아온 뒤 몇 초 만에 재실로 바뀌었는지와 구간 안 헛재실 초."""
    def score(get):
        miss = sum(1 for r in rows if r["truth_present"] and not get(r))
        false = sum(1 for r in rows if not r["truth_present"] and get(r))
        return {"acc": round(1 - (miss + false) / max(1, len(rows)), 4), "miss_s": miss, "false_s": false}
    out: dict[str, Any] = {"tof": score(lambda r: r["tof_present"]), "or": score(lambda r: r["tof_present"] or bool(r["mm_present"])),
                           "decision": score(lambda r: r["present"])}
    if any(r["mm_present"] is not None for r in rows):
        out["mmwave"] = score(lambda r: bool(r["mm_present"]))
    eps, cur = [], None
    for i, r in enumerate(rows):
        if not r["truth_present"] and cur is None:
            cur = i
        elif r["truth_present"] and cur is not None:
            eps.append((cur, i))
            cur = None
    if cur is not None:
        eps.append((cur, len(rows)))
    out["away_episodes"] = []
    for a, b in eps:
        if b - a < 30 or a < 10:
            continue
        leave = next((k - a for k in range(a, b) if not rows[k]["present"]), None)
        ret = next((k - b for k in range(b, min(len(rows), b + 120)) if rows[k]["present"]), None) if b < len(rows) else None
        out["away_episodes"].append({"t_rel_s": a, "away_s": b - a, "where": rows[a]["where"], "absent_after_s": leave,
                                     "present_after_return_s": ret,
                                     "false_present_s": sum(1 for k in range(a, b) if rows[k]["present"])})
    still = [r for r in rows if r["truth_present"] and r["kind"] in ("drowsy", "face_down")]
    out["still_person_miss_s"] = sum(1 for r in still if not r["present"])          # 졸기·엎드림 중 놓침
    out["tof_miss_by_label_s"] = {}
    for r in rows:
        if r["truth_present"] and not r["tof_present"]:
            out["tof_miss_by_label_s"][r["label"]] = out["tof_miss_by_label_s"].get(r["label"], 0) + 1
    out["mm_drowsy_state_s"] = {}
    for r in rows:
        if r.get("mm_drowsy"):
            out["mm_drowsy_state_s"][r["mm_drowsy"]] = out["mm_drowsy_state_s"].get(r["mm_drowsy"], 0) + 1
    return out


def summarize(scenario, rows, model, src, *, elapsed, run_dir) -> dict[str, Any]:
    return {"name": scenario["name"], "seed": scenario["seed"], "run_dir": run_dir, "simulated": True,
            "note": "시뮬레이션 결과 — 실측 증거가 아니다.", "duration_s": len(rows), "wall_s": round(elapsed, 1),
            "scene": getattr(src, "meta", {}), "model": getattr(model, "info", {}),
            "presence_cfg": scenario["sensors_resolved"].get("presence"),
            "posture": posture_metrics(rows), "presence": presence_metrics(rows)}


def format_summary(s: dict[str, Any]) -> str:
    p, pr = s["posture"], s["presence"]
    lines = [f"# {s['name']} (seed {s['seed']}) — 자세판정 시뮬 요약", "", f"> {s['note']}", "",
             f"- 길이 {s['duration_s']} s (벽시계 {s['wall_s']} s), 재실 규칙 {json.dumps(s['presence_cfg'], ensure_ascii=False)}",
             f"- 장면: {json.dumps(s['scene'], ensure_ascii=False)}",
             f"- 모델: {json.dumps({k: v for k, v in s['model'].items() if k != 'labels'}, ensure_ascii=False)}",
             f"- 자세 정확도 {p['acc_sec']:.3f}, misplaced 오탐 {p['misplaced_fp']:.2%}, 0x03 drowsy 재현율 {p['drowsy_flag_recall_0x03']:.2f} / "
             f"오탐 {p['drowsy_flag_fp_0x03']:.2%}, 혼동 상위 {p['confusions_top']}",
             "- 책상 재실: " + ", ".join(f"{k} {v['acc']} (놓침 {v['miss_s']} s·헛재실 {v['false_s']} s)"
                                        for k, v in pr.items() if isinstance(v, dict) and "acc" in v),
             f"- 졸기·엎드림 중 재실 놓침 {pr['still_person_miss_s']} s, ToF 가 놓친 자세 {pr['tof_miss_by_label_s']}, "
             f"mmWave drowsy_state {pr['mm_drowsy_state_s']}", "",
             "| t(s) | 자리비움 | 어디 | 부재 판정까지 | 복귀 뒤 재실까지 | 구간 안 헛재실 |", "|---|---|---|---|---|---|"]
    for e in pr["away_episodes"]:
        lines.append(f"| {e['t_rel_s']} | {e['away_s']} s | {e['where']} | {e['absent_after_s']} | {e['present_after_return_s']} | "
                     f"{e['false_present_s']} s |")
    lines += ["", "| 라벨 | 초 | 재현율 | 정밀도 |", "|---|---|---|---|"]
    for lab, v in p["per_label"].items():
        lines.append(f"| {lab} | {v['n_sec']} | {v['recall']} | {v['precision']} |")
    return "\n".join(lines) + "\n"
