# MQTT 토픽 규약

> **최우선 후보 초안:** MQTT 채택은 실기기 검증 전까지 미확정이다. MQTT/TCP JSON에는 별도 애플리케이션 CRC를 넣지 않으며, UART를 쓰는 구간의 binary frame만 CRC-16으로 검증한다.
> 논리 데이터 계약은 [`data-spec.md`](data-spec.md)를 우선하며, MQTT를 채택할 때 본 문서를 확정한다.

담당: 이민혁 · 변경 시 PR 로 이 문서를 함께 수정한다.

채택 시 브로커는 Raspberry Pi 4 에 두고, 페이로드는 JSON (UTF-8) 을 사용하는 안을 우선 검증한다.
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
| `deskmate/sensor/keystroke` | PC 수집기 | hub | 1Hz | 키 입력 타이밍 특징 |
| `deskmate/state/phase` | hub | display, control | 상태 변화 시 | 추론 결과 + 신뢰도 |
| `deskmate/interaction/request` | hub | display | 이벤트 | 불확실한 판정의 사용자 확인 질문 |
| `deskmate/control/cmd` | hub | control | 이벤트 | 기기 제어 명령 |
| `deskmate/feedback/user` | display | hub | 이벤트 | 사용자 수락 · 정정 |
| `deskmate/health/<node>` | 전 장치 | hub | 0.1Hz | 생존 신호 · RSSI · 재연결 카운트 |

## 페이로드 예시

### `deskmate/sensor/tof/<node>`

ToF 원본 54×42 배열은 전송하지 않는다. 운영 경로는 특징값만 보내며, 별도 디버그 topic에서만 축소 depth map을 최대 2Hz로 허용한다.

```json
{
  "schema_version": "1.0", "ts": 1769000000.123,
  "node": "tof-adapter", "boot_id": "7f2a91c4", "seq": 1042,
  "data": {
  "present": true,
  "posture": "upright",        // upright | slouch | lean_back | away
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
    "present": true, "motion_state": "still", "motion_level": 12,
    "resp_bpm": 15, "resp_valid": true,
    "heart_bpm": null, "heart_valid": false, "valid": true
  }
}
```

### `deskmate/sensor/env/<node>`

```json
{
  "schema_version": "1.0", "ts": 1769000000.5,
  "node": "esp32-b", "boot_id": "315ae820", "seq": 91,
  "data": {
    "co2_ppm": 812, "temp_c": 26.4, "humidity_pct": 48.2, "lux": 310,
    "co2_trend_ppm_5m": 74, "lux_trend_5m": -12,
    "co2_valid": true, "temp_valid": true, "humidity_valid": true, "lux_valid": true
  }
}
```

### `deskmate/sensor/keystroke`

키 값은 절대 포함하지 않는다. 타이밍 통계만 보낸다.

```json
{
  "schema_version": "1.0", "ts": 1769000001.0,
  "node": "pc-collector", "boot_id": "177ae911", "seq": 44,
  "data": {
  "window_s": 60,
  "event_count": 184,
  "dwell_mean_ms": 92.4, "dwell_std_ms": 21.8,
  "flight_mean_ms": 148.2, "flight_std_ms": 63.5,
  "idle_ratio": 0.18,          // 입력 공백 비율
  "correction_rate": 0.07,     // 백스페이스 빈도
  "valid": true
  }
}
```

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
  "source": "fsm",             // fsm | classifier | fused
  "gate": "auto",              // auto | suggest | none
  "cause": "environment",
  "reasons": ["posture_stable", "typing_rhythm_slow", "co2_rising"]
  }
}
```

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

### `deskmate/control/cmd`

```json
{
  "schema_version": "1.0", "ts": 1769000002.1,
  "node": "hub", "boot_id": "a8021bf0", "seq": 203,
  "data": {
  "target": "desk_lamp",       // desk_lamp | vent_fan | air_purifier | plug_1
  "cmd": "set_brightness", "value": 70,
  "origin": "phase:fatigue"
  }
}
```

### `deskmate/feedback/user`

```json
{
  "schema_version": "1.0", "ts": 1769000060.0,
  "node": "display", "boot_id": "991be831", "seq": 19,
  "data": {
    "request_id": "session-12-q-3",
    "verdict": "correct",       // accept | reject | correct | timeout
    "answer": "rhythm",
    "corrected_state": "FOCUS_PC",
    "response_ms": 4200
  }
}
```

## QoS · 보존

- 센서 스트림: QoS 0 (유실 허용, 고빈도)
- `state/phase` · `interaction/request` · `control/cmd` · `feedback/user`: QoS 1
- `state/phase` 는 retain 을 켜서 디스플레이 재시작 시 즉시 현재 상태를 받는다
- 노드는 재연결 시 지수 백오프(1s → 최대 30s)
