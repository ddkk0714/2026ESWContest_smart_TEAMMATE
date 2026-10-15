"""Read-only full-frame FSM/model replay and sustained sensor ablations."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import math
from collections import Counter
from dataclasses import asdict
from pathlib import Path
import yaml
from .frames import PHASE_CLASS
from deskmate_hub.inference import FSMEngine, State, SessionRecorder, load_config
from deskmate_hub.replay import frame_from_dict
from deskmate_hub.presentation import _PHASE_BY_STATE
from deskmate_hub.personalization import PersonalizationRuntime, load_personalization_config
from deskmate_hub.personalization.contract import SIGNALS, CLASSES
from deskmate_hub.personalization.tflite_backend import TFLiteBackend

DEFAULT_POLICY = Path(__file__).resolve().parents[2] / "hub/deskmate_hub/config/model_comparison.yaml"


def intervals(rows, predicate, max_gap):
    out = []
    for row in rows:
        if not predicate(row):
            continue
        if out and row["tick"] == out[-1]["end_tick"] + 1 and 0 < row["now"] - out[-1]["end"] <= max_gap:
            out[-1].update(end=row["now"], end_tick=row["tick"], ticks=out[-1]["ticks"]+1)
        else:
            out.append({"start": row["now"], "end": row["now"], "start_tick": row["tick"], "end_tick": row["tick"], "ticks": 1})
    return out


def summarize(rows, policy):
    predicted = [r for r in rows if r["model_status"] == "predicted"]
    comparable = [r for r in predicted if r["fsm_class"] is not None]
    matrix = [[0]*len(CLASSES) for _ in CLASSES]
    for row in comparable:
        matrix[CLASSES.index(row["fsm_class"])][CLASSES.index(row["model_label"])]+=1
    return {"ticks": len(rows), "predicted": len(predicted), "comparable": len(comparable),
            "status_counts": dict(Counter(r["model_status"] for r in rows)),
            "disagreements": sum(r["disagree"] for r in comparable),
            "agreement": sum(not r["disagree"] for r in comparable)/len(comparable) if comparable else None,
            "low_confidence": sum(r["low_confidence"] for r in predicted), "agreement_matrix": matrix,
            "missing_patterns": dict(Counter(",".join(r["missing_signals"]) or "none" for r in rows)),
            "disagreement_intervals": intervals(rows, lambda r:r["disagree"], policy["max_interval_gap_sec"]),
            "low_confidence_intervals": intervals(rows, lambda r:r["low_confidence"], policy["max_interval_gap_sec"])}


def compare_session(raw_frames, fsm_cfg, runtime_cfg, *, period, normalization, policy, backend_factory=None, drop_signal=None):
    if drop_signal is not None and drop_signal not in SIGNALS:
        raise ValueError("unknown ablation signal")
    if not 0 < policy["low_confidence"] <= 1 or not math.isfinite(policy["max_interval_gap_sec"]) or policy["max_interval_gap_sec"] <= 0:
        raise ValueError("invalid diagnostic policy")
    baseline, observed = FSMEngine(copy.deepcopy(fsm_cfg)), FSMEngine(copy.deepcopy(fsm_cfg))
    first_report, second_report = SessionRecorder(), SessionRecorder()
    runtime = PersonalizationRuntime(runtime_cfg, period=period, normalization=normalization, backend_factory=backend_factory)
    rows = []
    for tick, raw in enumerate(raw_frames):
        frame = copy.deepcopy(raw)
        if not math.isfinite(float(frame["now"])):
            raise ValueError("invalid frame time")
        if drop_signal:
            frame.setdefault("signals", {})[drop_signal] = {"phi": 0, "delta": 0, "available": False}
        a_frame, b_frame = frame_from_dict(frame), frame_from_dict(frame)
        # Canonicalize once so omitted-field defaults are identical for FSM/model.
        canonical = asdict(b_frame)
        previous = observed.state
        a, b = baseline.tick(a_frame), observed.tick(b_frame)
        first_report.observe(a_frame,a)
        second_report.observe(b_frame,b)
        prediction = runtime.observe(canonical, reset=b.state in (State.START, State.END) and b.state is not previous)
        if asdict(a) != asdict(b) or canonical != asdict(b_frame):
            raise AssertionError("observation changed FSM or its frame")
        fsm_class = PHASE_CLASS.get(_PHASE_BY_STATE[b.state])
        ready = prediction["status"] == "predicted"
        row = {"tick": tick, "now": b_frame.now, "fsm_state": b.state.value, "fsm_class": fsm_class,
               "fsm_gate": b.gate.value, "fsm_actions": list(b.actions), "cause": b.cause,
               "model_status": prediction["status"], "model_label": prediction.get("label"),
               "model_confidence": prediction.get("confidence"),
               "missing_signals": [s for s in SIGNALS if not b_frame.get(s).available],
               "disagree": ready and fsm_class is not None and prediction["label"] != fsm_class,
               "low_confidence": ready and prediction["confidence"] < policy["low_confidence"]}
        rows.append(row)
    if first_report.finalize() != second_report.finalize():
        raise AssertionError("observation changed session report")
    return {"summary": summarize(rows,policy), "trace": rows, "fsm_and_report_unchanged": True}


def run(frame_paths, bundle, output, *, fsm_config=None, ingest_config=None, policy_path=DEFAULT_POLICY, runtime_name="tflite_runtime", ablations=True):
    from deskmate_hub.ingest import load_ingest_config
    from .frames import read_jsonl
    output, bundle = Path(output), Path(bundle)
    if output.exists() and any(output.iterdir()):
        raise ValueError("output must be fresh")
    cfg = load_personalization_config()
    cfg.update(enabled=True, model_path=str(bundle/"selected.tflite"), metadata_path=str(bundle/"selected.metadata.json"))
    ingest = load_ingest_config(ingest_config)
    fsm = load_config(fsm_config)
    with open(policy_path,encoding="utf-8") as source:
        policy = yaml.safe_load(source)
    factory = None
    if runtime_name == "tensorflow":
        import tensorflow as tf
        factory = lambda path, meta:TFLiteBackend(path,meta,interpreter_factory=tf.lite.Interpreter)
    elif runtime_name != "tflite_runtime":
        raise ValueError("unknown runtime")
    report = {"schema_version": "1.0", "runtime": runtime_name, "policy": policy,
              "fsm_config_sha256": hashlib.sha256(json.dumps(fsm,sort_keys=True).encode()).hexdigest(),
              "ingest_config_sha256": hashlib.sha256(json.dumps(ingest,sort_keys=True).encode()).hexdigest(),
              "model_sha256": hashlib.sha256(Path(cfg["model_path"]).read_bytes()).hexdigest(),
              "sessions": [], "limitations": ["Agreement with FSM is not real-user accuracy.",
              "Ablations hold one feature unavailable for the whole replay; no device is disconnected.",
              "Actions are FSM outputs, not commands sent to a real or mock device.",
              "No fusion, intervention gate, or automatic deployment is changed."]}
    traces = []
    for index,path in enumerate(frame_paths):
        raw = read_jsonl(path)
        if not raw:
            raise ValueError("empty frame session")
        session = {"session_index":index,"frames_sha256":hashlib.sha256(Path(path).read_bytes()).hexdigest(),"variants":{}}
        clean_trace = None
        for signal in (None,)+ (SIGNALS if ablations else ()):
            name = "clean" if signal is None else "drop_"+signal
            result = compare_session(raw,fsm,cfg,period=float(ingest["frame_period_sec"]),
                                     normalization=ingest.get("normalization","linear"),policy=policy,
                                     backend_factory=factory,drop_signal=signal)
            summary = result["summary"]
            summary["fsm_and_report_unchanged"] = result["fsm_and_report_unchanged"]
            if clean_trace is None:
                clean_trace = result["trace"]
            summary["fsm_state_changes_vs_clean"] = sum(a["fsm_state"] != b["fsm_state"] for a,b in zip(clean_trace,result["trace"]))
            summary["model_label_changes_vs_clean"] = sum(a["model_label"] != b["model_label"] for a,b in zip(clean_trace,result["trace"]) if a["model_status"] == b["model_status"] == "predicted")
            session["variants"][name] = summary
            traces.extend(dict(row,session_index=index,variant=name) for row in result["trace"])
        report["sessions"].append(session)
    if not report["sessions"]:
        raise ValueError("no input sessions")
    output.mkdir(parents=True,exist_ok=True)
    (output/"comparison.json").write_text(json.dumps(report,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    (output/"trace.jsonl").write_text("".join(json.dumps(row,allow_nan=False)+"\n" for row in traces),encoding="utf-8")
    lines=["# Offline FSM/model comparison", "", "Agreement is not cognitive accuracy. Low confidence is diagnostic only.","", "| Session | Variant | Ticks | Predicted | Agreement | Low confidence | FSM changed vs clean |", "|---|---|---:|---:|---:|---:|---:|"]
    for session in report["sessions"]:
        for variant,summary in session["variants"].items():
            agreement = "N/A" if summary["agreement"] is None else f"{summary['agreement']:.2%}"
            lines.append(f"| {session['session_index']} | {variant} | {summary['ticks']} | {summary['predicted']} | {agreement} | {summary['low_confidence']} | {summary['fsm_state_changes_vs_clean']} |")
    lines.extend(["", "All comparison variants preserve their own FSM tick outputs and session reports.","Sensor ablations can change the FSM itself; this is different from observation mutating it."])
    (output/"comparison.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames",nargs="+",required=True)
    parser.add_argument("--bundle",required=True)
    parser.add_argument("--output",required=True)
    parser.add_argument("--fsm-config")
    parser.add_argument("--ingest-config")
    parser.add_argument("--policy",type=Path,default=DEFAULT_POLICY)
    parser.add_argument("--runtime",choices=("tflite_runtime","tensorflow"),default="tflite_runtime")
    parser.add_argument("--no-ablations",action="store_true")
    args=parser.parse_args()
    report=run(args.frames,args.bundle,args.output,fsm_config=args.fsm_config,ingest_config=args.ingest_config,
               policy_path=args.policy,runtime_name=args.runtime,ablations=not args.no_ablations)
    print(json.dumps({"sessions":len(report["sessions"]),"clean":[s["variants"]["clean"] for s in report["sessions"]]}))


if __name__=="__main__":
    main()
