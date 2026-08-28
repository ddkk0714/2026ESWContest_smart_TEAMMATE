# DESKMATE 데이터 명세서

기준 버전: `1.0` · 상세 토픽과 QoS는 [`mqtt-topics.md`](mqtt-topics.md) 참조

## 1. 설계 원칙

- 단위는 필드명에 포함한다: `_mm`, `_bpm`, `_ppm`, `_c`, `_pct`, `_lux`, `_ms`, `_s`.
- 값 `0`과 결측은 다르다. 측정할 수 없는 값은 `null`이고 관련 `*_valid`은 `false`다.
- 원시 ToF 8×8 배열, 키 내용, 원시 키 타임스탬프는 운영 토픽에 올리지 않는다.
- hub는 `valid=false`이거나 신선도 한계를 넘은 신호를 점수 계산에서 제외하고 가중치 합을 재정규화한다.

## 2. MQTT 공통 envelope

```json
{
  "schema_version": "1.0",
  "ts": 1787922730.125,
  "node": "esp32-desk-a",
  "boot_id": "7f2a91c4",
  "seq": 1042,
  "crc16": "6B3F",
  "data": {}
}
```

| 필드 | 타입 | 필수 | 정의 |
|---|---|---|---|
| `schema_version` | string | Y | `major.minor`. major 불일치는 폐기, minor 상승은 알 수 없는 필드를 무시. |
| `ts` | number | Y | 샘플 생성 시각 Unix epoch seconds(UTC). 발행 시각이 아님. |
| `node` | string | Y | 설치 단위 고유 ID. 예: `esp32-desk-a`, `pc-collector`, `hub`. |
| `boot_id` | string | Y | 부팅 때 새로 생성하는 8자 hex. `seq` 리셋 판별용. |
| `seq` | integer | Y | 부팅 단위 uint32 단조 증가. |
| `crc16` | string/null | 센서 Y | 4자 대문자 hex. 아래 CRC 규칙. hub/display 출력은 `null` 허용. |
| `data` | object | Y | 토픽별 payload. |

### CRC 규칙

- 알고리즘: CRC-16/CCITT-FALSE (`poly=0x1021`, `init=0xFFFF`, `refin=false`, `refout=false`, `xorout=0x0000`).
- 입력: RFC 8785 JSON Canonicalization Scheme으로 직렬화한 `data` 객체의 UTF-8 bytes.
- hub는 CRC 불일치 패킷을 FSM에 전달하지 않고 `crc_error` 카운터를 증가시킨다.
- Node-RED는 센서 토픽을 관찰만 하며 payload를 재발행하지 않는다. 재발행이 필요하면 `crc16`을 다시 계산한다.

### 순서·시간 규칙

- 동일 `node+boot_id`에서 `seq` 중복은 폐기한다. 간격이 1보다 크면 유실 건수를 로깅한다.
- `ts`가 hub 시각보다 5초 이상 미래이거나 토픽별 freshness를 넘으면 FSM 입력에서 제외한다.
- ESP32·Pi·PC는 NTP를 사용한다. NTP 미동기 상태는 health에 보고한다.

## 3. 센서 특성과 전처리 경계

| 센서 | 정체/역할 | 연결 | 노드 샘플링 | MQTT 발행 | 주요 전처리 |
|---|---|---|---|---|---|
| VL53L5CX | 8×8 ToF; 재실·자세·모션·노딩 | I2C | 목표 15Hz | 1Hz 특징 | 무효 zone 제거, median, baseline 상대 거리, window 특징 |
| SEN0623 | DFRobot C1001 mmWave; 재실·정지/활동·움직임 크기·호흡/심박 보조 | UART 115200 | 1Hz | 1Hz | 범위 검증, median, 호흡/심박 valid 게이트 |
| SEN0536 | DFRobot SCD41 CO₂·온도·습도. **mmWave 아님** | I2C `0x62` | 5s periodic | 0.2Hz | data-ready 확인, 이상치, 5분 추세 |
| SZH-EK070 | BH1750 GY-302 조도. **mmWave 아님** | I2C | 2~5Hz | 0.2Hz | median, 거치 위치별 offset, 5분 추세 |
| PC collector | 키스트로크 타이밍 통계 | OS input event | event | 1Hz/60s window | 키 내용 제거, 통계량만 유지 |

SEN0623의 제조사 예제는 재실, `none/still/active` 움직임, 움직임 파라미터, 호흡수, 심박수를 제공한다. 호흡·심박은 센서를 흉부 정면 약 1.5m에 두는 제조사 조건을 만족할 때만 보조 신호로 쓴다. 제조사의 `sleep mode`를 책상 졸음 정답으로 간주하지 않고 실제 거치 환경에서 보정한다.

## 4. 토픽별 `data` 스키마

### 4.1 ToF 특징

Topic: `deskmate/sensor/tof/<node>` · freshness: 3s

| 필드 | 타입/단위 | 범위/열거 | 설명 |
|---|---|---|---|
| `present` | boolean | | 유효 zone의 재실 판정. |
| `posture` | string | `upright`, `lean_forward`, `lean_back`, `slouch`, `away`, `unknown` | baseline 상대 자세. |
| `motion_score` | number | 0..1 | window 내 zone 거리 변화율. |
| `head_delta_mm` | number/null | | baseline 대비 머리/상체 세로축 거리 변화. |
| `nod_rate_hz` | number/null | 0.. 2 | 반복 숙임 주기. 검출 불가 시 null. |
| `valid_zones` | integer | 0..64 | 유효 zone 수. |
| `valid` | boolean | | `valid_zones` 최소 요건과 범위 검증 결과. |

### 4.2 mmWave 특징

Topic: `deskmate/sensor/mmwave/<node>` · freshness: 3s

| 필드 | 타입/단위 | 범위/열거 | 설명 |
|---|---|---|---|
| `present` | boolean | | C1001 재실. |
| `motion_state` | string | `none`, `still`, `active`, `unknown` | 제조사 상태를 표준 열거로 변환. |
| `motion_level` | number/null | 0..100 | body movement parameter. |
| `resp_bpm` | number/null | 4..40 유효 초기값 | 호흡 보조 신호. |
| `resp_valid` | boolean | | 거치·재실·정지 조건 통과 여부. |
| `heart_bpm` | number/null | 35..220 유효 초기값 | 연구/보정용. MVP FSM 기본 가중치 0. |
| `heart_valid` | boolean | | 심박 사용 가능 여부. |
| `valid` | boolean | | UART frame·범위·신선도 종합. |

### 4.3 환경 특징

Topic: `deskmate/sensor/env/<node>` · freshness: 15s

| 필드 | 타입/단위 | 설명 |
|---|---|---|
| `co2_ppm` | number/null | SEN0536 CO₂. |
| `temp_c` | number/null | SEN0536 온도. |
| `humidity_pct` | number/null | SEN0536 상대습도. |
| `lux` | number/null | SZH-EK070 조도. |
| `co2_trend_ppm_5m` | number/null | 5분 전 대비 변화량. |
| `lux_trend_5m` | number/null | 5분 선형 기울기 또는 변화량. |
| `co2_valid`, `temp_valid`, `humidity_valid`, `lux_valid` | boolean | 센서별 유효성. |

SEN0536 제조사 명세의 측정 범위(CO₂ 0..40000ppm, -10..60°C, 0..100%RH)는 통신 범위 검증에 쓴다. 쾌적/피로 임계값은 이 명세서가 아니라 FSM YAML에 두고 실측 후 보정한다.

### 4.4 키스트로크 특징

Topic: `deskmate/sensor/keystroke` · freshness: 5s

| 필드 | 타입/단위 | 설명 |
|---|---|---|
| `window_s` | integer/s | 통계 window, 기본 60. |
| `event_count` | integer | window 내 키 이벤트 수. |
| `dwell_mean_ms`, `dwell_std_ms` | number/null/ms | 누름 유지시간 통계. |
| `flight_mean_ms`, `flight_std_ms` | number/null/ms | 키 간격 통계. |
| `idle_ratio`, `correction_rate` | number/null | 0..1. |
| `valid` | boolean | 최소 이벤트 수·권한·신선도 통과. |

### 4.5 hub 추론 결과

Topic: `deskmate/state/phase`

| 필드 | 타입 | 정의 |
|---|---|---|
| `fsm_state` | string | VER5 18상태 열거. 코드 `State.value`와 동일. |
| `phase` | string | UI용 `idle`, `start`, `focus`, `fatigue`, `recovery`, `end`. |
| `context` | string | `pc`, `mixed`, `npc`. |
| `c_focus`, `c_fatigue` | number | 0..1 이중 점수. |
| `confidence` | number | 게이트에 쓰는 최종 신뢰도. |
| `source` | string | `fsm`, `classifier`, `fused`. |
| `gate` | string | `auto`, `suggest`, `none`. |
| `cause` | string/null | `environment`, `posture`, `cognitive`. |
| `reasons` | array[string] | UI에 표시 가능한 표준 reason code. |
| `sensor_quality` | object | 센서별 `valid`, `age_ms`. |

### 4.6 불확실성 질문·피드백

hub → display: `deskmate/interaction/request`

```json
{
  "schema_version": "1.0", "ts": 1787922732.0, "node": "hub",
  "boot_id": "01ab22cd", "seq": 77, "crc16": null,
  "data": {
    "request_id": "session-12-q-3",
    "kind": "state_disambiguation",
    "prompt_code": "drowsy_or_rhythm",
    "options": ["drowsy", "rhythm", "unsure"],
    "expires_in_s": 30,
    "evidence": ["tof_nod_repeated", "mmwave_motion_active"]
  }
}
```

display → hub: `deskmate/feedback/user`

| 필드 | 타입 | 정의 |
|---|---|---|
| `request_id` | string | 질문과 응답 연결. |
| `verdict` | string | `accept`, `reject`, `correct`, `timeout`. |
| `answer` | string/null | 표준 선택지. 예: `drowsy`, `rhythm`, `unsure`. |
| `corrected_state` | string/null | `correct`일 때 VER5 상태. |
| `response_ms` | integer/null | 질문 표시부터 응답까지. |

`timeout`은 거절이 아니다. 자동 제어 근거로 쓰지 않고 별도 ESM 라벨로 저장한다.

## 5. 융합·불확실성 규칙

| 상황 | ToF | SEN0623 | 환경 | 처리 |
|---|---|---|---|---|
| 졸음 가능성 강함 | 반복 노딩 또는 slouch+정적 | still/모션 저하, 재실 유지 | 보조 | 피로 기여도 상승. 3분 지속 확인. |
| 리듬 타기 가능성 | 반복 노딩 | active/반복 모션 | 정상 | 자동 확정 금지, `drowsy_or_rhythm` 질문. |
| 환경성 피로 | 집중 저하 보조 | 정적 보조 | CO₂ 상승/조도 부적합 | `environment` 원인, 환기/조명 제안. |
| 신호 충돌 | 졸음 기여 | 활동 기여 | 정상 | `suggest` 이하로 낮추고 사용자 확인. |
| 센서 고장/결측 | invalid | valid 또는 반대 | 별도 | 가용 항만 재정규화. 단일 센서로 졸음 확정 금지. |

이 시스템의 출력은 의료 진단이 아니며 `drowsiness_possible`, `fatigue_suspect`처럼 가능성을 나타내는 라벨로 취급한다.

## 6. baseline·보정 절차

1. 거치 후 재실·I2C/UART·유효성을 확인한다.
2. `START`에서 최소 5분 동안 정상 자세, 키스트로크, mmWave 모션, 환경 분포를 수집한다.
3. median, MAD/IQR, 가용 비율을 baseline으로 저장하고 원시 개인 데이터는 폐기한다.
4. 자세·노딩·타이핑 저하 특징을 baseline 대비 0..1 기여도(`phi`, `delta`)로 변환한다.
5. 센서 위치, 의자/책상, 주 사용자가 바뀌면 baseline을 무효화한다.

## 7. 로깅·보존

- 기본 실험 로그: 공통 envelope + 전처리 특징 + FSM 결과 + 피드백.
- 저장 금지: ToF raw, 키 내용, 원시 키 이벤트, 음성, 비밀값.
- 개인화 로그 보존 기간과 삭제 UI가 확정되기 전에는 세션 단위 임시 로그만 사용한다.
- 데이터셋·로그는 Git에 커밋하지 않는다.

## 8. 참고 자료

- DFRobot SEN0623/C1001 제조사 예제: https://wiki.dfrobot.com/sen0623/docs/21573
- DFRobot SEN0536/SCD41 제조사 명세: https://wiki.dfrobot.com/sen0536/docs/21655
- SZH-EK070/BH1750 판매 정보: https://www.devicemart.co.kr/goods/view?no=1289977
