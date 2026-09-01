# hub — Raspberry Pi 4 중앙 추론 허브

ESP32 노드와 PC 수집기가 발행한 센서 스트림을 구독해 슬라이딩 윈도우(30s ~ 5min)로
특징 벡터를 만들고, 상태와 신뢰도를 산출해 발행한다. 제어 판단도 여기서 한다.

## 모듈

| 디렉터리 | 담당 | 내용 |
|---|---|---|
| `ingest/` | 이민혁 | MQTT 구독, 시간 동기화, 로깅, 장애 재연결 |
| `features/` | 김태환 | ToF · 키스트로크 · 환경 특징 추출, baseline 캘리브레이션 |
| `inference/` | 박소연 | 규칙 기반 FSM(1단계), 신뢰도 공식, 신뢰도 게이트 |
| `inference/` | 조명희 | TFLite 경량 분류기 로딩 · 추론(2단계) |
| `control/` | 조명희 | ThinQ API, 스마트 플러그 · 조명 · 환기팬 제어 |
| `config/` | 공통 | 임계값 · 토픽 · 장치 설정 (YAML) |

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

`python -m deskmate_hub`도 현재는 같은 데모를 기본 실행한다. 실센서 ingest와 로그 리플레이는
아직 연결되지 않았으므로 실행 가능하다고 오해하지 않도록 별도 구현 후 이 문서를 갱신한다.

## 테스트

```bash
pytest tests/
```

FSM 상태 전이는 합성 입력으로 단위 테스트한다 — 실기기 없이 검증 가능한 유일한 부분이다.
