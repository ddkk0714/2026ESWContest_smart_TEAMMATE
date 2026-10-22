"""§B-4 개인화 효과 실험 — 같은 하루 각본을 계정 A(합성 30 일 학습 프로필) 와 B(전역 기본값) 로 돌려 비교한다.

    python -m tools.life_sim ab hub/tools/life_sim/scenarios/day_student.yaml --out runs/lifesim --model <ckpt>

같은 시드(사람·센서·반응 난수)로 두 번 돌리므로 차이는 정책(채널 선택·자동 문턱·거절 재제안 금지·상한·취침 규칙)에서만 난다 —
단, 개입이 달라지면 사람의 각본 전환(깨어남·자세 복귀)이 달라져 그 뒤 난수 소비도 달라진다(완전 짝 비교는 아니다).
출력: <out>/<persona>_<name>_AB/compare.md · compare.json · compare.png
검증 로그(compare.json "checks"): 되돌리기 2 회 뒤 자동→제안 전환, 거절 재제안 금지, 쿨다운·세션 상한.
"""
from __future__ import annotations

import copy
import json
import os
from typing import Any

from .harness import run_scenario


def _checks(summary: dict[str, Any]) -> dict[str, Any]:
    pz = summary.get("personalize") or {}
    if pz.get("engine") == "hub" and pz.get("log"):
        return _checks_hub(pz)
    log = summary.get("policy_log") or []
    out = {"undo_demote": None, "reject_block": None, "cooldown_or_cap_denials": 0, "auto_threshold_final": None}
    undos = [e for e in log if e.get("outcome") == "undo"]
    demoted = [e for e in log if "auto_demoted_after_undos" in (e.get("codes") or [])]
    out["undo_demote"] = {"undos": len(undos), "demotions_after": len(demoted),
                          "ok": (len(undos) < 2) or bool(demoted)}
    rejects = [e for e in log if e.get("outcome") == "reject"]
    blocked = [e for e in log if "rejected_before_no_resuggest" in (e.get("codes") or [])]
    out["reject_block"] = {"rejects": len(rejects), "blocked_resuggest": len(blocked)}
    out["cooldown_or_cap_denials"] = sum(1 for e in log if e.get("decision") == "deny")
    out["auto_threshold_final"] = (summary.get("policy") or {}).get("auto_threshold")
    out["whys"] = [iv.get("policy_why") for iv in summary.get("interventions") or [] if iv.get("policy_why")][:12]
    return out


def _agg(rs: list[dict[str, Any]]) -> dict[str, Any]:
    """여러 시드의 요약 → 평균(수치)·합(목록)."""
    if len(rs) == 1:
        return rs[0]

    def mean(key_fn):
        vals = [key_fn(s) for s in rs if key_fn(s) is not None]
        return round(sum(vals) / len(vals), 3) if vals else None
    out = copy.deepcopy(rs[0])
    out["n_interventions"] = mean(lambda s: s["n_interventions"])
    out["n_blocked_by_policy"] = mean(lambda s: s["n_blocked_by_policy"])
    out["cards"] = mean(lambda s: s["cards"])
    out["accepted"] = mean(lambda s: s["accepted"])
    out["rejected"] = mean(lambda s: s["rejected"])
    out["undos"] = mean(lambda s: s["undos"])
    out["accept_rate"] = mean(lambda s: s["accept_rate"])
    out["timing"]["first_intervention_rel_s"] = mean(lambda s: s["timing"]["first_intervention_rel_s"])
    out["wakes"] = [w for s in rs for w in s["wakes"]]
    out["rests"] = [w for s in rs for w in s["rests"]]
    out["bedtime_suggested"] = sum(1 for s in rs if s["bedtime_suggested"])
    out["vent_choices"] = [v for s in rs for v in s["vent_choices"]]
    out["fsm"]["mean_c_fatigue"] = mean(lambda s: s["fsm"]["mean_c_fatigue"])
    out["fsm"]["max_c_fatigue"] = mean(lambda s: s["fsm"]["max_c_fatigue"])
    out["fsm"]["fatigue_episodes"] = mean(lambda s: s["fsm"]["fatigue_episodes"])
    out["fsm"]["escalations"] = mean(lambda s: s["fsm"]["escalations"])
    out["interventions"] = [iv for s in rs for iv in s["interventions"]]
    out["policy_log"] = [e for s in rs for e in s["policy_log"]]
    out["arms"] = sorted({a for s in rs for a in (s.get("arms") or [])})
    out["asked_by_policy"] = mean(lambda s: s.get("asked_by_policy"))
    pz0 = rs[0].get("personalize") or {}
    if pz0.get("engine") == "hub" and pz0.get("log"):
        rows_all = [r for s in rs for r in ((s.get("personalize") or {}).get("log") or {}).get("rows", [])]
        by_arm: dict[str, int] = {}
        verdicts: dict[str, int] = {}
        excluded: dict[str, int] = {}
        for r in rows_all:
            by_arm[r.get("arm") or "fallback"] = by_arm.get(r.get("arm") or "fallback", 0) + 1
            verdicts[r.get("verdict") or "none"] = verdicts.get(r.get("verdict") or "none", 0) + 1
            for why in (r.get("excluded") or {}).values():
                excluded[why] = excluded.get(why, 0) + 1
        out["personalize"] = dict(pz0, log=dict(pz0["log"], rows=rows_all, episodes=len(rows_all), by_arm=by_arm, verdicts=verdicts,
                                                 excluded=excluded), final=(rs[-1].get("personalize") or {}).get("final"))
    out["policy_profile"] = rs[0]["policy_profile"] + f" (x{len(rs)} seeds)"
    curves: dict[int, list[float]] = {}
    for s in rs:
        for m, v in s["fsm"]["fatigue_curve_min"]:
            curves.setdefault(m, []).append(v)
    out["fsm"]["fatigue_curve_min"] = [[m, round(sum(v) / len(v), 3)] for m, v in sorted(curves.items())]
    out["seeds"] = [s["seed"] for s in rs]
    return out


def _checks_hub(pz: dict[str, Any]) -> dict[str, Any]:
    """personalize 패키지(hub 엔진) 로그로 검증: 되돌리기→자동 강등(personalize:ask), 거절 재제안 금지(excluded rejected_in_session),
    쿨다운·세션 상한(excluded cooldown / source fallback), 자동 문턱 변화."""
    lg = pz["log"]
    rows = lg.get("rows") or []
    undos = [r for r in rows if r.get("verdict") == "undo"]
    asked_after_undo = 0
    seen_undo = False
    for r in rows:
        if seen_undo and r.get("fsm_gate") == "auto" and r.get("delivered") == "suggest":
            asked_after_undo += 1
        if r.get("verdict") == "undo":
            seen_undo = True
    rejects = [r for r in rows if r.get("verdict") == "reject"]
    excluded = lg.get("excluded") or {}
    final = pz.get("final") or {}
    return {
        "engine": "hub", "episodes": lg.get("episodes"), "by_arm": lg.get("by_arm"), "verdicts": lg.get("verdicts"),
        "undo_demote": {"undos": len(undos), "asked_after_undo": asked_after_undo, "ok": (len(undos) == 0) or asked_after_undo > 0},
        "reject_block": {"rejects": len(rejects), "blocked_resuggest": excluded.get("rejected_in_session", 0)},
        "cooldown_excluded": excluded.get("cooldown", 0), "quiet_hours_excluded": excluded.get("quiet_hours", 0),
        "weather_excluded": excluded.get("weather", 0),
        "fallback_cap": sum(1 for r in rows if r.get("source") == "fallback"),
        "auto_threshold_final": final.get("auto_threshold"), "policy_mode": final.get("policy_mode"), "n_feedback": final.get("n_feedback"),
        "whys": [r.get("explanation") for r in rows if r.get("explanation")][:8],
    }


def run_ab(scenario: dict[str, Any], *, out_dir: str, model_path: str | None, posture: str = "gru", backend: str | None = None,
           tofsim_root: str | None = None, source_factory=None, progress: bool = True, seeds: int = 1) -> dict[str, Any]:
    base = f"{scenario['persona']}_{scenario['name']}"
    results: dict[str, dict[str, Any]] = {}
    for tag, profile in (("A", "synth"), ("B", "global")):
        per_seed = []
        for k in range(max(1, seeds)):
            sc = copy.deepcopy(scenario)
            sc["seed"] = int(scenario["seed"]) + k
            sc["policy"] = dict(sc.get("policy") or {}, profile=profile)
            src = source_factory(sc) if source_factory else None
            per_seed.append(run_scenario(sc, out_dir=out_dir, model_path=model_path, posture=posture, source=src, backend=backend,
                                         tofsim_root=tofsim_root, progress=progress,
                                         label=f"{base}_{tag}" + (f"_s{sc['seed']}" if seeds > 1 else "")))
        results[tag] = _agg(per_seed)
    cmp_dir = os.path.join(out_dir, f"{base}_AB")
    os.makedirs(cmp_dir, exist_ok=True)
    rows = []
    for tag, s in results.items():
        rows.append({
            "account": tag, "policy": s["policy_profile"], "engine": s.get("policy_engine"), "arms": s.get("arms"),
            "asked_by_policy": s.get("asked_by_policy"),
            "interventions": s["n_interventions"], "blocked": s["n_blocked_by_policy"],
            "cards": s["cards"], "accepted": s["accepted"], "rejected": s["rejected"], "undos": s["undos"], "accept_rate": s["accept_rate"],
            "kinds": [iv["kind"] for iv in s["interventions"] if not iv["blocked"]],
            "first_intervention_s": s["timing"]["first_intervention_rel_s"], "wakes": len(s["wakes"]),
            "rests": len(s["rests"]), "bedtime": s["bedtime_suggested"], "vent": [v["choice"] for v in s["vent_choices"]],
            "seeds": s.get("seeds", [s["seed"]]),
            "mean_c_fatigue": s["fsm"]["mean_c_fatigue"], "max_c_fatigue": s["fsm"]["max_c_fatigue"],
            "fatigue_episodes": s["fsm"]["fatigue_episodes"], "escalations": s["fsm"]["escalations"],
            "checks": _checks(s), "run_dir": s["run_dir"],
        })
    cmp = {"scenario": scenario["name"], "persona": scenario["persona"], "seed": scenario["seed"], "seeds": seeds, "rows": rows,
           "curves": {tag: s["fsm"]["fatigue_curve_min"] for tag, s in results.items()},
           "note": "시뮬레이션. A 프로필은 합성(가정)이며 실측 학습이 아니다."}
    with open(os.path.join(cmp_dir, "compare.json"), "w", encoding="utf-8") as fh:
        json.dump(cmp, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(cmp_dir, "compare.md"), "w", encoding="utf-8") as fh:
        fh.write(format_compare(cmp))
    try:
        plot_compare(cmp, results, os.path.join(cmp_dir, "compare.png"))
        cmp["plot"] = os.path.join(cmp_dir, "compare.png")
    except Exception as exc:  # noqa: BLE001
        cmp["plot_error"] = str(exc)
    cmp["dir"] = cmp_dir
    return cmp


def format_compare(cmp: dict[str, Any]) -> str:
    L = [f"# {cmp['persona']} / {cmp['scenario']} — 계정 A(합성 30 일 프로필) vs B(전역 기본값)", "", f"> {cmp['note']}", "",
         "| 계정 | 정책 | 개입 | 보류 | 카드 | 수락 | 거절 | 되돌리기 | 수락률 | 첫 개입(s) | 깨어남 | 휴식/취침 | 취침권유 | 환기 선택 | 평균 C_fat | 최대 | 피로 에피소드 | 격상 |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in cmp["rows"]:
        L.append(f"| {r['account']} | {r['policy']} | {r['interventions']} | {r['blocked']} | {r['cards']} | {r['accepted']} | {r['rejected']} | "
                 f"{r['undos']} | {r['accept_rate']} | {r['first_intervention_s']} | {r['wakes']} | {r['rests']} | {r['bedtime']} | "
                 f"{','.join(r['vent']) or '-'} | {r['mean_c_fatigue']:.3f} | {r['max_c_fatigue']:.2f} | {r['fatigue_episodes']} | {r['escalations']} |")
    L += ["", f"시드 {cmp['seeds']}개: 수치는 평균, 깨어남·휴식·환기 목록은 시드 합.", "", "개입 종류·시점:"]
    for r in cmp["rows"]:
        L.append(f"- {r['account']}: {', '.join(r['kinds']) or '-'}  | arms {r.get('arms')}  | 정책이 자동→제안 {r.get('asked_by_policy')}")
    L += ["", "검증 로그(되돌리기→자동 강등, 거절 재제안 금지, 쿨다운·상한):"]
    for r in cmp["rows"]:
        L.append(f"- {r['account']}: {json.dumps(r['checks'], ensure_ascii=False)}")
    L += [""]
    return "\n".join(L)


def plot_compare(cmp: dict[str, Any], results: dict[str, dict[str, Any]], path: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 1, figsize=(13, 7), sharex=True, gridspec_kw={"height_ratios": [2, 1]})
    ax = axes[0]
    for tag, curve in cmp["curves"].items():
        ax.plot([m for m, _ in curve], [v for _, v in curve], label=f"{tag} C_fatigue (per-minute mean)")
    ax.axhline(0.70, color="tab:red", ls=":", lw=0.8)
    ax.set_ylim(0, 1.02)
    ax.set_ylabel("C_fatigue")
    ax.legend(fontsize=8)
    ax.set_title(f"{cmp['persona']} / {cmp['scenario']} — A vs B (simulation)", fontsize=11)
    ax = axes[1]
    for i, (tag, s) in enumerate(results.items()):
        seeds = s.get("seeds", [s["seed"]])
        for iv in s["interventions"][: max(1, len(s["interventions"]) // max(1, len(seeds)))]:   # 첫 시드의 개입만 그린다
            if iv["blocked"]:
                continue
            x = iv["t_rel_s"] / 60.0
            ax.plot([x], [i], marker="v" if iv["gate"] == "auto" else "o", color="tab:red" if iv["kind"].startswith("posture") else
                    "tab:blue" if iv["kind"].startswith("env") or iv["kind"].startswith("vent") else "tab:green", ls="")
            ax.text(x, i + 0.12, iv["kind"].replace("_suggest", "?").replace("_auto", "!"), rotation=60, fontsize=6)
        for w in s["wakes"]:
            ax.plot([w["t_rel_s"] / 60.0], [i - 0.2], marker="*", color="k", ls="")
    ax.set_yticks([0, 1])
    ax.set_yticklabels(["A", "B"])
    ax.set_ylim(-0.6, 1.8)
    ax.set_xlabel("minutes (v auto, o suggest, * wake)")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
