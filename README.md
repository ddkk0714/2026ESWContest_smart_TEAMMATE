# DESKMATE

**제24회 임베디드SW경진대회 · 스마트 가전 부문 · 팀 TEAMMATE**

ToF 거리 센서, 60GHz mmWave, 키스트로크 타이밍, 환경 센서를 융합해 책상 작업 상태를
VER5 18개 내부 상태로 추론하고, 사용자 화면에는 여섯 phase로 축약한다. 판단 신뢰도에 따라
조명 · 환기 · 자세 교정 · 휴식 알림을 자동 실행하거나 제안하는 비침습 워크스페이스 가전.

카메라와 마이크를 사용하지 않고, 센싱 · 추론 · 제어를 모두 로컬 장치에서 수행한다.

> **현재 위치(2026-09-14)와 결선까지의 계획은 [`docs/roadmap.md`](docs/roadmap.md)** 에 있다.

---

## 시스템 구성

```
                  [ 센싱 ]                     [ 추론 ]                 [ 출력 · 제어 ]

  ESP32 노드 ──────┐
   mmWave C1001    │  UART2                 Raspberry Pi 4              Raspberry Pi 5
   SCD41 CO₂/온습도 ├─ COBS+CRC-16 ───────►  중앙 추론 허브  ─ MQTT ───►  AI Native OS Video Profile
   BH1750 조도     │  (실측 완료)           AI Native OS Headless      ATLAS 상태·제안·리포트
   DHT22 온습도    │                        FSM + TFLite                피드백 입력(터치)
                   │                              │              ◄────  수락 · 거절 · 정정
  PC 수집기 ── Wi-Fi/MQTT ─┤                      │
   키스트로크 타이밍        │                      ▼
  VL53L9CX ToF ────────────┘  경로 미결       스마트 플러그 · 조명 · 환기팬
   (Pi4 CSI-2 / ESP32 I2C)                   ThinQ (1기기 선택)
```

| 계층 | 장치 | 디렉터리 |
|---|---|---|
| 센싱 | ESP32 (센서 말단 노드) | [`firmware/`](firmware/) |
| 센싱 | PC (키스트로크 수집) | [`collector/`](collector/) |
| 추론 | Raspberry Pi 4 (중앙 허브) | [`hub/`](hub/) |
| 출력 | Raspberry Pi 5 + ATLAS (디스플레이 단말) | [`display/`](display/) |
| 학습 | PC (모델 학습 → TFLite) | [`ml/`](ml/) |

> LG 기술교육 제공 구성에 따라 Raspberry Pi 5(8GB)의 AI Native OS Video Profile을
> 출력 단말로 사용한다. 가산점 요건을 위해 ESP32·Pi 4·Pi 5 **3종 구성을 유지**한다.

> **보드 역할·통신(확정):** Pi 4는 AI Native OS Headless 위의 native service(`hub/atlas`)로 UART 디코딩·센서 융합·FSM·제어 판단을 맡고,
> Pi 5는 Atlas Flutter 기반 터치 UI·스피커 알림·사용자 수락/거절/정정을 맡는다. **Pi 4↔Pi 5는 이더넷 직결 + MQTT**,
> **ESP32↔Pi 4는 UART2(COBS + CRC-16, 2026-09-11 실측)** 다. display 중단 중에도 hub의 기본 판정과 제어는 유지한다.

---

## 디렉터리 구조

```
├── docs/          설계 문서 · 아키텍처 · MQTT 토픽 · FSM 사양 · 제출 서류
├── firmware/      ESP32 센서 노드 펌웨어
├── collector/     PC 키스트로크 타이밍 수집기
├── hub/           Raspberry Pi 4 중앙 추론 허브
│   ├── atlas/         AI Native OS Headless 용 native service IPK (제한 Python 으로 FSM 실행, UART 디코더 예정)
│   ├── mqtt/          Pi 4 Mosquitto 설정
│   └── deskmate_hub/
│       ├── ingest/      UART 라인 · MQTT 수신 → SensorFrame (미구현)
│       ├── features/    baseline 정규화 · 특징 추출 (미구현)
│       ├── inference/   규칙 기반 FSM (1단계) · 경량 분류기 (2단계) · 신뢰도 게이트
│       ├── control/     스마트 플러그 · ThinQ 제어 (미구현)
│       └── config/      임계값 · 토픽 · 장치 설정
├── display/       Raspberry Pi 5 + ATLAS 디스플레이 UI · 사용자 피드백
│   └── atlas/         Docker 기반 Atlas Flutter 개발 환경
├── ml/            학습 파이프라인 · TFLite 변환 · ESM 라벨링
└── tools/         데이터 로깅 · 시각화 · 실험 스크립트
```

각 디렉터리의 `README.md` 에 담당자와 범위가 적혀 있다.

---

## 개발 원칙

1. **1단계 규칙 기반 FSM 을 완성도의 축으로 둔다.**
   결선 최대 배점이 완성도(60점)이므로, ML 정확도보다 규칙 FSM + 실제 기기 제어의
   확실한 동작이 우선이다. 2단계 경량 분류기는 이를 보완 · 검증하는 역할이다.
2. **호흡 측정은 보조 신호다.** 재실 · 자세를 주력으로 하고, mmWave 순간 호흡·심박값은 판정에 쓰지 않는다
   (중앙값+기준선, "호흡 소실 여부"만 증거). 호흡 실패가 전체 상태 판정을 흔들지 않도록 설계한다.
3. **ToF 원본 54×42 배열을 운영 경로로 보내지 않는다.** 센서 host에서 특징값을 만들고,
   축소 depth map은 명시적 디버그/UI 모드에서만 최대 2Hz로 허용한다. ([`docs/data-spec.md`](docs/data-spec.md))
4. **프라이버시** — 키 값은 수집하지 않고 타임스탬프만 다룬다.
   카메라 · 마이크 미사용. 자가기록 라벨은 저장소에 커밋하지 않는다.
5. **불확실성은 사용자에게 확인한다.** 센서 신호가 충돌하면 자동 제어 대신 display에서
   제안·수락·거절·정정을 받고, 결과를 개인화에 활용한다.
6. **가전은 양방향으로 연동한다.** 제어 명령뿐 아니라 가전 상태와 실행 결과를 받아
   다음 판단과 안전한 복구에 반영한다.

---

## 명세 문서 — 개발 기준

요구사항 명세서와 데이터 명세서를 **작성 완료**했다. 이 두 문서와 FSM·MQTT 문서는
**모듈 간 계약**이므로, 구현을 바꾸기 전에 문서를 먼저 바꾸고 같은 PR 에 포함한다.

| 문서 | 내용 | 상태 |
|---|---|---|
| [`docs/requirements-spec.md`](docs/requirements-spec.md) | MVP 기능 · 비기능 요구사항, 수용 기준, 검증 항목, 미결정 항목 | 작성 완료 |
| [`docs/data-spec.md`](docs/data-spec.md) | L0~L5 데이터 계층, 값 · 단위 · 유효성 · 시간 · 보정 · 융합 계약 | 작성 완료 |
| [`docs/fsm-spec.md`](docs/fsm-spec.md) | VER5 18상태 전이 · 임계값 · 이중 신뢰도 공식 | 유지보수 중 |
| [`docs/mqtt-topics.md`](docs/mqtt-topics.md) | Pi 4↔Pi 5 MQTT topic · payload 계약 | 확정, UART 프레임 TYPE 은 미확정 |

현재 합성 입력으로 `Pi 4 FSM → Pi 5 Atlas 화면 → 사용자 피드백`을 시험할 수 있다(HTTP 개발 어댑터, MQTT 는 display 측 구독까지).
실행 순서와 하드웨어 연결은 [`docs/hardware.md`](docs/hardware.md), Pi 5 배포는
[`docs/atlas-build-handoff.md`](docs/atlas-build-handoff.md)를 따른다.
실센서는 아직 FSM 에 연결되지 않았다 — mmWave 는 Pi 4 UART 까지 도달을 확인했고, 디코더·ingest 가 다음 작업이다.

### Pi 5 Atlas 실제 개발·배포 흐름

```text
개발 PC (x86_64)                                      Raspberry Pi 5 (arm64)
┌─ Docker: deskmate-atlas-dev ─────────────────┐      ┌─ AI Native OS ──────────┐
│ 코드 수정 → Flutter 테스트 → Atlas 크로스 빌드 │ SSH  │ .ipk 설치 → 앱 실행      │
│       ↑                 ↓                    ├─────►│ 터치·화면 확인 → 실행 로그 │
│       └──── 로그를 보고 다시 수정             │      └────────────────────────┘
└───────────────────────────────────────────────┘
```

- Docker와 Atlas SDK는 **개발 PC에서만** 사용한다. Pi 5에 Docker를 설치하지 않는다.
- `flutter-atlas build atlas --ipk --release`가 arm64용 설치 패키지를 만든다.
- `flutter-atlas run -d <device_id> --release`가 SSH로 기존 앱 제거, 업로드, 설치, 실행을 처리한다.
- 개발 중에는 debug 실행과 hot reload로 반복하고, 인계·시연 후보는 release `.ipk`로 고정한다.

전체 준비·빌드·배포·로그 확인 명령은 [Pi 5 Atlas 개발 가이드](display/atlas/README.md)에 있다.

**AI CLI 로 개발한다면** [`docs/agent-briefing.md`](docs/agent-briefing.md)와
[`docs/roadmap.md`](docs/roadmap.md)를 먼저 읽는다.
**개발 방식은 각자 자유다.** 다만 확정·미결정 사항과 프라이버시 제약만은 누가 작업하든 같아야 해서 한곳에 모아뒀다.

**아직 미결정이므로 코드·문서에 확정으로 못 박지 않는다:** ToF 연결 경로(Path A/B, 09-19 결정), UART 프레임 TYPE·mmWave 출력 스키마,
`C_focus` 부호, 개인화 저장소.
전체 목록은 [`docs/agent-briefing.md`](docs/agent-briefing.md) §3 에 있다.

---

## 팀 구성

| 이름 | 역할 | 담당 | 주 디렉터리 |
|---|---|---|---|
| 김태환 | 팀장 | 총괄 · 일정, 센서 인터페이싱, ToF 전처리 · 재실/자세 판정, 캘리브레이션 | `hub/features/`, `firmware/` |
| 박소연 | 팀원 | 규칙 기반 FSM 추론 엔진, 신뢰도 공식, 신뢰도 게이트, 작업 리포트 | `hub/inference/` |
| 조명희 | 팀원 | 경량 분류기 · TFLite 변환 · 온디바이스 최적화, ThinQ 연동, ESM 라벨 체계 | `ml/`, `hub/control/` |
| 이민혁 | 팀원 | ESP32 펌웨어 · 1차 전처리, MQTT 브로커 · 토픽 설계, 로깅 · 시간 동기화 | `firmware/`, `hub/ingest/` |
| 최민경 | 팀원 | 디스플레이 UI · AOD, 사용자 피드백 처리 · 리포트 시각화, 키스트로크 수집 | `display/`, `collector/` |

---

## 일정

| 기간 | 내용 |
|---|---|
| ~ 2026-07-27 | 예선 합격 · 저장소 · 개발 환경 세팅 |
| 2026-07-30 | 1차 기술 교육 · **장비 수령** (Pi5 27W 어댑터 · SD 용량 확인) |
| 2026-08 | FSM 엔진 · Pi 5 Atlas 배포 파이프라인 · 명세 문서 · 통신 아키텍처 확정 |
| 2026-09-11 | **mmWave → ESP32 → Pi 4 UART 물리 링크 실측 통과** |
| 2026-09-15 ~ 09-28 | mmWave 수직 슬라이스(E2E) · ToF 경로 확정(09-19) · 환경/키스트로크 합류 (Pi 5 디스플레이 교체·터치 정상 09-14) |
| 2026-09-29 ~ 10-12 | 스마트 플러그 제어 · UI 4화면 · baseline 정규화 · 실측 로그 튜닝 |
| 2026-10-13 ~ 10-30 | 하우징 조립 · 장시간 시험 · **결선 서류 제출**(개발완료보고서 · 작품소개서 · 시연영상) |
| 2026-11-06 | 오프라인 발표 심사 |

주차별 상세와 통과 기준은 [`docs/roadmap.md`](docs/roadmap.md).

---

## 협업 규칙

- `main` 직접 push 금지. `feat/<기능명>` 브랜치 → PR → 리뷰 후 머지
- 커밋 메시지: `feat(scope): 내용` / `fix(scope): 내용` / `docs(scope): 내용`
- 데이터셋 · 학습 모델 · 자격증명은 커밋하지 않는다 (`.gitignore` 참조)

## 관련 링크

- 팀 Notion: 임베디드 SW 경진대회 (LG)
- [AI 개발 공통 브리핑](docs/agent-briefing.md) · [문서 지도](docs/README.md)
- [요구사항 명세서](docs/requirements-spec.md) · [데이터 명세서](docs/data-spec.md)
- [결선 로드맵 · 갭 분석](docs/roadmap.md) · [Pi 5 배포 인계](docs/atlas-build-handoff.md)
- [Pi 5 ATLAS Docker 개발 환경](display/atlas/README.md)
- 시연 영상: (결선 제출 시 추가)
