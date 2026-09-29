# DESKMATE — 프로젝트 개발 가이드

> 프로젝트 확정·미결정 사항과 제약은 [docs/agent-briefing.md](docs/agent-briefing.md)에 정리돼 있다.
> **결선 목표(포스터 수준)까지의 갭과 실행 체크리스트는 [docs/roadmap.md](docs/roadmap.md)** 가 기준이다.

> 제24회 임베디드SW경진대회 · 스마트 가전 부문 · 팀 TEAMMATE
> ToF·mmWave·키스트로크·환경 센서를 융합해 작업 상태(시작·몰입·피로·종료)를 추론하고
> 조명·환기·자세·휴식을 자동 실행/제안하는 비침습 워크스페이스 가전.
> **카메라·마이크 미사용. 센싱·추론·제어 모두 로컬.**

이 문서는 팀 전체가 보는 **러프 개발 로드맵 + 모듈 현황 + 결정사항**이다. 기준일 **2026-09-29**(모듈 현황 갱신, 이전 09-14). 브랜치별 미반영 작업은 `docs/roadmap.md` §2-1.
상세 설계와 현재 계획은 `docs/README.md`의 문서 지도를 참조한다.

> **개발 방식은 각자 자유다.** 어떤 CLI 로 어떻게 작업하든 상관없다. 다만 push 나 파괴적 Git 작업은 명시적으로 요청할 때만 하게 해두는 편이 안전하다.

---

## 1. 시스템 한눈에

```
[ 센싱 ]                                   [ 추론 ]                    [ 출력·제어 ]
ESP32 (mmWave C1001·CO₂·온습도·조도) ─UART2─┐
VL53L9CX ToF (경로 미결: Pi4 CSI-2 / ESP32 I2C)┼─► Raspberry Pi 4 hub ─MQTT(이더넷 직결)─► Raspberry Pi 5
PC (키스트로크 타이밍) ──────────Wi-Fi/MQTT──┘   FSM(1단계)+TFLite(2단계)   Atlas Flutter 7" 터치 UI
                                                        │                    수락·거절·정정 피드백 ↩
                                                        ▼
                                               스마트 플러그 / ThinQ (조명·환기)
```

| 계층 | 장치 | 디렉터리 | 담당 |
|---|---|---|---|
| 센싱 | ESP32 말단 노드 | `firmware/` | 이민혁·김태환 |
| 센싱 | PC 키스트로크 | `collector/` | 최민경 |
| 추론 | Raspberry Pi 4 허브 (AI Native OS Headless + `hub/atlas` native service) | `hub/` | 박소연·김태환·조명희·이민혁 |
| 출력 | Raspberry Pi 5 단말 (AI Native OS Video Profile + Atlas Flutter) | `display/` | 최민경 |
| 학습 | PC → TFLite | `ml/` | 조명희 |

---

## 2. 개발 원칙 (모두 지킨다)

1. **1단계 규칙 FSM 을 완성도의 축으로.** 결선 완성도 60점. ML 정확도보다 규칙 FSM +
   실기기 제어의 확실한 동작이 우선. 2단계 분류기는 보완·검증.
2. **1단계 FSM 은 2단계 없이 단독 완전 동작.** 분류기는 import 실패해도 허브가 안 죽는 선택적 의존.
3. **임계값 하드코딩 금지.** 전부 `hub/deskmate_hub/config/*.yaml`. 실측 후 값만 교체.
4. **호흡은 보조 신호.** mmWave 순간 호흡·심박값은 판정에 쓰지 않는다(중앙값+기준선, "호흡 소실 여부"만 증거).
   `respiration_enabled: false` 기본. 호흡 실패가 전체 판정을 흔들면 안 됨.
5. **프라이버시.** 키 값 미수집(타임스탬프만). 카메라·마이크 미사용. 운영 ToF raw 미전송. 자가기록 라벨 커밋 금지.
6. **판정 사이클 ≤ 500ms**, 신뢰도 계산 주기 **10s**(`score_period_sec`, 2026-09-14 확정 — 포스터와 일치). ingest 가 10s 마다 `SensorFrame` 을 만든다.
7. **수직 슬라이스 우선.** 센서 1종이라도 FSM 을 거쳐 화면·제어까지 닿는 경로를 먼저 만들고 센서를 하나씩 붙인다.

---

## 3. 모듈별 현황

범례: ✅ 동작 · 🟡 부분 · 🚧 진행 · ⬜ 미착수

| 모듈 | 담당 | 상태 | 메모 |
|---|---|---|---|
| `hub/inference/` FSM 1단계 | 박소연 | ✅ | 18상태 엔진·`report.py`·리플레이·데모, 엎드림·노딩 시나리오. hub 테스트 114 통과·1 xfail(`feat/edge-hub-port` 기준, PR #27 위). 병합 `aec531a` 이후 옛 API 를 import 하던 `test_native_mqtt_bridge.py` 수집 오류는 PR #27 에서 수정 |
| `hub/atlas/` Pi 4 native service | 공통 | 🟡 | IPK 로 제한 Python FSM 실행, C++ `uart_rx`(09-16 실수신 rx=132·crc 0), C++ 네이티브 MQTT 브리지. 재부팅·재설치 후 `pi4-hub-activate.sh` 필요(`feat/edge-mvp-nodered`). 자동 시작 없음 |
| `hub/ingest/` + `live.py` | 이민혁·공통 | ✅ | MQTT·`UART	` 라인 → `SensorCache` → 10 s `SensorFrame` → FSM → `state/phase`·`interaction/request`·`health/hub`. `config/ingest.yaml` 임시 스케일 |
| `hub/features/` baseline 정규화 | 김태환 | 🟡 | 중앙값·MAD Modified z `baseline.py`(세션 보정 창·시간대 버킷·opt-in 저장), ingest 기본 `normalization: baseline` — `feat/edge-hub-port` 로 이관. z_full·mad_floor 실측 튜닝 남음 |
| `hub/control/` 플러그·ThinQ | 조명희 | 🟡 | iLink 블루투스 램프 어댑터(main, PR #22) + ACTION_ENV 디스패처·mock 플러그·`config/control.yaml`(`feat/edge-hub-port`, 보드에서는 `CMD	` 라인 → C++ 가 `control/cmd` 발행). **스마트 플러그 미선정** |
| `hub/inference/` TFLite 2단계 | 조명희 | ⬜ | 선택적 의존. 라벨 축적 후 |
| `ml/` 학습→TFLite | 조명희 | ⬜ | 비어 있음 |
| `firmware/` ESP32 | 이민혁·김태환 | 🟡 | C1001 + DrowsyDetector, 환경 센서(SCD41·BH1750·DHT22) 드라이버, USB JSON + UART2 COBS/CRC. 실보드 플래시 09-16. C1001 무응답·재시도·SCD41 수정 5커밋은 `feat/edge-mvp-nodered` |
| `display/` Atlas UI | 최민경 | 🟡 | 통합 앱(PR #23): 센서 개요·ENV·mmWave 심박·음악·블루투스 피드백·데모 세션 리포트. 4화면 전환·자동 실행 알림·자동 시작 없음 |
| `display/atlas/camsvc`·`camtest` 카메라 자세 | 박소연 | ⚠ | ESP32-CAM + MediaPipe 스켈레톤 판정(PR #20). 통합 앱 자세 탭은 `base/camera-zone-views`(PR #25). **"카메라 미사용" 원칙과 충돌 — 팀 결정 필요**(`docs/roadmap.md` §3 I7) |
| `collector/` 키스트로크 | 최민경 | ✅ | main 진입(PR #15). 공통 envelope 발행·이식성 설정 문서는 `feat/edge-hub-port`(테스트 10) |
| `tools/` | 공통 | ✅ | `uart_mqtt_bridge.py`, Node-RED 대시보드, Pi 4 정적 Mosquitto·핫스팟 스크립트(PR #18), 시나리오 sim·로컬 리허설 |
| ToF 파이프라인 | 김태환 | ⬜ | **미연결, Path A/B 미결(09-19 마감 경과).** 기하 특징 7종 코드 없음 |
---

## 4. 실행 계획 (2026-09-14 재기준)

주차 표는 두지 않는다. 계획은 [docs/roadmap.md](docs/roadmap.md) §4 의 **체크리스트**가 유일한 기준이다.

**4-A. 이번 주 MVP — 마감 09-18(금).** 실센서 값이 MQTT 로 들어와 **Node-RED 에서 시각화**되고, **FSM 을 통과한 결과(상태·C_fatigue·C_focus)가 표시**된다.
제어·개입·개인화는 범위 밖. 지름길 두 개(ESP32→PC USB 브리지, hub 를 PC Python 으로 실행)를 쓰되 계약은 제품 경로와 같게 지킨다.

| 요일 | 목표 |
|---|---|
| 화 09-15 | ESP32 코드 이관 + USB 1 Hz JSON 라인 → `tools/uart_mqtt_bridge.py` → broker. `feat/merge-pending` 머지 후 키스트로크 토픽 확인 |
| 수 09-16 | Node-RED 대시보드(mmWave·환경·키스트로크 차트, seq 갭, JSONL 로거) |
| 목 09-17 | `hub/ingest/mqtt_source.py` → 10 s `SensorFrame` → FSM → `state/phase` 발행, Node-RED FSM 패널 (보너스: Pi 5 MQTT 빌드) |
| 금 09-18 | 시나리오 1회 통과(착석→타이핑→정적→이탈), 리플레이 재현, 노션 기록, PR |

**4-B. 11월 완전 개발 — 개발계획서 11항목 체크리스트.** 마감은 결선 역산: 통합 MVP 10-05 · 시험 평가 10-19 · 서류 10-30 · 발표 11-06.

| # | 개발계획서 항목 | 마감 | 상태 |
|---|---|---|---|
| 1 | 요구사항 분석·시스템 구조 설계 | 09-19 | 🟡 **마감 경과** — ToF 경로·UART TYPE 확정·플러그 모델 남음 |
| 2 | HW 구성·센서 인터페이스 | 09-28 | 🟡 **마감 경과** — mmWave·환경·화면 완료, ToF·플러그 남음 |
| 3 | MQTT 통신·로깅 파이프라인 | 09-28 | 🟡 Pi 4 broker·UART 디코더·ingest·hub 발행 완료. 자동 시작·리플레이 포맷 정합 남음 |
| 4 | ToF·환경·키스트로크 특징 추출 | 10-05 | 🟡 키스트로크·mmWave·baseline(`feat/edge-hub-port`) 완료. ToF 7종·환경 특징 남음 |
| 5 | 작업 모드 판단·규칙 FSM 엔진 | 10-12 | ✅ 엔진 완료, 합성 채터링 테스트(`feat/edge-hub-port`). 실센서 검증·충돌 확인 남음 |
| 6 | 디스플레이 UI·제안 카드·리포트 | 10-12 | 🟡 통합 앱·데모 리포트 완료, 4화면·자동 알림·리포트 MQTT 연결·자동 시작 남음 |
| 7 | 조명·환기팬·플러그 제어 | 10-05 | 🟡 iLink 램프(main)·디스패처(`feat/edge-hub-port`). 스마트 플러그 미선정 |
| 8 | 데이터 수집·ESM 라벨링·임계값 보정 | 10-19 | ⬜ |
| 9 | 통합 MVP | 10-05 | 🟡 4-A(09-18) 실센서 통과 기록 없음. 제어 1사이클 미재현 |
| 10 | 시험 평가·최적화 | 10-19 | ⬜ |
| 11 | 보고서·소개서·시연 영상 | 10-30 | ⬜ |

**병목·의존성**
- UART 프레임 계약(mmWave TYPE·스키마·CRC test vector)이 Pi 4 디코더·`ingest`·`firmware` 착수의 선행조건. MVP 는 PC 브리지로 우회.
- ToF Path A/B 결정(09-19)이 항목 2·4 의 게이트. 실패 시 Path B 자동 확정.
- 스마트 플러그 모델 선정이 항목 7·9 의 게이트.

---

## 5. 지금 정해야 할 결정사항

태그: **[사무국]** 대회 측 확인 · **[팀]** 팀 협의 · **[기본값]** 코드에 잠정 기본값 적용됨 · **[해소]** 결정 완료

### 해소된 항목 (기록)
- **[해소] 3종 HW** — ESP32 · Pi 4 · Pi 5 3종 유지. LG 제공 구성과 일치.
- **[해소] Pi 4↔Pi 5 통신** — 이더넷 직결 + Mosquitto(Pi 4) MQTT. TCP 1883 데이터, 22 SSH.
- **[해소] ESP32↔Pi 4 물리** — UART2(ESP32 GPIO25 TX/GPIO26 RX ↔ Pi 4 GPIO15/GPIO14), 09-11 실측. COBS + CRC-16/CCITT-FALSE.
- **[해소] Pi 4 FSM 런타임** — AI Native OS Headless(ATLAS). Python·컴파일러 없음 → `hub/atlas` native service IPK 가 제한 Python 을 감싼다. UART 디코더는 그 C++ 서비스 안에.
- **[해소] ToF 스켈레톤** — 채택(09-08). 단 온보드 실시간은 기하 특징 7종, 스켈레톤은 오프라인 검증(§I2).
- **[해소 09-14] 신뢰도 계산 주기** — `score_period_sec: 10`(포스터와 일치). 지속 조건은 초 단위 타이머라 무관.
- **[해소 09-14] 키스트로크 계약** — `collector/` 구현 기준 확정(60 s 윈도우·1 Hz·QoS 0·additive 신호·미입력=`typing_active=false`). 세부 튜닝은 이후. `data-spec.md` §6.4.
- **[해소 09-14] SCD41·DHT22** — 보정 없음, 단일 센서 측정(CO₂=SCD41, T/RH=DHT22, lux=BH1750).
- **[해소 09-14] Pi 5 터치** — 디스플레이 교체, 터치 정상.
- **[해소] 미머지 브랜치(09-14분)** — `feat/merge-pending` 은 PR #15 경유로 main 포함.
- **[해소 09-18] 브로커 위치** — Pi 4 정적 Mosquitto(PR #18). 보드 제한 Python 에 `_socket` 이 없어 hub MQTT 는 C++ 네이티브.

### 열려 있는 항목
0. **[팀·긴급] 카메라 자세 판정(I7)** — main 의 ESP32-CAM 판정을 개발용 대역으로 한정할지, 제품 기능으로 채택하고 "카메라 미사용" 서술을 바꿀지.
1. **[팀·09-19 마감 경과] ToF 연결 경로** — Path A(Pi 4 MIPI CSI-2 풀해상도) vs Path B(ESP32 I2C binning). 결정 기록 없음, 규칙상 B.
2. **[팀] UART 프레임 TYPE·스키마** — 잠정안(0x20 mmWave·0x10 env·0xF0 heartbeat, `data-spec.md` §13.1)으로 구현·실수신 중. 팀 확정만 남음. 목표 속도 460,800+ 실측.
3. **[팀·09-28 마감 경과] 스마트 플러그 모델** — 로컬 제어 가능 모델 선정·구매. 통합 MVP(10-05) 제어 1사이클의 게이트.
9. **[팀] 미머지 작업 PR** — PR #27(`feat/display-hub-followup`) → `feat/edge-hub-port`(펌웨어 C1001 수정·collector envelope·baseline·디스패처, PR #27 위에 쌓음) 순서로 머지.
4. **[팀] 새 디스플레이 기록** — 모델명·인터페이스(DSI/HDMI)·전원 경로를 `hardware.md`에 적기.
5. **[기본값] `C_focus` 부호** — 현재 "큰 값 = 집중 저하 증거". 바꾸려면 알려주기.
6. **[기본값] 개인화 저장소** — 로컬 파일(JSONL) 잠정. opt-in·삭제 정책은 10-19 전.
7. **[기본값] RL 정책** — 고정 규칙. 로그 축적 후 활성, 세션당 개입 상한.
8. **[사무국] 소스 공개 범위** — 전체 Public, 시크릿·라벨만 분리(기본값).

---

## 6. FSM 추론 엔진(inference/) 개발 현황 — 박소연

**상태: ✅ 1단계 코어 동작 · `main` 머지됨** (09-29: main 테스트 수집 오류 1건 발견, `feat/display-hub-followup` 에서 수정)

기준 설계: `docs/fsm-spec.md` (VER5, 5계층 18상태, C_fatigue/C_focus 이중 모니터링).

완료
- `config/fsm.yaml` — 임계값·컨텍스트 가중치·타이머(`score_period_sec: 10`)·게이트(0.45/0.75)·라우팅 전부 외부화.
- `inference/` — states(18상태) · types(SensorFrame/Signal 계약) · scoring(이중 신뢰도+재정규화) ·
  context(PC/MIXED/비PC + blend) · cause(dominant 라우팅) · engine(tick 루프·타이머·게이트).
- `tests/` — 전이 15 · 자세 시나리오(엎드림·노딩) 9 · 표시 9 · 리플레이 11 — **46개(45 통과 · 1 xfail)**, `pytest tests/ -q` 1초.
- 리플레이·데모 하네스 — `python -m deskmate_hub --replay <log.jsonl>` / `--demo`.
- Atlas 미리보기 API(`demo` 서브커맨드, HTTP 8765)와 `bridge` 서브커맨드(`hub/atlas` native service 용 라인 프로토콜).
- 실행: `cd hub && pip install -r requirements.txt && pytest tests/`

진행/예정
- 🟡 실센서 `SensorFrame` 연결 — `ingest/` 완료, UART 실수신 확인. 실센서 대표 경로 재현 기록은 아직 없음. baseline 은 `feat/edge-hub-port`.
- ✅ hub 측 MQTT 발행(`state/phase` retain, `interaction/request`, `feedback/user` 구독).
- ✅ `report.py` 세션 리포트 main 포함. `session/report` 10 s 스냅샷 발행·display 연결은 PR #27.
- 🟡 `control/` ACTION_* → 실제 제어 — iLink 램프(main), 디스패처(`feat/edge-hub-port`), 스마트 플러그 미선정. 10-05.
- ⬜ 2단계 TFLite 확신도 → 게이트 융합 — 10-19, 선택적.
- 🚧 MONITOR 의 RL 정책 — 고정 규칙 유지. 로그 축적 후.

---

## 7. 협업·개발 규칙

- **`main` 직접 push 금지.** `feat/<기능명>` 브랜치 → PR → 리뷰 후 머지.
- 커밋: `feat(scope): …` / `fix(scope): …` / `docs(scope): …` / `chore(scope): …`
- 데이터셋·학습 모델·자격증명 커밋 금지(`.gitignore`). `secrets.yaml` 은 환경변수/gitignore.
- FSM 상태 전이는 합성 입력으로 단위 테스트(실기기 없이 검증 가능한 유일한 부분).
- 임계값 바꿀 땐 코드가 아니라 `config/*.yaml` 만 수정.
- 푸시·브랜치 전환·병합·리베이스·stash·reset 은 명시적으로 요청할 때만 하게 해두면 사고가 줄어든다. (권장)
- 프로젝트 확정·미결정 사항은 [`docs/agent-briefing.md`](docs/agent-briefing.md) 에 모아뒀다. 바뀌면 그 문서를 고친다.
- 하드웨어 실측 기록은 노션(통신 아키텍처 · 센서 스펙 · UART 실측)이 원본이다. 레포 문서는 그 결론만 옮긴다.
