# firmware — ESP32 센서 말단 노드

담당: 이민혁 (펌웨어 · MQTT) · 김태환 (센서 인터페이싱 · ToF 전처리)

책상 주변에 분산 배치되어 센서를 I2C/SPI 로 읽고, 노드 단에서 1차 전처리
(이동평균 · 이상치 제거) 후 Wi-Fi 로 MQTT 발행한다.

## 원칙

- **ToF raw 8×8 배열을 발행하지 않는다.** 노드에서 특징값(재실 · 자세 · 모션 · 호흡)까지
  뽑아서 보낸다. raw 전송은 Wi-Fi 대역폭과 허브 부하를 모두 낭비한다.
- 부팅 시 NTP 동기화. 모든 페이로드에 `ts` 를 넣는다.
- Wi-Fi · MQTT 끊김 시 지수 백오프 재연결 (1s → 최대 30s), `health` 토픽으로 보고.

## 구조

```
esp32_sensor_node/
├── platformio.ini
├── include/          설정 헤더 (Wi-Fi · 브로커 · 노드 ID · 임계값)
└── src/
    ├── main.cpp
    ├── sensors/      vl53l5cx / scd41 / bh1750 드라이버 래퍼
    ├── preprocess/   이동평균 · 이상치 제거 · 자세 판정 · 호흡 추출
    └── net/          wifi · mqtt · ntp
```

## 개발 환경

PlatformIO (Arduino 프레임워크) 또는 ESP-IDF.
`platformio.ini` 에 보드 · 라이브러리 의존성을 고정한다.

Wi-Fi SSID · 비밀번호 · 브로커 주소는 커밋하지 않는다.
`include/secrets.h.example` 를 복사해 `include/secrets.h` 로 쓴다.

## 토픽

발행 토픽과 페이로드 스키마는 [`docs/mqtt-topics.md`](../docs/mqtt-topics.md) 참조.
