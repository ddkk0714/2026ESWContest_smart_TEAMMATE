# MQTT 토픽 규약

> **Pi 4↔Pi 5 구간 확정:** 이더넷 직결 MQTT를 사용한다. MQTT/TCP JSON에는 별도 애플리케이션 CRC를 넣지 않으며, UART를 쓰는 구간의 binary frame만 CRC-16으로 검증한다.
> 논리 데이터 계약은 [`data-spec.md`](data-spec.md)를 우선하며, MQTT를 채택할 때 본 문서를 확정한다.

담당: 이민혁 · 변경 시 PR 로 이 문서를 함께 수정한다.

broker는 Raspberry Pi 4 에 두고, 페이로드는 JSON (UTF-8) 을 사용한다.
필드 단위·유효성·보정 규칙은 [`data-spec.md`](data-spec.md)를 따른다.

## 공통 필드

현재 후보 envelope는 다음과 같다.

```json
{
  "schema_version": "1.0", "ts": 1769000000.123,
  "node": "tof-adapter", "boot_id": "7f2a91c4", "seq": 1042,
  "data": {}
}
```

- `ts` — 샘플 생성 Unix epoch seconds. 노드는 부팅 시 NTP 동기화한다.
- `node` — 발행 장치 식별자.
- `boot_id` + `seq` — 재부팅, 중복, 유실, 역순 패킷 판정.
- UART 어댑터는 CRC를 통과한 frame만 위 JSON envelope로 변환한다. MQTT에서는 `schema_version`, `boot_id`, `seq`, `ts`로 중복·유실·지연을 검증한다.

## 토픽 목록

| 토픽 | 발행 | 구독 | 주기 | 내용 |
|---|---|---|---|---|
| `deskmate/sensor/tof/<node>` | ToF adapter | hub | 특징 최대 30Hz | VL53L9CX **특징값** (raw 54×42 아님) |
| `deskmate/debug/tof/depth/<node>` | ToF adapter | display/debug | 최대 2Hz | 명시적 디버그 모드의 축소 depth map. retain 금지 |
| `deskmate/sensor/mmwave/<node>` | ESP32 | hub | 1Hz | SEN0623 재실·모션·호흡/심박 보조 특징 |
| `deskmate/sensor/env/<node>` | ESP32 | hub | 0.2Hz | CO₂ · 온습도 · 조도 |
| `deskmate/sensor/keystroke` | PC 수집기 | hub, display/PC 키스트로크 탭 | 1Hz | 키 입력 타이밍 특징 |
| `deskmate/sensor/weather` | Pi 5 앱 | hub(personalize) | 30 분 + 시작 시 1회(retain, QoS 0) | 바깥 날씨·계절. FSM 입력이 아니라 개입 선택(환기 vs 공기청정기, 겨울 짧은 환기)에만 쓴다 |
| `deskmate/state/phase` | hub | display, control | 10 s 주기(retain) | 추론 결과 + 신뢰도. `python -m deskmate_hub run` 이 발행 |
| `deskmate/display/message` | Node-RED/debug | display | 이벤트 | Pi 5 화면에 일회성 텍스트 표시 |
| `deskmate/session/report` | hub | display | 10 s 주기 스냅샷(retain) + 세션 종료 시 | 화면용 세션 요약. 개별 행동 로그 없음 |
| `deskmate/interaction/request` | hub | display | 이벤트 | 불확실한 판정의 사용자 확인 질문 |
| `deskmate/control/cmd` | hub | control 어댑터, Pi 5 앱(조명) | 이벤트, QoS 1, retain 없음 | 기기 제어 명령 (`control_command`, data-spec §10) |
| `deskmate/control/result` | control 어댑터, Pi 5 앱(조명) | hub | 이벤트, QoS 1 | 명령 결과 (`control_result`). hub 는 `command_id` 로 매칭 |
| `deskmate/feedback/user` | display | hub | 이벤트 | 사용자 수락 · 정정 · 조명 직접 조절 보고(`kind: manual_adjust`) |
| `deskmate/profile/user` | Pi 5 앱 | hub(personalize) | 온보딩·설정 변경 시(retain, QoS 1) | 첫 설정 프로필. FSM 입력이 아니라 개입 선택의 기준(prior)·취침 시각에만 쓴다 |
| `deskmate/fatigue/label` | Pi 5 피로 기록 앱(`display/atlas/fatigue_app`) | PC `tools/fatigue_logger.py`, hub | 이벤트, QoS 1 | 피로 데이터 수집: 작업 시작·끝, 15분 KSS 응답. hub 는 피로 점수 문맥·프로필 보정에만 씀, FSM 입력 아님 ([fatigue-dataset.md](fatigue-dataset.md)) |
| `deskmate/health/<node>` | 전 장치 | hub | LWT/retain | `{ts,node,status:online|offline,reconnects}`. hub 는 `deskmate/health/hub` |

## 페이로드 예시

### `deskmate/sensor/tof/<node>`

ToF 원본 54×42 배열은 전송하지 않는다. 운영 경로는 특징값만 보내며, 별도 디버그 topic에서만 축소 depth map을 최대 2Hz로 허용한다.

```json
{
  "schema_version": "1.0", "ts": 1769000000.123,
  "node": "tof-adapter", "boot_id": "7f2a91c4", "seq": 1042,
  "data": {
  "present": true,
  "posture": "upright",        // upright | lean_forward | recline | face_down | chin_rest | drowsy | away | unknown
  "motion_score": 0.42,        // 모션 변화율 0.0~1.0
  "valid_zones": 2014,         // 유효 zone 개수 (원본 전체 2268)
  "grid_width": 54, "grid_height": 42,
  "head_delta_mm": -18.4, "nod_rate_hz": 0.31,
  "valid": true
  }
}
```

### `deskmate/sensor/mmwave/<node>`

```json
{
  "schema_version": "1.0", "ts": 1769000000.2,
  "node": "esp32-a", "boot_id": "7f2a91c4", "seq": 1043,
  "data": {
    "present": true, "motion_state": "still", "motion_level": 12,   // movement 0/1/2 → none/still/active, bodyMove 0..100
    "distance_cm": 55,
    "resp_bpm": 15, "resp_valid": true,                               // resp_valid = present && lock && 6..30 bpm
    "heart_bpm": null, "heart_valid": false,
    "drowsy_state": "AWAKE",                                          // NOPERSON | NOLOCK | WARMUP | AWAKE | DROWSY
    "valid": true
  }
}
```

MVP(2026-09-18) 경로: ESP32 USB(UART0) 1 Hz JSON 라인 → `tools/uart_mqtt_bridge.py` 가 위 envelope 로 발행.
통합 MVP 이후에는 Pi 4 native service 가 UART2 프레임을 디코딩해 같은 토픽으로 발행한다.

### `deskmate/sensor/env/<node>`

```json
{
  "schema_version": "1.0", "ts": 1769000000.5,
  "node": "esp32-b", "boot_id": "315ae820", "seq": 91,
  "data": {
    "co2_ppm": 812, "temp_c": 26.4, "humidity_pct": 48.2, "lux": 310,   // CO₂=SCD41, T/RH=DHT22, lux=BH1750 (보정 없음)
    "co2_valid": true, "temp_valid": true, "humidity_valid": true, "lux_valid": true   // 센서 부재 = null + false
  }
}
```

### `deskmate/sensor/keystroke`

키 값은 절대 포함하지 않는다. 타이밍 통계만 보낸다.
**2026-09-14 `collector/` 구현 기준으로 확정** — 60 s 윈도우 · 1 Hz · QoS 0. 필드 정의는 [`data-spec.md`](data-spec.md) §6.4.
현재 구현은 아래 특징값을 공통 envelope(`schema_version`·`boot_id`·`seq`·`data`)의
`data` 필드에 담아 보낸다.

```json
{
  "schema_version": "1.0", "ts": 1769000001.0,
  "node": "pc-collector", "boot_id": "7f2a91c4", "seq": 1042,
  "data": {
    "node": "pc-collector", "window_s": 60,
    "dwell_mean_ms": 92.4, "dwell_std_ms": 21.8,
    "flight_mean_ms": 148.2, "flight_std_ms": 63.5,
    "idle_ratio": 0.18,          // 입력 공백 비율. keydown 없으면 1.0
    "correction_rate": 0.07,     // 백스페이스 빈도
    "typing_active": true,       // false 면 hub 는 키스트로크 신호 미가용으로 재정규화
    "mouse_active": true, "input_active": true,
    "flight_cv": 0.43, "mouse_event_rate": 96.0
  }
}
```

health: `deskmate/health/pc-collector` 에 `{"node","status":"online|offline","reconnects"}` (LWT, retain).

Pi 5의 **PC 키스트로크** 탭은 앱에 설정된 동일 MQTT 브로커에서 이 토픽을 QoS 0으로
직접 구독한다. 허브의 10초 국면 발행을 기다리지 않고 1Hz 특징값을 표시하며, 보드에
꽂힌 키보드의 로컬 캡처로 덮어쓰지 않는다. 표본 `ts`가 현재 시각보다 5초 넘게
오래되면 "수신 끊김"으로 표시하므로 PC·Pi의 시계 동기화가 필요하다.
최근 60개 집계 표본의 추이는 탭을 연 동안 메모리에만 유지하고, 미입력·수신 공백을
타이밍 곡선으로 연결하지 않는다. 내장 데모는 실제 PC 미연결임을 명시한다.

### `deskmate/sensor/weather` (2026-10-21 최종 확정: 날씨는 Pi 5 앱이 받아 MQTT 로 허브에 넘긴다)

발행자 Pi 5 앱(`node: pi5-display`), **retain, QoS 0, 30 분 주기 + 앱 시작 시 1회 즉시**. 공통 envelope 를 쓴다. 토픽 이름은 `deskmate/sensor/weather` 로 고정한다(`deskmate/env/weather` 는 계약 밖).
`deskmate/sensor/#` 아래라 hub 의 기존 구독(PC paho·보드 C++ 브리지 모두)이 그대로 받는다. 허브 `SensorCache.weather` 에 마지막 표본만
머물고 FSM `SensorFrame` 에는 들어가지 않는다 — `personalize/`(개입 선택)만 읽는다([`personalization-spec.md`](personalization-spec.md) §7).

```json
{
  "schema_version": "1.0", "ts": 1792561200.0,
  "node": "pi5-display", "boot_id": "c41d9a02", "seq": 17,
  "data": {
    "temp_c": 16.4,                 // 바깥 기온 °C
    "humidity": 58,                 // 바깥 상대습도 %RH (int)
    "wind_ms": 5.2,                 // 풍속 m/s. hub 는 personalize.yaml weather.windy_ms(4.0) 이상을 "바람 붊"으로 본다
    "condition": "clear",           // clear | cloudy | rain | snow | fog | unknown — 뇌우·폭풍·소나기는 rain, 진눈깨비·우박은 snow, 매핑 불가는 unknown
    "season": "autumn",             // spring | summer | autumn | winter (앱이 날짜로 계산). hub 는 자기 날짜를 우선한다
    "fetched_at": 1792560900,       // 날씨를 외부에서 받은 Unix epoch s (int). 재발행해도 이 값은 바뀌지 않는다
    "source": "kma-short-term"      // 출처 문자열(자유). 키·토큰·좌표는 넣지 않는다
  }
}
```

- **stale 기준**: hub 는 `now − fetched_at > weather.stale_sec`(기본 **2 h**) 이면 표본을 무시하고 **계절(월)만으로** 동작한다.
  retain 된 옛 표본이 재시작 뒤 들어와도 같은 규칙으로 걸러진다. `fetched_at` 이 없으면 envelope `ts` 를 쓴다.
- 앱이 날씨를 못 받으면 발행하지 않는다(빈 값·추정값을 만들어 보내지 않는다). 선택 필드 `pm_bad: true`(미세먼지 나쁨)는 넣으면 환기 후보 제외에 쓴다.
- 좌표·주소·API 키는 토픽에 싣지 않는다. 위치 설정은 앱 안에만 둔다.

### `deskmate/profile/user` (2026-10-25: 첫 설정 → 개인화 기준)

발행자 Pi 5 앱(`node: pi5-display`), **retain, QoS 1**, 온보딩을 마칠 때와 설정을 바꿀 때. 공통 envelope 를 쓴다. 허브는 보드 C++ 브리지·PC paho
둘 다 이 토픽을 따로 구독한다(`deskmate/sensor/#` 밖). 필드는 모두 선택이고 모르는 키는 버린다 — 표·사용처는 [data-spec](data-spec.md#사용자-첫-설정직접-조절-보고-2026-10-25).

```json
{
  "ts": 1792561200.0, "node": "pi5-display", "seq": 3,
  "data": {
    "profile_id": "u-7f3a",           // 불투명 id (이름·이메일 아님)
    "age_band": "20s",                // 10s | 20s | 30s | 40s | 50s+ (허브는 지금 저장하지 않는다)
    "sleep_hours": 6.5,
    "bedtime": "00:30", "wake_time": "07:30",
    "light_pref": "low",              // low | mid | high
    "sound_pref": "off",              // off | low | mid | high — off 면 알림음·음성 안내 후보 제외
    "wind_pref": "mid",               // off | low | mid | high
    "focus_help": ["light", "break"], // light | sound | wind | break | voice 중 복수, 순서 = 몰입 도움 순위. 별칭 lamp·fan·notify 도 받는다
    "placement": "left"               // left | center | right
  }
}
```

- envelope `node` 는 거르지 않는다(제품 UI `pi5-ui`·개발자 앱 `display` 모두). profile_id 는 앱이 재시작해도 같은 값을 유지한다.
- 다른 profile_id 가 오면 그 사람의 정책으로 바꾼다(사람별 `logs/profiles/<profile_id>/`, 지금 사용자 `active_profile.json`).
- 허브는 받으면 `logs/profiles/<profile_id>/user_profile.json` 에 저장하고(재시작·브로커 재시작 뒤에도 유지) 화면 `sensor_summary.personalize.signal` 에
  "첫 설정 반영: …" 한 줄을 싣는다. 같은 내용을 다시 받으면(retain 재수신) 아무것도 바꾸지 않는다.

### `deskmate/state/phase`

```json
{
  "schema_version": "1.0", "ts": 1769000002.0,
  "node": "hub", "boot_id": "a8021bf0", "seq": 201,
  "data": {
  "fsm_state": "FOCUS_PC",
  "phase": "focus",            // idle | start | focus | fatigue | recovery | end
  "context": "pc",
  "c_focus": 0.22, "c_fatigue": 0.81,
  "confidence": 0.81,
  "present": true, "scores_valid": true,   // 자리 비움(present false)이면 c_focus·c_fatigue·confidence 0.0, scores_valid false → 숫자 대신 "자리 비움" 표시(10-26). 카드 payload 도 같은 두 필드
  "source": "fsm",             // fsm | classifier | fused
  "gate": "auto",              // auto | suggest | none
  "cause": "environment",
  "reasons": ["posture_stable", "typing_rhythm_slow", "co2_rising"],
  "sensor_summary": {           // 선택: 화면 표시용 특징 요약. 센서 원본 금지
    "present": true, "co2_ppm": 1200, "lux": 310, "valid": true,
    "env_flags": ["co2_high"], // 선택: 유효한 환경 측정에서 임계값을 넘은 실행 이유
    "mmwave": {                 // 선택: 최신 mmWave 표본이 신선할 때만
      "motion_state": "still", "motion_level": 5, "distance_cm": 55,
      "resp_bpm": 15, "resp_valid": true, "heart_bpm": 72, "heart_valid": false,
      "drowsy_state": "AWAKE"
    },
    "keystroke": {              // 선택: deskmate/sensor/keystroke 규약 필드 그대로
      "node": "pc-collector", "ts": 1769000001.0,
      "window_s": 60, "event_count": 184,
      "dwell_mean_ms": 92.4, "dwell_std_ms": 21.8,
      "flight_mean_ms": 148.2, "flight_std_ms": 63.5,
      "idle_ratio": 0.18, "correction_rate": 0.07,
      "valid": true
    }
  }
  }
}
```

`sensor_summary`는 display가 별도 센서 topic을 조합하지 않아도 AOD를 그릴 수 있게 하는 선택 필드다.
ToF raw 54×42 배열이나 키 내용은 이 필드에 넣지 않는다. 현재 미리보기 HTTP API도 이와 동일한
envelope를 사용하므로 최종 전송 어댑터를 바꿔도 display 모델은 유지한다.

`sensor_summary.env_flags` 는 신선하고 유효한 환경 측정으로 계산한 화면용 실행 이유다.
순서는 `co2_high`, `co2_rising`, `too_hot`/`too_cold`, `too_humid`/`too_dry`, `too_dark` 이며
해당 이유가 없으면 키를 생략한다. CO₂ 상승은 세션 시작 뒤 첫 유효 CO₂ 값이 있을 때만 계산한다.
현재 live 발행 경로는 tracker를 전달하지 않으므로 `co2_rising`은 생략하고 나머지 이유를 발행한다.
환경 신호 자체는 CO₂·온도·습도·조도 중 유효한 측정이 하나라도 있으면 가용이다.

`sensor_summary.mmwave` 의 심박·호흡은 **값과 `*_valid` 가 짝**이다. 락온이 풀린 구간의 값을 그대로
그리면 "심박 0" 으로 읽히므로, display 는 `*_valid` 가 false 면 값을 표시하지 않는다(순간값은 판정에 쓰지 않는다).

`sensor_summary.keystroke`는 `deskmate/sensor/keystroke`의 `data` 필드를 그대로 옮긴 것이고,
여기에도 키 값은 들어가지 않는다. display가 collector 브로커에 따로 붙지 않아도 타이핑 지표를
그릴 수 있게 하기 위한 것이다. 다음 두 가지를 지킨다.

- **자체 `ts`를 함께 넣는다.** collector가 죽어도 hub는 국면을 계속 발행하므로, 이 `ts`가 없으면
  마지막 타이핑 값이 화면에 그대로 굳는다. display는 envelope `ts`와의 차이로 신선도를 판정하고,
  5초(1Hz 기준 5샘플) 넘게 묵으면 값을 흐리고 "수신 끊김"으로 표시한다.
  두 `ts`는 서로 다른 장치의 시계이므로 이 판정은 공통 필드의 NTP 동기화 전제에 기댄다.
  hub가 collector 표본을 받은 시각으로 다시 찍어 주면 이 전제를 없앨 수 있다 — 추후 정리 대상.
- **없는 필드는 생략한다. 0으로 채우지 않는다.** 0은 화면에서 "리듬 완벽 · 마우스 정지"로 읽힌다.

collector 규약 추가분(`typing_active` · `flight_cv` · `mouse_event_rate`)은 오면 쓰고 없으면
비운다. `flight_cv`가 없으면 display가 `flight_std_ms / flight_mean_ms`로 만들어 쓴다.

### `deskmate/display/message`

Node-RED에서 Pi 5 화면을 확인할 때 쓰는 일회성 안내 문구다. 키 내용·센서 원본·자격증명은
넣지 않으며, display는 앱 메모리에서만 표시한다. 빈 `text`는 현재 문구를 지운다.

```json
{
  "schema_version": "1.0", "ts": 1769000002.5,
  "node": "node-red", "boot_id": "demo", "seq": 1,
  "data": { "text": "잠시 스트레칭해 볼까요?" }
}
```

### `deskmate/session/report`

Hub가 발행하는 화면용 세션 요약이다. 세션 종료를 기다리지 않고 **10 s 주기 스냅샷**을 retain 으로
발행한다(진행 중이면 `t_end` = 발행 시각, START 전이면 `t_start` = 관측 시작 시각). 주기는 hub 환경변수
`DESKMATE_REPORT_PERIOD_SEC`(기본 10, 0 이면 끔)로 바꾼다. Pi 5는 이 값을 메모리에만
보관해 세션 리포트 화면에 표시한다. 키 내용, ToF raw, 개인 식별자, 개별 시각의 행동 로그는
포함하지 않는다.

```json
{
  "schema_version": "1.0", "ts": 1769003600.0,
  "node": "hub", "boot_id": "a8021bf0", "seq": 212,
  "data": {
    "t_start": 1769000000.0, "t_end": 1769003600.0,
    "duration_s": 3600, "focus_time_s": 2100, "focus_ratio": 0.583,
    "state_durations_s": {"FOCUS_PC": 1800, "REST": 300},
    "fatigue_episodes": [{"t_onset": 1769001800.0, "peak_fatigue": 0.81, "t_resolved": 1769002400.0}],
    "intervention_counts": {"total": 2, "recovered": 1},
    "break_accept_rate": 0.5
  }
}
```

`state_durations_s`는 상태별 누적 시간(초), `fatigue_episodes`는 화면에 필요한 발생 시각과
최대 피로도·해소 시각(`t_resolved`, 미해소면 `null`)만 담는다. 휴식 제안에 응답한 적이 없어 수락률을
계산할 수 없으면 `break_accept_rate`는 `null`(모름)이다 — 0 과 구분한다.
### `deskmate/interaction/request`

```json
{
  "schema_version": "1.0", "ts": 1769000003.0,
  "node": "hub", "boot_id": "a8021bf0", "seq": 202,
  "data": {
    "request_id": "session-12-q-3", "kind": "state_disambiguation",
    "prompt_code": "drowsy_or_rhythm",
    "options": ["drowsy", "rhythm", "unsure"], "expires_in_s": 30,
    "evidence": ["tof_nod_repeated", "mmwave_motion_active"]
  }
}
```

hub 가 현재 발행하는 종류(2026-09-16, `live.py`): **제안 게이트(0.45~0.75)로 `ACTION_*` 에 새로 들어올 때** 1건.
`kind` = `break_suggest` | `env_suggest` | `posture_suggest`, `prompt_code` = `take_a_break` | `adjust_environment` | `fix_posture`,
`options` = `["accept","reject"]`, `expires_in_s` = `timers.focus_break_poll_sec`, `evidence` = FSM `actions`, `cause`, `c_fatigue`.
자동(≥0.75)은 실행 후 알림이라 질문하지 않는다. retain 하지 않는다. display 는 `request_id` 를 기억해 `feedback/user` 에 실어 보내고,
hub 는 현재 질문의 `request_id`(또는 `request_id` 생략·`atlas-display`)만 반영한다. 신호 충돌(`state_disambiguation`)은 4-B §5 에서 추가.

**개인화 정책이 켜져 있으면(`personalize.yaml enabled: true`, 2026-10-21)** `data` 에 세 필드가 더 실린다 — `explanation`(왜 이 개입인지 한 줄,
예 `"조도 80 lx 어두움 · 졸음 2회 · 지난번 조명 밝고 차갑게 되돌리셔서 이번엔 여쭙니다 · 기본 정책 · 피드백 1건"`), `arm`(개입 id, `personalization-spec.md` §4),
`arm_label`(한국어 이름). 창문 환기(`arm: ventilate_suggest`)면 `ventilate_minutes`(날씨가 정한 권유 길이, 분, 2026-10-23)도 실린다 —
화면은 "N 분만 열어 주세요" 로 쓴다. 화면은 카드에 `explanation` 을 그대로 보여 준다. 정책이 FSM 의 자동 게이트를 제안으로 낮춘 경우 `evidence` 에 `personalize:ask` 가 들어 있다.
같은 내용이 `state/phase` 의 `data.sensor_summary.personalize` 에도 실린다(`{episode_id, arm, arm_label, source, explanation, fsm_gate, delivered_gate,
policy_mode, n_personal, auto_threshold, ventilate_minutes}`; 에피소드가 닫히면 `closed: true, outcome`) — 자동 실행 알림이 "왜" 를 보여 줄 때 쓴다.

**CO₂ 창문 환기 카드(2026-10-25, `co2_window.py`·`config/co2_window.yaml`, 보드 기본 켜짐)** — FSM 과 따로, SCD41 1 분 평균 CO₂ ≥ 1000 ppm 이 5 분 +
책상 재실 + 깨어 있음(자세가 drowsy·face_down 아님 60 s) + 날씨 허용(비·눈·바깥이 2 °C 이상 더움·바깥 PM2.5 ≥ 36 제외)일 때 1건.
자동 실행 안내 카드(2026-10-26): 개인화 정책이 기기 없는 arm(자세 카드·알림음·음성 안내·휴식 제안)을 골랐고 게이트가 자동이면 같은 `kind`(`posture_suggest`·`break_suggest`·`env_suggest`)에 `auto: true`, `options: ["dismiss"]`, `arm`, `arm_label`, `channel`(display|sound|voice), `explanation`, `text`, `speak`/`chime`(스피커 명령도 보냈는지), `request_id` 끝이 `-arm` 으로 나간다 — 수락·거절 없이 보여 주기만 한다(응답은 선택, FSM·ESM 에 안 들어감). 같은 때 channel voice 면 `control/cmd {target_id: speaker, operation: speak, value: {text, lang: ko}}`, sound 면 `control/cmd {target_id: speaker, operation: chime, value: {pattern: single|double|triple, volume: low|mid|high}}`(volume = 사용자 sound_pref, off 면 소리 명령 없음). 제안 게이트면 기존 제안 카드가 arm 을 싣고 가고 소리 명령만 더해진다. 기기 없는 arm 을 골랐을 때 control.yaml 기본 램프는 나가지 않는다(기본 램프는 정책 결정이 없을 때만).
`kind: rest_notice`(2026-10-26, 안내 — FSM 이 REST 에 들어가면 허브가 보낸다. `prompt_code: rest_now`, `options: ["dismiss"]`, `reason`: `accepted`(휴식 제안 수락) | `asleep`(아직 졸음·엎드림) | `escalated`(개입 실패 뒤 강제) | `reminder`(REST 가 길어짐), `cause`, `text`(한국어 안내 문구), `minutes`·`rest_min_sec`(휴식 길이), `speak`(스피커 명령도 보냈는지), `variant`(default|study), `expires_in_s`. 응답은 선택: `feedback/user {verdict: dismiss|timeout, request_id}` — FSM·ESM 에 들어가지 않는다. 같은 때 `control/cmd` 로 `{target_id: speaker, operation: speak, value: {text, lang: ko}, origin_state: REST, gate: notice}` 를 보내고(사용자 sound_pref off 면 안 보냄), 앱은 Pi 5 스피커(TTS)로 읽고 `control/result` 를 돌려준다. 설정 `control.yaml rest_notice:`),
`kind: vent_suggest`, `prompt_code: ventilate_window`, `minutes` = `ventilate_minutes`(창문 열 시간, personalize.yaml `ventilation:` 계산),
`why`(예 `["co2_rule","co2_avg=1180","windy"]`), `explanation`, `arm: ventilate_suggest`, `arm_label: 창문 환기`, `cause: environment`, `source: co2_window`, `expires_in_s: 180`.
FSM 이 `ACTION_*` 이거나 다른 카드가 떠 있으면 내지 않는다. 응답은 FSM·제어로 가지 않는다(기기 명령 없음). 수락 = 창문을 열었다고 보고
권유 시간 동안 다시 묻지 않는다(창문 센서 없음). 무응답 20 분·거절 40 분 뒤 재시도. 상태는 `state/phase` 의 `sensor_summary.co2_window`.

### `deskmate/control/cmd`

data-spec §10 `control_command` 필드 그대로(2026-09-16, `hub/deskmate_hub/control/`). 발행 조건은 `config/control.yaml`:
ACTION_ENV 진입 시 원인별 명령 목록, 게이트 auto 면 즉시 · suggest 면 `feedback/user` accept 후 · none 이면 발행 안 함.
같은 target/operation 은 `cooldown_sec` 안에 재발행하지 않고, `irreversible_operations` 는 자동 실행하지 않는다.

```json
{
  "schema_version": "1.0", "ts": 1769000002.1, "node": "hub",
  "data": {
    "command_id": "9f3c1a2b7d4e",          // 멱등 키. result 가 이 값으로 돌아온다
    "target_id": "vent_fan",               // vent_fan | desk_lamp | air_purifier | plug_1 (control.yaml)
                                           // air_purifier: set_mode off|auto|low|mid|high (auto = 자체 PM 자동, high = 3단계)
    "operation": "set_power", "value": "on",
    "origin_state": "ACTION_ENV", "cause": "environment",
    "gate": "auto",                        // auto | suggest(수락 후) | undo(자동 실행 뒤 거절 시 되돌리기)
    "requires_confirmation": false,        // 비가역 동작만 true — 어댑터는 이 명령을 사용자 확인 없이 실행하면 안 된다
    "expires_ts_ms": 1769000017100         // 이 시각까지 결과가 없으면 hub 는 timeout 처리
  }
}
```

### `deskmate/control/result`

```json
{
  "schema_version": "1.0", "ts": 1769000002.7, "node": "mock-plug",
  "data": {
    "command_id": "9f3c1a2b7d4e",
    "status": "succeeded",                 // accepted | executing | succeeded | failed | timeout | cancelled
    "actual_value": "on", "error_code": null, "error_message": null,
    "completed_ts_ms": 1769000002700
  }
}
```

모든 명령이 종료(succeeded/failed/timeout)되거나 제안이 만료(`suggest_timeout_sec`)되면 hub 는 `frame.action_done=true` 로
FSM 을 MONITOR 로 보낸다. `state/phase.sensor_summary.control` 에 진행 중 에피소드(명령·상태)가 실린다.
기본 비활성인 `environment_control.yaml` 정책을 Mock 시험에서 활성화한 경우에만
`state/phase.data.sensor_summary.control.execution_mode`가 `"mock"`으로 추가된다.
이는 합성 제어 결과를 실측 기기 동작과 구분하기 위한 선택 필드다. 기존 cmd/result 필드,
게이트·수락/거절·undo 계약과 정책 비활성 상태의 payload는 유지한다.

통합 검증에서 `sensor_summary.control`에 선택적 진단 필드를 보강했다:
`commands[*].command_id/error_code`, 명령 없는 사용자 행동 제안 `advice`, 비활성 에피소드의
직전 결과 `last_episode`. `advice.kind=manual_ventilation`은 기기 제어 성공을 뜻하지 않는다.
`MONITOR`는 실행 성공과 무관하게 에피소드가 종료됐다는 뜻이며 commands의 실제 status로 결과를 판단한다.
새 정책의 제안 수락은 현재 interaction/request의 정확한 request_id가 필요하다.
기존 cmd/result topic·필드와 정책 OFF의 legacy HTTP feedback 경로는 유지한다.

어댑터가 없을 때의 리허설: `python tools/mock_plug.py --broker <ip>` (결과 응답 + `deskmate/control/state/<target>` retain).

Pi 5 앱은 `desk_lamp`의 `adjust_brightness`(`value: {"percent": ±정수}` — **지금 밝기 대비 %**, 2026-10-25. 앱이 지금 원시 밝기로
계산·상하한을 정하고 결과에 `previous`·`actual`(원시 0~255)을 싣는다; value 에 `min_raw` 가 있으면 결과 = max(계산값, min_raw)), `set_brightness`(iLink 원시 밝기 정수 **0~255**, 허브의
되돌리기가 `previous` 로 보낸다)와 `set_power`(`on`/`off`)를 실행하고 `display-control` 노드의 결과를 QoS 1·retain 없음으로
돌려준다. 기본 설정은 환경 +30 %, 졸음 +60 %, 개인화 arm ±50 % 다. 다른 대상은 해당 어댑터가
처리하며, 앱이 환기팬 등의 성공 응답을 대신 만들지 않는다.

`vent_fan` 은 Pico 2 W 블루투스 선풍기(`firmware/pico2w_ble_fan/`)다. **Pi 5 앱의 블루투스 탭에서 `DESKMATE-FAN` 을
선풍기로 고르면 앱이 vent_fan 명령을 맡는다** — 램프와 같은 문(자동 피드백 스위치·gate·확인 필요·만료·retain·중복)을 거쳐
GATT로 쓴 뒤 유효한 선풍기 상태 특성을 읽고 요청과 일치할 때만 `succeeded`,
`confirmation=state_readback`, 읽은 `actual_value`를 돌려준다. 상태가 없거나 잘못되면
`executing`/`confirmation=transport_only`/`device_state_unconfirmed`이며 `actual_value`와 완료 시각을 만들지 않는다.
유효한 상태가 요청과 다르면 `failed`/`state_mismatch`와 읽은 값을 반환한다. 받는 operation: `set_power`(on|off) · `set_level`(0~3, 풍량) ·
`set_speed`(0~100) · `set_mode`(normal|natural) · `set_timer`(초). 선풍기를 고르지 않았으면 앱은 vent_fan 에 대답하지 않고,
그때는 PC 의 `python tools/ble_fan.py bridge --broker <ip>` 가 같은 문으로 맡을 수 있다(`ble-fan` 노드). **둘을 같이 띄우지 않는다** —
블루투스 연결이 하나뿐이라 한쪽은 실패한다. FSM 없이 경로만 확인하려면 `python tools/send_control.py --broker <ip> vent_fan set_level 3`.

앱의 자동 피드백 스위치가 꺼졌거나 램프가 미설정·실패·만료이면 `failed`와 `error_code`를
응답한다. `requires_confirmation=true` 명령과 지원하지 않는 동작은 실행하지 않는다.
retain으로 들어온 명령은 무시한다. 최근 128개 `command_id`의 처리 결과(진행 중 포함)를
메모리에 보관해 QoS 1 중복 수신 시 BLE를 다시 쓰지 않고 같은 결과를 재발행한다.
`executing`+`confirmation=transport_only`는 GATT 전송 완료만 의미한다. 상태 확인이 없으므로
`succeeded`/`actual_value`를 보고하지 않는다(아래 2026-10-19 보완 계약 참고).

제안 명령의 `expires_ts_ms`는 **수락 후 발행 시각 + result_timeout_sec**로 정한다.
카드가 30초 동안 대기했다고 수락 즉시 만료되는 일이 없어야 한다. 앱은 큐 대기·재연결
뒤에도 만료를 확인한다. PC·Pi 4·Pi 5 시계 동기화가 필요하다.

### `deskmate/feedback/user`

```json
{
  "schema_version": "1.0", "ts": 1769000060.0,
  "node": "display", "boot_id": "991be831", "seq": 19,
  "data": {
    "request_id": "session-12-q-3",
    "verdict": "correct",       // accept | reject | correct | timeout
    "corrected_state": "FOCUS_PC",
    "response_ms": 4200
  }
}
```

`accept`/`reject` 는 현재 질문 ID를 확인하고 처리한다. 자동 실행 알림의 되돌리기 `reject` 는
`request_id` 가 없거나 `atlas-display` 일 수 있으며, 실행 뒤 `control.yaml` 의 `undo_window_sec` 안에서만
되돌린다. `timeout` 은 현재 질문 ID가 맞을 때만 질문을 해제하고 무응답 라벨을 남기며, 대기 중인 제안 제어는
실행 없이 바로 만료한다(화면에 없는 제안을 `suggest_timeout_sec` 까지 붙들지 않는다). `correct` 는 질문이 없어도 받을 수 있으며
`corrected_state` 는 `FOCUS_PC`·`FATIGUE`·`REST`·`IDLE` 중 하나여야 한다. 정정은 평가용 라벨로만
저장하고 FSM 판정이나 제어 명령을 바꾸지 않는다. 선택 필드 `response_ms` 는 응답까지 걸린 밀리초다.
라벨은 hub 로컬 JSONL에 기록하며 별도 MQTT 토픽으로 발행하지 않는다.
Pi 5 앱의 프리뷰(개발자) 탭은 같은 메시지에 선택 필드 `note`(문자열, 200자 이내, 사용자가 적은 짧은 메모)를 더할 수 있다.
키 값·센서 원본은 싣지 않으며, hub 는 모르는 필드를 무시해도 된다(라벨 기록에 같이 남기는 것은 선택).

조명 직접 조절 보고(2026-10-25): 사용자가 앱의 조명 조절로 밝기를 바꾸면 앱이 `{"kind": "manual_adjust", "target_id": "desk_lamp",
"from": 180, "to": 90, "input": "app"}`(원시 0~255)를 같은 토픽에 보낸다. 허브 명령으로 생긴 변화는 보내지 않는다. 허브는 이 보고를
ESM·제어 승인에 쓰지 않고, 허브가 밝힌 뒤 `implicit.dim_window_min`(10 분) 안의 어둡게 하기를 그 개입의 음의 보상으로만 쓴다
([data-spec](data-spec.md#사용자-첫-설정직접-조절-보고-2026-10-25)).

## QoS · 보존

- 센서 스트림: QoS 0 (유실 허용, 고빈도)
- `sensor/weather` 는 QoS 0 + retain (주기가 길어 재시작 시 마지막 값을 바로 받는다. stale 판정은 hub 가 `fetched_at` 으로)
- `state/phase` · `display/message` · `session/report` · `interaction/request` · `control/cmd` · `feedback/user`: QoS 1
- `state/phase` 는 retain 을 켜서 디스플레이 재시작 시 즉시 현재 상태를 받는다
- `display/message` 는 retain 을 끈다. 이전 문구가 재시작 뒤 다시 표시되거나 broker에 남지 않게 한다.
- 노드는 재연결 시 지수 백오프(1s → 최대 30s)


### 선택적 개인화 관찰 진단

`deskmate/state/phase`의 `data.sensor_summary.personalization`은 개인화 활성화 시에만 추가한다.
`mode: observe`, `decision_source: fsm`, `status: warming_up|predicted|unavailable`을 포함한다.
정상 추론에는 label, confidence, probabilities(focus/fatigue/rest/idle), inference_ms를 추가한다.
모델 confidence는 기존 행동 게이트의 confidence를 대체하지 않는다.
기본 비활성에서는 기존 payload를 유지한다. 상세 계약은 `personalization-model-contract.md`를 따른다.

## 개입 사이클 결과 보완 (2026-10-06)

- interaction/request의 expires_in_s는 환경 제어의 경우 timers.focus_break_poll_sec와 control.suggest_timeout_sec 중 짧은 값이다. 다른 제안은 기존 FSM 설정을 따른다. 만료되면 hub가 timeout 라벨을 기록하며 늦게 도착한 수락·거절·중복 timeout은 같은 질문에 다시 적용하지 않는다.
- 판단은 hub 수신 시각을 기준으로 한다. MQTT payload에 새 timestamp 필드를 요구하지 않는다. tick 직전에 도착한 유효한 수락·결과가 다음 tick에서 만료로 바뀌지 않는다.
- control/cmd의 expires_ts_ms는 실제 명령 발행 시각 + result_timeout_sec이다. 결과가 기한 후 도착하면 성공으로 되살리지 않는다. 결과에 명시한 actual_value가 요청 값과 다르면 failed로 처리한다.
- session/report.data.control_results는 아래 집계를 추가한다. 기존 metrics와 화면 필드는 유지한다. 제어 성공과 회복 관측은 독립된 결과이다.

```json
"control_results": {
  "forward": {"total": 2, "executing": 0, "succeeded": 2, "failed": 0, "timeout": 0, "cancelled": 0},
  "undo": {"total": 0, "executing": 0, "succeeded": 0, "failed": 0, "timeout": 0, "cancelled": 0}
}
```

발행한 명령만 집계하며 제안 거절·무응답·쿨다운으로 생략한 명령은 total에 포함하지 않는다. forward는 원래 제어, undo는 되돌리기 명령이다. 기기별 동작 로그와 command_id는 추가하지 않는다. 종료된 에피소드의 되돌리기도 결과·타임아웃이 반영된다.

## 개인화 관리 요청 (2026-10-07)

기존 deskmate/feedback/user에 kind=personalization을 사용한다. 일반 accept/reject/correct/timeout과 별도로 처리하며 FSM 입력·ESM 라벨에 넣지 않는다. QoS 1, retain=false를 유지한다. 운영 ACL은 승인된 display만 이 요청을 발행하도록 제한해야 한다.

```json
{"schema_version":"1.0","node":"display","data":{"kind":"personalization","request_id":"privacy-display-1","action":"grant","confirmed":true,"policy_version":"approved-policy-version","hub_boot_id":"current-hub-boot-id"}}
```

허용 action은 grant, revoke, delete이다. revoke와 delete는 모두 사용을 중지하고 등록 파일·메모리를 초기화한다. 사용자 확인과 현재 hub_boot_id가 필요하다. grant는 승인된 policy_version이 일치해야 한다. 파일 경로를 요청에 넣으면 거부한다.

관리 기능이 켜져 있으면 state/phase.data.sensor_summary.privacy에 available, policy_ready, policy_version, consented, registered_model_files, hub_boot_id 및 최근 요청의 request_id/action/status/error를 넣는다. status는 succeeded 또는 failed이며 화면은 일치하는 request_id의 결과를 확인한다. error는 unavailable, policy_not_approved, stale_request, storage_error 중 하나이다. 관리 기능이 꺼져 있으면 기존 상태 출력을 유지한다.

상세 범위·설정·제한: [개인화 동의·삭제 흐름](personalization-privacy-flow.md).

## 온디바이스 학습 진단 (2026-10-08)

실행 중인 학습 job이 있고 실제 상태가 IDLE/END 밖이면 status=paused로 표시한다. 입력·판단·제어는 유지하고, 다시 유휴 상태가 되면 training으로 재개한다. Pi 5는 이 snapshot을 표시하며 연결 끊김이나 동의 철회 때 이전 개인 모델 상태를 현재처럼 표시하지 않는다. 서비스 시작 self-check는 stderr 진단만 사용하며 MQTT 명령·개인 표본을 새로 만들지 않는다.

ondevice.yaml이 활성화된 경우에만 `state/phase.data.sensor_summary.ondevice_learning`을 추가한다. `execution: local`, `decision_source: fsm`, `head_source: common|personal`, `samples`, `sessions`, `training_step`(job이 없으면 null)을 제공한다. status는 disabled, unavailable, awaiting_consent, awaiting_portable_model, collecting, training, paused, accepted, rejected, storage_error, head_load_failed이다. embedding·개인 head·표본·파일 경로는 MQTT로 보내지 않는다. 기본 비활성에서는 기존 출력과 FSM을 유지한다. [실행·학습 계약](ondevice-personalization.md)을 따른다.

### 환경 제안/결과의 표시 및 수락 바인딩 (2026-10-19)

기존 topic/schema 1.0은 유지하며, `env_suggest`의 `data.control_plan`을 선택 필드로 추가한다.
값은 같은 에피소드의 `sensor_summary.control` 스냅샷이다.

```json
{
  "request_id": "mock-hub-12", "kind": "env_suggest", "expires_in_s": 60,
  "control_plan": {
    "active": true, "execution_mode": "mock", "state": "ACTION_ENV",
    "cause": "environment", "gate": "suggest", "awaiting_user": true,
    "commands": [{"command_id": "mock-c1", "target": "mock_ac", "op": "set_power",
      "value": "on", "status": "accepted", "reason": "temperature_high", "error_code": null}],
    "advice": []
  }
}
```

- `commands[].reason`은 실제 planner rule(기존 planner는 cause)이다. UI는 target/op/value/reason을
  그대로 표시하며 env_flags에서 실행 동작을 추정하지 않는다. `advice`는 별도로 모두 표시한다.
- UI는 request_id/boot_id, 절대 만료(ts+expires_in_s), 최신 상태(30초, 미래 오차 2초 이내),
  gate=suggest/awaiting_user, mode 및 명령 ID/대상/op/value/reason/advice가 일치할 때만 수락한다.
  계획이 없는 구버전 ENV 카드나 연결이 끊긴 상태는 실행 수락을 차단한다. 기존 휴식/자세 카드는 유지한다.
- feedback/user는 기존 request_id+verdict를 사용한다. 재연결 전후 request_id를 다시 검사한다.
  `correct`는 제안을 소모하지 않는다. retain request/cmd는 재실행하지 않는다.
- `active=false`에서도 `last_episode`의 명령 상태·오류를 유지한다. `outcome=executed/undone`,
  MONITOR 진입, API accepted는 성공 증거가 아니다. Mock은 항상 "MOCK · 모의 제어"로 표시한다.
- Mock용 UART/FSM 종단 시험의 cmd에는 선택 필드 `execution_mode=mock`을 붙인다. Pi5 BLE는
  이 명령을 `cancelled/mock_command_blocked`로 차단한다. 일반 제어를 활성화하는 플래그가 아니다.
- undo cmd의 `undo_of_command_id`는 원래 명령을 가리킨다. Pi5는 마지막 램프 명령과 다르거나
  누락된 undo를 `cancelled/superseded_command`로 차단한다(구형 서버 undo는 안전하게 거절).

**BLE 우선순위와 확인 수준**: 사용자 직접 밝기 조작 > 명시적 환경 명령 > phase 피드백.
수동 조작은 프로세스 안의 대기 쓰기를 무효화하고, 자동 피드백을 명시적으로 OFF→ON 하기
전까지 자동/undo 쓰기를 차단한다. 환경 명령 후 MONITOR/RECOVERY phase는 램프를 덮어쓰지 않는다.
GATT 연결 이후, 각 프레임 쓰기 직전에 만료·권한·수동 변경을 재검사한다. 이미 전송된 프레임은
취소할 수 없으며 수동 쓰기가 뒤이어 적용된다. 실제 기기의 외부 버튼 조작 감지는 아직 없다.

기존 설명의 `succeeded=GATT 쓰기 완료`를 대체한다: BLE 쓰기만 완료되면
`executing`, `confirmation=transport_only`, `error_code=device_state_unconfirmed`를 반환하고
`actual_value`를 만들어 넣지 않는다. 상태 읽기/피드백이 없으면 Hub의 기존 timeout이 적용된다.
따라서 이를 실제 기기 성공으로 표시하지 않는다. 메모리 중복 억제는 프로세스 재시작을 넘지 않는다.

- `commands[].state_confirmed`는 일치하는 `actual_value`로 확인된 결과인지 나타낸다.
  이 필드 없는 legacy succeeded는 새 UI에서 '기기 상태 미확인'으로 남긴다.
  BLE executing의 completed_ts_ms도 null이다. MqttStateSource는 MQTT 3.1.1을 명시적으로 사용한다.

환경 결과 카드의 되돌리기에는 선택 필드 `feedback/user.data.undo_command_ids`에 표시 중인
에피소드의 command_id 목록을 넣는다(verdict=reject 유지). UI는 현재 상태/계획과 대조하고,
Hub도 새 pending 제안이 없고 현재/직전 에피소드의 ID 목록과 일치할 때만 처리한다.
이전 결과 카드의 늦은 터치가 새 에피소드의 명령을 되돌리는 것을 차단한다.
휴식/자세 카드의 기존 feedback 및 기존 client payload는 유지한다.

### 독립 Mock Gateway 실행

일반 Linux의 `deskmate_hub.control.gateway_service`는 기존 cmd/result QoS1 계약만 사용한다.
명시적 Mock 실행과 private journal이 필요하며, 등록된 `mock_*` target과
`data.execution_mode=mock`인 명령만 처리한다. 결과는 `node=device-gateway-mock`,
retain=false다. 재시작/재연결 시 journal의 결과를 다시 알릴 수 있지만 명령은 재실행하지 않는다.
기존 BLE/실기 controller의 target을 소유하지 않는다. 실행·인증·TLS·ACL·저장 위치는
[mock-device-gateway.md](mock-device-gateway.md)를 참조한다.

### main / PR #57 통합: BLE 결과 확인 수준

- GATT 쓰기 완료는 전송 반환이다. 현재 램프·선풍기 프로토콜에는 별도 command_id별 기기 수락 ACK가 없으므로 수락을 추정하지 않는다.
- 램프는 상태 읽기가 없어 `executing`/`transport_only`, completed_ts_ms=null로 유지하고 Hub timeout을 따른다.
- 선풍기는 유효한 상태 readback과 요청값 일치를 확인한 경우만 성공한다. 이는 기기가 보고한 설정 상태이며 모터 회전·실제 풍량의 물리 검증이 아니다.
- `vent_fan.set_timer`의 readback은 기존 Flutter 계약과 같이 요청값 대비 2초 차이까지 비교한다. 실제 읽은 초를 보내며 요청 초로 덮어쓰지 않는다. 다른 operation은 기존 값 비교를 유지한다.
- `succeeded`에 `confirmation=transport_only`가 함께 오면 Hub는 성공 종료하지 않고 executing/상태 미확인으로 처리한다. 기존 결과의 선택 필드가 없는 경우 하위 호환 검사를 유지한다.
- Mock 차단은 램프·선풍기 모두 적용한다. undo는 대상별 마지막 명령 ID와 일치해야 하며, 선풍기는 연결 대기 후 실제 쓰기 직전에도 만료·활성 권한·선택 장치를 재검사한다.

- 일반 DeviceGateway도 기존 Pi 5/BLE bridge의 canonical `desk_lamp`와 `vent_fan`을 소유하지 않는다. 미등록 실패 결과도 대신 발행하지 않으며 새 Gateway 기기는 별도 논리 target을 사용한다.

- 구버전 BLE controller가 confirmation 없이 succeeded/actual_value를 보내도 `desk_lamp`·`vent_fan`의 `state_confirmed`는 false다. legacy 명령은 기존 처리 응답 상태를 유지하되 UI는 미확인으로 표시하고, 새 정책 명령(policy_rule)은 executing/미확인으로 유지해 timeout을 적용한다.

## 피로 데이터 수집 (2026-10-20)

`deskmate/fatigue/label` 은 Pi 5 피로 기록 앱(`com.atlas.app.deskmate_fatigue`)이 보내는 학습 라벨이다. hub FSM 입력은 아니다 —
hub 는 이 토픽을 구독해 피로 점수의 세션 문맥과 프로필 보정에만 쓰고, 점수는 `state/phase.data.sensor_summary.fatigue`
(`{kss, kss_personal, window_s, n_ticks, model_id, valid, ts}`, 1분마다 갱신, 모델이 없으면 생략)로 낸다([fatigue-dataset.md](fatigue-dataset.md) §6).
QoS 1, retain 없음. 연결이 끊긴 동안의 기록은 앱이 순서대로 쌓아 두었다가 다시 보낸다(중복은 기록기가 거른다).

```json
{"schema_version":"1.0","ts":1792900000.0,"node":"display-fatigue","boot_id":"fatigue-1","seq":4,
 "data":{"event":"kss","session_id":"A-3f2k-1792899100000","profile_id":"A-3f2k","ts":1792900000.0,
         "kss":6,"prompt_ts":1792899992.0,"response_ms":8000}}
```

`event`: `session_start`(kss, sleep_hours_last_night, profile{chronotype, usual_sleep_h, work_type}) ·
`kss`(kss 1~9, prompt_ts, response_ms) · `sleepy` · `prompt_missed`(prompt_ts) · `session_end`(kss).
프로필 슬롯 이름(A~F)은 보내지 않고 `profile_id` 만 보낸다.
