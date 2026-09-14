# DESKMATE 개발 브리핑

> 기준일: 2026-09-14. 이 문서는 작업을 시작할 때 읽는 프로젝트 요약이다.
> 세부 계약은 아래의 원본 명세를 우선한다.

## 1. 현재 제품과 구성

DESKMATE는 카메라·상시 음성·키 내용 수집 없이, 로컬 센서 특징으로 책상 위
집중·피로 상태를 추정하고 제안 또는 가역 제어를 제공하는 시스템이다.

```text
ESP32 (mmWave·환경) ── UART2 / COBS + CRC-16 ──> Pi 4 Hub
                                                      │ MQTT (LAN, TCP 1883)
PC 키스트로크 특징 ─────────────── MQTT ─────────────┤
                                                      └────────────> Pi 5 Atlas UI
ToF VL53L9CX ── 경로 결정 전 (Pi 4 CSI-2 또는 ESP32 I2C)
```

| 계층 | 장치 | 책임 | 저장소 |
|---|---|---|---|
| 센싱 | ESP32 | mmWave·환경 수집, UART2 전송 | `firmware/` |
| 수집 | PC | 키 내용 없는 입력 특징 수집 | `collector/` |
| 추론·제어 | Raspberry Pi 4 | UART 디코드, 특징 통합, FSM, MQTT broker/client, 제어 판단 | `hub/` |
| 표시 | Raspberry Pi 5 | Atlas Flutter UI, 터치 피드백, 음성·화면 안내 | `display/` |
| 학습 | PC | 오프라인 검증·TFLite 변환 | `ml/` |

Pi 4는 ATLAS Headless native-service IPK, Pi 5는 Atlas Flutter IPK로 배포한다.
Docker는 개발 PC의 빌드 환경일 뿐 보드에서 실행하지 않는다. Pi 4↔Pi 5의 제품
통신은 MQTT이며, HTTP 8765은 개발 fallback이다. Node-RED는 관찰·주입용 도구이며
제품 실행 경로의 필수 의존성이 아니다.

## 2. 이미 정한 사항

- ToF는 VL53L9CX(54×42 zone)만 대상으로 한다. 제품 처리·전송은 특징값만 하며,
  raw depth map은 명시적 디버그 UI에서 최대 2 Hz만 허용한다.
- ToF 자세는 온보드 기하 특징 7종을 우선하고, V2V-PoseNet은 PC 오프라인 검증용이다.
- ESP32→Pi 4는 UART2(ESP32 GPIO25/26, Pi 4 GPIO15/14, 공통 GND), 115200 8N1,
  COBS + `0x00` 구분자 + CRC-16/CCITT-FALSE다. UART TYPE과 payload는 아직 확정 전이다.
- Pi 4↔Pi 5는 유선 LAN MQTT(TCP 1883), SSH 22를 병행한다. DHCP 주소를 문서의 고정값으로
  취급하지 않는다.
- FSM은 TFLite가 없어도 완전히 동작해야 한다. 상태·전이·가중치·임계값은
  `hub/deskmate_hub/config/*.yaml`에만 둔다. 신뢰도 계산 주기는 10초다.
- mmWave는 재실·움직임 중심의 보조 신호다. 호흡은 중앙값·기준선 대비 “소실 여부”만
  증거로 쓸 수 있으며, HRV·순간 심박·자세 판정에 쓰지 않는다.
- 키스트로크는 dwell/flight/idle/correction 등 집계 특징만 쓴다. 미입력은
  `typing_active=false`, `idle_ratio=1.0`으로 표현한다.
- 환경값은 SCD41(CO₂), DHT22(온습도), BH1750(조도)의 단일 센서 측정값이며 보정값을
  만들어 넣지 않는다.
- Pi 5 디스플레이는 교체 후 터치가 정상이다. 앱 자동 시작과 실기 release 재검증은 남았다.

## 3. 아직 결정하지 말아야 할 사항

| 항목 | 현재 상태 |
|---|---|
| UART TYPE·mmWave payload·CRC test vector | W1에서 `data-spec.md`로 확정 |
| `C_focus`의 부호 | 현재 구현의 “큰 값 = 집중 증거”를 임의 변경하지 않음 |
| ToF 연결 경로 | Pi 4 CSI-2(Path A) / ESP32 I2C binning(Path B) 중 spike 후 결정 |
| 개인화 저장소·동의 | 로컬 opt-in 정책과 저장 방식을 결정 전에는 확장하지 않음 |
| 스마트 플러그·ThinQ 제어 모델 | 기본은 스마트 플러그 기반 가역 제어, 실기기 모델은 미선정 |

## 4. 비협상 제약

- 키 내용, 카메라 영상, 상시 음성, 운영 중 ToF raw 배열을 수집·전송·저장하지 않는다.
- 자격증명·토큰·개인 로그·데이터셋·학습 모델·SDK·빌드 산출물을 커밋하지 않는다.
- 불확실하거나 서로 충돌하는 신호는 보수적으로 처리하고 UI에 이유를 보인다.
- 전원 차단 등 비가역 제어는 명시적 사용자 확인 없이 실행하지 않는다.
- `reference/raspberrypi/`의 제공 샘플은 직접 수정하지 않는다.
- MQTT topic/payload를 바꾸면 `mqtt-topics.md`, FSM 계약을 바꾸면 `fsm-spec.md`와 테스트를
  같은 변경에 갱신한다.

## 5. 문서 지도

| 목적 | 기준 문서 |
|---|---|
| 기능 범위·수용 기준 | [`requirements-spec.md`](requirements-spec.md) |
| 센서 특징·정규화·통합 계약 | [`data-spec.md`](data-spec.md) |
| FSM 상태·전이·점수 | [`fsm-spec.md`](fsm-spec.md) |
| MQTT topic·payload | [`mqtt-topics.md`](mqtt-topics.md) |
| 현재 상태·우선순위·주간 계획 | [`roadmap.md`](roadmap.md) |
| 배선·센서·BOM | [`hardware.md`](hardware.md) |
| Pi 5 IPK 빌드·배포 | [`atlas-build-handoff.md`](atlas-build-handoff.md) |
| 협업 규칙·제출물 | [`development-rules.md`](development-rules.md), [`submission.md`](submission.md) |

계약 문서끼리 충돌하면 프라이버시·안전 제약, 실제 `SensorFrame`/`TickResult` 구현,
데이터 계약, 전송 계약 순으로 확인하고 결정을 기록한다.
