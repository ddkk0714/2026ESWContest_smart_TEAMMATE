# DESKMATE — 프로젝트 개발 가이드

> 제24회 임베디드SW경진대회 · 스마트 가전 부문 · 팀 TEAMMATE
> ToF·키스트로크·환경 센서를 융합해 작업 상태(시작·몰입·피로·종료)를 추론하고
> 조명·환기·휴식을 자동 실행/제안하는 비침습 워크스페이스 가전.
> **카메라·마이크 미사용. 센싱·추론·제어 모두 로컬.**

이 문서는 팀 전체가 보는 **러프 개발 로드맵 + 모듈 현황 + 결정사항**이다.
상세 설계는 `docs/` 참조. (`README.md`=소개, `docs/architecture.md`, `docs/fsm-spec.md`,
`docs/mqtt-topics.md`, `docs/hardware.md`)

---

## 1. 시스템 한눈에

```
[ 센싱 ]                          [ 추론 ]                 [ 출력·제어 ]
ESP32 (ToF·CO₂·온습도·조도) ─┐
                            ├─MQTT─► Raspberry Pi 4 ─────► Raspberry Pi 5(구 Zero)
PC (키스트로크 타이밍) ──────┘        FSM(1단계)+TFLite(2단계)   디스플레이·피드백
                                          │
                                          ▼
                                    ThinQ API / 스마트 플러그 (조명·환기)
```

| 계층 | 장치 | 디렉터리 | 담당 |
|---|---|---|---|
| 센싱 | ESP32 말단 노드 | `firmware/` | 이민혁 |
| 센싱 | PC 키스트로크 | `collector/` | 최민경 |
| 추론 | Raspberry Pi 4 허브 | `hub/` | 박소연·김태환·조명희·이민혁 |
| 출력 | Raspberry Pi 5 단말 | `display/` | 최민경 |
| 학습 | PC → TFLite | `ml/` | 조명희 |

---

## 2. 개발 원칙 (모두 지킨다)

1. **1단계 규칙 FSM 을 완성도의 축으로.** 결선 완성도 60점. ML 정확도보다 규칙 FSM +
   실기기 제어의 확실한 동작이 우선. 2단계 분류기는 보완·검증.
2. **1단계 FSM 은 2단계 없이 단독 완전 동작.** 분류기는 import 실패해도 허브가 안 죽는 선택적 의존.
3. **임계값 하드코딩 금지.** 전부 `hub/deskmate_hub/config/*.yaml`. 8월 실측 후 값만 교체.
4. **호흡은 보조 신호.** 초기 비활성, 8월 go/no-go 통과 시에만 상향. 호흡 실패가 전체 판정을 흔들면 안 됨.
5. **프라이버시.** 키 값 미수집(타임스탬프만). 카메라·마이크 미사용. 자가기록 라벨 커밋 금지.
6. **판정 사이클 ≤ 500ms**, 신뢰도 계산 주기 30s.

---

## 3. 모듈별 현황

범례: ✅ 동작 · 🚧 진행 · ⬜ 미착수

| 모듈 | 담당 | 상태 | 메모 |
|---|---|---|---|
| `hub/inference/` FSM 1단계 | 박소연 | 🚧 | **엔진 스켈레톤 + 17 테스트 통과** (feat/fsm). 아래 §6 |
| `hub/features/` 특징 추출·baseline | 김태환 | ⬜ | SensorFrame(δ/φ) 입력 규격 확정 필요 → inference 와 계약 |
| `hub/ingest/` MQTT·시간동기 | 이민혁 | ⬜ | 토픽 설계(`docs/mqtt-topics.md`) 기반 구독기 |
| `hub/control/` ThinQ·플러그 | 조명희 | ⬜ | ACTION_* → 제어 명령. 자격증명 gitignore |
| `hub/inference/` TFLite 2단계 | 조명희 | ⬜ | 신뢰도 게이트에 융합(선택적) |
| `ml/` 학습→TFLite | 조명희 | ⬜ | ESM 라벨 축적 후(9월~) |
| `firmware/` ESP32 | 이민혁 | ⬜ | 1차 전처리 후 특징만 발행 |
| `display/` UI·AOD | 최민경 | ⬜ | 플랫폼 앱 언어 Flutter/React 검토 |
| `collector/` 키스트로크 | 최민경 | ⬜ | 타임스탬프만, 키 값 미수집 |

---

## 4. 전체 개발 로드맵 (러프, 2026)

| 시기 | 목표 | 핵심 산출물 |
|---|---|---|
| ~7월 | 예선 통과·저장소·환경 세팅 | 설계 문서, FSM 사양(VER5), **FSM 엔진 스켈레톤** |
| 8월 | HW·MQTT 파이프라인·특징 추출, **ToF 호흡/자세 go-no-go** | ESP32 발행, 허브 구독, baseline 캘리브레이션, 실측 로그 |
| 9월 | FSM 추론 완성·디스플레이·제어·통합 MVP | ACTION_* 제어 연동, 디스플레이 UI, 임계값 보정, RL 정책 |
| 10월 | 시험 평가·최적화·**결선 서류 제출** | 개발완료보고서, 작품소개서, 시연영상, 소스 공개 |
| 11월 | 오프라인 발표 심사·전시 | 실기 시연 |

**병목·의존성**
- `features` ↔ `inference` 입력 계약(δ/φ [0,1] 정규화)이 8월 착수의 선행조건.
- 호흡 가중치 상향은 8월 go/no-go 통과가 게이트.
- `control`·`display` 연동은 9월 통합 MVP 에서 합류.

---

## 5. 지금 정해야 할 결정사항

태그: **[사무국]** 대회 측 확인 · **[팀]** 팀 협의 · **[기본값]** 코드에 잠정 기본값 적용됨(바꾸려면 알려주기)

### 대회·플랫폼
1. **[사무국] 3종 HW 가산점** — 안내사항은 `ESP32·RPi4·RPi Zero 필수` 명시. 우리는 출력단말을 Pi5 로 대체.
   "3가지 *종류*"로 충족되는지 vs Pi Zero 필수인지 **조기 확인**. → 디스플레이 타깃 단말 결정에 직결.
2. **[팀] FSM ↔ LG 플랫폼 공존** — RPi4 에 LG 플랫폼 사전 탑재. Python FSM 을 독립 서비스로 띄울지.
   플랫폼 실체는 7~9월 장비 수령 시 확정. → **기본값: MQTT 경계로 느슨히 결합**해 종속성 최소화.
3. **[팀] 소스 공개 범위** — 수상 시 GitHub Public(핵심 포함) 필수. → 기본값: 전체 Public, 시크릿·라벨만 분리.

### FSM 내부 (기본값 적용됨 — 이견 시 알려주기)
4. **[기본값] ACTIVE 슈퍼상태** — FOCUS_PC/MIXED/비PC 공통 전이. → 지금은 상태별 개별 처리, 전이 안정화 후 리팩터.
5. **[기본값] 경과시간 정규화** — → 세션 내 상대(개인 평균 몰입 지속) 기준 권장.
6. **[기본값] MIXED blend r 갱신** — → 초기 15분 윈도우 고정, 튜닝 단계에서 EMA 검토.
7. **[8월] 호흡 가중치 상향 기준** — go/no-go 정량 지표 정의 필요. → 기본값 `respiration_enabled: false`.
8. **[팀] ESM 라벨 스키마** — ACTION_BREAK 수락/거절 기록 필드. 조명희 라벨 체계와 정합.
9. **[기본값] RL 정책 활성 시점·상한** — → 9월+ 활성, 세션당 개입 횟수 상한으로 폭주 방지.

### 데이터 계약 (8월 착수 전 합의)
10. **[팀] SensorFrame 규격** — δ(피로)/φ(집중) [0,1] 정규화, 신호 available 플래그. (김태환↔박소연)
11. **[팀] MQTT 토픽 최종** — `docs/mqtt-topics.md` 확정. (이민혁)
12. **[8월] 임계값 초기값** — `config/fsm.yaml` 값 실측 보정.

---

## 6. FSM 추론 엔진(inference/) 개발 현황 — 박소연

**상태: 🚧 1단계 스켈레톤 완료 · 브랜치 `feat/fsm` (미머지)**

기준 설계: `docs/fsm-spec.md` (VER5, 5계층 18상태, C_fatigue/C_focus 이중 모니터링).

완료
- `config/fsm.yaml` — 임계값·컨텍스트 가중치·타이머·라우팅 전부 외부화.
- `inference/` — states(18상태) · types(SensorFrame/Signal 계약) · scoring(이중 신뢰도+재정규화) ·
  context(PC/MIXED/비PC + blend) · cause(dominant 라우팅) · engine(tick 루프·타이머·게이트).
- `tests/test_fsm_transitions.py` — **17종 전이 테스트 전부 통과** (합성 입력, 실기기 불필요).
- 실행: `cd hub && pip install -r requirements.txt && pytest tests/`

진행/예정 (코드 TODO)
- 🚧 MONITOR 의 RL 정책 학습 — 현재 진입 대비 피로 부호로 판정하는 고정 규칙. 9월 로그 축적 후
  5~10분 트렌드 윈도우 + 정책 학습으로 교체.
- ⬜ 호흡 가중치 — 8월 go/no-go 통과 시 `respiration_enabled: true`.
- ⬜ `features`(김태환) 입력 계약 확정 후 실신호 연결.
- ⬜ `control`(조명희) ACTION_* → 실제 제어 명령 발행.
- ⬜ 2단계 TFLite 분류기 확신도 → 신뢰도 게이트 융합.
- ⬜ 로그 리플레이 하네스(`python -m deskmate_hub --replay`)로 8월 데이터 튜닝.

---

## 7. 협업·개발 규칙

- **`main` 직접 push 금지.** `feat/<기능명>` 브랜치 → PR → 리뷰 후 머지.
- 커밋: `feat(scope): …` / `fix(scope): …` / `docs(scope): …`
- 데이터셋·학습 모델·자격증명 커밋 금지(`.gitignore`). `secrets.yaml` 은 환경변수/gitignore.
- FSM 상태 전이는 합성 입력으로 단위 테스트(실기기 없이 검증 가능한 유일한 부분).
- 임계값 바꿀 땐 코드가 아니라 `config/*.yaml` 만 수정.
