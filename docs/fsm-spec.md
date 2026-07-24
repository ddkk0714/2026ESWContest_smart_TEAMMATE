# 규칙 기반 FSM 사양 (1단계 추론 엔진) — VER5

담당: 박소연 · 기준 다이어그램: `DESKMATE_FSM-VER5`
임계값·가중치는 `hub/deskmate_hub/config/fsm.yaml` 에 두고 코드에 하드코딩하지 않는다.

> 이전 버전은 `start/focus/fatigue/end` 4상태 초안이었다. 본 문서는 이를 확장한
> **이중 신뢰도(C_fatigue·C_focus) 기반 18상태** 확정 설계다. 개발 계획·일정·WBS는
> 별도 개발계획서(FSM 작업 폴더, 저장소 미포함)에 있고, 본 문서는 **구현 사양**만 다룬다.

## 핵심 개념

두 지표를 **30초 주기로 동시 계산**하고, 각 임계값으로 **독립 판정**한다.

```
C_fatigue = Σ (wᵢ · δᵢ)     피로도 점수   (0~1)
C_focus   = Σ (vᵢ · φᵢ)     집중도 점수   (0~1)
```

- `δᵢ` = 신호 i의 피로 기여도, `φᵢ` = 집중 기여도. baseline 대비 **상대값**으로 정규화.
- 가용하지 않은 신호(비타이핑 구간의 키스트로크 등)는 항에서 제외하고 분모를 재정규화한다.

## 상태 정의 (5계층 · 18상태)

| 계층 | 상태 | 의미 | 핵심 동작 |
|---|---|---|---|
| 대기 | `IDLE` | 시스템 대기 | `tof_poll()`, `dual_monitor_standby()`, idle 화면 |
| 시작 | `START` | Baseline 측정(5~10분) | `baseline_timer.start()`, `capture(tof, resp, keystroke_rate)` |
| 시작 | `CONTEXT_DETECT` | 컨텍스트 판정 | `PC_ratio`(15분 윈도우) → 가중치 선택 |
| 몰입 | `FOCUS_PC` | 컴퓨터 작업 | 주력 키스트로크 / 보조 ToF·호흡·환경 |
| 몰입 | `FOCUS_MIXED` | 혼용 | `blend = r·PC + (1−r)·NPC` |
| 몰입 | `FOCUS_비PC` | 비컴퓨터 작업 | 주력 ToF·호흡 / 보조 CO₂·조도·시간 (키스트로크 N/A) |
| 판단 | `FOCUS_BREAK` | 집중 저하 감지 | `classify_focus_cause(φⱼ)`, `micro_intervention()`, `poll C_focus(Δt=3min)` |
| 판단 | `FATIGUE_SUSPECT` | 피로 의심 | `display_warning(yellow)`, `esc_timer.start()`, `poll_C_fatigue(Δt=30s)` |
| 판단 | `FATIGUE` | 피로 확정 | `log_fatigue_event()`, await CAUSE_ANALYSIS |
| 후속조치 | `CAUSE_ANALYSIS` | 원인 분석·라우팅 | `dominant = argmax(wᵢ·δᵢ)` |
| 후속조치 | `MONITOR` | 개입 효과 검증 | `measure C_fatigue trend(Δt=5~10min)` → reward → `update_AI_policy()` |
| 후속조치 | `ESCALATE` | 개입 실패·격상 | `log_intervention_fail()`, `reward(−)` |
| 후속조치 | `RECOVERY` | 회복 판정 | `poll_C_fatigue + C_focus(Δt=30s)` |
| 출력 | `ACTION_ENV` | 환경 조정 | `ThinQ_API(조명·환기)`, `await_api_response()` |
| 출력 | `ACTION_POSTURE` | 자세 교정 | `posture_alert(display)`, `await_correction(Δt=3min)` |
| 출력 | `ACTION_BREAK` | 휴식 권유 | `display_break_suggest()`, `await_touch(user_decision)` |
| 출력 | `REST` | 휴식 중 | `rest_timer.start()`, `maintain_env()`, `ambient_display()` |
| 출력 | `END` | 작업 종료 | `save_session_log()`, `display_summary()` |

## 컨텍스트별 가중치

`PC_ratio` (15분 윈도우 내 키보드/PC 전원 활성 비율)로 컨텍스트를 정한다.
`> 70% → PC`, `< 30% → 비PC`, `30~70% → MIXED`.

**C_focus 가중치 (vᵢ)**

| 컨텍스트 | 키스트로크 | ToF 자세 | 호흡수 | 환경 | 경과시간 |
|---|---|---|---|---|---|
| PC | 0.35 | 0.25 | 0.15 | — | 0.20 |
| 비PC | N/A | 0.50 | — | 0.25 | 0.25 |
| MIXED | `blend = r·PC + (1−r)·NPC` | | | | |

**C_fatigue 가중치 (wᵢ)**

| 컨텍스트 | 키스트로크 | ToF 자세 | 호흡수 | 환경 | 경과시간 |
|---|---|---|---|---|---|
| PC | 0.35 | 0.25 | 0.15 | 0.10 | 0.15 |
| 비PC | N/A | 0.40 | 0.25 | 0.20 | 0.15 |
| MIXED | `blend = r·PC + (1−r)·NPC` | | | | |

`r = PC_ratio`.

## 자세 해석 분기 (핵심 차별점)

동일한 "숙임"도 컨텍스트에 따라 집중/피로로 갈린다. 자세 단독 판정 금지 — 반드시 융합.

| 컨텍스트 | 관찰 | 해석 |
|---|---|---|
| PC | 숙임 + 키입력↓ | 피로↑ |
| PC | 숙임 + 키입력 정상 | 중립 |
| 비PC | 숙임 + motion↓ | 집중 |
| 비PC | 숙임 + 정적 | 피로↑ |
| 공통 | 젖힘 · 슬럼프 | 양쪽 피로↑ |

## 판정 임계값 (초안 — 8월 실측 후 보정)

| 상태/전이 | 조건 |
|---|---|
| Baseline 완료 (`START`→`CONTEXT_DETECT`) | `timer_10s ∧ baseline_ok` |
| 몰입 유지 | `C_fatigue < 0.40` |
| 집중 저하 (`FOCUS_BREAK`) | `C_focus ≥ 0.30 ∧ C_fatigue < 0.40` |
| 재유도 성공 → 몰입 복귀 | `C_focus < 0.25` |
| 피로 의심 (`FATIGUE_SUSPECT`) | `0.40 ≤ C_fatigue < 0.70` |
| 피로 확정 (`FATIGUE`) | `C_fatigue ≥ 0.70` (3분 지속) |
| 자연 회복 | `C_fatigue < 0.30` |
| 회복 승인 (`RECOVERY`→몰입) | `C_fatigue < 0.30` |
| 회복 실패 (`RECOVERY`→`ESCALATE`) | `C_fatigue ≥ 0.70` |
| 대기 강제 전환 (→`IDLE`) | 재실 없음 10분 |

**baseline 캘리브레이션** — 자세·키스트로크·호흡의 작업 초기 분포를 기준값으로 저장하고
**상대 변화**로 판정한다. 체격·의자 높이·개인 타이핑 속도에 자동 적응. (담당: 김태환)

## 상태 전이

```
IDLE ──TouchEvent──► START ──baseline_ok──► CONTEXT_DETECT
                                                 │ PC_ratio
                    ┌────────────────┬───────────┴───────────┐
                    ▼ >70%           ▼ 30~70%                 ▼ <30%
                FOCUS_PC ◄──────► FOCUS_MIXED ◄──────► FOCUS_비PC
                    └────────────────┼────────────────────────┘
                                     │
             C_focus≥0.30 ┌──────────┴──────────┐ C_fatigue≥0.40 (3분)
                          ▼                      ▼
                    FOCUS_BREAK ───재유도 실패──► FATIGUE_SUSPECT
                          │ 재유도 성공                │ C≥0.70(3분)
                          └──► (몰입 복귀)             ▼
                                                    FATIGUE
                                                       │ dominant
                                                       ▼
                                                 CAUSE_ANALYSIS
                            ┌──────────────────┬───────┴────────┐
                     환경성 ▼           자세성 ▼          인지성 ▼
                     ACTION_ENV      ACTION_POSTURE     ACTION_BREAK
                            └──────────────────┴────────┬───────┘
                                        실행 완료        │  (BREAK 수락→REST)
                                                        ▼
                                                    MONITOR
                                        C↓(개선) ┌─────┴─────┐ C↑(불변)
                                                 ▼           ▼
                                            RECOVERY      ESCALATE
                                    C<0.30 복귀 │            │ remaining_options → CAUSE_ANALYSIS
                                    C≥0.70 실패 └──►ESCALATE  └ options 소진 → force REST → RECOVERY
```

- `FOCUS_PC/MIXED/비PC` 는 판단·후속조치 전이를 공유한다(구현은 `ACTIVE` 슈퍼상태 후보 — 설계 확정 필요).
- 모든 활성 상태에서 **재실 없음 10분 → IDLE**, 디스플레이 터치(작업 종료) → `END`.

## 개입 라우팅 (CAUSE_ANALYSIS)

```
dominant = argmax(wᵢ · δᵢ)
  환경성 (CO₂·조도 항 지배)        → ACTION_ENV      ThinQ: 조명·환기
  자세성 (ToF 자세 항 지배)         → ACTION_POSTURE  자세 교정 알림
  인지성 (경과시간·키스트로크 지배)  → ACTION_BREAK    휴식 권유
```

## RL 정책 갱신 (MONITOR)

- `C_fatigue trend(Δt=5~10min)` 측정 → `C↓ = reward(+)`, `C↑/불변 = reward(−)`
- `update_AI_policy(reward)` 로 개입 정책을 점진 학습 (예: "오후 인지피로엔 BREAK 우선")
- **초기에는 고정 규칙**으로 라우팅. RL 갱신은 로그 축적 후(9월~) 활성화하며 개입 폭주 상한을 둔다.

## 신뢰도 게이트 (자동/제안/무동작)

| 신뢰도 | 행동 |
|---|---|
| `≥ conf_auto` (초안 0.75) | 자동 제어 실행 + 디스플레이에 실행 사실 표시 |
| `≥ conf_suggest` (초안 0.45) | 제안 카드만 표시, 사용자 수락 시 실행 |
| `< conf_suggest` | 무동작 (로그만 기록) |

2단계 TFLite 분류기가 활성화되면 FSM 점수와 분류기 확신도를 융합해 같은 게이트에 입력한다.
두 판정이 엇갈리면 보수적으로 `suggest` 로 낮춘다. 분류기 import 실패 시에도 FSM 은 단독 동작한다.

## 채터링 방지

- 상태 전이에 최소 유지 시간(`T_settle`)과 히스테리시스(임계값 진입/이탈 간격)를 둔다.
- 동일 제어 명령 재발행은 쿨다운(기본 300s).
- 사용자가 `reject` 한 제안은 해당 세션 내 재제안하지 않는다(ESM 라벨로 기록).

## 오프라인·성능

- 1단계 규칙 FSM 은 완전 오프라인 동작. 2단계 TFLite 는 RasPi 탑재 성공 시 오프라인.
- 판정 사이클 ≤ 500ms, 신뢰도 계산 주기 30s.
- 장시간 구동: deque 버퍼 상한 · 주기적 정리 · systemd 자동 재시작.
