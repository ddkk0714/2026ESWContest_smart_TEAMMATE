# firmware — ESP32 센서 말단 노드

담당: 이민혁 (펌웨어 · MQTT) · 김태환 (센서 인터페이싱 · ToF 전처리)

도크 내부에 1대 배치되어 mmWave·환경 센서를 읽고 노드 단에서 1차 전처리(이동평균·이상치 제거·졸음 FSM)한 뒤
**UART2 바이너리 프레임(COBS + CRC-16/CCITT-FALSE)** 으로 Pi 4 에 보낸다. 2026-09-11 에 C1001 → ESP32 → Pi 4 `/dev/serial0` 도달을 실측했다.
VL53L9CX 는 연결 경로(Path A: Pi 4 CSI-2 / Path B: ESP32 I2C 1 MHz binning)가 09-19 결정 대기이며, Path B 일 때만 이 노드에 붙는다.

> **현재 상태(2026-09-15):** `esp32_sensor_node/`에 PlatformIO 프로젝트, 공식 DFRobot C1001 라이브러리 기반
> `C1001Passive`, 5상태 `DrowsyDetector`, UART0(USB) JSON 출력이 들어왔다. 환경 센서는 MVP 스텁이며 5초마다
> 모든 값을 `null`, valid 플래그를 `false`로 출력한다. 별도 ESP32-mmWave 저장소의 UART2 COBS/CRC 소스는 이
> 작업 공간에 제공되지 않아 바이너리 송신 로직은 이관하지 못했다. TYPE 계약을 추측하지 않기 위해 UART2는 핀과
> baud 초기화까지만 유지하며 송신하지 않는다.

## 배선·UART 할당 (실측)

| 채널 | 용도 | 핀 |
|---|---|---|
| UART0 (`Serial`) | 디버그 모니터 · `log2file` 로깅 · 구간 마커 입력 | USB |
| UART1 (`Serial1`) | C1001 mmWave, 115200 8N1 | GPIO16 RX / GPIO17 TX |
| UART2 | Pi 4 링크, 115200 8N1 (목표 460,800+) | GPIO25 TX → Pi 4 GPIO15(물리 10) / GPIO26 RX ← Pi 4 GPIO14(물리 8) |

GPIO16/17 은 WROVER 계열에서 PSRAM 용이다. 현재 `esp32dev`(WROOM)라 무관하지만 보드를 바꾸면 핀을 옮긴다.
공통 GND 필수. Pi 4 와 5V 를 직접 연결하지 않는다. 업로드가 안 되면 BOOT 를 누른 채 EN 을 한 번 누른다(`Wrong boot mode` = 업로드 모드 진입 실패).

## 원칙

- **ToF 원본 54×42 배열을 운영 경로로 발행하지 않는다.** 대체 경로에서는 재실·자세·모션·노딩
  특징값을 만들고, 축소 depth map은 명시적 디버그/UI 모드에서만 최대 2Hz로 허용한다.
- 부팅 시 NTP 동기화. 모든 페이로드에 `ts` 를 넣는다.
- MQTT를 채택하면 Wi-Fi 끊김 시 지수 백오프 재연결(1s → 최대 30s)과 `health` 토픽을 사용한다.
- UART를 채택하면 binary frame에 CRC-16을 적용한다. MQTT/TCP JSON에는 별도 애플리케이션 CRC를 넣지 않는다.

## 구조

```
esp32_sensor_node/
├── platformio.ini
├── include/          설정 헤더 (Wi-Fi · 브로커 · 노드 ID · 임계값)
└── src/
    ├── main.cpp
    ├── sensors/      sen0623 / scd41 / bh1750 및 vl53l9cx 대체 경로 래퍼
    ├── preprocess/   이동평균 · 이상치 제거 · 특징 추출
    └── transport/    uart · mqtt 후보 adapter · ntp
```

## 개발 환경

PlatformIO (Arduino 프레임워크) 또는 ESP-IDF.
`platformio.ini` 에 보드 · 라이브러리 의존성을 고정한다.

Wi-Fi SSID · 비밀번호 · 브로커 주소는 커밋하지 않는다.
`include/secrets.h.example` 를 복사해 `include/secrets.h` 로 쓴다.

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
