# 하드웨어 구성 · BOM

## 구성

| 계층 | 장치 | 역할 |
|---|---|---|
| 센싱 | ESP32 DevKit V1 (esp32dev) × 1 | mmWave·환경 센서 노드. 1차 전처리 후 **UART2** 로 Pi 4 전달 (도크 내부, 무선 아님) |
| 추론 | Raspberry Pi 4 Model B | 중앙 허브. AI Native OS Headless. UART 디코딩 + Mosquitto broker + 특징 융합 + FSM/TFLite + 제어 판단 |
| 출력 | Raspberry Pi 5 (8GB) | AI Native OS Video Profile 디스플레이 단말. Atlas Flutter 상태·제안·리포트·피드백 UI. Pi 4 와 이더넷 직결 |
| 학습 | PC | 모델 학습 → TFLite 변환 → Pi 4 배포. 키스트로크 수집기도 여기서 구동 |

> **일체형 Dock 구조(포스터 기준).** 화면부(Head 196×34×122)와 연산부(Dock 208×120×54)를 힌지로 연결한다.
> 한 본체 안이므로 **기기 내부 통신은 전부 유선**(UART·이더넷), 외부(PC 키스트로크·플러그·ThinQ)만 Wi-Fi 다.
> 발열이 큰 Pi 4 는 환경 센서와 격벽으로 분리한다(Pi 4 부하 시 60~80°C).

## 보드 간 배선 (2026-09-11 실측)

| 연결 | 핀 | 비고 |
|---|---|---|
| C1001 TX → ESP32 | GPIO16 (UART1 RX) | 115200 8N1 |
| C1001 RX ← ESP32 | GPIO17 (UART1 TX) | |
| C1001 VIN/GND | ESP32 VIN(5V) / 공통 GND | ESP32 는 PC USB 전원으로 C1001 에 5V 공급 |
| ESP32 UART2 TX → Pi 4 | GPIO25 → Pi 4 물리 10번(GPIO15 RX) | 교차 연결 |
| ESP32 UART2 RX ← Pi 4 | GPIO26 ← Pi 4 물리 8번(GPIO14 TX) | |
| 공통 GND | ESP32 GND → Pi 4 물리 6번, C1001 GND → Pi 4 물리 9번 | 세 장치 기준 전압 통일 |
| Pi 4 ↔ Pi 5 | RJ45 유선 LAN (DHCP 주소 확인) | MQTT 1883 + SSH 22 |

- Pi 4 와 ESP32 의 5V 는 직접 연결하지 않는다. 각 보드는 자기 전원을 쓴다.
- Pi 4 `/dev/serial0 → ttyS0`. 커널 콘솔·`serial-getty@ttyS0` 가 기본 활성이라 실험 중 `systemctl stop serial-getty@ttyS0.service` 로 중지했다.
  읽기 전용 rootfs 라 재부팅 시 원복 — 영구 설정은 ATLAS 공식 절차 확인 필요.
- ESP32 UART0 = 디버그·`log2file`, UART1 = C1001, UART2 = Pi 4 링크. ESP32 는 UART 3개라 채널 부족은 제약이 아니다.
- 실측 프레임: COBS 인코딩 + 끝 `0x00` + CRC-16/CCITT-FALSE, 약 26 B, 1 Hz. 목표 속도 460,800 bps 이상은 미검증.
- USB Ethernet Gadget 은 Pi 4·Pi 5 USB-C 가 전원 전용이라 불가.

> **3종 HW 유지 필수.** Pi 5 가 강력해도 Pi 4 의 허브 역할을 Pi 5 로 합치지 않는다.
> ESP32 · Pi 4 · Pi 5 3종 연동이 "3종 HW 연동 AI 가전" 가산점 요건이다.
> LG 기술교육 제공 구성은 Pi 5의 AI Native OS Video Profile, Pi 4B의 Headless Profile, ESP32이며 AI Native OS 사용은 필수다.

## 센서

| 센서 | 부품 | 인터페이스 | 용도 |
|---|---|---|---|
| ToF 54×42 | VL53L9CX | **미결(09-19 결정)** — Path A: Pi 4 MIPI CSI-2(풀해상도 30~100 Hz) / Path B: ESP32 I2C 1 MHz(풀 18~22 fps, 27×21 binning 30 Hz+) | 재실·자세·모션·노딩. 스켈레톤 트래킹은 오프라인 검증 |
| ToF 시험용 | VL53L0X | Pi 5 I2C `0x29` | 연결·I2C bring-up 전용, 제품 센서 아님 |
| mmWave | SEN0623 (DFRobot C1001, 60 GHz) | ESP32 UART1, 115200 8N1 | 재실·정지/활동·체동(0–100)·거리·호흡/심박 **보조**. 체동 중심 졸음 FSM, ToF 교차 검증 |
| CO₂ | SEN0536 (SCD41) | ESP32 I2C `0x62`, 5 s 주기 | **CO₂ 전용.** 자체 T/RH 출력은 쓰지 않는다(2026-09-14 확정: 보정 없음·단일 센서) |
| 온습도 | AM2302 / DHT22 | ESP32 단선 디지털, 약 2 s | **온습도 전용.** 갱신 주기·프레임 크기 데이터시트 미확인 |
| 조도 | SZH-EK070 (BH1750 GY-302) | ESP32 I2C, 120 ms 측정 | 조명 제어 · 환경 맥락 |
| 공기질 | ENS160 | I2C | (선택) 환경 맥락 |
| I2C 멀티플렉서 | TCA9548A | — | I2C 경로에서 동일 주소 센서가 2개 이상일 때 검토 |

**VL53L9CX 인터페이스 주의** — 데이터시트상 호스트는 MIPI CSI-2 / I3C(12.5 MHz) / I2C(1 MHz). **SPI 는 없다.**
Pi 4·Pi 5 는 I3C 미지원, ESP32 는 CSI-2·I3C 모두 불가(I2C 만). Pi 4 CSI-2 는 STEVAL-VL53L9 flex 로 ST 가 Raspberry Pi 연동을 명시.
정상 운영은 특징값만 사용하고 축소 depth map은 디버그/UI 모드에서 최대 2Hz로 제한한다.

**mmWave 실측 제약(별도 저장소 로그 기준)** — 심박 추정값은 정지 상태에서도 3 bpm 계단으로 흔들린다(86→126→81, 90 s).
HRV 불가, 내장 수면 판정·`inBed` 는 착석에서 무용, 거리는 약 12 cm 단위 양자화(정상 착석 47~59 cm, 노트북 가림 시 11 cm),
파형 필드는 부팅 덤프에서만. → 판정에는 체동 이동평균(τ=15 s)·심박 중앙값(120 s)·각성 기준선(τ=600 s)만 쓴다.

## 보유 장비 (2026-09-14)

- Raspberry Pi 4 Model B Rev 1.2 — ATLAS Headless, 유선 MAC `DC:A6:32:85:F3:72`, DHCP
- Raspberry Pi 5 Model B Rev 1.1 — ATLAS Platform 26.06.0, 호스트명 `atlas`, 유선 MAC `88:A2:9E:3C:CC:CA`, DHCP
- ESP32 DevKit V1 (WROOM, `esp32dev`)
- 7인치 터치 화면 — **2026-09-14 교체 완료, 터치 정상 동작.** 새 화면의 모델명·인터페이스(DSI/HDMI)·전원 경로는 기록 필요. 이전 화면(`0416:c168 TSTP MTouch`, `xhci-hcd.1` 사망)은 이력
- C1001 mmWave, VL53L0X(시험용). VL53L9CX·SCD41·BH1750·DHT22 는 구매 내역·보유 여부 확인 필요

## 미해결 하드웨어 항목

- [x] Pi 5 터치 — 화면 교체로 해결(2026-09-14). 새 화면 모델·배선 기록만 남음
- [ ] VL53L9CX 연결 경로 Path A/B — 09-19 spike
- [ ] ESP32 UART2 460,800 bps 이상 유실률
- [ ] 힌지 구간(10~15 cm) 배선 신호 무결성, I2C 풀업(4.7 kΩ → 1~2.2 kΩ) 조정
- [ ] Pi 4 발열 vs 환경 센서 격벽·흡기구 배치
- [ ] 스마트 플러그(로컬 제어 모델) 선정·구매

## BOM

동아리 지원금 10만원 — 즉시 사용 가능

| 제품 | 출처 | 가격 |
|---|---|---|
| ToF (VL53L9CX) | 구매 내역 확인 필요 | 확인 필요 |
| 온습도 (AM2302/DHT22) | 디바이스마트 | 5,230 |
| **소계** | | **VL53L9CX 구매 내역 확인 후 재산정** |

### 추가 선발주 권장

- SCD41 (CO₂ 전용) — 환기 제어 데모의 핵심. 온습도는 DHT22 가 맡는다
- BH1750 (조도)
- SEN0623 (C1001 mmWave) — ToF 자세·노딩 판정의 교차 검증
- Pi 5 27W PSU · 액티브 쿨러 (지원 장비에 없을 경우)
- 스마트 플러그 + LED 스탠드 / 환기팬 — **기본 제어 데모 라인**

## 제어 데모 라인

ThinQ 실기기 연동은 LAN · 플랫폼 접근 · NDA · 부스 물류 부담이 크다.
스마트 플러그 + LED 스탠드 / 환기팬을 **기본 제어 라인**으로 먼저 완성하고,
실제 ThinQ 연동은 **1개 기기만** 시연한다.
