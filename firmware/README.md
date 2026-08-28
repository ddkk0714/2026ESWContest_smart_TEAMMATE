# firmware — ESP32 센서 말단 노드

담당: 이민혁 (펌웨어 · MQTT) · 김태환 (센서 인터페이싱 · ToF 전처리)

책상 주변에 분산 배치되어 mmWave·환경 센서를 읽고 노드 단에서 1차 전처리
(이동평균·이상치 제거)한다. Pi 4 전달 방식은 UART/MQTT 후보 중 팀 결정 후 확정한다.
VL53L9CX는 Pi 4 MIPI CSI-2 직접 연결을 우선 검증하며, 실패할 때만 ESP32 I2C 축소 경로를 구현한다.

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

논리 스키마는 [`docs/data-spec.md`](../docs/data-spec.md), MQTT 후보 매핑은 [`docs/mqtt-topics.md`](../docs/mqtt-topics.md) 참조.
