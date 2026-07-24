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
python -m deskmate_hub --demo                            # 합성 세션 스모크
python -m deskmate_hub --replay logs/2026-08-01.jsonl    # 로그 리플레이
python -m deskmate_hub --demo --report                   # + 세션 작업 리포트
```

로그 리플레이는 실기기 없이 FSM 임계값을 튜닝하기 위한 것이다. 초기에 만들어 두면
8월 데이터 수집 이후 반복 실험이 훨씬 빨라진다. `--report` 는 세션 요약(몰입 시간·피로
에피소드·개입 결과·ESM 라벨)을 함께 출력한다. (실시간 허브 기동 모드는 ingest 연결 후 구현)

## 테스트

```bash
pytest tests/
```

FSM 상태 전이는 합성 입력으로 단위 테스트한다 — 실기기 없이 검증 가능한 유일한 부분이다.
