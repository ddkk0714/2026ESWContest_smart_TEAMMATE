# DESKMATE 데이터 명세서

> 논리 스키마: `1.0-draft`
> 범위: ESP32·PC 수집, Pi 4 융합·FSM, Pi 5 UI, 제어·로깅
> 원칙: MQTT·Node-RED·HTTP 등 전송 기술과 독립적인 데이터 계약을 먼저 정의한다.

## 1. 목적·우선순위

이 문서는 값, 단위, 시간, 유효성, 계층 간 전달 계약을 정의한다. MQTT를 채택하면 [`mqtt-topics.md`](mqtt-topics.md)가 이 논리 계약을 topic·QoS·retain으로 매핑한다.

정합성 우선순위:

1. 프라이버시·안전 규칙
2. [`fsm-spec.md`](fsm-spec.md)와 실제 `SensorFrame`/`TickResult`
3. 본 문서의 논리 스키마
4. 통신 어댑터·UI 표시 스키마

## 2. 데이터 계층

```text
L0 물리 raw → L1 표준 샘플 → L2 window 특징 → L3 FSM SensorFrame
                                                    ↓
                                      L4 상태·질문·제어
                                                    ↓
                                      L5 세션·ESM·리포트
```

| 계층 | 원칙 |
|---|---|
| L0 raw | 센서 호스트 메모리에서 순간 처리. 보정 전용 명시적 모드 외 전송·저장 금지. 축소 depth map은 디버그/UI 모드에서만 최대 2Hz 허용. |
| L1/L2 | 운영 전송·장애 분석 가능. 키 내용·원시 키 시퀀스 금지. |
| L3/L4 | Pi 4 추론·UI·제어 인터페이스. 재현을 위한 근거 코드 포함. |
| L5 | 세션 요약·정정 라벨. opt-in·보존 정책 확정 전에는 임시 로그만 사용. |

## 3. 공통 메타데이터

| 필드 | 타입 | 필수 | 정의 |
|---|---|---|---|
| `schema_name` | string | Y | `tof_feature`, `fsm_result`, `user_feedback` 등. |
| `schema_version` | string | Y | `major.minor`. major 불일치는 변환기 없이 처리하지 않음. |
| `sample_ts_ms` | int64 | Y | 측정·이벤트 생성 UTC Unix epoch milliseconds. |
| `received_ts_ms` | int64 | Pi 4 이후 | 수신 시각. 지연 계산용. |
| `source_id` | string | Y | 설치 단위 ID. 예: `esp32-desk-a`, `pc-collector`, `hub`. |
| `boot_id` | string | 네트워크 시 | 부팅별 ID. 순번 리셋 판별. |
| `sequence` | uint32 | 네트워크 시 | 부팅 단위 단조 증가. |
| `session_id` | string/null | 세션 중 | 작업 세션 ID. |
| `correlation_id` | string/null | 연결 시 | 질문↔응답, 명령↔결과 연결. |
| `quality` | object | Y | 유효성·신선도·오류. |
| `data` | object | Y | 스키마별 본문. |

### 3.1 `quality`

```json
{"valid":true,"status":"ok","age_ms":83,"confidence":0.91,"errors":[]}
```

- `status`: `ok`, `degraded`, `stale`, `missing`, `out_of_range`, `sensor_error`, `unsynced`
- `confidence`: 센서·특징 생성 신뢰도 0..1. FSM 점수와 다름.
- `errors`: `insufficient_zones`, `ntp_unsynced` 등 표준 코드.

## 4. 시간·순서·결측

- 기준 시각은 발행 시각이 아닌 `sample_ts_ms`이다.
- ESP32·Pi·PC는 NTP를 시도하고 미동기 시 `unsynced`로 표시한다.
- 동일 `source_id+boot_id+sequence`는 한 번만 처리한다.
- 순번 간격은 유실로 기록하되 스트리밍 센서는 기본적으로 재전송하지 않는다.
- `0`, `false`, 결측 `null`, 무효 `quality.valid=false`를 분리한다.
- freshness 초기 후보: ToF/mmWave/키 3초, 환경 15초, FSM 상태 60초. 실측 후 YAML에서 보정한다.

## 5. 소스 카탈로그

| 소스 | 부품·신호 | 연결 | 목표 취득 | 특징 갱신 | 역할 |
|---|---|---|---|---|---|
| ToF | VL53L9CX 54×42 | Pi 4 MIPI CSI-2 우선, 실패 시 ESP32 I2C 축소 경로 | 연결 spike에서 결정, 최대 100Hz 사양 | 특징 30Hz 목표/L2 30s | 재실·자세·모션·노딩 |
| mmWave | SEN0623/C1001 60GHz | UART | 1Hz 초기 | 1Hz/L2 30s | 재실·still/active·호흡/심박 보조 |
| 환경 | SEN0536/SCD41 | I2C `0x62` | 5s periodic | 0.2Hz/L2 5m | CO₂·온도·습도 |
| 조도 | SZH-EK070/BH1750 | I2C | 2~5Hz 가능 | 0.2Hz/L2 5m | 조도·추세 |
| PC 입력 | OS 키 이벤트 | OS API | event | 1Hz/L2 60s | 키 내용 없는 통계 |
| PC 전원/활성 | 플러그 또는 PC 수집기 | 미결정 | 0.2~1Hz | L2 15m | `pc_ratio`, 세션 경계 |

VL53L9CX는 54×42(2,268 zone) ToF 센서이며 실제 운영률은 Pi 4 MIPI CSI-2 직접 연결 1일 spike에서 드라이버·처리율을 측정해 정한다. 직접 연결이 실패하면 ESP32 I2C에서 해상도·주기를 낮춘 축소 경로로 전환한다. SEN0623의 sleep 출력은 책상 졸음의 정답이 아니며 실제 거치 후 보정한다. SEN0536은 mmWave가 아닌 SCD41 기반 CO₂·온습도 센서이다. VL53L0X는 Pi 5 I2C 연결 확인용 시험 센서이며 제품 데이터 소스가 아니다.

## 6. L1 표준 샘플

### 6.1 `tof_feature`

| 필드 | 타입·단위 | 정의 |
|---|---|---|
| `present` | boolean | ToF 재실. |
| `posture` | enum | `upright`, `lean_forward`, `lean_back`, `slouch`, `away`, `unknown`. |
| `motion_score` | float, 0..1 | zone 거리 변화율. |
| `head_delta_mm` | float/null, mm | baseline 대비 머리·상체 거리 변화. |
| `nod_rate_hz` | float/null, Hz | 반복 숙임 주기. |
| `valid_zones` | uint16, 0..2268 | 유효 zone 수. 축소 경로에서는 실제 전송 grid 크기를 함께 기록한다. |
| `grid_width` | uint8 | 처리에 사용한 zone grid 너비. 원본은 54. |
| `grid_height` | uint8 | 처리에 사용한 zone grid 높이. 원본은 42. |
| `coverage_ratio` | float, 0..1 | 유효 상체 영역 비율. |

ToF 원본 2,268-zone 배열은 L0이며 기본 운영 스키마에 포함하지 않는다. 디버그/UI 모드에서는 `debug_depth_map` 별도 스키마로 축소 grid를 최대 2Hz 전송할 수 있고, 활성화 여부·원본 grid·축소 grid·보존 만료 시각을 반드시 기록한다.

### 6.1.1 `debug_depth_map`

| 필드 | 타입·단위 | 정의 |
|---|---|---|
| `enabled` | boolean | 사용자가 명시적으로 디버그 모드를 켰는지 여부. 항상 `true`. |
| `source_grid_width` / `source_grid_height` | uint8 | 원본 grid. VL53L9CX는 54×42. |
| `grid_width` / `grid_height` | uint8 | 축소 후 실제 payload grid 크기. |
| `distance_mm` | uint16/null[] | row-major 축소 거리 배열. 길이는 `grid_width*grid_height`. |
| `expires_ts_ms` | int64 | 메모리·임시 로그에서 삭제해야 하는 시각. |

이 스키마는 FSM 입력과 영구 로그에 사용할 수 없고 MQTT를 채택해도 retain하지 않는다.

### 6.2 `mmwave_feature`

| 필드 | 타입·단위 | 정의 |
|---|---|---|
| `present` | boolean | C1001 재실. |
| `motion_state` | enum | `none`, `still`, `active`, `unknown`. |
| `motion_level` | float/null | 제조사 몸 움직임 파라미터. |
| `resp_bpm` | float/null, breaths/min | 호흡 보조 신호. |
| `resp_valid` | boolean | 거리·각도·재실·정지 조건 통과. |
| `heart_bpm` | float/null, beats/min | 연구·보정용. MVP 가중치 0. |
| `heart_valid` | boolean | 심박 유효성. |

### 6.3 `environment_sample`

| 필드 | 타입·단위 | 정의 |
|---|---|---|
| `co2_ppm` | float/null, ppm | SEN0536 CO₂. |
| `temperature_c` | float/null, °C | SEN0536 온도. |
| `humidity_rh_pct` | float/null, %RH | SEN0536 상대습도. |
| `illuminance_lux` | float/null, lux | SZH-EK070 조도. |
| `*_valid` | boolean | 물리 측정별 유효성. |

물리 측정 범위와 쾌적·피로 임계값을 구분한다. 전자는 드라이버 검증, 후자는 실측 후 FSM YAML에 둔다.

### 6.4 `keystroke_feature`

| 필드 | 타입·단위 | 정의 |
|---|---|---|
| `window_ms` | uint32, ms | 초기 60,000ms. |
| `event_count` | uint32 | window 내 키 이벤트 수. |
| `dwell_mean_ms`, `dwell_std_ms` | float/null, ms | 누름 유지시간 통계. |
| `flight_mean_ms`, `flight_std_ms` | float/null, ms | 키 간격 통계. |
| `idle_ratio`, `correction_rate` | float/null, 0..1 | 입력 공백·정정 비율. |
| `collector_active` | boolean | OS 권한·훅 상태. |

최소 이벤트 수 미만이면 통계는 null·`degraded`/`missing`이다. 키 값, scan code, 문자, 원시 시퀀스는 정의하지 않는다.

### 6.5 `pc_activity_sample`

- `pc_power_on: boolean/null`
- `input_active: boolean`
- `pc_ratio: float 0..1` - 15분 PC/키보드 활성 비율

## 7. L2 window 특징·baseline

| 그룹 | 특징 예 | window |
|---|---|---|
| posture | 자세 유지·전환, slouch 비율, nod 빈도 | 30s~5m |
| motion | ToF 변화, mmWave still/active, 두 센서 일치도 | 30s~3m |
| respiration | 호흡수·변동·유효 비율·센서 차이 | 최소 30s |
| keystroke | dwell·flight 변화, idle·correction, baseline 대비 | 60s~5m |
| environment | CO₂ 절대·Δ5m, 온습도 이탈, 조도 절대·Δ5m | 5m~15m |
| elapsed | 세션·휴식 후 경과, 개인 평균 대비 | session |

각 특징은 `value`, `available`, `confidence`, `source_count`, `valid_ratio`를 가진다.

`baseline_record`:

- `baseline_id`, `created_ts_ms`, `sensor_layout_id`, `duration_ms`
- 신호별 `sample_counts`, median, MAD/IQR, 유효 비율
- `valid`, `invalidated_reason(layout_changed/user_changed/insufficient_samples/manual)`

baseline은 절대 자세·타이핑 속도가 아닌 상대 변화를 만든다. 원시 시퀀스 대신 요약 통계를 저장한다.

## 8. L3 FSM 입력 계약

### 8.1 `sensor_frame`

| 필드 | 타입 | 정의 |
|---|---|---|
| `now_s` | float, epoch s | tick 기준 시각. 현재 Python `now`. |
| `present` | boolean | ToF 주·mmWave 보조 융합 재실. |
| `pc_ratio` | float, 0..1 | 15분 PC 활성 비율. |
| `signals` | map | `keystroke`, `posture`, `respiration`, `environment`, `elapsed`. |
| `touch_start` | boolean | 현재 Python `touch`. |
| `touch_end` | boolean | 현재 Python `end_touch`. |
| `action_done` | boolean | 향후 command result ID로 확장. |
| `break_accepted` | boolean/null | 향후 일반 feedback로 확장. |

### 8.2 `signal_contribution`

```json
{"phi":0.62,"delta":0.18,"available":true,"confidence":0.84,"reasons":["typing_idle_ratio_up"]}
```

- `phi`: 0..1. **현재 코드에서 집중 저하 증거**. 용어·부호 결정 필요.
- `delta`: 0..1 피로 증거.
- `available=false`는 분자·분모에서 제외.
- `confidence`, `reasons`는 현재 Python 타입 확장 필요.

가용 항이 없으면 점수 0을 정상으로 해석하지 않고 `insufficient_evidence`를 별도 표시해야 한다.

## 9. L4 FSM 출력·UI

### 9.1 `fsm_result`

| 필드 | 정의 |
|---|---|
| `fsm_state` | VER5 18상태, Python `State.value`와 동일. |
| `phase` | UI용 `idle/start/focus/fatigue/recovery/end`. |
| `context` | `pc/mixed/npc`. |
| `focus_evidence_score` | 현재 `c_focus`; 용어 결정 전 명시적 별칭. |
| `fatigue_score` | 현재 `c_fatigue`. |
| `focus_weights`, `fatigue_weights` | tick에 적용된 가중치. |
| `gate` | `auto/suggest/none`. |
| `cause` | `environment/posture/cognitive/null`. |
| `actions`, `reasons` | FSM 행동·사용자용 근거 코드. |
| `sensor_quality` | 소스별 `valid/status/age_ms`. |
| `state_entered_ts_ms` | 현재 상태 진입 시각. |

### 9.2 내부 상태→UI phase 초안

| phase | FSM 상태 |
|---|---|
| `idle` | `IDLE` |
| `start` | `START`, `CONTEXT_DETECT` |
| `focus` | `FOCUS_PC`, `FOCUS_MIXED`, `FOCUS_NPC`, `FOCUS_BREAK` |
| `fatigue` | `FATIGUE_SUSPECT`, `FATIGUE`, `CAUSE_ANALYSIS`, `ACTION_ENV`, `ACTION_POSTURE`, `ACTION_BREAK`, `MONITOR`, `ESCALATE` |
| `recovery` | `REST`, `RECOVERY` |
| `end` | `END` |

개발계획서의 4단계와 VER5 18상태를 연결하는 초안이다. 4단계 노출 방식은 UI 테스트 후 확정한다.

### 9.3 `interaction_request`·`user_feedback`

| 스키마 | 필드 |
|---|---|
| `interaction_request` | `request_id`, `kind(state_disambiguation/action_confirm/manual_label)`, `prompt_code`, `options`, `expires_ts_ms`, `evidence` |
| `user_feedback` | `request_id`, `verdict(accept/reject/correct/unsure/timeout)`, `answer_code`, `corrected_state`, `response_ms`, `input_method` |

`timeout`은 `reject`가 아니며 `unsure`는 틀린 라벨이 아니다. 개인화·수용률에서 분리한다.

## 10. 제어 계약

| 스키마 | 필드 |
|---|---|
| `control_command` | `command_id`, `target_id`, `operation`, `value`, `origin_state`, `cause`, `gate`, `requires_confirmation`, `expires_ts_ms` |
| `control_result` | `command_id`, `status(accepted/executing/succeeded/failed/timeout/cancelled)`, `actual_value`, `error_code`, `error_message`, `completed_ts_ms` |

`command_id`는 멱등성을 보장한다. 오류 메시지에 비밀값을 포함하지 않는다.

## 11. 장치 상태·세션·ESM

`device_health`:

- `uptime_s`, `software_version`, `clock_synced`
- `network_state`, `rssi_dbm`, `reconnect_count`
- `sensor_status`, `queue_depth`, `memory_used_bytes`

`session_summary`:

- `session_id`, `started_ts_ms`, `ended_ts_ms`, `baseline_id`
- `context_durations_ms`, `state_durations_ms`
- `fatigue_events`, `focus_break_events`, `interventions`, `feedback_counts`
- `environment_summary`

`esm_label`:

- `label_id`, `source`, `target_window_start_ms`, `target_window_end_ms`
- `predicted_state`, `corrected_state`, `answer_code`, `label_confidence`

## 12. 프라이버시·보존

| 데이터 | 운영 저장 | Git | 초기 정책 |
|---|---|---|---|
| ToF raw 54×42 | 금지, 명시적 보정 모드 예외 | 금지 | 특징 생성 후 즉시 폐기 |
| ToF 축소 depth map | 디버그/UI 모드에서 최대 2Hz | 금지 | 시험 종료 후 삭제 |
| 키 내용·원시 이벤트 | 금지 | 금지 | 특징 생성 후 폐기 |
| L1/L2 특징 | 실험·리플레이용 가능 | 금지 | 보존 기간 미결정 |
| FSM·제어 | 세션 리포트용 가능 | 샘플만 | 세션 단위 초기 보존 |
| ESM·개인화 | opt-in 후 | 금지 | 보존·삭제 확정 필요 |
| 자격증명·토큰 | 보호 설정에만 | 금지 | 로그 마스킹 |

개인화 저장소는 파일·SQLite·기타 로컬 DB 중 미결정이다. 사용자별 조회·삭제·동의 취소는 필수다.

## 13. 전송·무결성

MQTT 사용 시 본 레코드를 JSON/UTF-8로 매핑하고 topic·QoS·retain·ACL은 [`mqtt-topics.md`](mqtt-topics.md)에서 정의한다. Node-RED를 사용해도 논리 스키마를 변경하지 않고 Pi 4 hub를 판정의 단일 기준으로 둔다.

- `schema_version`, `source_id`, `boot_id`, `sequence`, 시간 검증은 통신 방식과 무관하게 필요.
- UART binary frame을 쓰는 구간은 CRC-16으로 검증한다. 상세 다항식·초깃값·바이트 순서와 test vector는 UART 어댑터 구현 전에 고정한다.
- MQTT/TCP JSON에는 별도 애플리케이션 CRC를 넣지 않는다. `schema_version`, `boot_id`, `sequence`, timestamp와 TCP 무결성을 사용한다.
- Node-RED는 개발 모니터링·센서값 주입·로깅용 선택 어댑터이며 FSM의 운영 의존성이 아니다.

## 14. 버전 규칙

- minor: 선택 필드·enum 추가. 알 수 없는 필드는 무시.
- major: 필수 필드 제거·이름·의미·단위·부호 변경.
- enum `unknown`을 항상 수신 가능하게 처리.
- 스키마 변경은 샘플·변환기·리플레이 테스트와 함께 수행.

## 15. 수용 테스트

| ID | 시나리오 | 기대 결과 |
|---|---|---|
| DAT-T01 | `0`, `false`, `null`, `valid=false` | 서로 다르게 처리. |
| DAT-T02 | 키 결측, 나머지 유효 | 키 항 제외 후 재정규화. |
| DAT-T03 | 모든 신호 무효 | 0=정상으로 해석하지 않고 `insufficient_evidence`. |
| DAT-T04 | 중복·역순·지연 | 중복 1회 처리, 지연 입력 추론 제외. |
| DAT-T05 | ToF 노딩+mmWave active | 자동 확정 없이 사용자 확인. |
| DAT-T06 | 질문→응답→ESM | correlation ID로 연결. |
| DAT-T07 | 제어 재전송 | 동일 `command_id` 중복 실행 방지. |
| DAT-T08 | 어댑터 교체 | 합성·리플레이·실시간 입력이 동일 `sensor_frame` 생성. |
| DAT-T09 | 금지 데이터 검사 | 키 내용·ToF raw·자격증명이 운영 로그에 없음. |

## 16. 미결정 항목

| ID | 항목 | 필요 결정 |
|---|---|---|
| DATA-DEC-001 | `C_focus` | 큰 값=집중 저하 증거 유지 / 집중도로 부호 변경 |
| DATA-DEC-002 | 통신 envelope | ESP32↔Pi 4 및 Pi 4↔Pi 5의 UART/MQTT/혼합 최종 결정 후 매핑 |
| DATA-DEC-003 | UART CRC 세부값 | CRC-16 다항식·초깃값·바이트 순서·test vector |
| DATA-DEC-004 | freshness·샘플링 | 실제 ESP32·LAN 지연 분포로 보정 |
| DATA-DEC-005 | 특징 정규화 | baseline→`phi/delta` 수식·clip·결측 규칙 |
| DATA-DEC-006 | 개인화 보존 | 저장소·보존·삭제·opt-in UI |
| DATA-DEC-007 | 평가 라벨 | 상태 정답·피드백 일치율·수용률 계산식 |

## 17. 참고 자료

- 개발계획서: [`plan/02_2026ESWContest_스마트가전_팀명_개발계획서_v2.docx`](plan/02_2026ESWContest_스마트가전_팀명_개발계획서_v2.docx)
- ST VL53L9CX: https://www.st.com/en/imaging-and-photonics-solutions/vl53l9cx.html
- DFRobot SEN0623/C1001: https://wiki.dfrobot.com/sen0623/
- DFRobot SEN0536/SCD41: https://wiki.dfrobot.com/sen0536/
- SZH-EK070/BH1750: https://www.devicemart.co.kr/goods/view?no=1289977
