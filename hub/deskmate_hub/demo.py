"""Deterministic demo inputs for replay and the Atlas preview API."""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Iterator

from .inference import FSMEngine, SensorFrame, Signal
from .presentation import state_envelope
from .preview_api import PreviewStateStore, make_server


@dataclass(frozen=True)
class DemoStep:
    now: float
    label: str
    touch: bool = False
    end_touch: bool = False
    action_done: bool = False
    signals: dict[str, Signal] = field(default_factory=dict)


def demo_steps() -> Iterator[DemoStep]:
    high = {
        "keystroke": Signal(phi=0.25, delta=0.95),
        "posture": Signal(phi=0.30, delta=0.90),
        "environment": Signal(phi=0.10, delta=0.75),
        "elapsed": Signal(phi=0.35, delta=0.90),
    }
    calm = {
        "keystroke": Signal(phi=0.10, delta=0.10),
        "posture": Signal(phi=0.10, delta=0.10),
        "environment": Signal(phi=0.10, delta=0.10),
        "elapsed": Signal(phi=0.10, delta=0.10),
    }
    yield DemoStep(0, "작업 시작 터치", touch=True, signals=calm)
    yield DemoStep(301, "기준값 측정 완료", signals=calm)
    yield DemoStep(302, "PC 작업 컨텍스트", signals=calm)
    yield DemoStep(332, "피로 신호 상승", signals=high)
    yield DemoStep(522, "피로 의심", signals=high)
    yield DemoStep(552, "피로 확인 중", signals=high)
    yield DemoStep(742, "피로 확정", signals=high)
    yield DemoStep(772, "원인 분석", signals=high)
    yield DemoStep(802, "개입 제안", signals=high)
    yield DemoStep(832, "개입 실행", action_done=True, signals=high)
    yield DemoStep(862, "회복 모니터링", signals=calm)
    yield DemoStep(892, "집중 복귀", signals=calm)
    yield DemoStep(922, "작업 종료", end_touch=True, signals=calm)


def _frame(now: float, signals: dict[str, dict[str, float]], **flags: object) -> SensorFrame:
    return SensorFrame(
        now=now,
        present=True,
        pc_ratio=0.9,
        signals={name: Signal(**values) for name, values in signals.items()},
        **flags,
    )


def demo_frames() -> list[SensorFrame]:
    """Return the longer replay smoke path used by hub regression tests."""
    calm = {
        "posture": {"delta": 0.10, "phi": 0.10},
        "elapsed": {"delta": 0.10, "phi": 0.10},
        "keystroke": {"delta": 0.10, "phi": 0.10},
        "environment": {"delta": 0.10},
    }
    high = {
        "posture": {"delta": 0.90},
        "elapsed": {"delta": 0.90},
        "keystroke": {"delta": 0.85},
        "environment": {"delta": 0.80},
    }
    frames = [_frame(0, calm, touch=True)]
    frames += [_frame(now, calm) for now in range(30, 331, 30)]
    frames += [_frame(360, calm), _frame(390, calm)]
    frames += [_frame(now, high) for now in range(420, 601, 30)]
    frames += [_frame(now, high) for now in range(630, 811, 30)]
    frames += [_frame(840, high), _frame(870, high)]
    frames += [_frame(900, high, action_done=True)]
    frames += [_frame(now, calm) for now in range(930, 1051, 30)]
    frames += [_frame(1080, calm, end_touch=True)]
    return frames


def run_demo(host: str, port: int, interval: float, cycles: int) -> None:
    store = PreviewStateStore()
    server = make_server(host, port, store)
    thread = threading.Thread(target=server.serve_forever, name="preview-api", daemon=True)
    thread.start()
    print(f"DESKMATE preview API: http://{host}:{server.server_port}/api/state")

    boot_id = uuid.uuid4().hex[:8]
    seq = 0
    completed = 0
    try:
        while cycles == 0 or completed < cycles:
            engine = FSMEngine()
            for step in demo_steps():
                feedback = store.pop_feedback()
                verdict = feedback.get("verdict") if feedback else None
                if verdict:
                    print(f"      display feedback: {verdict}")
                frame = SensorFrame(
                    now=step.now,
                    present=True,
                    pc_ratio=0.9,
                    signals=step.signals,
                    touch=step.touch,
                    end_touch=step.end_touch,
                    action_done=step.action_done or verdict == "correct",
                    break_accepted=(True if verdict == "accept" else False if verdict == "reject" else None),
                )
                result = engine.tick(frame)
                store.publish(
                    state_envelope(
                        result,
                        boot_id=boot_id,
                        seq=seq,
                        sensor_summary={
                            "present": True,
                            "co2_ppm": 720 if result.scores.c_fatigue < 0.7 else 1180,
                            "lux": 410,
                            "valid": True,
                            "scenario": step.label,
                        },
                    )
                )
                print(f"[{seq:03d}] {step.label}: {result.state.value}")
                seq += 1
                time.sleep(interval)
            completed += 1
    except KeyboardInterrupt:
        print("\nDESKMATE demo stopped")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
