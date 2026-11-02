# DESKMATE 개발 브리핑

> 기준일: 2026-11-02 (이전 09-29). 이 문서는 작업을 시작할 때 읽는 프로젝트 요약이다.
> 모듈 현황·결정사항은 [`../CLAUDE.md`](../CLAUDE.md) §3·§5 가 기준이고, 서술 수준의 기준은 개발완료보고서 v17 이다.
> 세부 계약은 아래의 원본 명세를 우선한다.

## 1. 현재 제품과 구성

DESKMATE는 카메라·상시 음성·키 내용 수집 없이, 로컬 센서 특징으로 책상 위
집중·피로 상태를 추정하고 제안 또는 가역 제어를 제공하는 시스템이다.

```text
ESP32 (ToF L8 ×2 · mmWave · 환경) ── USB 시리얼 / COBS + CRC-16 ──> Pi 4 Hub
                                                      │ MQTT (LAN, TCP 1883)
PC 키스트로크 특징 ─────────────── MQTT ─────────────┤
                                                      └────────────> Pi 5 Atlas UI
                                                                     └─ BLE·A2DP ─> 무드등 · 선풍기 · 스피커
ToF 자세: ESP32 가 센서별 원본 8×8 을 UART 0x05 로 Pi 4 에만 → Pi 4 CNN+GRU 추론 (hub/atlas-tof)
카메라는 제품 경로에 없다. camsvc·camtest 는 개발 앱, depth_teacher 는 오프라인 라벨링 도구다.
```

| 계층 | 장치 | 책임 | 저장소 |
|---|---|---|---|
| 센싱 | ESP32 | mmWave·환경 수집, UART2 전송 | `firmware/` |
| 수집 | PC | 키 내용 없는 입력 특징 수집 | `collector/` |
| 추론·제어 | Raspberry Pi 4 | UART 디코드, 특징 통합, FSM, MQTT broker/client, 제어 판단 | `hub/` |
| 표시 | Raspberry Pi 5 | Atlas Flutter UI, 터치 피드백, 음성·화면 안내 | `display/` |
| 공통 모델 준비 | PC | 최초 공통 CNN 학습·오프라인 검증·TFLite/portable 변환 | `ml/` |
| 운영 개인화 | Raspberry Pi 4 | 동의 후 개인 head 학습·검증·적용(10-08 구현, 실기 미검증) | `hub/deskmate_hub/personalization/` |

Pi 4는 ATLAS Headless native-service IPK, Pi 5는 Atlas Flutter IPK로 배포한다.
Docker는 개발 PC의 빌드 환경일 뿐 보드에서 실행하지 않는다. Pi 4↔Pi 5의 제품
통신은 MQTT이며, HTTP 8765은 개발 fallback이다. Node-RED는 관찰·주입용 도구이며
제품 실행 경로의 필수 의존성이 아니다.

## 2. 이미 정한 사항

- ToF 는 **VL53L8CX(8×8) 두 개**를 ESP32 I2C 에 붙인다(10-12 L9 포기, 10-16 실물 연결). VL53L9CX(54×42) 는 LDD 폴트로 끝났다.
- **ToF 데이터의 단위는 센서 하나의 8×8** 이다(10-17 표준). 두 센서는 바깥 8° 조준이라 시야가 대부분 겹쳐 16×8 스티칭은 한 장면이
  아니다. ESP32 는 원본 8×8 을 센서별 UART `0x05` 프레임으로 Pi 4 에만 보내고(MQTT·로그·화면엔 안 나감), Pi 4 모델은 센서별
  추론 → log-softmax 합 융합. 디버그 depth map 은 명시적 디버그 UI 에서 최대 2 Hz(`0x01`, 예약).
- 자세 모델은 Pi 4 에서 돈다(10-17). ESP32 의 규칙 추정기(0x03)는 비교·폴백. Pi 4 안의 배치(hub Python MLP / 별도 서비스
  `deskmate_tofposture` PostureNet)는 실측 성능으로 정한다 — `CLAUDE.md` §5 열린 항목 11.
- ESP32→Pi 4는 UART2(ESP32 GPIO25/26, Pi 4 GPIO15/14, 공통 GND), 115200 8N1,
  COBS + `0x00` 구분자 + CRC-16/CCITT-FALSE다. TYPE·payload 는 잠정안(§3)으로 구현·실수신 중이다.
- Pi 4↔Pi 5는 유선 LAN MQTT(TCP 1883), SSH 22를 병행한다. DHCP 주소를 문서의 고정값으로
  취급하지 않는다.
- 보드(ATLAS)는 루트 FS 읽기 전용·AppArmor·제한 Python(`_socket` 없음)이다. 보드에서 도는 것은
  전부 IPK 로 배포하고, hub 의 MQTT 는 C++ 네이티브 클라이언트가 맡는다. Python 은 stdin/stdout 라인만.
- 브로커는 Pi 4 정적 빌드 Mosquitto(`/data/share/deskmate/bin/pi4-broker-start.sh`, PR #18)다.
  자동 시작이 없어 재부팅 후 브로커 스크립트와 `hub/atlas/tools/pi4-hub-activate.sh` 를 한 번씩 실행한다.
- FSM은 TFLite가 없어도 완전히 동작해야 한다. 상태·전이·가중치·임계값은
  `hub/deskmate_hub/config/*.yaml`에만 둔다. 신뢰도 계산 주기는 10초다.
- mmWave는 재실·움직임 중심의 보조 신호다. 호흡은 중앙값·기준선 대비 “소실 여부”만
  증거로 쓸 수 있으며, HRV·순간 심박·자세 판정에 쓰지 않는다.
- 키스트로크는 dwell/flight/idle/correction 등 집계 특징만 쓴다. 미입력은
  `typing_active=false`, `idle_ratio=1.0`으로 표현한다.
- 환경값은 SCD41(CO₂), DHT22(온습도), BH1750(조도)의 단일 센서 측정값이며 보정값을
  만들어 넣지 않는다.
- Pi 5 디스플레이는 교체 후 터치가 정상이다. 앱 자동 시작은 아직 없다.
- **제어 기기는 iLink BLE 무드등 · Pico 2 W BLE 선풍기 · A2DP 스피커**다(스마트 플러그는 채택하지 않았다).
  LG ThinQ 공기청정기는 API 로 풍량 제어·공기질 읽기를 확인했고 FSM 연동은 예정이다.
- **블루투스 연결 규칙**: 스피커는 페어링하고, **램프·선풍기는 페어링하지 않는다**(설정 앱에서 연결해 두면 앱 Connect 가 거절된다).
  복원 순서는 램프 → 선풍기 → 스피커. 근거와 판별 기록은 `display/atlas/ui_demo_app/README.md` 맨 위.
- **UART TYPE 은 구현 기준으로 확정**: 0x20 mmWave · 0x10 env · 0x03 자세 규칙 · 0x05 ToF 원본 8×8 · 0xF0 heartbeat(`data-spec.md` §13.1).
  운영 경로는 ESP32 → Pi 4 **USB 직결**이고 GPIO UART2 는 대체 경로다.
- **부팅 자동 시작은 현재 ATLAS 이미지에서 불가**하다. 부팅마다 `install.sh pi4|pi5 --runtime-only` 를 한 번 돌린다(`docs/reboot-recovery.md`).

## 3. 아직 결정하지 말아야 할 사항

| 항목 | 현재 상태 |
|---|---|
| **카메라 코드** (`display/atlas/camsvc`·`camtest`) | **제품 경로에는 카메라가 없다**(제품 앱에 카메라 기능 없음, 자세는 ToF). 둘은 ToF 도착 전에 쓴 개발 앱이다. 카메라 경로를 확장하거나 시연·제출 빌드에 넣지 않는다. 레포에서 지울지는 팀 결정(`CLAUDE.md` §5 열린 항목 0) |
| `C_focus`의 부호 | 현재 구현의 “큰 값 = 집중 저하 증거”를 임의 변경하지 않음 |
| 개인화 저장소·동의 | 로컬 opt-in 정책과 저장 방식을 결정 전에는 확장하지 않음 |
| 스마트 플러그·ThinQ 제어 모델 | 기본은 스마트 플러그 기반 가역 제어, 실기기 모델은 미선정(09-28 마감 경과). 제어 디스패처·mock 플러그는 `feat/edge-hub-port` 에 있음 |

## 4. 비협상 제약

- 키 내용, 카메라 영상, 상시 음성을 수집·전송·저장하지 않는다. ToF raw 8×8 은 ESP32→Pi 4 유선 UART(`0x05`)와 Pi 4 안에서만 다루고
  MQTT·로그·화면으로 내보내지 않는다(10-17).
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
