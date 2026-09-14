# hub — Raspberry Pi 4 중앙 추론 허브

ESP32 노드(UART2)와 PC 수집기(MQTT)가 보낸 센서 스트림을 받아 슬라이딩 윈도우(30s ~ 5min)로
특징 벡터를 만들고, 상태와 신뢰도를 산출해 MQTT 로 발행한다. 제어 판단도 여기서 한다.

**런타임(확정):** Pi 4 는 AI Native OS Headless(ATLAS). 이미지에 Python·컴파일러가 없으므로
[`atlas/`](atlas/README.md) 의 native service IPK 가 `/restricted/python3` 로 이 패키지를 실행한다.
UART 디코딩(COBS/CRC-16)과 MQTT 클라이언트는 그 C++ 서비스에 두고, 라인 프로토콜(`bridge` 서브커맨드)로 Python 과 주고받는다.
보드에 개발 도구를 설치하지 않는다.

## 모듈 (2026-09-14)

| 디렉터리 | 담당 | 내용 | 상태 |
|---|---|---|---|
| `atlas/` | 공통 | ARC IPK native service. 제한 Python 으로 FSM 실행, HTTP 8765 개발 API | ✅ 실행. UART 수신·MQTT 발행은 미구현 |
| `mqtt/` | 이민혁 | Pi 4 Mosquitto 설정 | ✅ |
| `ingest/` | 이민혁 | UART 라인(bridge) · MQTT 키스트로크 → `SensorFrame`, freshness·seq 검증, 로깅 | ⬜ 빈 패키지 — **MVP 09-17: MQTT 센서 토픽 → 10 s SensorFrame**, UART 라인은 이후 |
| `features/` | 김태환 | baseline 캘리브레이션(중앙값·MAD Modified z-score, 시간대별) → `phi/delta` | ⬜ 빈 패키지 |
| `inference/` | 박소연 | 규칙 기반 FSM(1단계), 신뢰도 공식, 신뢰도 게이트 | ✅ 18상태, 테스트 46개(45 통과·1 xfail) |
| `inference/` | 조명희 | TFLite 경량 분류기 로딩 · 추론(2단계) | ⬜ 선택적 의존 |
| `control/` | 조명희 | 스마트 플러그 · 조명 · 환기팬 제어, ThinQ 1기기 | ⬜ 빈 패키지 |
| `config/` | 공통 | 임계값 · 토픽 · 장치 설정 (YAML) | ✅ `fsm.yaml` |
| `replay.py` `demo.py` `preview_api.py` `service_bridge.py` | 박소연 | 리플레이 하네스 · 합성 데모 · HTTP 미리보기 · Atlas 라인 브리지 | ✅. `report.py` 는 `feat/fsm-report` 미머지 |

## 설계 규칙

- **임계값을 코드에 하드코딩하지 않는다.** 전부 `config/` YAML 로 뺀다.
  8월 실측 후 값만 바꿔 재실험할 수 있어야 한다.
- 1단계 FSM 은 2단계 분류기 없이 **단독으로 완전 동작**해야 한다.
  분류기는 import 실패해도 허브가 죽지 않도록 선택적 의존으로 둔다.
- 판정 사이클 500ms 이하 목표.
- ThinQ 자격증명은 커밋하지 않는다. 환경변수 또는 `config/secrets.yaml`(gitignore).

## 실행

```bash
cd hub
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m deskmate_hub demo --host 0.0.0.0 --port 8765
```

위 명령은 합성 센서 입력을 FSM에 넣고 상태를 1초마다 순환한다. Pi 5 display는
`http://<Pi4-IP>:8765/api/state`에서 상태를 읽고 `/api/feedback`으로 수락·거절을 돌려준다.
이 HTTP API는 **최종 통신을 정하기 전 보드 간 화면 검증용 어댑터**다. MQTT 채택 여부와
무관하게 FSM·화면 계약은 유지하고 어댑터만 교체한다.

```bash
curl http://127.0.0.1:8765/health
curl http://127.0.0.1:8765/api/state
```

Atlas 앱의 **센서 테스트** 화면이 `/api/test-frame`에 입력을 보내면 자동 순환을 멈추고
같은 `FSMEngine`·`config/fsm.yaml`로 가상 시간을 진행한다. 입력은 원시 센서값이 아닌
baseline 대비 `phi`·`delta` 정규화 기여도이며 운영 입력으로 저장하지 않는 개발 전용 API다.
`reset`은 설정된 baseline 시간을 가상으로 진행해 몰입 상태로 빠르게 진입하고,
`tick`은 앱에서 지정한 30초 또는 3분만큼 진행한다.

`python -m deskmate_hub --demo` / `--replay <log.jsonl>` 는 합성 세션·JSONL 리플레이를 돌린다.
**실센서 ingest 와 hub 측 MQTT 발행은 아직 없다.** Pi 5 가 MQTT 로 상태를 받는 경로는 지금까지 Node-RED 주입으로만 확인했다.
다음 순서로 채운다.

1. `atlas/` C++ 서비스: `/dev/serial0` 수신 → COBS 해제 → CRC 검증 → `UART\t<json>` 라인으로 Python 에 전달
2. `ingest/`: 라인·MQTT 를 `SensorFrame` 으로 변환(seq·ts freshness 검증)
3. `atlas/` C++ 서비스 또는 Python: `deskmate/state/phase`(retain)·`interaction/request` 발행, `feedback/user` 구독
4. HTTP 8765 는 센서 테스트·화면 분리 검증용 fallback 으로 유지

## 테스트

```bash
pytest tests/
```

FSM 상태 전이는 합성 입력으로 단위 테스트한다 — 실기기 없이 검증 가능한 유일한 부분이다.
