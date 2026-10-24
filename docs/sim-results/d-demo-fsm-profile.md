# (d) 시연 FSM 프로파일 demo-A/B · a1_wake_first 리허설 · 첫 깨우기 실패 수정 (10-24)

> **[시뮬]** = PC life_sim `a1_wake_first`(졸음 608 s 시작), 합성 격자 + 정답 자세. **시연 전용 — 시뮬 근거이며 실측 성공 기록에 쓰지 않는다.**
> 출처: PC `tof-sim_ubai_results_1024/laptop_answer_1024.md` §2~4, 커밋 `2b9252a`(fix(fsm), 팀 PR #73 `feat/pi4-hub-1023` 에 포함).

## 결론

- 기본 프로파일은 졸음 뒤 첫 개입까지 440~620 s 라 5분 시연에 맞지 않는다. **demo-B 는 집중 진입 20 s, 졸음 뒤 첫 개입 102 s, 졸기 전 헛개입 0** [시뮬].
- 첫 깨우기가 실패하면 RECOVERY 에 17~22 분 머무는 문제를 `2b9252a` 로 고쳤다. demo-A·demo-B 리허설 **12/12 깸** [시뮬]. 기본 프로파일은 그대로.
- 보드: `DESKMATE_PROFILE=demo-a|demo-b`(hub.env 한 줄). 10-24 Pi 4 실기에 demo-b 로 올라가 있다(PR #73 본문). `2b9252a` 는 IPK 재빌드 전이라 보드에는 아직 없다.

## 프로파일 비교 [시뮬]

3 시드, 출처 `laptop_answer_1024.md` §2.

| 프로파일 | 집중 진입 | 졸음 뒤 첫 개입 | 개입 |
|---|---|---|---|
| 기본 | 310 s | 440~620 s | 자동(화면+무드등+TTS) |
| demo-A | 310 s | 120~180 s | 자동, 깸 |
| **demo-B** | **20 s** | **102 s** | 자동, 깸, 졸기 전 헛개입 0 |

```yaml
# demo-A (fsm.yaml 덮어쓰기)
timers: {fatigue_elevated_hold_sec: 20, fatigue_confirm_hold_sec: 20, fatigue_hold_min_frac: 0.8, monitor_window_sec: 30, rest_min_sec: 60}
gate: {conf_auto: 0.60}
# demo-A (ingest.yaml 덮어쓰기)
pc_ratio_window_sec: 120
elapsed: {reset_on_rest: true}
# demo-B = demo-A + 아래
timers: {baseline_sec: 10}
gate: {conf_auto: 0.55}
thresholds: {fatigue_confirm: 0.60}
normalization: linear      # ingest.yaml
```

- 함정: `baseline_sec=10` 만 바꾸면(정규화 baseline 그대로) 피로 점수가 0.62 에서 멈춰 개입이 안 나온다.
- 자동 기준 0.75 를 두면 짧은 유지로 확정되는 순간 점수가 0.68 근처라 카드만 뜬다.

## 첫 깨우기 실패 → RECOVERY 정체 (`2b9252a`)

리허설(73cd7ac, 합성·정답 자세, 시드 1~6)에서 찾은 문제:
- 첫 깨우기가 실패하면 MONITOR(30 s)가 점수 부호(±0.003)로 RECOVERY 를 고르고, 자는 동안 점수가 0.57~0.70 이라 `recovery_fail` 0.70 에 못 닿아 **17~22 분 다시 깨우지 않았다**(demo-a 1/6, demo-b 2/6 끝까지 못 깸). MONITOR 판정 21번 중 12번이 정답과 반대.

수정(기본값 null = 지금 동작):
- `thresholds.still_asleep_delta`: 자세 delta 가 이 이상(졸음·엎드림 1.0)이면 MONITOR 에서 격상. 깨어 있으면 자세 원인일 때만 회복. RECOVERY 중 `monitor_window_sec` 동안 다시 졸음이면 격상.
- `gate.asleep_retry`: `rest` 면 아직 졸음일 때 다른 원인 제안 카드 대신 바로 강제 휴식.
- demo-a·demo-b 에 0.9 · rest.

결과 [시뮬]: 리허설 12/12 깸, 첫 실패 뒤 50 s(졸음 뒤 +152~182 s), 자는 동안 카드 0, 되돌리기 0. 기본 프로파일은 시드 1~3 같은 결과.
PR #73 본문 기준 리허설: demo-a/demo-b 12/12 첫 개입 자동, 같은 tick 에 `desk_lamp set_brightness 230 (auto)`, 졸음 시작 뒤 첫 개입 demo-b +102 s, demo-a +112~182 s, 기본 +442~622 s.

## 깨우기 경로·기기 (출처 `laptop_answer_1024.md` §3)

- 졸음(drowsy·face_down) → posture 신호 → routing posture → **ACTION_POSTURE**. 자동 `posture_auto: [screen_notice, lamp_cool, tts]`, 제안 `posture_suggest: [screen_card]`.
- 기기 id: `desk_lamp`(KLZS-L1 iLink, 원시 밝기 0~255) · `speaker`(A2DP, 한국어 TTS 미확인) · `vent_fan`(0~3단) · `screen`(Pi 5 카드).
- 실측 자세 정확도가 낮아([a](a-board-posture-model.md)) 시연 졸음은 **엎드리기**가 확실하다.

## 폐루프 리허설 요청 방법

hub 변경을 fork 커밋이나 diff 로 주면 PC life_sim 에서 `a1_wake_first` 를 기본·demo-A·demo-B 로 돌려 개입 시각·나간 명령·되돌리기·깬 시각을 표로 준다(합성 10분, MuJoCo+GRU 는 UBAI 1시간 안).
