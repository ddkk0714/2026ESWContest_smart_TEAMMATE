"""Line-framed bridge used by the Atlas native HTTP service.

stdin  ← C++ 서비스:  POST	<id>	/api/feedback|/api/test-frame	<json>   (HTTP 요청 중계)
                      UART	{"type","seq","ts_ms","payload_hex"}         (UART2 프레임, CRC 통과분)
                      TOFSVC	<deskmate_tofposture /api/state json>       (별도 자세 서비스 판정, C++ 가 1 s 폴링)
stdout → C++ 서비스:  STATE	<envelope json> · ACK	<id>	<status>

모드 (환경변수 DESKMATE_HUB_MODE):
  demo (기본) — 합성 세션 순환 + 센서 테스트 API (기존 동작)
  live        — UART/MQTT 라인을 SensorFrame 으로 만들어 FSM 실행. Atlas native service에서는 C++가
                MQTT 연결을 소유하고 Python은 라인 프로토콜만 사용한다.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from typing import Any

from .demo import run_demo_loop
from .preview_protocol import PreviewStateStore, validate_test_frame


class BridgeStateStore(PreviewStateStore):
    def __init__(self) -> None:
        super().__init__()
        self._output_lock = threading.Lock()

    def write_message(self, message: str) -> None:
        with self._output_lock:
            sys.stdout.write(message + "\n")
            sys.stdout.flush()

    def publish(self, payload: dict[str, Any]) -> None:
        super().publish(payload)
        self.write_message(
            "STATE\t" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )


def _read_commands(store: BridgeStateStore, uart_source=None, mqtt_line_source=None, tof_service_source=None) -> None:
    for raw_line in sys.stdin.buffer:
        request_id = "0"
        try:
            line = raw_line.decode("utf-8")
            if line.startswith("UART\t"):
                if uart_source is not None:
                    uart_source.feed_line(line)
                continue
            if line.startswith("TOFSVC\t"):
                if tof_service_source is not None:
                    tof_service_source.feed_line(line)
                continue
            if line.startswith("MQTT\t"):
                if mqtt_line_source is not None:
                    mqtt_line_source.feed_line(line)
                continue
            kind, request_id, path, raw_body = line.rstrip("\n").split("\t", 3)
            if kind != "POST":
                raise ValueError("invalid command")
            body = json.loads(raw_body)
            if not isinstance(body, dict):
                raise ValueError("body must be an object")
            if path == "/api/feedback":
                if body.get("verdict") not in {"accept", "reject", "correct"}:
                    raise ValueError("invalid verdict")
                store.add_feedback(body)
            elif path == "/api/test-frame":
                validate_test_frame(body)
                store.add_test_frame(body)
            else:
                raise ValueError("invalid path")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, AttributeError):
            store.write_message(f"ACK\t{request_id}\t400")
        else:
            store.write_message(f"ACK\t{request_id}\t202")


def run_bridge(interval: float = 1.0) -> None:
    if os.environ.get("DESKMATE_HUB_MODE", "demo").lower() == "live":
        return _run_live_bridge()
    store = BridgeStateStore()
    reader = threading.Thread(target=_read_commands, args=(store,), daemon=True)
    reader.start()
    run_demo_loop(store, interval=interval, cycles=0)


def _run_live_bridge() -> None:
    """실센서 모드: UART와 MQTT 라인 → LiveHub → C++ native MQTT 발행 라인."""
    import time

    from .ingest import SensorCache
    from .ingest.mqtt_lines import MqttLineSource, control_envelope
    from .ingest.uart_source import UartLineSource
    from .live import LiveHub

    from .ingest import load_ingest_config
    from .inference import load_config
    from .ingest.mapping import mmwave_presence_hint
    from .ingest.tof_service import TofServiceLineSource, TofServicePoller, should_poll, uses_service
    from .posture.tof_mlp import load_posture_from_config
    from .profile import apply_profile

    store = BridgeStateStore()
    cache = SensorCache()
    # 시연 프로파일(hub.env DESKMATE_PROFILE=demo-a|demo-b)은 여기서 한 번 얹고 LiveHub 에는 얹은 설정을 그대로 준다(profile="")
    fsm_cfg, ingest_cfg, profile = apply_profile(load_config(), load_ingest_config(),
                                                 log=lambda m: print(m, file=sys.stderr, flush=True))
    # ToF 자세 MLP(원본 격자 0x05 → posture). 가중치가 없으면 None 이고 0x03 규칙 판정으로 돈다.
    posture = load_posture_from_config((ingest_cfg.get("tof") or {}),
                                       log=lambda m: print(m, file=sys.stderr, flush=True))
    # 사람이 있는지는 mmWave 가 정한다 — 없다고 하면 자세 모델을 돌리지 않고 기준선도 모으지 않는다
    uart = UartLineSource(cache, posture=posture, presence=mmwave_presence_hint(cache, ingest_cfg))
    native_mqtt = os.environ.get("DESKMATE_NATIVE_MQTT") == "1"
    mqtt_lines = MqttLineSource(cache) if native_mqtt else None
    # 별도 자세 서비스(deskmate_tofposture, GRU) 판정. 보드에서는 C++ 가 1 s 마다 /api/state 를 받아 TOFSVC 줄로 넣고,
    # PC 에서는 Python 폴러가 직접 받는다(보드 제한 Python 은 소켓이 없다). 둘 다 없으면 MLP → 규칙으로 돈다.
    tof_cfg = ingest_cfg.get("tof") or {}
    tof_service = TofServiceLineSource(cache) if uses_service(tof_cfg) else None
    service_poller = None
    if should_poll(tof_cfg, os.environ):
        service_poller = TofServicePoller(cache, tof_cfg, on_log=lambda m: print(m, file=sys.stderr, flush=True))
        service_poller.start()
    elif tof_service is not None:
        print("[tof_service] posture_source=service — C++ hub/atlas 의 TOFSVC 줄을 기다린다", file=sys.stderr, flush=True)
    reader = threading.Thread(
        target=_read_commands,
        args=(store, uart, mqtt_lines, tof_service),
        daemon=True,
    )
    reader.start()

    mqtt_source = None
    host = os.environ.get("DESKMATE_MQTT_HOST")
    if host and not native_mqtt:
        try:
            from .ingest.mqtt_source import MqttSource

            mqtt_source = MqttSource(cache, host, int(os.environ.get("DESKMATE_MQTT_PORT", "1883")),
                                     on_log=lambda m: print(m, file=sys.stderr))
            mqtt_source.start()
        except Exception as exc:  # noqa: BLE001 — paho 부재 등. MQTT 없이도 FSM 은 돈다.
            print(f"[bridge] MQTT 비활성: {exc}", file=sys.stderr)
            mqtt_source = None

    def publish(envelope: dict[str, Any]) -> None:
        store.publish(envelope)
        if mqtt_source is not None:
            mqtt_source.publish_state(envelope)

    def publish_request(envelope: dict[str, Any]) -> None:
        store.write_message("REQUEST\t" + json.dumps(envelope, ensure_ascii=False, separators=(",", ":")))
        if mqtt_source is not None:
            mqtt_source.publish_request(envelope)

    def publish_report(envelope: dict[str, Any]) -> None:
        store.write_message("REPORT\t" + json.dumps(envelope, ensure_ascii=False, separators=(",", ":")))
        if mqtt_source is not None:
            mqtt_source.publish_report(envelope)

    def publish_control(command: dict[str, Any]) -> None:
        # 보드(네이티브 MQTT)에서는 C++ 서비스가 CMD 라인을 control/cmd 로 발행한다(retain 없음).
        store.write_message("CMD\t" + json.dumps(control_envelope(command), ensure_ascii=False, separators=(",", ":")))
        if mqtt_source is not None:
            mqtt_source.publish_control(command)

    hub = LiveHub(cache, fsm_cfg=fsm_cfg, ingest_cfg=ingest_cfg, profile="",
                  publish=publish, publish_request=publish_request, publish_control=publish_control,
                  out=sys.stderr)
    if os.environ.get("DESKMATE_PERSONALIZATION_SELF_CHECK") == "1":
        from .personalization.self_check import run_self_check
        check = run_self_check(hub.personalization_cfg, hub.ondevice_cfg, period=hub.period,
                               normalization=hub.ingest_cfg.get("normalization", "linear"))
        print("[personalization-self-check] " + json.dumps(check), file=sys.stderr, flush=True)
    next_tick = time.time()
    # 리포트는 세션 종료를 기다리지 않고 주기 스냅샷으로 낸다. retain 발행이라
    # 나중에 붙는 화면도 바로 최신 요약을 받는다. tick 은 보통 1 s 라 tick 마다
    # 내보내면 과하고, 사람이 읽는 요약이라 10 s 면 충분하다.
    report_period = float(os.environ.get("DESKMATE_REPORT_PERIOD_SEC", "10"))
    next_report = time.time() + report_period
    # UART 통계 로그 주기: C++ uart_rx 의 30 s 리포트와 맞춘다(tick 이 10 s 면 3 tick 마다).
    stats_ticks = max(1, round(30.0 / hub.period)) if hub.period > 0 else 30
    try:
        while True:
            feedback = store.pop_feedback()
            if feedback is not None:
                cache.put_feedback(feedback)
            hub.tick_once()
            if hub.seq % stats_ticks == 0:
                st = uart.stats
                mqtt_stats = mqtt_lines.stats if mqtt_lines is not None else None
                mqtt_status = (
                    f" | mqtt lines={mqtt_stats.lines} routed={mqtt_stats.routed} "
                    f"rejected={mqtt_stats.rejected}"
                    if mqtt_stats is not None
                    else ""
                )
                # grids = 완성된 0x05 표본(센서 프레임이 다 모인 것), parts = 받은 0x05 센서 프레임,
                # grid_discarded = frame_id 가 바뀌어 버린 미완성 표본(TofGridAssembler.discarded).
                # mlp = posture/tof_mlp 의 최근 판정(없으면 '-'). baseline 이 안 잡히면 unknown/used=0 이 정상이다.
                mlp = cache.tof_mlp.data if cache.tof_mlp is not None else None
                mlp_status = (
                    f" | mlp={mlp.get('posture')}/{mlp.get('posture_confidence')} present={int(bool(mlp.get('present')))} "
                    f"used={mlp.get('sensors_used')} frames={mlp.get('frames')}"
                    + (f" err={mlp['error']}" if mlp.get("error") else "")
                    if mlp else " | mlp=-"
                )
                # svc = 별도 자세 서비스(GRU) 의 최근 판정과 수신 통계(TOFSVC 줄 또는 Python 폴러)
                svc = cache.tof_service.data if cache.tof_service is not None else None
                if tof_service is not None or service_poller is not None:
                    svc_rx = (f"lines={tof_service.stats.accepted}/{tof_service.stats.lines}"
                              if tof_service is not None else "")
                    if service_poller is not None:
                        svc_rx += f" polls={service_poller.stats.ok}/{service_poller.stats.polls}"
                    mlp_status += (
                        f" | svc={svc.get('posture')}/{svc.get('posture_confidence')} valid={int(bool(svc.get('valid')))} "
                        f"online={svc.get('online_sensors')} {svc_rx}"
                        if svc else f" | svc=- {svc_rx}"
                    )
                print(
                    f"[bridge] uart frames={st.frames} dropped={st.dropped} hb={st.heartbeats} "
                    f"unknown={st.unknown_types} grids={st.grids} parts={st.grid_parts} "
                    f"grid_discarded={uart.grid.discarded}{mlp_status}{mqtt_status}",
                    file=sys.stderr, flush=True,
                )
            if report_period > 0 and time.time() >= next_report:
                publish_report(hub.report_envelope())
                next_report = time.time() + report_period
            next_tick += hub.period
            time.sleep(max(0.0, next_tick - time.time()))
    finally:
        if mqtt_source is not None:
            mqtt_source.stop()


if __name__ == "__main__":
    run_bridge()
