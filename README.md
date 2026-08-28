# DESKMATE

**제24회 임베디드SW경진대회 · 스마트 가전 부문 · 팀 TEAMMATE**

ToF 거리 센서, 키스트로크 타이밍, 환경 센서를 융합해 책상 작업 상태를
VER5 18개 내부 상태로 추론하고, 사용자 화면에는 여섯 phase로 축약한다. 판단 신뢰도에 따라
조명 · 환기 · 휴식 알림을 자동 실행하거나 제안하는 비침습 워크스페이스 가전.

카메라와 마이크를 사용하지 않고, 센싱 · 추론 · 제어를 모두 로컬 장치에서 수행한다.

---

## 시스템 구성

```
                  [ 센싱 ]                [ 추론 ]            [ 출력 · 제어 ]

  ESP32 노드 ──┐
   mmWave       │                      Raspberry Pi 4        Raspberry Pi 5
   CO2/온습도   ├── 통신 후보 ───────►  중앙 추론 허브  ─────►  AI Native OS Video Profile
   조도         │   UART / MQTT        FSM + TFLite          ATLAS 상태·제안·리포트
                │                            │                피드백 입력
  PC 수집기 ───┤                            │
   키스트로크 타이밍                        ▼
  VL53L9CX ────┘ Pi4 MIPI CSI-2 우선  ThinQ D-Bus/API adapter
                                      스마트 플러그 · 조명 · 환기팬
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

> **ATLAS 배치 결정:** ATLAS 런타임은 Pi 5 Video Profile display에 둔다. Pi 4는 센서 융합·FSM·제어 판단을 맡고,
> Pi 5는 Atlas Flutter 기반 터치 UI·스피커 알림·사용자 수락/거절/정정을 맡는다. 보드 간 통신은 MQTT가
> 최우선 후보지만 아직 확정하지 않았으며, 어떤 방식을 택해도 display 중단 중 hub의 기본 판정과 제어는 유지한다.

---

## 디렉터리 구조

```
├── docs/          설계 문서 · 아키텍처 · MQTT 토픽 · FSM 사양 · 제출 서류
├── firmware/      ESP32 센서 노드 펌웨어
├── collector/     PC 키스트로크 타이밍 수집기
├── hub/           Raspberry Pi 4 중앙 추론 허브 (Python 우선, 실물 OS 확인 필요)
│   └── deskmate_hub/
│       ├── ingest/      통신 adapter · 시간 동기화
│       ├── features/    ToF · 키스트로크 · 환경 특징 추출
│       ├── inference/   규칙 기반 FSM (1단계) · 경량 분류기 (2단계) · 신뢰도 게이트
│       ├── control/     ThinQ API · 스마트 플러그 제어
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
2. **호흡 측정은 보조 신호다.** 재실 · 자세를 주력으로 하고,
   호흡 실패가 전체 상태 판정을 흔들지 않도록 설계한다.
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
| [`docs/mqtt-topics.md`](docs/mqtt-topics.md) | MQTT 채택 시 topic · payload 매핑 초안 | 통신 확정 대기 |

**AI CLI 로 개발하기 전에** [`docs/agent-briefing.md`](docs/agent-briefing.md) 를 읽히고,
[`docs/agent-kickoff-prompt.md`](docs/agent-kickoff-prompt.md) 의 공통 문구를 세션 첫 입력으로 넣는다.
Claude Code · Codex 어느 쪽이든 동일한 기준으로 작업하게 하기 위한 단일 출처다.

**아직 미결정이므로 코드·문서에 확정으로 못 박지 않는다:** 보드 간 물리 통신(MQTT 최우선 후보),
`C_focus` 부호, Pi 4 FSM 배포 런타임, 개인화 저장소, 호흡 go/no-go.
전체 목록은 [`docs/requirements-spec.md`](docs/requirements-spec.md) §9 와 [`docs/data-spec.md`](docs/data-spec.md) §16 에 있다.

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
| 2026-08 | HW 구성 · MQTT 파이프라인 · 특징 추출 · **ToF 호흡/자세 go-no-go 판단** |
| 2026-09 | FSM 추론 엔진 · 디스플레이 UI · 제어 모듈 · 통합 MVP |
| 2026-10-01 ~ 10-30 | 시험 평가 · 최적화 · **결선 서류 제출** |
| 2026-11-06 | 오프라인 발표 심사 |

---

## 협업 규칙

- `main` 직접 push 금지. `feat/<기능명>` 브랜치 → PR → 리뷰 후 머지
- 커밋 메시지: `feat(scope): 내용` / `fix(scope): 내용` / `docs(scope): 내용`
- 데이터셋 · 학습 모델 · 자격증명은 커밋하지 않는다 (`.gitignore` 참조)

## 관련 링크

- 팀 Notion: 임베디드 SW 경진대회 (LG)
- [AI 개발 공통 브리핑](docs/agent-briefing.md) · [CLI 시작 공통 문구](docs/agent-kickoff-prompt.md)
- [요구사항 명세서](docs/requirements-spec.md) · [데이터 명세서](docs/data-spec.md)
- [개발 진행 현황](docs/development-progress.md)
- [Pi 5 ATLAS Docker 개발 환경](display/atlas/README.md)
- 시연 영상: (결선 제출 시 추가)
