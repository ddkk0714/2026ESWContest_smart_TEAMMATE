# MQTT 토픽 규약

담당: 이민혁 · 변경 시 PR 로 이 문서를 함께 수정한다.

브로커는 Raspberry Pi 4 에 두고, 페이로드는 JSON (UTF-8) 을 사용한다.

## 공통 필드

모든 페이로드에 포함한다.

```json
{ "ts": 1769000000.123, "node": "esp32-a" }
```

- `ts` — Unix epoch (초, 소수점 포함). 노드는 부팅 시 NTP 동기화한다.
- `node` — 발행 장치 식별자

## 토픽 목록

| 토픽 | 발행 | 구독 | 주기 | 내용 |
|---|---|---|---|---|
| `deskmate/sensor/tof/<node>` | ESP32 | hub | 1Hz | ToF **특징값** (raw 8×8 아님) |
| `deskmate/sensor/env/<node>` | ESP32 | hub | 0.2Hz | CO₂ · 온습도 · 조도 |
| `deskmate/sensor/keystroke` | PC 수집기 | hub | 1Hz | 키 입력 타이밍 특징 |
| `deskmate/state/phase` | hub | display, control | 상태 변화 시 | 추론 결과 + 신뢰도 |
| `deskmate/control/cmd` | hub | control | 이벤트 | 기기 제어 명령 |
| `deskmate/feedback/user` | display | hub | 이벤트 | 사용자 수락 · 정정 |
| `deskmate/health/<node>` | 전 장치 | hub | 0.1Hz | 생존 신호 · RSSI · 재연결 카운트 |

## 페이로드 예시

### `deskmate/sensor/tof/<node>`

ToF raw 배열은 전송하지 않는다. 노드에서 전처리 후 특징값만 보낸다.

```json
{
  "ts": 1769000000.123, "node": "esp32-a",
  "present": true,
  "posture": "upright",        // upright | slouch | lean_back | away
  "motion": 0.42,              // 모션 변화율 0.0~1.0
  "valid_zones": 51,           // 유효 zone 개수 (전체 64)
  "resp_bpm": null,            // 호흡수 — 정지 구간에서만 유효, 아니면 null
  "resp_valid": false
}
```

### `deskmate/sensor/env/<node>`

```json
{
  "ts": 1769000000.5, "node": "esp32-b",
  "co2_ppm": 812, "temp_c": 26.4, "humidity": 48.2, "lux": 310
}
```

### `deskmate/sensor/keystroke`

키 값은 절대 포함하지 않는다. 타이밍 통계만 보낸다.

```json
{
  "ts": 1769000001.0, "node": "pc-collector",
  "window_s": 60,
  "dwell_mean_ms": 92.4, "dwell_std_ms": 21.8,
  "flight_mean_ms": 148.2, "flight_std_ms": 63.5,
  "idle_ratio": 0.18,          // 입력 공백 비율
  "correction_rate": 0.07      // 백스페이스 빈도
}
```

### `deskmate/state/phase`

```json
{
  "ts": 1769000002.0, "node": "hub",
  "phase": "focus",            // start | focus | fatigue | end
  "confidence": 0.81,
  "source": "fsm",             // fsm | classifier | fused
  "action": "auto",            // auto (자동 제어) | suggest (사용자 제안) | none
  "reason": ["posture_stable", "typing_rhythm_slow", "co2_rising"]
}
```

### `deskmate/control/cmd`

```json
{
  "ts": 1769000002.1, "node": "hub",
  "target": "desk_lamp",       // desk_lamp | vent_fan | air_purifier | plug_1
  "cmd": "set_brightness", "value": 70,
  "origin": "phase:fatigue"
}
```

### `deskmate/feedback/user`

```json
{
  "ts": 1769000060.0, "node": "display",
  "ref_phase": "fatigue",
  "verdict": "reject",         // accept | reject | correct
  "corrected_phase": "focus"   // verdict=correct 일 때만
}
```

## QoS · 보존

- 센서 스트림: QoS 0 (유실 허용, 고빈도)
- `state/phase` · `control/cmd` · `feedback/user`: QoS 1
- `state/phase` 는 retain 을 켜서 디스플레이 재시작 시 즉시 현재 상태를 받는다
- 노드는 재연결 시 지수 백오프(1s → 최대 30s)
