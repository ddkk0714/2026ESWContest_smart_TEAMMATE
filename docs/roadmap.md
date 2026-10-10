# DESKMATE 결선 로드맵 — 포스터 수준 완성을 위한 갭 분석과 실행 계획

> 기준일: **2026-10-01** (이전 기준 09-29) · 결선 제출 2026-10-01~10-30 · 오프라인 발표 2026-11-06
> 목표 기준: 2026 UOS ECE Innovation Fair 출품작 포스터(「DESKMATE : 카메라 없이 읽는 책상 위 인지 상태」)에
> 서술한 기능 수준. 이 문서는 **그 서술과 현재 구현 사이의 간극**을 관리한다.
> **2026-09-14 팀 결정 반영**: 신뢰도 주기 10 s · 미머지 브랜치 병합 PR(`feat/merge-pending`) · Pi 5 디스플레이 교체(터치 정상) · 키스트로크 계약 구현 기준 확정 · 환경 센서 보정 없음(단일 센서).
> **2026-09-29 갱신**: §2 상태표·§2-1 브랜치 현황 신설·§3 I7(카메라 자세 판정)·§4 체크박스·§5 리스크.
> **2026-10-01 갱신**: PR #27~#32 머지(`origin/main` = `7ab7ea0`) — hub 기준선·제어 디스패처·앱 연결 안정화(연결 탭·배지·BT 재연결) main 진입.
> 실기 없이 진행하는 다음 계획 W1~W7([`plan/next-development-plan.md`](plan/next-development-plan.md))의 W1~W4·W6 가 `feat/display-intervention`(로컬, PR 예정)에 있다.

---

## 0. 현재 시스템 스냅샷

```text
ESP32(mmWave·환경) ── UART2 / COBS + CRC ──> Pi 4 Hub ── MQTT / LAN ──> Pi 5 Atlas UI
PC 키스트로크 특징 ─────────────── MQTT ────────────┘
ToF VL53L9CX ── 연결 경로 결정 전 (Pi 4 CSI-2 또는 ESP32 I2C) — 09-19 마감 지남, 미연결
(임시) ESP32-CAM + RP2040 ── USB ──> Pi 5 camsvc(MediaPipe) ──> 앱 `자세` 화면   ※ §3 I7 참고
```

- Pi 4는 UART 수신·특징 통합·FSM·제어 판단·Mosquitto를 맡는다. Headless ATLAS native-service
  IPK에서 제한 Python FSM을 실행하며, UART 디코더와 MQTT 연결은 해당 서비스에 둔다.
  보드 제한 Python 에는 `_socket` 이 없어 **MQTT 는 C++ 네이티브 클라이언트**가 맡는다(09-17).
- **브로커(09-18 기준)**: Pi 4 에 정적 빌드 Mosquitto 2.0.20 을 `/data/share/deskmate/bin` 에 두고
  `pi4-broker-start.sh` 로 기동(PR #18). 자동 시작 IPK 는 없어 재부팅 후 한 번 실행해야 한다.
  개발용 PC 브로커는 `tools/pc_broker.py`(amqtt, `feat/edge-hub-port`).
- Pi 4 재부팅·IPK 재설치 후 hub 미기동·UART `rx=0` 은 `hub/atlas/tools/pi4-hub-activate.sh` 로 복구한다
  (uid 변경·D-Bus `User=`·`own` 정책·`/dev/serial0` 권한 4가지 원인, PR #27 `2841e70`).
- Pi 5는 MQTT 상태를 구독하는 Atlas Flutter 표시·피드백 장치다. HTTP 8765은 개발 fallback이고,
  Node-RED는 관찰·테스트 주입용이다.
- 수직 슬라이스의 목표는 한 센서 신호가 Pi 4 FSM을 통과해 Pi 5 UI와 가역 제어까지 닿는 것이다.
  미결정 사항과 안전 제약은 [`agent-briefing.md`](agent-briefing.md), 물리 연결은
  [`hardware.md`](hardware.md)를 기준으로 한다.

---

## 1. 포스터가 약속한 것 (= 결선 완성 기준)

| # | 영역 | 포스터 서술 | 결선 시연에서 보여야 하는 것 |
|---|---|---|---|
| G1 | ToF 자세 | VL53L9CX depth → V2V-PoseNet 스켈레톤 → 기본·엎드림·턱 괴기·상체 기울임 분류 → 자세 교정·스트레칭·휴식 제안 | 실기기에서 최소 **엎드림·깊게 기대기·기본** 3분류가 화면에 실시간 반영. 턱 괴기·스켈레톤은 오프라인 결과 그림으로 대체 가능 |
| G2 | mmWave | 재실·움직임·호흡·심박, 117분 7,085프레임 체크섬 오류 0, 움직임 ≥30 급증 판정 | C1001 값이 Pi 4 FSM 입력으로 들어가 졸음 의심(정적+호흡 소실) 판정에 기여 |
| G3 | 환경 | SCD41·DHT22·BH1750 로 CO₂·온습도·조도 | 환경값이 화면 대기 상태에 표시되고 CO₂ 임계 초과 시 환기 개입 라우팅 |
| G4 | 키스트로크 | dwell·flight 타이밍만, 키 내용 미수집 | PC 수집기 → Pi 4 → C_focus/C_fatigue 기여, 화면 키스트로크 패널 |
| G5 | 1단계 FSM | 이중 모니터링, PC/비PC/혼용 가중치, 신뢰도 개입 루프, 실패 개입 음의 보상 | 이미 구현. 실센서 리플레이로 임계값 보정 |
| G6 | 2단계 개인화 | 중앙값·MAD Modified z-score 기준선(시간대별), 1D CNN 백본 동결 + 개인 헤드 온보드 학습 | 최소: **baseline 정규화 실동작**. 목표: 개인 헤드 학습을 PC 에서 시연하고 TFLite 를 Pi 4 에 선택적 적재 |
| G7 | 확신도 개입 | <0.45 무동작 / 0.45~0.75 제안 / ≥0.75 가역 자동 실행 + 이유 표시 | 제안 카드·자동 실행 알림 두 경로가 실제 제어(스마트 플러그)까지 연결 |
| G8 | UI | 저자극 집중 화면 / 터치 시 상세 / 제안 확인 / 세션 리포트 | 4 화면 모두 실기 터치로 전환. 리포트 화면 신규 |
| G9 | 하드웨어 | 힌지형 Dock+Head 일체형, 발열 분리, ESP32·Pi 4·Pi 5 분산 처리 | 하우징 안에 3보드·센서 조립, 부스 전원 1구에서 기동 |
| G10 | 제어 | 조명·환기·공기청정기 제안/실행 | 스마트 플러그 + LED/환기팬 기본 라인. ThinQ 는 1기기 선택 |

---

## 2. 현재 위치 (2026-10-01)

범례: ✅ 동작 · 🟡 부분 · ⬜ 없음 · ⚠ 결정 필요. "main" = `origin/main` `7ab7ea0`, `(DI)` = `feat/display-intervention`(로컬, PR 예정).

| # | 상태 | 근거 |
|---|---|---|
| G1 ToF | ⬜ ⚠ | VL53L9CX **미연결**, Path A/B 미결(09-19 마감 지남). 기하 특징 7종 코드 없음. ESP32-CAM 자세 판정(`display/atlas/camsvc`·`camtest`)은 main → §3 I7 |
| G2 mmWave | 🟡 | 펌웨어 main(C1001 + DrowsyDetector, PR #28 수동 파서). UART2 → Pi 4 `uart_rx` 실수신(09-16). 앱 심박·호흡 표시. 재플래시 후 실측 남음 |
| G3 환경 | 🟡 | 드라이버 main(PR #21). hub 환경 특징 확장 — CO₂ 세션 상승·온습도 쾌적 이탈·조도 부족과 `env_flags` (DI, W2). 실센서 재측정 남음 |
| G4 키스트로크 | ✅ | `collector/` main. 평면·envelope payload 수용 |
| G5 FSM | ✅ | 18상태 엔진 main. **재시도 원인은 제안까지만**(`gate.retry_max`, DI). hub 테스트 155 통과·1 xfail(DI). 5분 시연 시나리오·dry-run·동선 시험(DI, W4) |
| G6 개인화 | 🟡 | 중앙값·MAD 기준선 main(PR #27). ESM 라벨 기록·정량 지표(DI, W3). 2단계 학습용 창·증강·증거 리포트(DI, W5 — 합성 기준, 결측 내성 +2.7%p) |
| G7 개입 | 🟡 | 게이트 0.45/0.75. 디스패처(auto/suggest/undo/쿨다운)·mock 플러그 main. 자동 실행 알림·되돌리기(60 s 창)·제안 카드 이유·남은 시간·정정 입력(DI, W1·W3). **스마트 플러그 미선정** |
| G8 UI | 🟡 | 통합 앱 main + 연결 탭·연결 배지·MQTT 감시·BT 자동 재연결(PR #30~#32). 대기 위젯(온디바이스 문구·오늘 집중)·개입 카드(DI, W1). 재부팅 자동 시작 없음 |
| G9 HW | ⬜ | CAD·하우징 산출물 없음 |
| G10 제어 | 🟡 | iLink 램프 + mock 플러그. 스마트 플러그·환기팬 실기 없음 |
| 통신 | 🟡 | Pi 4 정적 Mosquitto·C++ 네이티브 MQTT 브리지 main. PC 경로 브로커 재시작 자동 재연결 시험(DI, W6). UART 프레임 잠정 규약 그대로, 보드 재부팅 시 수동 기동 |
| 시험 | 🟡 | 결측·유실 시나리오(센서 분리·브로커 재시작·디스플레이 무응답)·판정 주기 측정(PC p95 ≈ 0.6 ms) (DI, W6). Pi 4 실측·2시간 연속 구동 남음 |

### 2-1. 브랜치 현황 (2026-10-01)

| 브랜치 | 상태 | 내용 | 조치 |
|---|---|---|---|
| `feat/display-intervention` | 로컬 커밋(미푸시), main 위 | W1 개입 UI · W2 환경 특징(Codex) · W3 ESM·지표·되돌리기 창(Codex) · W5 학습 창·증강·증거(Codex) · W4 시연 시나리오 · 재시도 게이트 · W6 결측 시험·판정 주기·데이터셋 조사 · W7 문서 | 묶음 3 끝나면 PR(사용자 요청 시 모아서) |
| `feat/atlas-hotspot-broker` | 로컬 워크트리 | 핫스팟·정적 브로커 산출물(`dist/`) | 보존 |
| `origin/feat/edge-mvp-nodered-followup`·`origin/feat/hub-swonly` | 원격 | 일부만 이관된 옛 작업 | 보존(필요한 부분 이관 완료) |

PR #24(옛 앱)·#26 은 닫힘, #27~#32 머지.

---

## 3. 포스터 서술과 구현 사이의 정합성 이슈 (결정 필요)

| # | 이슈 | 선택지 | 권장 |
|---|---|---|---|
| I1 | ~~포스터 "10초마다" vs `score_period_sec: 30`~~ | — | **해소(09-14)**: `score_period_sec: 10` 으로 변경. 지속 조건(3분 등)은 초 단위 타이머라 영향 없음. ingest 가 10 s 프레임을 만든다. 채터링은 4-B §5 에서 확인 |
| I2 | 스켈레톤(V2V-PoseNet) 온보드 실시간 | Pi 4 CPU 불가 전제 → (a) PC 오프라인 검증 그림 + 온보드는 기하 특징 (b) Pi 5 이관 (c) 경량화 | **(a)**. 결선 완성도는 실시간 동작이 좌우. 스켈레톤은 "검증 결과"로 제시 |
| I3 | ToF 연결 경로 | Path A(CSI-2, 풀해상도) / Path B(ESP32 I2C, binning) | 09-19 까지 1일 spike. **드라이버 확보 실패 시 즉시 Path B**. 기하 특징은 두 경로 공통 |
| I4 | 호흡·심박 표현 | 포스터는 "호흡·심박 신호" 언급 | 순간값 미사용·중앙값+기준선·"호흡 소실 여부"만 증거로 쓴다고 보고서에 명시. `respiration_enabled` 의미를 이 기준으로 재정의 |
| I5 | 2단계 "개인 헤드 온보드 학습" | 온보드 학습 / PC 학습 후 TFLite 적재 | PC 학습 + Pi 4 TFLite 추론(선택적 의존). "온보드"는 추론을 의미하도록 서술 조정 |
| I6 | 공기청정기 제어 | ThinQ 실기 / 스마트 플러그 | 스마트 플러그로 LED·환기팬. 공기청정기는 ThinQ 1기기 선택 시연 |
| I7 | **카메라 기반 자세 판정(신규, 09-18)** — 포스터·CLAUDE.md 는 "카메라·마이크 미사용"인데 ESP32-CAM 영상 + MediaPipe 스켈레톤 판정과 카메라 영상 보기가 main(`display/atlas/camsvc`·`camtest`)에 있음 | (a) ToF 도착 전 **개발용 대역**으로만 쓰고 시연·제출 빌드에서 제외 (b) 제품 기능으로 채택하고 포스터·보고서의 "카메라 미사용" 서술 변경 (c) 카메라 영상 보기만 빼고 판정은 유지 | **팀 결정 필요.** 핵심 차별점("카메라 없이")과 직결되므로 (a) 가 기본 권장. 어느 쪽이든 `agent-briefing.md` §4 비협상 제약과 `docs/posture-camera.md` 를 같이 고친다 |

---

## 4. 실행 계획

두 단계로 나눈다. **4-A 는 이번 주 금요일(2026-09-18)까지 만드는 MVP**, **4-B 는 11월 발표까지 개발계획서 기준 완전 개발 체크리스트**다.
주차 표는 두지 않는다. 항목을 끝내면 체크박스를 채우고, 막히면 그 항목 아래에 한 줄로 이유를 적는다.

### 4-A. 이번 주 MVP — 마감 2026-09-18(금)

**범위**: 실센서 값이 MQTT 로 들어와 **Node-RED 에서 시각화**되고, 같은 값이 **FSM 을 통과한 결과(상태·C_fatigue·C_focus)가 표시**된다.
제어·개입 제안·개인화·하우징은 범위 밖. Pi 5 화면 표시는 보너스(이미 MQTT 구독이 구현돼 있어 broker 만 맞추면 된다).

```text
ESP32 (C1001 mmWave, 가능하면 SCD41·BH1750·DHT22)
  └─ USB(UART0) 1 Hz JSON 라인 ──► PC 브리지 tools/uart_mqtt_bridge.py ──► MQTT broker (Pi 4 Mosquitto, 없으면 PC)
PC collector (키스트로크, feat/merge-pending) ─────────────────────────────► MQTT deskmate/sensor/keystroke

MQTT ──► Node-RED (PC)  : 센서 차트·게이지 + FSM 상태 패널
     ──► hub ingest     : 센서 토픽 구독 → 10 s SensorFrame → FSMEngine → deskmate/state/phase 발행 (PC 에서 python -m deskmate_hub run)
     ──► Pi 5 Atlas     : DESKMATE_MQTT_HOST 빌드로 state/phase 표시 (보너스)
```

> **MVP 지름길 두 가지를 명시한다.** ① ESP32 → PC USB 브리지는 Pi 4 C++ UART 디코더(4-B §3)가 생기기 전까지의 임시 경로다.
> ② hub 는 PC Python 으로 돌려도 된다. Pi 4 native service 배포는 4-B §9 에서 한다. 두 지름길 모두 계약(`data-spec.md`·`mqtt-topics.md`)은 제품 경로와 동일하게 지킨다.

> **09-29 기록**: 마감(09-18)이 지났다. 아래 항목 중 실센서 시나리오·리플레이 재현·MVP 통과 기준은 **실기 확인 기록이 저장소·노션에 없어** 미체크로 둔다. 확인했다면 체크하고 날짜를 적는다.

**일별 목표**

- [ ] **화 09-15 — 센서가 MQTT 에 보인다**
  - [x] `firmware/esp32_sensor_node/` PlatformIO 프로젝트 생성(09-14, PR #12) — 공식 DFRobot C1001 라이브러리 기반 `C1001Passive` + 5상태 `DrowsyDetector`. 원본 저장소 미제공으로 UART2 COBS/CRC 송신은 미이관(핀·baud 초기화만). **`pio run` 빌드는 미검증** — 화요일 실보드에서 확인
  - [x] 펌웨어 UART0(USB) 1 Hz mmWave JSON + 5 s 환경 스텁 JSON(`{"t":"mmwave"|"env", ...}`) — PR #12
  - [x] `tools/uart_mqtt_bridge.py`: `{"t":` 라인 → 공통 envelope → `deskmate/sensor/{mmwave,env}/<node>`, health LWT, 백오프, 날짜별 JSONL. 순수 변환 테스트 9개 — PR #12
  - [x] broker 결정·기동 — 09-17 PC 브로커(`pc_broker.py`, `feat/hub-swonly`) → 09-18 **Pi 4 정적 Mosquitto 2.0.20**(`tools/atlas-hotspot-broker/`, PR #18, `/data/share/deskmate/bin/pi4-broker-start.sh`). 자동 시작 없음
  - [x] `feat/merge-pending` 머지(PR #15 경유). `python -m collector --broker <ip>` 이식성 설정 문서는 `feat/edge-hub-port`
- [ ] **수 09-16 — Node-RED 시각화**
  - [x] `tools/node-red-visualizer/flows.json` 대시보드(node-red-dashboard 3.6.6): mmWave·환경·키스트로크·FSM 패널 — PR #12 (화면 캡처는 실센서 연결 후)
  - [x] 토픽별 신선도·seq 갭 표시 — PR #12
  - [x] `deskmate/#` JSONL 기록 노드 — PR #12
- [ ] **목 09-17 — FSM 결과 표시**
  - [x] `hub/deskmate_hub/ingest/` (cache·mapping·protocol·mqtt_source): 센서 토픽 구독, 10 s 마다 `SensorFrame` 생성 — 09-14 구현, 테스트 12개. 신호 매핑 초안은 `config/fsm.yaml` 의 임시 스케일(고정 선형)로 두고 baseline 정규화는 4-B §4 에서 교체
    - mmWave: `presence` ← presence/거리, `respiration`·모션 증거 ← drowsy_state·body_move 이동평균
    - keystroke: `typing_active=false` → available=false, 그 외 idle_ratio·flight_cv·correction_rate → phi/delta
    - env: co2_ppm 절대 구간 → environment delta
  - [x] `python -m deskmate_hub run --broker <ip>`: ingest → `FSMEngine.tick` → `deskmate/state/phase`(retain, QoS 1) 발행, `feedback/user` 수락/거절 반영 — 09-14 구현. 로컬 amqtt 브로커로 E2E 확인(센서 3토픽 수신 → START 발행 → health LWT · feedback 수신). Pi 4 Mosquitto 실연동은 화요일
  - [x] Node-RED FSM 패널(상태·phase·context·gate, C_fatigue/C_focus 차트, reasons) — PR #12
  - [ ] (보너스) Pi 5 를 `DESKMATE_MQTT_HOST=<broker>` 로 빌드해 같은 상태가 화면에 뜨는지 확인
- [ ] **금 09-18 — 통합 리허설·기록**
  - [ ] 시나리오 1회 통과: 착석 → 타이핑(FOCUS_PC) → 손 떼고 정적 10분(FATIGUE_SUSPECT 이상) → 자리 비움(IDLE) — **합성 sim 으로는 09-16 통과**(`tools/rehearsal_local.py`: START→CONTEXT_DETECT→FOCUS_PC→FATIGUE_SUSPECT→FATIGUE→CAUSE_ANALYSIS→ACTION→MONITOR→RECOVERY). 실센서로 재확인 필요
  - [ ] 위 세션의 JSONL 을 `python -m deskmate_hub --replay` 로 재생해 같은 전이가 나오는지 확인
  - [ ] 노션 SW 페이지에 화면 캡처·발견한 임계값 문제·다음 할 일 기록
  - [ ] MVP 브랜치(`feat/mvp-nodered`) PR 생성

**MVP 통과 기준**

- [ ] 센서 앞에서 움직이면 3 s 안에 Node-RED 차트가 반응한다
- [ ] 키보드를 치면 키스트로크 패널 값이 바뀌고, 멈추면 `typing_active=false` 가 된다
- [ ] FSM 상태가 10 s 주기로 갱신되고 `START → CONTEXT_DETECT → FOCUS_*` 전이가 실센서로 일어난다
- [ ] 센서 하나를 뽑아도 hub 가 죽지 않고 해당 신호만 `available=false` 로 빠진다
- [ ] 세션 JSONL 이 남고 리플레이가 재현된다

### 4-B. 11월 완전 개발 체크리스트 — 개발계획서 항목 기준

개발계획서 v2 「개발 일정」의 11개 항목을 그대로 작업 단위로 쓴다. 각 항목의 마감은 결선 일정에서 역산했다:
**통합 MVP 10-05 · 시험 평가 10-19 · 서류 제출 10-30 · 발표 11-06.** ✅ 는 2026-09-29 기준 완료. `(브랜치명)` 이 붙은 항목은 그 브랜치에만 있고 main 에는 아직 없다(§2-1).

#### 1. 요구사항 분석 및 시스템 구조 설계 — 마감 09-19

- [x] 요구사항 명세서·데이터 명세서·FSM 명세·MQTT 계약 작성
- [x] 보드 역할 확정(ESP32 센서 / Pi 4 hub / Pi 5 display), Pi 4↔Pi 5 이더넷+MQTT, ESP32↔Pi 4 UART2
- [x] 신뢰도 주기 10 s, 키스트로크 계약, 환경 센서 단일 측정 결정
- [ ] ToF 연결 경로 Path A/B 결정 (09-19 spike, 실패 시 B) — **09-29: 마감 경과, 결정 기록 없음.** 규칙대로면 Path B 자동 확정. 팀 확인 필요
- [x] UART 프레임 **잠정** 규약 기록(09-15, `data-spec.md` §13.1): mmWave 0x20·env 0x10·heartbeat 0xF0, 헤더·payload 구조체·CRC/COBS test vector. **팀 확정(D1)만 남음** — 값 바꾸려면 `uart_frame.py`·`frame_types.h` 상수만
- [ ] 스마트 플러그 모델 선정(로컬 제어, 클라우드 의존 없음)·구매 — **09-29: 미선정.** 임시로 iLink 블루투스 램프(`control/ilink_light.py`, PR #22)만 제어 가능
- [ ] 새 Pi 5 디스플레이 모델·인터페이스·전원 경로 기록

#### 2. HW 구성 및 센서 인터페이스 구축 — 마감 09-28

- [x] C1001 mmWave → ESP32 UART1 수신, ESP32 UART2 → Pi 4 `/dev/serial0` 도달 실측(09-11)
- [x] Pi 5 디스플레이 교체, 터치 정상
- [x] ESP32 에 SCD41(I2C `0x62`)·BH1750(I2C)·DHT22(단선) 드라이버 — PR #21 main. main 구현으로 재플래시 후 값 실측 필요(노션에 기록)
- [ ] VL53L9CX 를 결정된 경로에 연결(Path A: Pi 4 CSI-2 + STEVAL flex / Path B: ESP32 I2C 1 MHz), 프레임레이트 실측
- [ ] ESP32 UART2 460,800 bps 이상 유실률 실측, 힌지 구간 배선 신호 확인
- [ ] Pi 4 `serial-getty@ttyS0` 영구 비활성(ATLAS 공식 절차) — 영구 방법 미확보. 재부팅마다 `hub/atlas/tools/pi4-hub-activate.sh` 로 권한·D-Bus 복구(`feat/edge-mvp-nodered` `389324b`). 이 보드는 `/dev/serial0 -> ttyAMA0`
- [ ] 스마트 플러그 + LED 스탠드/환기팬 배선·전원

#### 3. MQTT 통신 및 데이터 로깅 파이프라인 구축 — 마감 09-28

- [x] Pi 4 Mosquitto 설정, `mqtt-topics.md` 계약, Node-RED 모니터, display MQTT 구독·`feedback/user` 발행
- [x] Pi 4 native service(`hub/atlas`) IPK 로 제한 Python 실행, HTTP 8765 개발 API
- [x] ESP32 UART2 프레임 송신 — mmWave 0x20 · 환경 0x10 · 하트비트 0xF0 (`transport/frame.cpp`, 09-16). `pio run`·실보드 플래시 09-16 확인
- [x] Pi 4 C++ 서비스에 `/dev/serial0` 수신 → COBS 해제 → CRC 검증 → 라인 브리지(`UART\t<json>`) — `hub/atlas/src/uart_rx.cpp`(09-16, 호스트 테스트 포함). 09-16 ARC 빌드·Pi 4 실수신 rx=132·crc 0
- [x] hub `ingest/`: MQTT 센서·키스트로크 + `UART\t` 라인(`uart_source.py`) → `SensorCache` → `SensorFrame`, freshness·seq 갭 (09-14/15)
- [x] hub MQTT 발행: `state/phase`(retain)·`health/hub`, `feedback/user` 구독 → FSM 반영 (09-14). `interaction/request` 는 충돌 신호 경로(§5)와 함께
- [ ] 전 토픽·UART 라인 JSONL 로거, 리플레이 포맷과 동일 — Node-RED 기록 노드(main)·`pc_broker.py --log-dir`(`feat/edge-hub-port`). 리플레이 포맷 정합 미확인
- [x] collector payload 에 공통 envelope(`schema_version/boot_id/seq`) 추가 (`feat/edge-hub-port`)
- [ ] Node-RED 를 끄고도 운영 경로가 동작하는지 확인

#### 4. ToF·환경·키스트로크 특징 추출 모듈 개발 — 마감 10-05

- [x] 키스트로크 특징(dwell·flight·idle·correction + typing/mouse/input_active·flight_cv) 구현·테스트 8개
- [x] mmWave 체동 이동평균·심박 중앙값·각성 기준선·5상태 DrowsyDetector(`firmware/esp32_sensor_node/src/sensors/c1001/`)
- [ ] ToF 기하 특징 7종(`presence_count` `centroid_depth` `head_row_index` `shoulder_tilt` `motion_indicator` `posture_change_rate` `baseline_deviation`) → posture enum(`upright/lean_forward/lean_back/slouch/away`)·`motion_score`·`nod_rate_hz` — 알고리즘·호스트 테스트 21개는 `feat/tof-posture`(`firmware/.../preprocess/tof/`, 격자 무관). 센서 연결·UART 0x03/0x04·실측 임계값 남음
- [ ] 환경 특징: CO₂ 절대 구간·시작 대비 누적 상승, 온습도 쾌적 범위 이탈, 조도 구간
- [x] `features/` baseline 캘리브레이션: START 보정 창 + 시간대 버킷 중앙값·MAD Modified z → `phi/delta`, 기준선 없으면 선형 폴백 (`feat/edge-hub-port`, 테스트 5개). z_full·mad_floor 실측 튜닝 남음
- [ ] mmWave DrowsyDetector 출력을 FSM `presence`·`respiration`(소실 여부) 신호로 매핑
- [ ] PC 오프라인: 기록한 depth 로 V2V-PoseNet 스켈레톤 추론 → 기본·엎드림·턱 괴기·기울임 그림(보고서용)
- [ ] `tools/tof_probe.py`: 축소 depth map 실시간 확인(디버그 ≤ 2 Hz)

#### 5. 작업 모드 판단 및 규칙 기반 FSM 추론 엔진 개발 — 마감 10-12

- [x] VER5 18상태 엔진, 이중 신뢰도·재정규화, PC/MIXED/비PC 컨텍스트·blend, 원인 라우팅, 게이트(0.45/0.75), 타이머·히스테리시스
- [x] `config/fsm.yaml` 외부화, 리플레이·데모 하네스, 세션 리포트 `report.py`(병합 PR), 테스트 46개 + 리포트 8개
- [x] 합성 센서로 대표 경로 재현 — `tools/demo_dryrun.py`·`test_demo_scenario.py`(DI, W4)
- [ ] 실센서 `SensorFrame` 로 대표 경로 재현: IDLE→START→CONTEXT_DETECT→FOCUS→FATIGUE_SUSPECT→FATIGUE→CAUSE_ANALYSIS→ACTION→MONITOR→RECOVERY→END
- [x] 10 s 주기에서 채터링 검증(합성 진동 입력) — `tests/test_chattering.py` (`feat/edge-hub-port`). 실센서 노이즈는 실측 후
- [ ] 자세 해석 분기(PC 숙임+키입력↓=피로, 비PC 숙임+motion↓=집중 등)를 실센서로 확인
- [ ] 신호 충돌(ToF 노딩 + mmWave active) 시 `interaction/request` 발행 → 사용자 확인 경로 (제안 게이트 ACTION_* 진입 시 질문 발행·`request_id` 매칭은 09-16 구현)
- [ ] `C_focus` 부호 최종 결정, 필요 시 문서·테스트 동시 수정
- [ ] MONITOR 보상 로그 축적(정책 학습은 로그 충분 시, 세션당 개입 상한)
- [ ] `tick()` ≤ 500 ms Pi 4 실측 — PC 측정 도구 `tools/measure_tick.py`(p95 ≈ 0.6 ms, DI). 보드 실측 남음

#### 6. 디스플레이 UI·제안 카드·작업 리포트 개발 — 마감 10-12

- [x] Atlas Flutter 앱, 대시보드 재설계, 18상태 전이 그래프, 키스트로크 패널, 센서 테스트 화면, 오디오 재생, release IPK 배포, MQTT 구독
- [x] 제안 카드 수락·거절 → hub 전달 (MQTT: hub 가 `interaction/request` 로 `request_id` 를 주고 `feedback/user` 에서 매칭, 09-16)
- [ ] 상태 적응형 4화면: 대기(시각·환경·재실) / 집중 저자극 / 터치 시 상세(집중 시간·환경·수동 제어) / 세션 리포트
- [x] 자동 실행 알림(≥ 0.75) + 되돌리기 + 실행 이유 문구 (DI, W1 — 되돌리기는 W3 의 60 s 창)
- [x] 정정(`correct`) 입력 — 4 국면 중 선택, 무응답(timeout) 기록 (DI, W1·W3)
- [ ] 세션 리포트 화면: 데모 리포트·상태 체류 차트 화면은 main(PR #22). hub `session/report` 10 s 스냅샷 발행 → display 구독 연결은 PR #27
- [ ] 재부팅 자동 시작(Pi 5 앱 + Pi 4 hub 서비스)
- [ ] 스피커 알림 음소거·볼륨

#### 7. 조명·환기팬·스마트 플러그 제어 모듈 개발 — 마감 10-05

- [ ] 스마트 플러그 로컬 프로토콜 확인·`control/` 어댑터(명령 ID·대상·값·원인·만료)
- [x] `ACTION_ENV` → `deskmate/control/cmd` → 결과 수신 → 실패·타임아웃 → MONITOR (`control/dispatcher.py`·`tools/mock_plug.py`, `feat/edge-hub-port`). 실기 플러그·재시도·수동 복구 UI 남음
- [x] 게이트 연동: 제안 수락 시 실행 / 자동 즉시 / 거절 시 undo, 쿨다운 300 s (`feat/edge-hub-port`). 자동 실행 **알림 화면**은 display 남음
- [x] 비가역 동작(전원 차단)은 자동 게이트에서 제외, 제안 수락 시에만 `requires_confirmation=true` (`feat/edge-hub-port`)
- [ ] ThinQ 1기기(공기청정기 또는 조명) 연동 여부 결정, 되면 어댑터 추가 (선택)
- [ ] 자격증명은 `.env`/gitignore `secrets.yaml` 만

#### 8. 데이터 수집·ESM 라벨링·임계값 보정 — 마감 10-19

- [x] ESM 라벨 스키마 확정(수락·거절·정정·무응답, 질문 ID·원 판정·응답 시간) — `data-spec.md` §11, `hub/deskmate_hub/esm.py`(DI, W3)
- [ ] 팀원 실사용 세션 로그 축적(JSONL + 라벨, 비공개 드라이브, 커밋 금지) — 목표 5인 × 3세션 이상
- [ ] `--replay` 로 임계값·가중치 보정, 정규화 전후 오판정 비교표
- [ ] 개인화 저장소(로컬 JSONL)·opt-in·삭제 정책 문서화
- [x] 증강 데이터 파이프라인 + 증거 리포트 `ml/training/`·`docs/ml-augmentation-evidence.md`, 공개 데이터셋 조사 `docs/dataset-survey.md`(DI, W5·W6)
- [ ] 2단계 TFLite(선택): PC 학습(1D CNN 백본 동결 + 개인 헤드) → TFLite → Pi 4 선택적 적재 → 게이트 융합. 라벨 부족 시 "개념 실증"으로 고정하고 보고서 서술 조정

#### 9. 통합 MVP 구현 — 마감 10-05

- [ ] 4-A MVP 통과 (09-18)
- [x] Pi 4 native service 에 UART 디코더·C++ MQTT 클라이언트 탑재 — 09-16 실수신, native MQTT 브리지 main(PR #21 계열)
- [ ] PC 브리지(`tools/uart_mqtt_bridge.py`) 제거 — Pi 4 경로로 실브로커까지 end-to-end 확인 후
- [x] **hub 테스트 수집 오류 수정** — `test_native_mqtt_bridge.py` 를 `MqttLineSource` API 로 갱신(09-29, `feat/display-hub-followup`)
- [ ] 4 신호(ToF·mmWave·환경·키스트로크) 모두 `SensorFrame`·`reasons` 에 등장
- [ ] 센서 → 피로 판정 → 제안 → 터치 수락 → 플러그 ON → MONITOR → RECOVERY 1사이클 실기 재현
- [ ] display 를 꺼도 hub 계속 동작, Node-RED 없이 동작, 인터넷 없이 동작 — PC 경로 시험 완료(DI, W6: 무응답 카드 자동 만료·센서 분리·브로커 재시작). 실기 남음
- [ ] Dock+Head 하우징에 3보드·센서·화면 조립, Pi 4 발열과 환경 센서 격벽, 부스 전원 1구 기동

#### 10. 시험 평가 및 최적화 — 마감 10-19

- [ ] 2시간 연속 구동: 판정 사이클 ≤ 500 ms(1,000 tick p95), 메모리·온도, 재연결
- [ ] 상태 분류 정확도·사용자 피드백 일치율·선제 행동 수용률 산출(평가 프로토콜 정의 포함) — 지표 계산·`session/report.metrics`·`tools/evaluate_sessions.py` 준비(DI, W3). 실사용 로그 남음
- [x] 결측·유실 시나리오(센서 분리, broker 재시작, display 종료) — `hub/tests/test_fault_scenarios.py`·`test_mqtt_reconnect.py`(DI, W6). 충돌(ToF 노딩 + mmWave active)은 ToF 후
- [x] 5분 시연 시나리오 고정(시작→몰입→피로→개입→회복→리포트) + 시연용 프로파일 — `docs/demo-scenario.md`, `--scenario demo`, 실시간 리허설 1회 통과(DI, W4)
- [ ] 금지 데이터 미수집·자격증명 스캔

#### 11. 개발 완료 보고서·작품 소개서·시연 영상 — 마감 10-30, 발표 11-06

- [ ] 개발완료보고서 20p(개요·배경 2 / HW·통신 3 / 센서 처리 4 / FSM·개인화 4 / UI·개입 3 / 시험 3 / 결론·링크 1) — 포스터 서술과 다른 항목은 구현 기준으로 정정(§3)
- [ ] 작품소개서 2p
- [ ] 시연 영상(고정 시나리오) YouTube
- [ ] 저장소 Public 전환: 시크릿·라벨 이력 점검, LICENSE·오픈소스 출처, README 에 영상·보고서 링크
- [ ] 발표 자료: "왜 카메라·마이크를 안 쓰나", 3종 HW·멀티모달 융합 가산점 근거, 실기 시연 동선
- [ ] 11-06 실기 시연 리허설

---

## 5. 리스크 상위 5

| 순위 | 리스크 | 영향 | 대응 |
|---|---|---|---|
| 1 | **ToF 미연결·경로 미결(09-19 마감 경과)** — 카메라 대역(I7)이 자리를 채우는 중 | G1 전체, "카메라 없이" 서술 | Path B 즉시 확정 후 ESP32 I2C 연결. 불가하면 I7 에서 결선 서술을 정한다 |
| 2 | 실센서 노이즈로 FSM 채터링 (10 s 주기라 30 s 보다 민감) | 시연 신뢰도 | 리플레이 튜닝 + 히스테리시스, 시연 시나리오 임계값 프로파일 별도 |
| 3 | **실기 미검증 누적** — 앱 연결 안정화·개입 UI·제어·결측 처리가 모두 PC·합성 입력으로만 확인됨 | 통합 MVP(10-05), 시연 신뢰도 | 보드 확보 즉시 PR #30~#32 체크리스트와 `docs/demo-scenario.md` §5 를 한 번에 확인 |
| 4 | 라벨 부족으로 2단계 미완 | 포스터 G6 미달 | 개념 실증 범위로 사전 고정, 보고서 서술 조정(I5) |
| 5 | **스마트 플러그 미선정(09-28 마감 경과)** | G7·G10 제어 시연, 통합 MVP 1사이클 | 즉시 구매. 도착 전에는 iLink 램프 + mock 플러그로 1사이클 시연 경로 확보 |
| — | ~~ESP32 코드가 별도 저장소~~ | — | **해소(09-14~16)** `firmware/esp32_sensor_node` 이관·실보드 플래시 |
| — | ~~Pi 5 터치 컨트롤러 사망~~ | — | **해소(09-14)** 디스플레이 교체 |

---

## 6. 이 문서의 갱신 규칙

- 항목을 끝내면 §4 체크박스를 채우고 §2 상태를 갱신한다. 마감을 넘기면 지우지 말고 새 마감과 이유를 한 줄 적는다.
- 확정 사항이 바뀌면 [`agent-briefing.md`](agent-briefing.md) §2·§3 을 같은 커밋에서 고친다.
- 노션 하드웨어 기록(통신 아키텍처 · 센서 스펙 · UART 실측)과 어긋나면 노션이 실측 기준, 이 문서가 계획 기준이다.
