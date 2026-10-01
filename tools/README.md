# tools — 개발 · 실험 보조 스크립트

실기기 없이도 개발을 진행하기 위한 도구를 모은다. 제품 운영 경로에는 포함되지 않는다.

## 있는 것

| 도구 | 용도 |
|---|---|
| [`node-red-visualizer/`](node-red-visualizer/README.md) | PC 에서 Pi 4 MQTT broker 를 구독·관찰하고 `state/phase`·`feedback/user`·`display/message` 합성 메시지를 주입. hub MQTT 발행이 생기기 전 화면 경로 확인용 |
| [`uart_mqtt_bridge.py`](uart_mqtt_bridge.py) | ESP32 USB(UART0) JSON 라인을 MQTT 센서 envelope로 변환하고 날짜별 JSONL로 기록 |
| [`mqtt_scenario_sim.py`](mqtt_scenario_sim.py) | 합성 센서 시나리오(착석→타이핑→정적/졸음→회복→이탈)를 계약 그대로 MQTT 로 발행. ESP32·collector 없이 hub·Node-RED·Pi 5 리허설 |
| [`rehearsal_local.py`](rehearsal_local.py) | amqtt 로컬 브로커 + mock 플러그 + sim(short·demo) + `hub run --config fsm.demo.yaml` 을 한 번에 띄우고 전이 요약(약 6분). `--host 0.0.0.0` 이면 Pi 5 앱도 붙는다 |
| [`find-boards.ps1`](find-boards.ps1) | 휴대폰 핫스팟 등 지금 Wi-Fi 에서 Pi 4·Pi 5 를 MAC 으로 찾아 ssh 명령 출력, `-UpdateSshConfig` 면 `ssh atlas-hs`·`ssh pi5-hs` 별명 갱신 |
| [`ui_step_sim.py`](ui_step_sim.py) | hub 없이 앱이 받는 메시지(state·request·report)를 10초 간격으로 보내 Pi 5 화면 흐름을 1~2분에 확인. 화면 응답을 콘솔에 표시 |
| [`mqtt_watch.py`](mqtt_watch.py) | 실기 점검용 MQTT 감시(읽기 전용) — 토픽별로 점검에 필요한 값만 한 줄로. 점검표 `docs/field-checklist.md` |
| [`measure_tick.py`](measure_tick.py) | hub tick 한 번의 시간 분포(p50·p95·p99) — 판정 사이클 ≤ 500 ms 를 PC 에서 먼저 확인. Pi 4 는 실기에서 다시 잰다 |
| [`demo_dryrun.py`](demo_dryrun.py) | 시연 시나리오를 브로커·실시간 없이 hub 에 흘려 전이·제어·리포트를 수 초 만에 확인. 동선 시험은 `test_demo_scenario.py`, 동선은 [`docs/demo-scenario.md`](../docs/demo-scenario.md) |
| [`mock_plug.py`](mock_plug.py) | `deskmate/control/cmd` 를 받아 `control/result` 로 응답하는 모의 플러그(지연·실패 옵션, `control/state/<target>` retain). 실기 플러그 모델 선정 전 제어 경로 리허설 |
| [`uart_frame_tool.py`](uart_frame_tool.py) | UART2 바이너리 프레임(COBS+CRC-16) test vector 출력·인코딩·디코딩, USB-TTL 로 ESP32 UART2 직접 읽기. 코덱은 `hub/deskmate_hub/ingest/uart_frame.py` 공유 |
| `connect-deskmate-pi4.ps1` | DHCP 로 바뀌는 Pi 4 주소를 탐색해 SSH 별칭(`atlas`·`rpi4`·`deskmate-pi4`)을 갱신 |
| [`atlas-hotspot-broker/`](atlas-hotspot-broker/README.md) | 휴대폰 핫스팟으로 Pi 4·Pi 5 를 묶고 Pi 4 에 정적 빌드 Mosquitto 를 띄우는 개발 통신 경로 (opkg 불가 환경용) |

## 만들면 유용한 것 (우선순위 순)

| 도구 | 용도 | 언제 필요한가 |
|---|---|---|
| `log_recorder.py` | 전 토픽·UART 라인을 JSONL 로 기록 | 실측 로그 축적 → `--replay` 튜닝 소스 |
| `plot_session.py` | 세션 타임라인 시각화 (자세 · 체동 · 타이핑 · CO₂ · 국면) | 임계값 튜닝, 보고서 그림 |
| `tof_probe.py` | VL53L9CX 원본·축소 depth map 실시간 확인 | Path A/B spike 와 자세 특징 검증 |

## 사용

```bash
python -m pip install -r tools/requirements.txt
python tools/uart_mqtt_bridge.py --port COM5 --baud 115200 --broker <pi4-ip> --node esp32
python tools/mqtt_scenario_sim.py --broker <broker-ip> --scenario default   # 실제 타이머, 약 25분
python tools/rehearsal_local.py 400                                          # PC 단독, 짧은 타이머 프로파일
python tools/demo_dryrun.py                                                  # 5분 시연 동선을 수 초 만에 확인
python tools/measure_tick.py                                                 # 판정 주기 p95 (PC)
python tools/rehearsal_local.py --scenario demo --host 0.0.0.0              # 5분 시연 실시간 리허설(+Pi 5 화면)
python tools/log_recorder.py --broker <pi4-ip> --out logs/
python tools/plot_session.py logs/2026-08-01.jsonl
```

브리지 옵션은 `DESKMATE_SERIAL_PORT`, `DESKMATE_SERIAL_BAUD`, `DESKMATE_MQTT_BROKER`,
`DESKMATE_MQTT_PORT`, `DESKMATE_NODE`, `DESKMATE_LOG_DIR` 환경변수로도 지정할 수 있다.
센서 envelope는 MQTT 연결 여부와 무관하게 `logs/<node>-YYYYMMDD.jsonl`에 남는다.
