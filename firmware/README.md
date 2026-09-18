# firmware — ESP32 센서 말단 노드

담당: 이민혁 (펌웨어 · MQTT) · 김태환 (센서 인터페이싱 · ToF 전처리)

도크 내부에 1대 배치되어 mmWave·환경 센서를 읽고 노드 단에서 1차 전처리(이동평균·이상치 제거·졸음 FSM)한 뒤
**UART2 바이너리 프레임(COBS + CRC-16/CCITT-FALSE)** 으로 Pi 4 에 보낸다. 2026-09-11 에 C1001 → ESP32 → Pi 4 `/dev/serial0` 도달을 실측했다.
VL53L9CX 는 연결 경로(Path A: Pi 4 CSI-2 / Path B: ESP32 I2C 1 MHz binning)가 09-19 결정 대기이며, Path B 일 때만 이 노드에 붙는다.

> **현재 상태(2026-09-18):** `esp32_sensor_node/`에 PlatformIO 프로젝트, 수신 전용 `C1001Passive` 파서,
> 5상태 `DrowsyDetector`, 환경 센서 실드라이버(SCD41·BH1750·DHT22), UART0(USB) JSON 출력이 들어왔다.
> `pio run -e esp32dev` 실빌드 통과(RAM 7.1% · Flash 23.2%). 실보드 UART 검증은 아직이다.
>
> **mmWave 경로(2026-09-18):** C1001 읽기를 벤더 폴링에서 **수신 전용 파서**로 바꿨다. 출처는
> [76EHwan/ESP32-mmWave](https://github.com/76EHwan/ESP32-mmWave) — 실제 G60SM1SY / R60A 로그로 맞춘
> 구현이다. 벤더 `getData()` 는 호출마다 수신 버퍼를 비우고 질의를 보낸 뒤 바이트당 delay 로 기다려서,
> 폴링 주기가 센서의 실제 갱신 시점과 어긋나 값이 통째로 빠졌다. 파서는 아무것도 버리지 않고
> (`poll()` 논블로킹), 능동 보고가 2초 이상 끊기면 질의로 폴백한다. 졸음 판정은 체동 스파이크의
> 부재를 주 신호로, 호흡 소실·심박 하락을 보조 증거로 써서 증거 개수에 따라 확정 시간을
> 3분/1분/25초로 줄인다. 임계값마다 어떤 실측 때문에 그 값인지 `DrowsyDetector.h` 에 적혀 있고,
> 호스트 유닛 테스트 12개(`pio test -e native`)가 그 계약을 붙잡는다.
>
> **2026-09-16 추가:** UART2 로 `docs/data-spec.md` §13.1 잠정 규약의 바이너리 프레임을 보낸다 —
> `transport/frame.cpp`(헤더·CRC-16/CCITT-FALSE·COBS·`0x00` 종료), `include/frame_types.h`(TYPE 0x20 mmWave 1 Hz ·
> 0x10 환경 0.2 Hz(스텁이면 valid_bits 0) · 0xF0 하트비트 1 Hz). 빌드 플래그 `-DDESKMATE_UART2_TX=1`(기본 켬),
> `-DDESKMATE_FIRMWARE_VERSION`. Python 코덱(`hub/deskmate_hub/ingest/uart_frame.py`)과 test vector 로 교차 검증했다.
> **`pio run` 실빌드와 실보드 UART 검증은 아직 안 했다.** 환경 센서 실제 드라이버(SCD41·BH1750·DHT22)는 미구현.

## 배선·UART 할당 (실측)

| 채널 | 용도 | 핀 |
|---|---|---|
| UART0 (`Serial`) | 디버그 모니터 · `log2file` 로깅 · 구간 마커 입력 | USB |
| UART1 (`Serial1`) | C1001 mmWave, 115200 8N1 | GPIO16 RX / GPIO17 TX |
| UART2 | Pi 4 링크, 115200 8N1 (목표 460,800+) | GPIO25 TX → Pi 4 GPIO15(물리 10) / GPIO26 RX ← Pi 4 GPIO14(물리 8) |

GPIO16/17 은 WROVER 계열에서 PSRAM 용이다. 현재 `esp32dev`(WROOM)라 무관하지만 보드를 바꾸면 핀을 옮긴다.
공통 GND 필수. Pi 4 와 5V 를 직접 연결하지 않는다. 업로드가 안 되면 BOOT 를 누른 채 EN 을 한 번 누른다(`Wrong boot mode` = 업로드 모드 진입 실패).

## 원칙

- **ToF 원본 54×42 배열을 운영 경로로 발행하지 않는다.** Path B 에서는 재실·자세·모션·노딩
  특징값을 만들고, 축소 depth map은 명시적 디버그/UI 모드에서만 최대 2Hz로 허용한다.
- 모든 프레임에 `SEQ`·`TS` 를 넣는다(유실 감지·Pi 4 슬라이딩 윈도 정렬).
- UART binary frame 에만 CRC-16 을 적용한다. Pi 4 이후 MQTT JSON 에는 별도 애플리케이션 CRC 를 넣지 않는다.
- mmWave 순간 호흡·심박값은 보내더라도 Pi 4 판정 근거가 아니다. 체동 이동평균(τ=15 s)·심박 중앙값(120 s)·각성 기준선(τ=600 s)·졸음 FSM state/evidence 가 계약 대상이다.

## 프레임 규약 (계약 확정 전 — `docs/data-spec.md` 가 우선)

```
[SOF 1B][VER 1B][TYPE 1B][SEQ 2B][LEN 2B][TS 4B][PAYLOAD N][CRC16 2B]   → COBS 인코딩 + 끝 0x00
```

| TYPE | 내용 | 주기 | 상태 |
|---|---|---|---|
| 0x01 | (디버그) binning depth map | ≤ 2 Hz | 예약. **실험 펌웨어가 mmWave 에 임시 사용 중 → 충돌, 재배정 필요** |
| 0x03 / 0x04 | ToF 특징 벡터 / 자세 클래스+신뢰도 | 30 Hz | Path B 시 |
| 0x10 | 환경 묶음 (SCD41 CO₂ 2B + DHT22 T 2B + RH 2B + BH1750 lux 2B = 8B). SCD41 T/RH 는 보내지 않음(보정 없음·단일 센서, 09-14 확정) | 0.2 Hz | 미구현 |
| **미배정** | mmWave 졸음 state + evidence + 요약값 (10~16 B) | 1 Hz | **TYPE 배정 필요** |
| 0xF0 | 상태/하트비트 | 1 Hz | |

## 구조 (목표)

```
esp32_sensor_node/
├── platformio.ini
├── include/          설정 헤더 (노드 ID · 핀 · 임계값)
└── src/
    ├── main.cpp
    ├── sensors/      c1001 (파서·DrowsyDetector) / scd41 / bh1750 / dht22 / vl53l9cx(Path B)
    ├── preprocess/   이동평균 · 이상치 제거 · 특징 추출
    └── transport/    uart2 프레이밍(COBS · CRC-16) · 하트비트
```

## 개발 환경

PlatformIO (Arduino 프레임워크) 또는 ESP-IDF.
`platformio.ini` 에 보드 · 라이브러리 의존성을 고정한다.

```bash
cd firmware/esp32_sensor_node
pio run  -e esp32dev   # 보드 빌드
pio test -e native     # 호스트 유닛 테스트 (졸음 판정 · 환경 센서 유효성/페이로드)
```

`env:native` 는 보드가 없어도 도는 순수 로직만 컴파일한다(`build_src_filter`). 판정기는
`update(C1001Passive&)` 만 `#ifdef ARDUINO` 로 갈라 두고 나머지는 Arduino 의존이 없으므로,
테스트가 개별 이벤트(`onBodyMove` · `onHeartRate` · `tick`)를 직접 넣어 시간 축을 만든다.
시연용으로 확정 시간만 줄이려면 `-DDESKMATE_DROWSY_HOLD_SCALE_PCT=10` — 임계값 검증에는
쓰면 안 된다. 심박 중앙값 창(120초)과 기준선 시정수(600초)는 이 배율을 따라가지 않는다.

ESP32 는 Pi 4 와 유선이므로 Wi-Fi 자격증명이 기본 경로에 필요 없다. 필요해지면 `include/secrets.h.example` 를 복사해 `include/secrets.h` 로 쓰고 커밋하지 않는다.

## 통신 계약

논리 스키마는 [`docs/data-spec.md`](../docs/data-spec.md), Pi 4 이후 MQTT 매핑은 [`docs/mqtt-topics.md`](../docs/mqtt-topics.md) 참조.
실측 기록: 노션 「ESP32–Pi4 UART 하드웨어 연결·실측 검증」, 「센서별 데이터·처리·전송 스펙」.

### MVP USB(UART0) JSON 라인

115200 8N1에서 한 줄에 JSON 객체 하나를 쓰고 `\n`으로 끝낸다. JSON 센서 줄은 반드시 `{"t":`로 시작하므로
같은 포트의 일반 디버그 로그와 함께 쓸 수 있다.

```json
{"t":"mmwave","ms":1234,"present":true,"motion_state":"still","motion_level":12,"distance_cm":87,"resp_bpm":15,"resp_valid":true,"heart_bpm":null,"heart_valid":false,"drowsy_state":"AWAKE","valid":true}
{"t":"env","ms":5000,"co2_ppm":null,"temp_c":null,"humidity_pct":null,"lux":null,"co2_valid":false,"temp_valid":false,"humidity_valid":false,"lux_valid":false}
```

- mmWave는 1 Hz, 환경은 0.2 Hz(5초 간격)다.
- `motion_state`는 `none|still|active`, `drowsy_state`는 `NOPERSON|NOLOCK|WARMUP|AWAKE|DROWSY`다.
- 유효하지 않거나 없는 수치는 `null`로 출력하고 해당 valid 플래그를 `false`로 둔다.
- warmup·정지 지속·motion level 임계값은 `platformio.ini`의 `build_flags`에서 조정한다.
- 빌드: `cd firmware/esp32_sensor_node; pio run`
- 업로드가 안 되면 BOOT를 누른 채 EN을 한 번 누른 뒤 업로드한다.
