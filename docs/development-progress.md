# DESKMATE 개발 진행 현황

> 기준일: 2026-09-14
> 목적: 제품 방향, 현재 구현 상태, 다음 의존성을 한 화면에서 관리한다. 개인 이름과 담당자 표기는 이 문서에서 제외한다.
> 결선 목표(포스터 수준) 대비 갭과 주차별 계획은 [`roadmap.md`](roadmap.md) 에 있다. 이 문서는 "지금 무엇이 되고 무엇이 안 되는가"만 다룬다.

> **2026-09-14 팀 결정** — 신뢰도 주기 `score_period_sec: 10` · 미머지 브랜치 → `feat/merge-pending` PR · Pi 5 디스플레이 교체(터치 정상) · 키스트로크 계약 구현 기준 확정 · 환경 센서 보정 없음(단일 센서).

> **2026-09-14 갱신** — 노션 09-11 실측을 반영했다. **C1001 mmWave → ESP32 UART1 → ESP32 UART2 → Pi 4 `/dev/serial0` 물리 링크 검증 완료**(26 B COBS+CRC 프레임, 1 Hz, 115200 bps). Pi 4 ATLAS 이미지에는 Python·컴파일러가 없어 UART 디코더는 `hub/atlas` C++ 서비스에 둔다. **ToF 는 스켈레톤 트래킹을 채택(09-08)했으나 코드가 없고 연결 경로(Path A/B)는 미결**이다. Pi 4↔Pi 5 MQTT 는 display 구독 측만 구현됐고 hub 발행은 아직 없다.

## 1. 제품 지향점

- **초개인화**: 개인별 baseline(중앙값·MAD Modified z-score)과 시간대별 패턴을 이용해 피로·집중·환경 개입 기준을 조정한다.
- **공간 이해**: ToF·mmWave·환경 센서를 융합해 사용자 상태와 주변 환경을 함께 해석한다.
- **프라이버시 우선**: 카메라·마이크의 상시 수집 없이, 센싱·추론·제어를 로컬에서 처리한다. 마이크는 상시 감지가 아닌 명시적 명령/가전 제어 검토 항목으로만 둔다.
- **양방향 가전 연동**: 명령 전송뿐 아니라 가전 상태(예: 온도·동작 상태)와 사용자 피드백을 받아 다음 판단에 반영한다.
- **사용자 주도 자동화**: 불확실한 판정은 display에서 제안·확인·정정을 받고, 피드백을 개인화 데이터로 축적한다.

## 2. 보드 배치와 런타임 (확정)

| 구분 | ESP32 | Raspberry Pi 4 hub | Raspberry Pi 5 display |
|---|---|---|---|
| 주 역할 | mmWave·환경 센서 취득, 1차 전처리(DrowsyDetector 등), UART2 송신 | UART 수신·디코딩, 센서 융합, FSM, 제어 판단, Mosquitto broker | 터치 UI, 제안·정정 입력, 스피커 알림, 상태/리포트 표시 |
| 런타임 | PlatformIO(Arduino) | **AI Native OS Headless(ATLAS)**. Python·컴파일러 없음 → `hub/atlas` native service IPK 가 `/restricted/python3` 로 FSM 실행. UART 디코더·MQTT 클라이언트는 C++ 서비스 측 | AI Native OS Video Profile, Atlas Flutter `.ipk` |
| 통신 | UART2 115200 8N1(목표 460,800+), COBS + CRC-16/CCITT-FALSE | Pi 4↔Pi 5: 이더넷 직결, MQTT TCP 1883 + SSH 22 | MQTT QoS 1 구독(`state/phase` retain, `interaction/request`, `display/message`), `feedback/user` 발행 |
| 장애 영향 | 해당 센서 결측 → FSM 재정규화 | 핵심 경로 | 화면·상호작용만 제한 |

### 확인된 배선 (2026-09-11)

| 연결 | 핀 |
|---|---|
| C1001 TX/RX ↔ ESP32 | GPIO16(UART1 RX) / GPIO17(UART1 TX), 115200 |
| ESP32 UART2 TX → Pi 4 RX | GPIO25 → Pi 4 물리 10번(GPIO15) |
| ESP32 UART2 RX ← Pi 4 TX | GPIO26 ← Pi 4 물리 8번(GPIO14) |
| 공통 GND | ESP32 GND → Pi 4 물리 6번, C1001 GND → Pi 4 물리 9번 |
| Pi 4 측 | `/dev/serial0 → ttyS0`. `serial-getty@ttyS0` 를 중지해야 함(읽기 전용 rootfs 라 재부팅 시 원복 — 영구 설정은 ATLAS 공식 절차 확인) |

ESP32 UART0 은 디버그·`log2file` 로깅 전용으로 남긴다.

### 바로 할 일

- [x] `display/atlas/app/` Atlas Flutter 앱, `state/phase` 호환 모델, HTTP 개발 어댑터, release `.ipk`, Pi 5 설치·실행
- [x] 센서 테스트 화면(Pi 4 `/api/test-frame`), 18상태 전이 그래프, 키스트로크 패널, 오디오 재생, 대시보드 재설계
- [x] display MQTT 구독(`state/phase`·`interaction/request`·`display/message`)과 `feedback/user` 발행
- [x] `hub/atlas` native service IPK (제한 Python 으로 FSM 실행, HTTP 8765)
- [x] Pi 4 Mosquitto 설정·Node-RED 모니터
- [x] C1001 → ESP32 → Pi 4 UART 물리 링크 실측
- [ ] ESP32 펌웨어(C1001 파서·DrowsyDetector·UART2 송신)를 별도 저장소에서 `firmware/esp32_sensor_node/` 로 이관
- [ ] Pi 4 C++ 서비스에 UART 수신·COBS 해제·CRC 검증 → Python FSM 입력
- [ ] hub 측 MQTT 발행(`state/phase` retain 등) — 현재는 Node-RED 주입으로만 화면 MQTT 경로를 확인함
- [x] Pi 5 디스플레이 교체 — 터치 정상 (09-14). 새 화면 모델·배선 기록 필요
- [x] 미머지 브랜치 병합 브랜치 `feat/merge-pending` 푸시 (PR 머지 대기)
- [ ] Pi 5 앱·Pi 4 서비스 재부팅 자동 시작
- [ ] display 가 꺼져도 Pi 4 hub 가 계속 동작하는지 통합 테스트

## 3. 시스템 흐름

```text
C1001 mmWave ─UART1─┐
SCD41·BH1750·DHT22 ─┤ ESP32 ── UART2 (COBS + CRC-16) ──> Pi 4 hub (C++ 서비스: UART 디코딩 · MQTT)
VL53L9CX ToF ───────┘ (Path B 시)                          └─ 제한 Python: 융합 · FSM · 제어 판단
VL53L9CX ToF ── MIPI CSI-2 (Path A 시) ─────────────────────┘
PC 키보드 타이밍 ── Wi-Fi/MQTT ────────────────────────────>│
                                                            ├── 이더넷 직결 MQTT ──> Pi 5 display
LG 가전 상태 <── 양방향 MQTT/API ───> ────────────────────────┴── 제어 명령 ──> 스마트 플러그 · LG 가전
                                                             ↑
                                               사용자 수락·거절·정정 피드백(MQTT)
```

## 4. 분야별 현황

| 분야 | 확정 방향 | 현재 상태 | 다음 산출물 / 의존성 |
|---|---|---|---|
| 보드 간 통신 | 논리 데이터 계약과 전송 기술을 분리 | **Pi 4↔Pi 5: 이더넷+MQTT 확정, display 측 구현.** **ESP32↔Pi 4: UART2 물리 실측 완료**, 프레임 TYPE·스키마 미확정. hub 측 MQTT 발행 없음 | mmWave TYPE 배정(실험용 0x01 은 ToF 디버그와 충돌), DrowsyDetector 출력 스키마, CRC test vector, 460,800 bps+ 유실률 |
| ToF | 스켈레톤 트래킹(V2V-PoseNet) 주 경로 + 기하 특징 7종 폴백. 운영은 특징값만, 디버그 depth map 최대 2 Hz | **센서 미연결, 코드 없음.** 연결 경로 Path A(Pi 4 CSI-2) / Path B(ESP32 I2C 1 MHz binning) 미결. 스켈레톤 온보드 연산 부하 미결 | 09-19 까지 1일 spike 로 경로 확정. 온보드는 기하 특징 7종, 스켈레톤은 PC 오프라인 검증 그림 |
| mmWave | 체동 중심 보조 신호. 순간 호흡·심박·HRV·`inBed`·부팅 덤프 파형은 판정 근거에서 제외 | C1001 파서·5상태 DrowsyDetector 는 **별도 저장소**에 구현. Pi 4 까지 실프레임 도달 확인 | 레포 편입, Pi 4 디코더, FSM `respiration`/`presence` 신호 매핑 |
| 환경 센서 | **보정 없음·단일 센서(09-14 확정)**: CO₂=SCD41, T/RH=DHT22, lux=BH1750 | 펌웨어 없음. DHT22 갱신 주기·프레임 크기 미검증 | 0.2 Hz 환경 묶음 프레임(TYPE 0x10, 8 B), Pi 4 발열 격벽 |
| 키스트로크 | 키 내용 미수집, 타이밍 통계만. **계약은 `collector/` 구현 기준 확정(09-14)** — 60 s·1 Hz·QoS 0·additive 신호·미입력=`typing_active=false` | 수집기·테스트 8개가 `feat/merge-pending` PR 로 main 진입 중. UI 패널과 demo envelope 요약값은 main 에 있음 | hub ingest(평면 payload 수용), 세부 튜닝(envelope 필드 추가) |
| FSM·불확실성 처리 | FSM 우선, 불확실하면 사용자 확인. **신뢰도 주기 10 s(09-14 확정)** | 18상태 FSM, 신뢰도 게이트(0.45/0.75), 테스트 46개(45 통과·1 xfail), 엎드림·노딩 시나리오, 리플레이 하네스. `report.py`·`--report` 는 `feat/merge-pending` PR 로 진입 중 | 실센서 `SensorFrame` 연결(10 s 프레임), 리플레이 튜닝 |
| 개인화·AI | baseline 정규화(중앙값·MAD) → 2단계 TFLite(1D CNN 백본 동결 + 개인 헤드) 선택적 융합 | `features/` `ml/` 비어 있음 | baseline 정규화(DATA-DEC-005) 가 선행. 라벨 부족 시 개념 실증 범위 |
| 가전 연동 | 스마트 플러그 + LED/환기팬 기본 라인, ThinQ 1기기 선택 | `control/` 비어 있음. 플러그 미선정 | 로컬 제어 가능 플러그 선정, ACTION_ENV → 명령·결과·실패 복구 |
| display·UI | 개발 PC Docker 크로스 빌드 → Pi 5 네이티브 `.ipk` | 대시보드·18상태 그래프·키스트로크 패널·오디오·MQTT 구독 완료, release IPK 배포. **디스플레이 교체 후 터치 정상(09-14)** | 새 화면 모델·배선 기록, 상태 적응형 4화면(리포트 신규), 자동 시작 |
| CAD·브랜딩 | 힌지형 Dock+Head 일체형, 발열 분리 | 산출물 없음 | 3보드·센서·화면 조립 치수, Pi 4 배기와 환경 센서 격벽 |
| 명세·품질 | 요구사항·데이터 계약을 구현보다 먼저 고정 | 요구사항·데이터·MQTT·FSM 명세 존재 | UART 프레임·mmWave·키스트로크 계약 확정, 시험 시나리오 |

## 5. 이번 우선순위 (W1: 09-15 ~ 09-21)

1. **mmWave 수직 슬라이스**: ESP32 코드 이관 → Pi 4 C++ UART 디코더 → Python `SensorFrame` → FSM → hub MQTT 발행 → Pi 5 화면. 센서 1종이 끝까지 닿는 경로를 먼저 만든다.
2. **UART 프레임 계약 확정**: mmWave TYPE, DrowsyDetector 출력, CRC test vector 를 `data-spec.md` 에 기록.
3. **`feat/merge-pending` PR 머지**: 세션 리포트·키스트로크 수집기를 main 에 넣고 세 브랜치 삭제.
4. **ToF 경로 spike 준비**: 09-19 까지 Path A 드라이버 확보 여부 판정. 실패 시 Path B.
5. **스마트 플러그 선정**과 새 디스플레이 모델·배선 기록.

## 6. 결정이 필요한 항목

| 항목 | 선택지 / 확인할 내용 | 결정 기준 |
|---|---|---|
| ToF 연결 경로 | Path A(Pi 4 MIPI CSI-2 풀해상도) / Path B(ESP32 I2C 1 MHz binning) | 09-19 spike. 드라이버 미확보 시 B |
| UART 프레임 TYPE | mmWave 신규 TYPE(현 실험 0x01 은 ToF 디버그와 충돌) | 팀 TYPE 표에서 배정 |
| mmWave 출력 스키마 | state·evidence 만 / 체동평균·심박중앙값·기준선·거리·락온 요약값 포함 | Pi 4 FSM 입력 계약과 정합 |
| 스마트 플러그 | 로컬 제어 가능 모델 | 클라우드 의존 없음, 부스 Wi-Fi 없이 동작 |
| 개인화 저장소 | 로컬 JSONL / SQLite | 개인정보 최소화, 삭제 가능성 |
| 마이크 기능 | 제외 유지 / 명시적 활성화형 보조 입력 | 프라이버시와 데모 효과 |

## 7. 완료 기준

- 센서 패킷은 스키마 버전·시퀀스·타임스탬프로 유효성을 판정하고 오류·중복·지연을 로그로 남긴다. UART frame 은 CRC-16, MQTT JSON 은 별도 CRC 없이 검증한다.
- Raw 개인 데이터와 실제 키 입력은 운영 중 전송·저장하지 않는다. 축소 depth map 은 명시적 디버그/UI 모드에서 최대 2 Hz 로만 사용한다.
- Node-RED 는 개발 모니터링·센서값 주입·로깅에만 사용하며 중단되어도 핵심 경로가 동작한다.
- 센서 융합 결과가 불확실하면 자동 제어 대신 display 에서 사용자 확인을 받는다.
- 가전 제어는 명령 발행, 실행 결과 수신, 실패 시 안전한 복구까지 하나의 시나리오로 검증한다.
- 개인화 기능은 opt-in, 데이터 보존 기간, 삭제 방법이 정해진 뒤에만 활성화한다.

## 8. 관련 문서

- 결선 로드맵·갭 분석: [roadmap.md](roadmap.md)
- AI 개발 공통 브리핑: [agent-briefing.md](agent-briefing.md)
- CLI 시작 공통 문구: [agent-kickoff-prompt.md](agent-kickoff-prompt.md)
- 요구사항 계약: [requirements-spec.md](requirements-spec.md)
- 데이터 계약: [data-spec.md](data-spec.md)
- 통신 계약: [mqtt-topics.md](mqtt-topics.md)
- FSM 계약: [fsm-spec.md](fsm-spec.md)
- FSM 구현 계획: [fsm-dev-plan.md](fsm-dev-plan.md)
- 하드웨어·열 설계 참고: [hardware.md](hardware.md)
- Pi 5 Atlas 개발 환경: [../display/atlas/README.md](../display/atlas/README.md)
- Pi 4 native service: [../hub/atlas/README.md](../hub/atlas/README.md)
- Pi 4→Pi 5 실기 연결 순서: [hardware-bringup.md](hardware-bringup.md)
- 노션 통신 아키텍처(2026-09-11 갱신): <https://app.notion.com/p/3c22d27b08d38163a31eeba4bdf1bff1>
- 노션 센서별 데이터·처리·전송 스펙(2026-09-11 갱신): <https://app.notion.com/p/3d02d27b08d3816fa189dd73c81b876b>
- 노션 ESP32–Pi 4 UART 하드웨어 연결·실측: <https://app.notion.com/p/3d72d27b08d3817f836ac5d537394705>
- 노션 Pi 4 UART 수신 앱 구현 기록: <https://app.notion.com/p/3d72d27b08d3817c8840d7580736633e>
- 노션 Pi 5 DSI·VL53L0X 실측 기록(2026-07-27): <https://app.notion.com/p/3aa2d27b08d38125b494f57226cf2352>
- LG 스마트 가전 기술교육: 저장소 외부 로컬 `개발자료/스마트 가전_기술교육 (1).pdf` (Git 미포함)
- 전년도 수상팀 공개자료: 저장소 외부 로컬 `개발자료/제23회ESWC_동방예의지국_발표자료_공개용 (1) (1).pdf` (Git 미포함)
