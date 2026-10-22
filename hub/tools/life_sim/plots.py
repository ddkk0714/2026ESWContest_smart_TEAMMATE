"""PNG 타임라인 — 정답/판정 자세, C_fatigue·C_focus, FSM 상태, 환경·기기, 개입·반응 표시. 그림 안 글자는 영어(서버에 한글 글꼴이 없다)."""
from __future__ import annotations

from typing import Any

LABEL_ORDER = ["away", "upright", "forward_head", "lean_forward", "chin_rest", "recline", "drowsy", "face_down", "misplaced", "unknown"]
STATE_ORDER = ["IDLE", "START", "CONTEXT_DETECT", "FOCUS_PC", "FOCUS_MIXED", "FOCUS_NPC", "FOCUS_BREAK", "FATIGUE_SUSPECT",
               "FATIGUE", "CAUSE_ANALYSIS", "ACTION_POSTURE", "ACTION_ENV", "ACTION_BREAK", "MONITOR", "RECOVERY", "ESCALATE",
               "REST", "END"]


def plot_timeline(rows: list[dict[str, Any]], ticks: list[dict[str, Any]], events: list[dict[str, Any]], path: str, *,
                  title: str = "", thresholds: dict[str, float] | None = None) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if not rows:
        return
    t0 = rows[0]["t"]
    x = [(r["t"] - t0) / 60.0 for r in rows]
    fig, axes = plt.subplots(5, 1, figsize=(14, 12), sharex=True, gridspec_kw={"height_ratios": [2, 1.6, 1.8, 1.2, 1.0]})
    ax = axes[0]
    li = {l: i for i, l in enumerate(LABEL_ORDER)}
    ax.step(x, [li.get(r["label"], len(LABEL_ORDER)) for r in rows], where="post", color="k", lw=1.5, label="truth (observable)")
    ax.step(x, [li.get(r["pred"] or "unknown", len(LABEL_ORDER)) + 0.15 for r in rows], where="post", color="tab:red", lw=1, alpha=0.8,
            label="model")
    flag_x = [xx for xx, r in zip(x, rows) if r["drowsy_flag"]]
    ax.scatter(flag_x, [li["drowsy"] - 0.3] * len(flag_x), s=4, color="tab:orange", label="0x03 drowsy flag (rule)")
    ax.set_yticks(range(len(LABEL_ORDER)))
    ax.set_yticklabels(LABEL_ORDER, fontsize=8)
    ax.legend(loc="upper right", fontsize=8)
    ax.set_title(f"{title} — simulation, not measured", fontsize=11)
    ax = axes[1]
    tx = [(tk["t"] - t0) / 60.0 for tk in ticks]
    ax.plot(tx, [tk["c_fatigue"] for tk in ticks], color="tab:red", label="C_fatigue")
    ax.plot(tx, [tk["c_focus"] for tk in ticks], color="tab:blue", label="C_focus (focus-loss evidence)")
    for key, c in (("fatigue_confirm", "tab:red"), ("fatigue_focus_low", "tab:orange"), ("recovery_success", "tab:green")):
        if thresholds and key in thresholds:
            ax.axhline(thresholds[key], color=c, ls=":", lw=0.8)
    ax.axhline(0.75, color="gray", ls="--", lw=0.6)
    ax.axhline(0.45, color="gray", ls="--", lw=0.6)
    ax.set_ylim(0, 1.02)
    ax.legend(loc="upper right", fontsize=8)
    ax = axes[2]
    si = {s: i for i, s in enumerate(STATE_ORDER)}
    ax.step(tx, [si.get(tk["state"], 0) for tk in ticks], where="post", color="tab:purple")
    ax.set_yticks(range(len(STATE_ORDER)))
    ax.set_yticklabels(STATE_ORDER, fontsize=7)
    for ev in events:
        if ev.get("kind") == "intervention":
            ax.axvline((ev["t"] - t0) / 60.0, color="tab:red", lw=0.8, alpha=0.7)
            ax.text((ev["t"] - t0) / 60.0, len(STATE_ORDER) - 1, ev["kind"], rotation=90, fontsize=6, va="top", color="tab:red")
        elif ev.get("kind") == "reaction":
            ax.axvline((ev["t"] - t0) / 60.0, color="tab:green", lw=0.8, alpha=0.7)
            ax.text((ev["t"] - t0) / 60.0, 0.5, ev.get("what"), rotation=90, fontsize=6, va="bottom", color="tab:green")
        elif ev.get("kind") == "feedback":
            ax.axvline((ev["t"] - t0) / 60.0, color="tab:olive", lw=0.6, alpha=0.6)
    ax = axes[3]
    ax.plot(x, [r["env"]["co2_ppm"] for r in rows], color="tab:brown", label="CO2 ppm")
    ax.set_ylabel("CO2 ppm")
    ax2 = ax.twinx()
    ax2.plot(x, [r["env"]["lux"] for r in rows], color="tab:olive", lw=0.8, label="lux")
    ax2.set_ylabel("lux")
    ax.legend(loc="upper left", fontsize=8)
    ax = axes[4]
    ax.step(x, [r["devices"]["fan"] for r in rows], where="post", label="fan 0-3")
    ax.step(x, [r["devices"]["lamp_pct"] / 33.0 for r in rows], where="post", label="lamp %/33")
    ax.step(x, [3.5 if r["devices"]["window"] else 0 for r in rows], where="post", label="window")
    ax.step(x, [min(3, r["devices"]["tts"]) for r in rows], where="post", label="TTS count (<=3)")
    ax.legend(loc="upper right", fontsize=7, ncol=4)
    ax.set_xlabel("minutes")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
