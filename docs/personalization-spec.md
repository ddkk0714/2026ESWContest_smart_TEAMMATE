# 사용자 맞춤 개입 선택 정책 (3층 구조) — 설계 사양

> 기준일 2026-10-21. 구현: `hub/deskmate_hub/personalize/`, 설정: `hub/deskmate_hub/config/personalize.yaml`,
> 로그 스키마: [`personalization-log.md`](personalization-log.md).
> 전제: [`fsm-spec.md`](fsm-spec.md)(VER5 18상태)의 상태·게이트·라우팅은 **그대로** 둔다. 이 문서는 FSM 이
> "어떤 원인 그룹에 개입할지" 정한 **뒤**, "그 그룹 안에서 어떤 개입을 어떤 방식으로 낼지"를 사용자별로 고르는 층을 정의한다.
> `personalization/` 패키지(2단계 관찰 모델·개인 head, [`ondevice-personalization.md`](ondevice-personalization.md))와는 별개다.
> 그쪽은 *상태 추정*의 개인화고, 이 문서는 *개입 선택*의 개인화다.

## 0. 한 줄 요약

```
1층  규칙 FSM            : 상태 · 원인(cause) · 게이트(auto/suggest/none) 를 정한다            ← 변경 없음
2층  사용자 프로필        : 로그에서 일 단위 생활습관 통계를 만든다 (하루 한 줄 JSONL)             ← profile.py
3층  개입 선택 정책        : 원인 그룹 안의 후보 가운데 상황 벡터로 하나를 고른다 (컨텍스트 밴딧)   ← policy.py
```

3층은 1층의 결정을 **좁히기만** 한다. 게이트를 올리지 않고(제안→자동 금지), 원인 그룹을 바꾸지 않으며,
쿨다운·거절 재제안 금지를 그대로 지킨다. 밴딧이 고를 수 있는 후보가 없으면 FSM 기본 동작으로 돌아간다.

## 1. 3층 구조

| 층 | 입력 | 출력 | 갱신 주기 | 구현 |
|---|---|---|---|---|
| 1층 규칙 FSM | `SensorFrame`(10 s) | `TickResult(state, cause, gate, c_fatigue, …)` | 10 s | `inference/engine.py` (수정 없음) |
| 2층 사용자 프로필 | 개인화 로그 JSONL | `DailyStats`(일 단위) → `ProfileSummary`(최근 N일 집계) | 세션 종료·하루 1회 | `personalize/profile.py` |
| 3층 개입 선택 정책 | 상황 벡터 + cause + gate + 프로필 | 개입 후보 1개 + 전달 게이트 + 설명 한 줄 | ACTION_* 진입 tick | `personalize/policy.py` |
| 기록(2층의 입력) | 결정 tick 의 상황·FSM 결과, 피드백, 10 분 뒤 C_fatigue | `personalize_episode`·`personalize_session` JSONL | tick | `personalize/record.py` ← `live.py` (10-21 연결, `record: true`. `enabled: true` 면 같은 자리에서 3층 `decide()` 호출. 전부 순수 Python — Pi 4 에서도 돈다) |

연결점은 `personalize/adapter.py` 하나다. `TickResult.actions` 에 `route:<cause>` 가 들어 있는 tick(= CAUSE_ANALYSIS 가
ACTION_* 로 보낸 직후)이 **결정 시점**이다. 그 tick 의 `SensorFrame`/`TickResult` 와, 프레임이 싣지 않는 부가 정보
(자세 분포·환경 원값·시각·날씨)를 `SituationExtras` 로 받아 상황 벡터를 만든다. engine 은 건드리지 않는다.

```
SensorFrame ─► FSMEngine.tick ─► TickResult ──(route:<cause>)──► adapter.situation(...) ─► policy.decide(...)
                                                                   ▲ SituationExtras(자세 분포·CO₂·조도·시각·날씨…)
                                             ◄── 피드백(accept/reject/timeout/undo) · 10분 뒤 C_fatigue ── policy.update(...)
```

## 2. 상황 벡터 (3층 입력)

단위를 적은 원값은 로그에 그대로 남기고, 밴딧에는 `[0,1]` 또는 `[-1,1]` 로 정규화한 값을 넣는다.
정규화 상수는 모두 `personalize.yaml` `features:` 에 둔다.

| 그룹 | 항목 | 원값(단위) | 정규화 | 출처 |
|---|---|---|---|---|
| 자세 | 최근 10분 ToF 자세 9라벨 분포 | 비율 ×9 (`upright, lean_forward, recline, face_down, chin_rest, drowsy, away, misplaced, forward_head`) | 그대로 | Pi 4 자세 모델 다수결 이력 (`posture/tof_mlp`) |
| 자세 | 현재 자세 지속 시간 | 분 | `/ posture_hold_full_min`(30) clip | 같은 이력 |
| 자세 | 슬럼프 라벨 지속 | 분 | 〃 | 슬럼프 = `slump_labels`(recline·face_down·chin_rest·drowsy) |
| 재실·졸음 | 재실 | 0/1 | 그대로 | `SensorFrame.present` |
| 재실·졸음 | 졸음 플래그·최근 30분 졸음 횟수 | 0/1, 회 | 회수 `/ drowsy_count_full`(4) | mmWave DrowsyDetector(0x03 drowsy) |
| 환경 | CO₂ | ppm | `(ppm-400)/1600` clip | SCD41 |
| 환경 | 조도 | lux | `1-ramp(lux_dim_low, lux_dim_high)` = 어두움 정도 | BH1750 |
| 환경 | 온도·습도 편차 | °C, %RH | 쾌적 범위 밖 비율 (`ingest.yaml` environment 와 같은 식) | DHT22 |
| 활동 | 키스트로크 활동 | PC_ratio [0,1], 타이핑 중 0/1 | 그대로 | `SensorFrame.pc_ratio`, keystroke available |
| 점수 | C_fatigue · C_focus | [0,1] | 그대로 | `TickResult.scores` |
| 세션 | 세션 경과 | 분 | `/ session_full_min`(240) clip | START 이후 |
| 세션 | 마지막 개입 뒤 경과 | 분 | `/ since_last_full_min`(60) clip, 없으면 1 | policy 내부 |
| 시간 | 시각 | 시(0–24, 지역시) | `sin·cos(2π·h/24)` | Pi 4 시계 |
| 시간 | 요일 | 0–6 | 주말 0/1 | 〃 |
| 시간 | 평소 취침 뒤 여부 | 0/1 | 그대로 | 프로필 `usual_bedtime` 비교 |
| 날씨·계절 | 계절 | 월 | `sin·cos(2π·m/12)` | 날짜만으로 폴백 가능 |
| 날씨·계절 | 바람·비 | 0/1, 0/1 | `wind_ms ≥ windy_ms`(4.0), `condition ∈ rain_conditions` | MQTT `deskmate/sensor/weather`(Pi 5 앱 발행, 10-21 결정). stale(2 h) 이면 0 |
| 날씨·계절 | 실내외 온도차 | °C | `/10` clip [-1,1] | 〃 (`weather.temp_c` − 실내 `temp_c`) |

벡터 길이는 상수 `FEATURE_NAMES` 가 정하고(`personalize/features.py`), 로그 `features` 는 원값, 밴딧 입력은 정규화 값이다.
항목이 없으면(날씨 미수신 등) 0 으로 두되 `features.missing` 에 키를 적는다.

## 3. 생활습관 프로필 (2층)

로그를 하루 단위로 접어 `DailyStats` 한 줄을 만들고(`profiles/<profile_id>/daily.jsonl`), 최근 `profile.window_days`(30)
을 중앙값·평균으로 모은 것이 `ProfileSummary` 다. 전부 집계값이라 원시 센서·키 내용은 들어가지 않는다.

| 항목 | 정의 | 단위 | 쓰임 |
|---|---|---|---|
| `first_slump_min` | 세션 START 뒤 처음 슬럼프 라벨이 `slump_min_hold_min`(2) 이상 지속될 때까지 | 분 | 자세 안내 타이밍 prior, 취침 규칙 |
| `slumps_per_hour` | 슬럼프 구간 수 / 세션 시간 | 회/h | 자세성 개입 빈도 상한 |
| `recovery_after_alert_min` | 자세 안내 뒤 `upright` 로 돌아오기까지 | 분 | 자세 안내 효과, 설명 문구 |
| `fatigue_by_hour[24]` | 시간대별 C_fatigue 평균(표본 수 함께) | [0,1] | "오후 3시에 피로가 높다" 설명, 시각 특징 보정 |
| `session_len_min` | 세션 길이 중앙값 | 분 | 긴 휴식 제안 prior |
| `break_interval_min` | 휴식(REST) 사이 간격 중앙값 | 분 | 짧은 휴식 타이밍 |
| `late_night_ratio` | 23시 이후에도 활성이던 세션 비율 | [0,1] | 취침 권유 규칙 |
| `usual_bedtime` | 21시 이후 끝난 세션의 종료 시각 중앙값(없으면 `default_bedtime` 23:30) | HH:MM | 취침 권유 규칙 |
| `co2_sensitivity_ppm` | 환경성 원인이 CO₂(environment 신호 reason `co2_high`/`co2_rise`) 였던 피로 확정 당시 CO₂ 의 하위 25 % 값. 그런 피로가 없으면 null | ppm | 환경성 개입 설명, 공기청정기 prior |
| `arm_stats[arm]` | 개입별 수락률·무응답률·거절률·되돌리기율·효과 평균·횟수 | 비율, [-1,1], 회 | 밴딧 설명·콜드 스타트 판정 |

## 4. 개입 후보 표 (3층의 팔, arms)

| arm | 원인 그룹 | 기기·채널 | 동작 | 가역 | 자동 허용 | 비고 |
|---|---|---|---|---|---|---|
| `light_cool_bright` | environment, cognitive | iLink 무드등 | 색온도 6000 K(n=1)·밝기 ↑ (각성) | ○ | ○ | `control.yaml` desk_lamp |
| `light_warm_dim` | cognitive | iLink 무드등 | 색온도 3000 K(n=5)·밝기 ↓ (휴식 유도) | ○ | ○ | 휴식 카드와 함께 |
| `fan_on` | environment | 보조 환기팬·선풍기 `vent_fan` | 켜기 | ○ | ○ | CO₂·더위. 바람 불면 창문 환기보다 뒤 (§7) |
| `air_fan_up` | environment | LG ThinQ 공기청정기 `air_purifier` | `set_mode high`(되돌리기 `auto`) | ○ | ○ | CO₂ 는 못 내린다 → 비·미세먼지로 창문을 못 열 때만 후보(`purifier.when_weather`). 평소 PM 운전은 공기청정기 자체 auto |
| `ventilate_suggest` | environment | 화면(+음성) | 창문 환기 권유 | — | ✕ | 바람 불면 선풍기보다 먼저, 겨울·비는 짧게/보류 (§7) |
| `posture_card` | posture | 화면 | 자세 안내 카드 | — | ○(알림) | 기존 `posture_alert` |
| `posture_voice` | posture | 스피커 TTS | 자세 안내 음성 | — | ○(알림) | ATLAS VoiceManager1, 한국어 지원 확인 필요 |
| `sound_cue` | posture, cognitive | 스피커 | 짧은 알림음 | — | ○ | 몰입 중 무음 시간은 `quiet_hours` |
| `break_short` | cognitive | 화면 카드 | 5 분 짧은 휴식 → 수락 시 REST | — | ✕ | 기존 `break_suggest` |
| `break_long` | cognitive | 화면 카드 | 15 분 이상 긴 휴식 | — | ✕ | 세션 2 h 넘으면 prior ↑ |
| `sleep_suggest` | (모든 그룹) | 화면(+음성) | 오늘은 여기까지, 취침 권유 | — | ✕ | **밴딧이 아니라 규칙**(§6). cause 와 무관하게 조건이 맞으면 낸다 |

- 원인 그룹은 FSM `cause` 와 같은 키(`environment`/`posture`/`cognitive`)다. `cause` 에 맞는 arm 만 후보가 된다.
  → `ACTION_ENV`·`ACTION_POSTURE`·`ACTION_BREAK` 라우팅과 모순되지 않는다.
- "자동 허용 ✕" 인 arm 은 FSM 게이트가 `auto` 여도 `suggest` 로 낸다(사용자 행동이 필요한 개입).
- 기기 명령 값(색온도 단계·밝기·풍량)은 `personalize.yaml` `arms:` 에 둔다. 코드에는 arm id 만 있다.

## 5. 컨텍스트 밴딧 (3층)

**알고리즘** — arm 별 베이지안 선형 회귀 + Thompson 샘플링. 순수 Python(`personalize/linalg.py`: 가우스-조르당 역행렬·숄레스키·SplitMix64 난수, 10-23 이식) — numpy 판과 같은 값(`tests/test_personalize_linalg.py`).
한 arm 에 보상 축 두 개(§5.1)를 따로 회귀한다. 선택 점수는 두 축 가중합이다.

```
arm a, 축 k ∈ {accept, effect}:
  A_ak = λI + Σ x xᵀ          b_ak = λ·θ⁰_ak + Σ r_k x       (θ⁰ = 전역 prior 평균, λ = prior 정밀도)
  μ_ak = A_ak⁻¹ b_ak           θ̃_ak ~ N(μ_ak, σ²·A_ak⁻¹)      (Thompson 샘플)
score(a) = w_accept·(θ̃_a,accept · x) + w_effect·(θ̃_a,effect · x)
선택 = argmax over 후보 arm 들 (후보 = cause 일치 ∧ 쿨다운 밖 ∧ 세션 내 미거절 ∧ quiet_hours 밖)
```

- `λ`, `σ²`, `w_accept`, `w_effect` 는 `personalize.yaml` `bandit:`.
- 전역 prior `θ⁰` 는 규칙에서 손으로 적은 값(`personalize.yaml` `prior:`)이다: "CO₂ 높으면 `air_fan_up`", "어두우면 `light_cool_bright`",
  "밤이면 `light_warm_dim`·`break_long`", "세션 2 h 넘으면 `break_long`" 같은 상식을 bias·특징 가중치로 적는다.
  **개인 로그가 없어도 이 prior 만으로 동작한다**(콜드 스타트, §8).
- 개인 posterior 는 같은 식에 개인 피드백을 누적한 것이다. 전역 prior 와 개인 posterior 는 같은 수식의 λ·θ⁰ 로 자연히 섞이고,
  개인 피드백 수 `n` 이 `cold_start.personal_min`(15) 을 넘으면 Thompson 분산 배율을 `cold_start.explore_scale_personal`(0.5) 로
  줄여 "개인값 우선" 이 된다. 설명 문자열에 `(기본 정책 · 피드백 n건)` / `(개인 정책 · 피드백 n건)` 으로 표시한다.

### 5.1 보상 두 축

| 축 | 값 | 측정 시점 |
|---|---|---|
| 수용도 `r_accept` | 수락 **+1** · 무응답(timeout) **0** · 거절 **−1** · 자동 실행 뒤 되돌리기 **−2** · 자동 실행 뒤 그대로 둠 **0** | 피드백 도착 또는 카드 만료 |
| 효과 `r_effect` | 개입 뒤 `effect_window_min`(10) 동안 C_fatigue 추세: `clip((C_before − C_after) / effect_full_drop(0.30), −1, 1)` | 결정 10 분 뒤 (세션이 먼저 끝나면 `status: session_ended`, 효과 축 갱신 생략) |

- MONITOR 의 `reward:+/-` 는 FSM 내부 동작이라 그대로 두고, 3층은 자기 로그의 10 분 추세를 쓴다.
- `timeout` 은 거절이 아니고(`data-spec.md` §9.3) 0 으로만 둔다. `correct`(정정) 은 상태 라벨이지 개입 피드백이 아니라 보상에 넣지 않는다.

### 5.2 게이트·쿨다운·거절 재제안 금지와의 결합

| 규칙 | 1층(FSM) | 3층(policy) 에서 |
|---|---|---|
| 게이트 | `auto ≥ 0.75`, `suggest ≥ 0.45`, 재시도 원인은 `suggest` 까지 | FSM 게이트는 **상한**. `none` 이면 결정하지 않는다(로그만). `suggest` 를 `auto` 로 올리지 않는다. `auto` 는 arm 이 자동 허용이고 `c_fatigue ≥ auto_threshold(arm)` 일 때만 유지, 아니면 `suggest` 로 내린다 |
| 개인 자동 문턱 | — | `auto_threshold(arm) = conf_auto + undo_penalty(arm)`. 되돌리기 1회마다 `undo_penalty_step`(0.05) 만큼 오르고, 자동 실행이 되돌려지지 않고 끝날 때마다 `undo_penalty_decay`(0.01) 씩 내린다. 상한 `undo_penalty_max`(0.25) → 사실상 "이 사용자에겐 늘 묻는다" |
| 쿨다운 | 같은 제어 명령 300 s | arm 별 마지막 실행 시각으로 `cooldown_sec`(300) 안이면 후보에서 뺀다 (dispatcher 의 target 쿨다운과 중복이어도 무해) |
| 거절 재제안 금지 | 세션 내 재제안 금지 | 세션 id 별 거절 arm 집합. `END`/`IDLE` 로 세션이 바뀌면 비운다 |
| 세션당 개입 상한 | (`agent-briefing` 기본값 7) | `max_per_session`(6) 넘으면 결정하지 않는다 |
| 무음 시간 | — | `quiet_hours` 안에서는 `sound_cue`·`posture_voice` 를 후보에서 뺀다 |

**되돌리기 규칙(10-21 추가, `gate.undo_blocks_auto_in_session`)** — 자동 실행을 사용자가 되돌리면 (1) 그 arm 의 개인 자동 문턱이
`undo_penalty_step` 만큼 올라가고(위), (2) **같은 세션에서는 그 arm 을 다시 자동으로 내지 않고 묻는다**, (3) 그 arm 의 쿨다운은 푼다
(되돌린 실행은 없던 일이므로 같은 상황이 오면 묻는 것으로 바뀐다). 수용도 보상 −2 가 바로 반영되므로 다음 결정은 보통 다른 arm 을 고르고,
설명 줄에 "지난번 <되돌린 개입> 되돌리셔서 이번엔 여쭙니다" 가 붙는다. 시연(`tools/demo_dryrun.py --scenario undo_learn --personalize --undo-at <초>`,
`docs/demo-scenario.md` §6)은 이 규칙으로 "되돌리기 한 번에 행동이 바뀐다" 를 2분대에 보여 준다.

**live 연결(10-21)** — `live.py` 는 결정 tick 마다 `personalize/record.py` 를 통해 `decide()` 를 부르고, 정책이 `suggest` 로 낮추면
`TickResult.gate` 를 바꿔 제어 디스패처·질문 카드·`state/phase` 가 모두 그 게이트를 따른다. 정책이 고른 arm 에 `device` 가 있으면 그 명령
(`adapter.arm_commands`, 조명은 `set_brightness` 만)을 디스패처에 넘겨 **ACTION_ENV 가 아닌 상태(ACTION_BREAK·ACTION_POSTURE)에서도** 실행·되돌리기가
된다. 후보가 없거나 세션 상한이면 FSM 기본 동작이다. 정책 상태는 `logs/profiles/<profile_id>/policy.json` 에 피드백·결과마다 저장한다.

### 5.3 첫 설정·묻지 않고 얻는 신호 (2026-10-25)

사용자 설계: "응답은 많이 받지 않고, UI 처음 설정한 내용을 바탕으로 표준을 잡는다. 무드등을 밝혔는데 사용자가 그 반응을 보고
다시 어둡게 하면 그 사용자는 밝음을 싫어하는 것 — 이런 식으로 맞춰 간다. 잠에서 잘 깨면 그걸 좋은 신호로 본다. 환기는 FSM."
숫자는 전부 `personalize.yaml` 의 `onboarding:`·`implicit:`·`wake:` 절, 코드는 `personalize/onboarding.py`·`signals.py`·`record.py`.

| 신호 | 입력 | 정책에 들어가는 곳 |
|---|---|---|
| 첫 설정 프로필 | `deskmate/profile/user`(retain, [mqtt-topics](mqtt-topics.md)) | `onboarding.mapping` 표대로 전역 prior θ⁰ 에 가산(`b` 의 λθ⁰ 만 교체 — 쌓인 학습 Σrx 는 그대로), `disable` arm 은 후보 제외(`excluded: user_profile`), bedtime→취침 규칙·after_bedtime, bedtime~wake_time→quiet_hours |
| 밝힘 뒤 직접 어둡게(앱) | `feedback/user kind=manual_adjust` | 그 에피소드 맥락 x 로 밝힘 arm 수락 축 `implicit.app_reward`(−1, 거절과 같은 급). 명시 되돌리기(undo −2)와 따로 센다 |
| 밝힘 뒤 직접 어둡게(조도) | env `lux`(BH1750) | 같은 자리, `implicit.lux_reward`(−0.5). 우리 밝힘이 조도를 `lux_rise_min_ratio`·`lux_rise_min_lx` 이상 올렸을 때만, 하락이 `lux_drop_hold_sec` 이어지고 그 사이 허브 무드등 명령이 없을 때만. 하락폭이 상승폭의 `lux_drop_max_factor` 배를 넘으면 방 조명으로 보고 무시 |
| 깨우기 성공·실패 | 자세 라벨·키 입력·ToF 움직임·C_fatigue | 자세 원인 + 결정 tick 이 drowsy/face_down(또는 졸음 플래그·C_fatigue ≥ `wake.fatigue_min`)인 에피소드의 효과 축. `window_sec` 안에 깨어 있는 자세로 `hold_sec` 이어지고 (활동 재개 또는 C_fatigue −`fatigue_drop`) 이면 `reward_success`, 아니면 `reward_fail`. `effect_mode: replace` 면 10 분 C_fatigue 추세 대신(판정 즉시 갱신) |

"밝힘" = 허브가 실행한 desk_lamp 명령 중 `adjust_brightness` percent > 0(결과 actual ≤ previous 면 아님) 또는 `set_brightness` 가
결과의 previous 보다 큼(previous 를 모르면 `implicit.brighten_unknown_min` 이상). 갱신할 arm 은 그 에피소드 arm 이
`implicit.brighten_arms` 중 하나면 그것, 아니면(FSM 기본 `actions.posture` 밝힘) 목록 전부. 화면에는 `sensor_summary.personalize.signal.text_ko`
한 줄("밝힘 뒤 직접 어둡게 함 → 밝은 조명 덜 고름", "깨우기 성공 (엎드림→바른 자세 25 s)", "첫 설정 반영: …")이 실린다.

## 6. 취침 권유 — 밴딧 밖의 규칙

밴딧은 "가능한 후보 중 최선"을 고를 뿐이라 "오늘은 그만하라"는 판단을 학습하게 두지 않는다. 다음 네 조건이 **모두** 성립하면
`sleep_suggest` 를 `suggest` 게이트로 낸다(절대 `auto` 아님).

```
local_time ≥ profile.usual_bedtime
∧ c_fatigue ≥ sleep.fatigue_min(0.70)             (= fatigue_confirm 과 같은 값을 기본으로)
∧ 최근 sleep.drowsy_window_min(30) 안 졸음 ≥ sleep.drowsy_min_count(2)
∧ 이 세션의 직전 개입 2회가 무효 (r_effect ≤ 0 또는 ESCALATE 로 이어짐)
```

한 세션에 한 번만 낸다. 거절하면 그 세션에서 다시 내지 않는다(거절 재제안 금지와 동일).

## 7. 날씨·계절 보정 (환경성 arm 의 전처리)

**입력 경로(2026-10-21 결정)**: Pi 5 앱이 바깥 날씨를 받아 `deskmate/sensor/weather`(retain, QoS 0, 30 분 + 시작 시 1회)로 발행한다
([`mqtt-topics.md`](mqtt-topics.md)). 허브는 `SensorCache.weather` 에 마지막 표본을 두고, `personalize/weather.py` 가
`fetched_at` 기준 `stale_sec`(2 h) 안의 표본만 `Situation.weather` 로 바꾼다(`adapter.extras_from_cache`). FSM 입력이 아니다.
토픽이 없거나 stale 이면 `weather = None` → 아래 규칙 중 **계절(월) 항만** 적용되고 바람·비 특징은 0 이다.

밴딧 입력에 바람·비·계절이 들어가지만, 아래는 학습 전에 적용하는 고정 규칙이다(`personalize.yaml` `weather:`).

- 바람 부는 날(`windy`) → `weather.windy_bias`: `ventilate_suggest` `+0.3`, `fan_on` `−0.3` (선풍기보다 환기 먼저).
- 비·미세먼지 나쁨(`rain`/`pm_bad`) → `ventilate_suggest` 후보 제외, 대신 `air_fan_up`(공기청정기)이 후보가 된다.
- **창문 환기 길이는 날씨가 정한다**(2026-10-23, `personalize.yaml ventilation:`, `policy.ventilation_plan`): 계절 기본값
  (봄 15·여름 10·가을 15·겨울 5 분; 계절은 날씨 토픽 `season`, 없으면 달) × 바람(`windy` 0.7, 풍속 ≤ 1 m/s 잔잔 1.3)
  × 실내외 온도 차(≥ 12 °C 면 0.6) × 바깥 습도(≥ 85 % 면 0.7), 3~20 분으로 자른다. `Decision.ventilate_minutes` 와
  `interaction/request data.ventilate_minutes` 로 나가고, 설명에 이유가 붙는다(예 "환기 3분(겨울 · 바람 6 m/s · 바깥 -2 °C(실내와 26 °C 차))").
- 실외가 실내보다 `outdoor_hotter_margin_c`(2 °C) 이상 더우면(여름 한낮) → `ventilate_suggest` 후보 제외.
- 날씨를 못 받으면(토픽 없음·stale) 계절(날짜)만으로 위 규칙 중 계절 항만 적용한다.

## 8. 콜드 스타트

| 단계 | 조건 | 동작 |
|---|---|---|
| 0 전역 기본 | 개인 피드백 0건 | prior `θ⁰` 만. Thompson 분산 `explore_scale_global`(1.0) — 탐색 넉넉히 |
| 1 혼합 | 1 ~ `personal_min`−1 건 | 같은 posterior, 분산 그대로. 설명은 "기본 정책" |
| 2 개인 우선 | ≥ `personal_min`(15) 건 | 분산 배율 `explore_scale_personal`(0.5). 설명은 "개인 정책". 프로필 요약 문구를 설명에 덧붙인다 |

개인 posterior 파일(`profiles/<profile_id>/policy.json`)이 손상되면 전역 prior 로 돌아가고 로그에 적는다.
동의 철회·삭제(`personalization-privacy-flow.md`)는 이 파일과 `daily.jsonl`·개인화 로그를 함께 삭제 대상으로 등록해야 한다(§9).

## 9. 시연안

**계정 A(학습됨) vs 계정 B(새 계정)** — 같은 상황 벡터를 넣고 화면에 나란히 보인다.

| 단계 | 화면 | A (합성 30일 학습) | B (전역 기본) |
|---|---|---|---|
| 1 | 오후 3시, CO₂ 1,300 ppm, 피로 확정, cause=environment | `air_fan_up` **자동** — "CO₂ 1,300 ppm·평소 민감 문턱 1,050 ppm 넘음 · 공기청정기 풍량 수락 9/10 · 개인 정책" | `air_fan_up` **제안** — "CO₂ 높음 · 기본 정책 · 피드백 0건" |
| 2 | 밤 11시 40분, 세션 3 h, cause=cognitive, 졸음 2회, 앞 개입 2회 무효 | `sleep_suggest` 제안 — "평소 취침 23:20 지남 · 졸음 2회 · 앞 개입 효과 없음" | `break_short` 제안 — "세션 3 h · 기본 정책" (B 는 취침 시각 미학습 → 기본 23:30 전이라 규칙 미성립) |
| 3 | 자세성, 거북목 12분 | `posture_card` — "음성 안내는 되돌리기 3회라 묻고 진행 · 카드 수락 7/8" | `posture_card` 제안 — "기본 정책" |

화면의 "왜 이 개입인지" 한 줄은 `Decision.explanation` 을 그대로 쓴다. 형식:
`<핵심 근거 1~2개> · <수락/효과 통계 또는 '기본 정책'> · <피드백 n건>`.
합성 로그로 학습된 A 는 `simulated: true` 가 찍힌 데이터라는 것을 화면 모서리에 표시한다(시뮬레이션 표기).

## 10. fsm-spec 과의 정합 점검

| fsm-spec 항목 | 이 설계 |
|---|---|
| CAUSE_ANALYSIS `dominant = argmax` → ACTION_ENV/POSTURE/BREAK | cause 그룹 안에서만 arm 을 고른다. 그룹을 바꾸지 않는다 |
| 게이트 0.45/0.75, 재시도 원인 `suggest` 까지 | FSM 게이트가 상한. 개인 문턱은 올리는 방향만 |
| MONITOR `update_AI_policy(reward)` | 이 자리표시를 3층 `policy.update()` 가 채운다. 보상 정의는 §5.1 |
| 쿨다운 300 s, 거절 재제안 금지 | §5.2 그대로 |
| ACTION_BREAK 수락 → REST | `break_short`·`break_long` 수락은 기존 `break_accepted=True` 경로 |
| 카메라·마이크 미사용 | 입력에 자세 9라벨(ToF)·mmWave 졸음만. 음성은 출력(스피커)뿐 |
| 임계값은 YAML 에만 | `personalize.yaml` |

## 11. 결정이 필요한 항목

1. ~~날씨 입력 경로~~ — **해소(10-21)**: Pi 5 앱이 받아 `deskmate/sensor/weather` 로 넘긴다(§7). 앱 쪽 구현은 남음.
2. ~~Pi 4 제한 Python 의 numpy~~ — **해소(10-23)**: 정책을 순수 Python 으로 옮겼다(`linalg.py`, 34×34 역행렬·숄레스키는 피드백 때만 다시 구함,
   난수는 `_random` 없는 보드용 `PurePRNG`). `profile.py` 의 `statistics`(→ `random` → `_random`)도 `mathutil.median` 으로 바꿨다.
   보드 패키지(`build_payload.py`)에 `personalize.json` 이 함께 묶인다. `tests/test_board_runtime.py` 가 numpy·`_random` 없이 결정·학습·저장을 확인한다.
3. 개인화 로그·프로필·posterior 파일의 보존 기간과 삭제 등록(동의 철회 범위)은 기존 미결정 항목(DATA-DEC-006)과 함께 정한다.
