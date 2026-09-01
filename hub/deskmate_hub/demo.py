"""Deterministic demo inputs for replay and the Atlas preview API."""
from __future__ import annotations

import math
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


def keystroke_summary(signal: Signal | None, ts: float, *, seq: int = 0) -> dict[str, object]:
    """데모용 키스트로크 원지표를 만든다.

    실제 경로는 반대다. collector 가 원지표를 보내고 features 가 baseline 대비
    delta 로 정규화한다. 데모에는 collector 가 없으므로 정규화된 delta 에서
    그럴듯한 원지표를 거꾸로 만들어 화면만 채운다. 판정에는 쓰지 않는다.

    FSM 신호는 calm/high 두 단계뿐이라 그대로 쓰면 같은 숫자가 십수 초씩 이어져
    화면이 멈춘 것처럼 보인다. 실제 1Hz 창은 표본이 조금씩 갈려 매번 흔들리므로,
    seq 로 결정적인 흔들림을 얹는다. 결정적이라 리플레이·테스트가 재현된다.
    """
    delta = 0.0 if signal is None else max(0.0, min(1.0, float(signal.delta)))

    # 주기가 서로 안 맞는 두 사인을 겹쳐 반복이 눈에 띄지 않게 한다. 범위는 -1..1.
    wobble = (math.sin(seq * 1.7) + math.sin(seq * 0.41)) / 2.0

    def mix(calm: float, tired: float, jitter: float) -> float:
        return calm + (tired - calm) * delta + jitter * wobble

    def ratio(calm: float, tired: float, jitter: float) -> float:
        return max(0.0, min(1.0, mix(calm, tired, jitter)))

    return {
        "node": "pc-collector",
        "ts": ts,
        "window_s": 60,
        "event_count": max(0, round(mix(214, 92, 9))),
        "dwell_mean_ms": round(max(1.0, mix(88.0, 124.0, 5.0)), 1),
        "dwell_std_ms": round(max(0.0, mix(15.0, 42.0, 2.5)), 1),
        "flight_mean_ms": round(max(1.0, mix(131.0, 233.0, 11.0)), 1),
        "flight_std_ms": round(max(0.0, mix(38.0, 158.0, 7.0)), 1),
        "idle_ratio": round(ratio(0.11, 0.41, 0.03), 3),
        "correction_rate": round(ratio(0.03, 0.13, 0.015), 3),
        "mouse_event_rate": round(max(0.0, mix(0.4, 1.6, 0.2)), 2),
        "typing_active": delta < 0.9,
        "valid": True,
    }


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
                            "keystroke": keystroke_summary(
                                step.signals.get("keystroke"), time.time(), seq=seq
                            ),
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
