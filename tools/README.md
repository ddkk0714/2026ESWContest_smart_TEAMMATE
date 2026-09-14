# tools — 개발 · 실험 보조 스크립트

실기기 없이도 개발을 진행하기 위한 도구를 모은다. 제품 운영 경로에는 포함되지 않는다.

## 있는 것

| 도구 | 용도 |
|---|---|
| [`node-red-visualizer/`](node-red-visualizer/README.md) | PC 에서 Pi 4 MQTT broker 를 구독·관찰하고 `state/phase`·`feedback/user`·`display/message` 합성 메시지를 주입. hub MQTT 발행이 생기기 전 화면 경로 확인용 |
| [`uart_mqtt_bridge.py`](uart_mqtt_bridge.py) | ESP32 USB(UART0) JSON 라인을 MQTT 센서 envelope로 변환하고 날짜별 JSONL로 기록 |
| `connect-deskmate-pi4.ps1` | DHCP 로 바뀌는 Pi 4 주소를 탐색해 SSH 별칭(`atlas`·`rpi4`·`deskmate-pi4`)을 갱신 |

## 만들면 유용한 것 (우선순위 순)

| 도구 | 용도 | 언제 필요한가 |
|---|---|---|
| `mqtt_sim.py` | 가짜 센서 스트림 발행 | ESP32 없이 허브 · 디스플레이 개발. **가장 먼저 필요** |
| `log_recorder.py` | 전 토픽 JSONL 로 기록 | 8월 데이터 수집 · 리플레이 소스 |
| `plot_session.py` | 세션 타임라인 시각화 (자세 · 타이핑 · CO₂ · 국면) | 임계값 튜닝, 발표 자료 |
| `tof_probe.py` | VL53L9CX 54×42 원본·축소 depth map 실시간 확인 | Pi 4 MIPI CSI-2 연결 1일 spike와 자세 특징 검증용 |

`mqtt_sim.py` 를 초반에 만들어 두면 5명이 장비 1세트를 기다리지 않고
병렬로 작업할 수 있다. 장비 수령이 7/30 이므로 그 전까지는 이게 유일한 개발 경로다.

## 사용

```bash
python -m pip install -r tools/requirements.txt
python tools/uart_mqtt_bridge.py --port COM5 --baud 115200 --broker <pi4-ip> --node esp32
python tools/mqtt_sim.py --broker localhost --scenario fatigue
python tools/log_recorder.py --broker <pi4-ip> --out logs/
python tools/plot_session.py logs/2026-08-01.jsonl
```

브리지 옵션은 `DESKMATE_SERIAL_PORT`, `DESKMATE_SERIAL_BAUD`, `DESKMATE_MQTT_BROKER`,
`DESKMATE_MQTT_PORT`, `DESKMATE_NODE`, `DESKMATE_LOG_DIR` 환경변수로도 지정할 수 있다.
센서 envelope는 MQTT 연결 여부와 무관하게 `logs/<node>-YYYYMMDD.jsonl`에 남는다.
