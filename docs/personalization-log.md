# 개인화 로그 (`personalize_episode`) JSONL 스키마

> 기준일 2026-10-21. 설계: [`personalization-spec.md`](personalization-spec.md). 기록·읽기: `hub/deskmate_hub/personalize/log.py`.
> 한 줄 = 개입 결정 한 번(에피소드). FSM 이 `CAUSE_ANALYSIS` 에서 `ACTION_*` 로 보낸 tick 에 시작해, 피드백과 10 분 뒤 결과까지
> 채운 뒤 **한 줄로** 쓴다. 기존 `esm_label`(`data-spec.md` §11)·`control_result` 를 대체하지 않고 그 옆에 둔다.
> 기본 경로 `logs/personalize-<boot_id>.jsonl` (gitignore). 원시 센서·키 내용·ToF 격자는 넣지 않는다.

## 1. 한 줄 구조

| 필드 | 타입 | 필수 | 정의 |
|---|---|---|---|
| `schema_name` | string | Y | `personalize_episode` |
| `schema_version` | string | Y | `1.0` |
| `simulated` | bool | Y | 합성 생성기(`synth.py`)가 만든 줄이면 `true`. 실기 로그는 `false` |
| `persona` | string/null | 합성만 | 합성 페르소나 id. 실기는 null |
| `episode_id` | string | Y | `<boot_id>-pe<n>` |
| `session_id` | string | Y | 작업 세션 id (START 마다 새로) |
| `profile_id` | string | Y | 프로필 id. 단일 기기는 `default` |
| `ts_ms` | int64 | Y | 결정 시각 UTC epoch ms |
| `local_time` | string | Y | 결정 시각 지역시 ISO 8601 (`2026-10-21T15:42:10+09:00`) |
| `features` | object | Y | 상황 벡터 **원값**(§2). 정규화 전 |
| `fsm` | object | Y | 1층 결과(§3) |
| `decision` | object | Y | 3층 결정(§4) |
| `feedback` | object/null | — | 사용자 피드백(§5). 자동 실행이 되돌려지지 않으면 `verdict: "auto_kept"` |
| `outcome` | object/null | — | 10 분 뒤 결과·보상(§6). 세션이 먼저 끝나면 `status: session_ended` |

## 2. `features` (단위 포함 원값)

| 키 | 단위 | 비고 |
|---|---|---|
| `posture_dist` | 비율 object, 9라벨 | 최근 10 분 다수결 라벨 분포. 키 순서는 `upright, lean_forward, recline, face_down, chin_rest, drowsy, away, misplaced, forward_head` |
| `posture_label`, `posture_hold_min` | 라벨, 분 | 현재 자세와 지속 시간 |
| `slump_hold_min` | 분 | 슬럼프 라벨(recline·face_down·chin_rest·drowsy) 연속 지속 |
| `present` | bool | 재실 |
| `drowsy`, `drowsy_count_30min` | bool, 회 | mmWave 졸음 |
| `co2_ppm`, `lux`, `temp_c`, `humidity_pct` | ppm, lx, °C, %RH | 환경 원값. 미수신이면 null |
| `pc_ratio`, `typing_active` | [0,1], bool | 키스트로크 활동 |
| `c_fatigue`, `c_focus` | [0,1] | tick 점수 |
| `session_elapsed_min` | 분 | START 이후 |
| `since_last_intervention_min` | 분/null | 세션 첫 개입이면 null |
| `hour_local`, `weekday` | 0–23.99, 0(월)–6(일) | 지역시 |
| `after_bedtime` | bool | 프로필 `usual_bedtime` 이후 |
| `month`, `season` | 1–12, `spring/summer/autumn/winter` | 날짜만으로 |
| `weather` | object/null | `{windy, rain, pm_bad, outdoor_temp_c, wind_ms, humidity_pct, condition, season, fetched_at, source}` — `deskmate/sensor/weather` 를 `personalize/weather.py` 가 정규화한 것. 미수신·stale(2 h) 이면 null |
| `missing` | string[] | 비어 있던 키 목록 |

## 3. `fsm`

| 키 | 값 |
|---|---|
| `state` | `ACTION_ENV` / `ACTION_POSTURE` / `ACTION_BREAK` |
| `cause` | `environment` / `posture` / `cognitive` |
| `gate` | FSM 이 낸 게이트 `auto` / `suggest` (`none` 이면 에피소드를 만들지 않는다) |
| `retry` | bool — 재시도 원인(`gate.retry_max`) 여부 |
| `context` | `pc` / `mixed` / `npc` |
| `guards` | 성립한 guard 이름 배열 |

## 4. `decision`

| 키 | 값 |
|---|---|
| `arm` | 고른 개입 id (`personalization-spec.md` §4). 규칙이 고르면 `sleep_suggest` |
| `candidates` | 후보였던 arm 배열(쿨다운·거절·무음 제외 뒤) |
| `excluded` | `{arm: 이유}` — `cooldown` / `rejected_in_session` / `quiet_hours` / `weather` / `not_auto_capable` |
| `source` | `bandit` / `rule_sleep` / `fallback`(후보 없음 → FSM 기본 동작) |
| `policy_mode` | `global` / `personal` |
| `n_personal` | 반영된 개인 피드백 수 |
| `scores` | `{arm: 점수}` Thompson 샘플 점수 |
| `delivered_gate` | 실제 전달 게이트 `auto` / `suggest`. FSM 게이트보다 높을 수 없다 |
| `auto_threshold` | 그 arm 의 개인 자동 문턱 (`conf_auto + undo_penalty`) |
| `explanation` | 화면 한 줄 설명 |
| `ventilate_minutes` | `ventilate_suggest` 일 때 권유 길이(분, 겨울 5·그 밖 15). 다른 arm 은 null |

## 5. `feedback`

| 키 | 값 |
|---|---|
| `verdict` | `accept` / `reject` / `timeout` / `undo`(자동 실행 뒤 되돌리기) / `auto_kept`(자동 실행, 되돌리기 창 지남) |
| `response_ms` | 카드 표시~응답. `timeout`·`auto_kept` 는 null |
| `feedback_ts_ms` | 피드백 시각 |
| `request_id` | `interaction/request` 의 id (자동 실행이면 null) |

## 6. `outcome`

| 키 | 값 |
|---|---|
| `status` | `measured` / `session_ended` / `missing` |
| `c_fatigue_before` | 결정 tick 의 C_fatigue |
| `c_fatigue_after` | 결정 + `effect_window_min`(10) 시점의 C_fatigue (`measured` 만) |
| `trend` | `c_fatigue_after − c_fatigue_before` (음수 = 개선) |
| `posture_recovered` | 자세성 개입 뒤 10 분 안에 `upright` 복귀 여부 (자세성만, 아니면 null) |
| `next_state` | 10 분 뒤 FSM 상태 (RECOVERY/FOCUS_* 면 회복, ESCALATE 면 실패) |
| `reward_accept` | +1 / 0 / −1 / −2 |
| `reward_effect` | `clip((before − after) / 0.30, −1, 1)`, `measured` 아니면 null |
| `reward` | `w_accept·reward_accept + w_effect·reward_effect` (effect 가 null 이면 accept 축만) |

## 7. 예시 3줄

```json
{"schema_name":"personalize_episode","schema_version":"1.0","simulated":false,"persona":null,"episode_id":"b7f2-pe3","session_id":"s-20261021-1402","profile_id":"default","ts_ms":1792561330000,"local_time":"2026-10-21T15:42:10+09:00","features":{"posture_dist":{"upright":0.55,"lean_forward":0.1,"recline":0.0,"face_down":0.0,"chin_rest":0.0,"drowsy":0.05,"away":0.0,"misplaced":0.0,"forward_head":0.3},"posture_label":"forward_head","posture_hold_min":6.5,"slump_hold_min":0.0,"present":true,"drowsy":false,"drowsy_count_30min":1,"co2_ppm":1310,"lux":180,"temp_c":26.4,"humidity_pct":58,"pc_ratio":0.82,"typing_active":true,"c_fatigue":0.78,"c_focus":0.31,"session_elapsed_min":98,"since_last_intervention_min":null,"hour_local":15.7,"weekday":2,"after_bedtime":false,"month":10,"season":"autumn","weather":null,"missing":["weather"]},"fsm":{"state":"ACTION_ENV","cause":"environment","gate":"auto","retry":false,"context":"pc","guards":[]},"decision":{"arm":"air_fan_up","candidates":["air_fan_up","light_cool_bright","ventilate_suggest"],"excluded":{},"source":"bandit","policy_mode":"personal","n_personal":23,"scores":{"air_fan_up":0.62,"light_cool_bright":0.21,"ventilate_suggest":0.33},"delivered_gate":"auto","auto_threshold":0.75,"explanation":"CO₂ 1310 ppm · 평소 민감 문턱 1050 ppm 넘음 · 풍량 올리기 수락 9/10 · 개인 정책 · 피드백 23건"},"feedback":{"verdict":"auto_kept","response_ms":null,"feedback_ts_ms":1792561390000,"request_id":null},"outcome":{"status":"measured","c_fatigue_before":0.78,"c_fatigue_after":0.52,"trend":-0.26,"posture_recovered":null,"next_state":"RECOVERY","reward_accept":0,"reward_effect":0.87,"reward":0.43}}
{"schema_name":"personalize_episode","schema_version":"1.0","simulated":false,"persona":null,"episode_id":"b7f2-pe4","session_id":"s-20261021-1402","profile_id":"default","ts_ms":1792563010000,"local_time":"2026-10-21T16:10:10+09:00","features":{"posture_dist":{"upright":0.2,"lean_forward":0.05,"recline":0.0,"face_down":0.0,"chin_rest":0.15,"drowsy":0.0,"away":0.0,"misplaced":0.0,"forward_head":0.6},"posture_label":"forward_head","posture_hold_min":14.0,"slump_hold_min":3.0,"present":true,"drowsy":false,"drowsy_count_30min":0,"co2_ppm":880,"lux":210,"temp_c":25.1,"humidity_pct":55,"pc_ratio":0.9,"typing_active":true,"c_fatigue":0.72,"c_focus":0.28,"session_elapsed_min":126,"since_last_intervention_min":28,"hour_local":16.2,"weekday":2,"after_bedtime":false,"month":10,"season":"autumn","weather":null,"missing":["weather"]},"fsm":{"state":"ACTION_POSTURE","cause":"posture","gate":"suggest","retry":true,"context":"pc","guards":["slouch_with_idle"]},"decision":{"arm":"posture_card","candidates":["posture_card"],"excluded":{"posture_voice":"quiet_hours","sound_cue":"quiet_hours"},"source":"bandit","policy_mode":"personal","n_personal":24,"scores":{"posture_card":0.48},"delivered_gate":"suggest","auto_threshold":0.90,"explanation":"거북목 14분 · 음성 안내는 되돌리기 3회라 묻고 진행 · 카드 수락 7/8 · 개인 정책 · 피드백 24건"},"feedback":{"verdict":"reject","response_ms":3100,"feedback_ts_ms":1792563020000,"request_id":"rq-0192"},"outcome":{"status":"measured","c_fatigue_before":0.72,"c_fatigue_after":0.74,"trend":0.02,"posture_recovered":false,"next_state":"ESCALATE","reward_accept":-1,"reward_effect":-0.07,"reward":-0.53}}
{"schema_name":"personalize_episode","schema_version":"1.0","simulated":true,"persona":"night_owl","episode_id":"synth-pe117","session_id":"synth-d12-s1","profile_id":"synth-night_owl","ts_ms":1791842400000,"local_time":"2026-10-13T23:40:00+09:00","features":{"posture_dist":{"upright":0.3,"lean_forward":0.1,"recline":0.25,"face_down":0.0,"chin_rest":0.1,"drowsy":0.15,"away":0.0,"misplaced":0.0,"forward_head":0.1},"posture_label":"recline","posture_hold_min":9.0,"slump_hold_min":9.0,"present":true,"drowsy":true,"drowsy_count_30min":2,"co2_ppm":1020,"lux":90,"temp_c":23.0,"humidity_pct":48,"pc_ratio":0.95,"typing_active":false,"c_fatigue":0.81,"c_focus":0.4,"session_elapsed_min":190,"since_last_intervention_min":12,"hour_local":23.67,"weekday":0,"after_bedtime":true,"month":10,"season":"autumn","weather":{"windy":false,"rain":false,"pm_bad":false,"outdoor_temp_c":12.0},"missing":[]},"fsm":{"state":"ACTION_BREAK","cause":"cognitive","gate":"suggest","retry":true,"context":"pc","guards":[]},"decision":{"arm":"sleep_suggest","candidates":["break_short","break_long","light_warm_dim"],"excluded":{"sound_cue":"quiet_hours"},"source":"rule_sleep","policy_mode":"personal","n_personal":41,"scores":{},"delivered_gate":"suggest","auto_threshold":null,"explanation":"평소 취침 23:20 지남 · 졸음 2회 · 앞 개입 2회 효과 없음 · 취침 권유(규칙) · 피드백 41건"},"feedback":{"verdict":"accept","response_ms":5200,"feedback_ts_ms":1791842410000,"request_id":"synth-rq-117"},"outcome":{"status":"session_ended","c_fatigue_before":0.81,"c_fatigue_after":null,"trend":null,"posture_recovered":null,"next_state":"END","reward_accept":1,"reward_effect":null,"reward":0.5}}
```

## 8. 세션 요약 줄 (`personalize_session`)

프로필(2층)의 생활습관 항목은 에피소드만으로는 못 만드는 것이 있어(첫 슬럼프·휴식 간격·시간대별 피로) 세션이 끝날 때(`END`/`IDLE`)
같은 파일에 한 줄 더 쓴다. 집계값만 있다.

| 키 | 단위 | 정의 |
|---|---|---|
| `schema_name` | — | `personalize_session` (`schema_version` `1.0`, `simulated`, `persona`, `profile_id`, `session_id` 는 에피소드와 같은 뜻) |
| `ts_ms`, `local_time` | ms, ISO | 세션 시작 |
| `start_hour_local`, `end_hour_local` | 시(소수) | 지역시. 자정을 넘기면 end < start |
| `length_min` | 분 | 세션 길이 |
| `first_slump_min` | 분/null | START 뒤 처음 슬럼프 라벨이 2 분 이상 지속된 시점 |
| `slump_count` | 회 | 슬럼프 구간 수 |
| `recovery_after_alert_min` | 분 배열 | 자세 안내 뒤 `upright` 복귀까지 (회복한 것만) |
| `break_interval_min` | 분 배열 | REST 사이 간격 |
| `fatigue_by_hour` | `{"15": [합, 표본수]}` | 시간대별 C_fatigue (10 s tick 평균의 합·개수) |
| `co2_at_fatigue` | ppm 배열 | 환경성 원인이 CO₂(reason `co2_high`/`co2_rise`) 였던 피로 확정 시점의 CO₂ |

```json
{"schema_name":"personalize_session","schema_version":"1.0","simulated":false,"persona":null,"profile_id":"default","session_id":"s-20261021-1402","ts_ms":1792555320000,"local_time":"2026-10-21T14:02:00+09:00","start_hour_local":14.03,"end_hour_local":17.4,"length_min":202.0,"first_slump_min":48.5,"slump_count":3,"recovery_after_alert_min":[2.5],"break_interval_min":[55.0,70.0],"fatigue_by_hour":{"14":[22.1,348],"15":[31.8,360],"16":[29.0,360],"17":[9.6,144]},"co2_at_fatigue":[1310]}
```

## 9. 지금부터 쌓을 때 (2026-10-21 연결됨 — 기록 전용 모드)

- **켜는 법:** `personalize.yaml` `record: true`(기본). 정책 `enabled` 와 별개라 정책을 켜지 않아도 쌓인다.
  `python -m deskmate_hub run --broker …` 가 `--log-dir`(기본 `logs/`)에 `personalize-<boot_id>.jsonl` 을 만든다.
  PC 에서 돈다. **Pi 4 제한 Python 에는 numpy 가 없어** `personalize/` 가 numpy 없이 import 되게 해 두었지만(3층
  `InterventionPolicy` 만 `None`), 보드 bridge(`service_bridge`)에는 아직 안 붙였다.
- **누가 부르나:** `live.py` → `personalize/record.py` `EpisodeRecorder`. tick 마다 `on_tick()`:
  - `TickResult.actions` 의 `route:<cause>`(CAUSE_ANALYSIS → ACTION_*) 이고 게이트가 `none` 이 아니면 에피소드를 연다.
    정책이 꺼져 있으면 `decision` 은 `{source: "fallback", arm: null, delivered_gate: <FSM 게이트>, explanation: "정책 미적용 … FSM 기본 동작 <prompt_code>"}`.
  - `interaction/request` 의 `request_id` 를 그 에피소드에 묶고, `feedback/user`(accept/reject/timeout, display 또는 hub 만료)를 `feedback` 에 넣는다.
    자동 게이트는 되돌리기(`kind: auto_undo`)면 `undo`, 아무 피드백 없이 10 분이 지나면 `auto_kept`.
  - `effect_window_min`(10) 이 지난 tick 에 `outcome.status: measured` 로 닫고(C_fatigue·next_state·자세성이면 upright 복귀 여부) 한 줄 쓴다.
  - START 진입에 `session_id` 를 새로 주고(`<boot_id>-s<n>`), END/IDLE 진입에 열린 에피소드를 `session_ended` 로 닫은 뒤 §8 요약 한 줄을 쓴다.
    프로세스 종료(Ctrl-C)는 열린 에피소드를 `missing` 으로 닫고 진행 중 세션 요약만 쓴다.
- **프레임 밖 값은 recorder 가 모은다:** 자세 라벨은 ToF 0x03(`tof` 표본, MLP 판정 덮어쓰기 포함)에서 tick 마다 읽어
  10 분 분포·지속·슬럼프 지속을, 졸음은 ToF `drowsy` 또는 mmWave `DROWSY` 의 거짓→참 전이를 30 분 창으로 센다.
  ToF 가 없으면 `posture_dist` 는 `missing` 에 들어간다(지금 PC 시험이 그렇다). 환경 원값은 `env` 표본의 `*_valid` 가 참인 것만, 날씨는 `deskmate/sensor/weather`.
- 합성 줄(`simulated: true`)과 실기 줄을 같은 파일에 섞지 않는다. 합성은 `logs/synth/` 아래.
- 보존·삭제 범위는 `personalization-privacy-flow.md` 의 삭제 등록 목록에 이 파일을 넣는 것으로 결정한다(미결).
- 시험: `hub/tests/test_personalize_record.py`(합성 MQTT 입력으로 피로→제안→수락/거절→10 분, 자리비움→세션 요약, numpy 없는 import).
