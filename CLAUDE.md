# DESKMATE — 프로젝트 개발 가이드

> 프로젝트 확정·미결정 사항과 제약은 [docs/agent-briefing.md](docs/agent-briefing.md)에 정리돼 있다.
> **결선 목표(포스터 수준)까지의 갭과 실행 체크리스트는 [docs/roadmap.md](docs/roadmap.md)** 가 기준이다.

> 제24회 임베디드SW경진대회 · 스마트 가전 부문 · 팀 TEAMMATE
> ToF·mmWave·키스트로크·환경 센서를 융합해 작업 상태(시작·몰입·피로·종료)를 추론하고
> 조명·환기·자세·휴식을 자동 실행/제안하는 비침습 워크스페이스 가전.
> **카메라·마이크 미사용. 센싱·추론·제어 모두 로컬.**

이 문서는 팀 전체가 보는 **러프 개발 로드맵 + 모듈 현황 + 결정사항**이다. 기준일 **2026-11-02**(main `7e7289e`, PR #103 까지. 이전 10-01).
서술 수준의 기준은 **개발완료보고서 v17** 이고, 숫자·실측은 노션이 원본이다. 아래 "(DI)" 표기는 PR #33 으로 머지돼 더 이상 쓰지 않는다.
상세 설계와 현재 계획은 `docs/README.md`의 문서 지도를 참조한다.

> **개발 방식은 각자 자유다.** 어떤 CLI 로 어떻게 작업하든 상관없다. 다만 push 나 파괴적 Git 작업은 명시적으로 요청할 때만 하게 해두는 편이 안전하다.

---

## 1. 시스템 한눈에

```
[ 센싱 ]                                   [ 추론 ]                    [ 출력·제어 ]
ESP32 (ToF L8 ×2·mmWave C1001·CO₂·온습도·조도)┐
            └ USB 시리얼(COBS+CRC-16, 115200)─┼─► Raspberry Pi 4 hub ─MQTT(이더넷 직결)─► Raspberry Pi 5
PC (키스트로크 타이밍) ──────────Wi-Fi/MQTT──┘   ToF 자세(CNN+GRU)            Atlas Flutter 7" 터치 UI
                                                 FSM 18상태 + 개인화          수락·거절·되돌리기 ↩
                                                        │                            │ BLE·A2DP
                                                        ▼                            ▼
                                               ThinQ 공기청정기(연동 예정)   무드등·선풍기·스피커
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
| `hub/inference/` FSM 1단계 | 박소연 | ✅ | 18상태 엔진·이중 신뢰도·원인 라우팅·게이트(0.45/0.75+α)·조합 조건 guard(PR #46)·빠른 시연 모드(`demo_fast`). `report.py`·리플레이·데모 하네스 |
| `hub/atlas/` Pi 4 native service | 공통 | ✅ | IPK 로 제한 Python FSM 실행, C++ USB/UART 디코더·네이티브 MQTT·ToF 0x05 중계·제어 로그 API(8765). **부팅 자동 시작은 ATLAS 제약으로 불가** — 부팅마다 `install.sh pi4 --runtime-only` |
| `hub/atlas-tof/` ToF 자세 서비스 | 김태환 | ✅ | 센서별 8×8 → CNN+GRU → log-softmax 융합(9라벨), 장치 위치별 모델 전환·GRU 리셋(PR #52·#55·#72). 벽 잡음 실측 σ 2 mm — 오판 원인 아님(PR #67·#76) |
| `hub/ingest/` + `live.py` | 이민혁·공통 | ✅ | USB 직결(PR #61)·MQTT → `SensorCache` → 10 s `SensorFrame` → FSM → `state/phase`·`interaction/request`·`health/hub`. 환경 특징(CO₂ 상승·온습도·조도 `env_flags`)·ESM 기록 |
| `hub/features/` baseline 정규화 | 김태환 | ✅ | 중앙값·MAD Modified z(세션 보정 창·시간대 버킷·opt-in 저장), ingest 기본 `normalization: baseline` |
| `hub/control/` 제어 | 조명희 | ✅ | ACTION_* → `control/cmd`/`control/result`, % 밝기·쿨다운 300 s·되돌리기·비가역 자동 금지, CO₂ 창문 환기(`co2_window.yaml`), 자리 비움 끄기/복귀, **제어 진행 로그**(PR #101·#103). 가전 제어 플랫폼·Mock Gateway(PR #57·#80) |
| 개인화 (`personalize/` `learning/` `personalization/`) | 조명희·박소연 | ✅ | 보드에서 도는 프로필 prior·암묵 신호·반응학습(PR #73). 2단계 공용 1D CNN 은 **관찰 전용**(FSM 판정 불변) |
| `ml/` 학습·변환 | 조명희 | 🟡 | 공용 CNN 학습·증강·portable/TFLite 변환·리플레이 비교. **실사용 ESM 라벨로 재학습은 결선 이후** |
| `firmware/esp32_sensor_node` | 이민혁·김태환 | ✅ | VL53L8CX ×2(UART `0x05`)·C1001 mmWave·SCD41/BH1750/DHT22, USB 직결 COBS+CRC-16. 10-24 실기 rx 527·crc 0 |
| `firmware/pico2w_ble_fan` | 박소연 | 🟡 | BLE 선풍기 1·2·3단·자연풍·꺼짐 타이머(PR #66·#68·#69·#70). 주소·광고·본딩 수정 PR #91·#93 은 **실기 미검증** |
| `display/` Atlas UI | 최민경 | ✅ | DESKMATE 앱 V46 — 위젯 홈·제안/자동 실행 카드·되돌리기·리포트·개발자 모드·제어 진행 탭, 블루투스 세 기기(램프·선풍기·스피커) 연결(PR #95·#97). 재부팅 자동 시작 없음 |
| `display/atlas/camsvc`·`camtest` | 박소연 | 개발용 | ToF 도착 전 자세 판정을 확인하려고 만든 **별도 개발 앱**. 제품 앱(`ui_demo_app`)에는 카메라 기능이 없고, 제품 자세 판정은 ToF 다. 시연·제출 빌드에 넣지 않는다 |
| `collector/` 키스트로크 | 최민경 | ✅ | 키 값 미수집 타이밍 특징, Pi 4 브로커 자동 찾기·로그인 자동 시작(PR #98) |
| `tools/` | 공통 | ✅ | 노트북 개발자 화면 devview(제어 진행·빠른 시연)·시나리오 sim·로컬 리허설·복구 설치기·Node-RED·ThinQ 수집기 |
| `dataset/` | 김태환·박소연 | ✅ | MuJoCo·SMPL-X ToF 시뮬(`tof_sim_v5`), 실측 녹화·벽 잡음 도구, 깊이 카메라 교사 라벨(`depth_teacher`, **오프라인 라벨링 전용·제품 경로 아님**) |
---

## 4. 개발계획서 11항목 — 최종 상태 (2026-11-02)

서류 제출 10-28 · 발표 심사 11-06. 상세 이력은 [docs/roadmap.md](docs/roadmap.md).

| # | 개발계획서 항목 | 상태 |
|---|---|---|
| 1 | 요구사항 분석·시스템 구조 설계 | ✅ 3종 보드·USB 직결·MQTT·ToF 경로 전부 확정 |
| 2 | HW 구성·센서 인터페이스 | ✅ ToF L8 ×2·mmWave·환경 3종 배선·실수신. 하우징은 조립 수준 |
| 3 | MQTT 통신·로깅 파이프라인 | ✅ Pi 4 Mosquitto·C++ 네이티브 MQTT·USB 디코더·ESM/제어 로그. **부팅 자동 시작만 불가**(ATLAS 제약) |
| 4 | ToF·환경·키스트로크 특징 추출 | ✅ 4종 신호 → 10 s `SensorFrame`, 개인 기준선 정규화 |
| 5 | 작업 모드 판단·규칙 FSM 엔진 | ✅ 18상태·guard·게이트. 실센서 경로 10-24 확인 |
| 6 | 디스플레이 UI·제안 카드·리포트 | ✅ 앱 V46. 재부팅 자동 시작 없음 |
| 7 | 조명·환기팬·제어 | ✅ BLE 무드등·선풍기·스피커 + 되돌리기·쿨다운·제어 진행 로그. ThinQ 공기청정기는 API 확인까지(FSM 연동 예정) |
| 8 | 데이터 수집·ESM 라벨링·임계값 보정 | 🟡 ESM·KSS 수집 도구·시뮬 보정 완료. **실사용 로그 기반 보정은 결선 이후** |
| 9 | 통합 MVP | ✅ 실센서 → FSM → 제어 → 되돌리기 경로 실기 확인(10-24 · 11-02) |
| 10 | 시험 평가·최적화 | 🟡 결측·유실·판정 주기·시연 시나리오 완료. **2시간 연속 구동 미측정** |
| 11 | 보고서·소개서·시연 영상 | 🟡 보고서 v17·소개서 제출. **시연 영상 링크 미정** |

**남은 것 (11-02 기준)**
- 시연 영상 업로드 → README·보고서·노션에 링크
- 부팅 자동 시작: ATLAS 이미지에서 불가 → 부팅마다 `install.sh pi4|pi5 --runtime-only`. 영구 등록 방법은 LG 측 확인
- 선풍기 펌웨어 PR #91·#93 실기 확인, 2시간 연속 구동, 실사용 효과 측정

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
- **[해소 10-12] ToF 센서** — VL53L9CX 는 LDD 폴트로 포기, **VL53L8CX(8×8) 두 개**를 ESP32 I2C(Path B)에 붙인다. 10-16 실물 연결(펌웨어 `TofArray`, 실기 미검증).
- **[해소 10-17] ToF 데이터 단위 = 센서 하나의 8×8** — 두 센서는 바깥 8° 조준이라 시야가 대부분 겹쳐 16×8 스티칭은 한 장면이 아니다. ESP32 는 원본 8×8 을 센서별 UART `0x05` 프레임으로 Pi 4 에 보내고(같은 frame_id·TS, 5 Hz), Pi 4 모델은 **센서별 추론 → log-softmax 합 융합**. 16×8 은 노드 규칙 추정기(0x03)·수집기 표시 내부 근사. `docs/data-spec.md` §6.1·§13.1.
- **[해소 10-17] 자세 모델은 Pi 4 에서** — ESP32 는 원본 전달만.
- **[해소 10-16] ToF 연결 경로** — **Path B**(ESP32 I2C `Wire1` 1 MHz). L8 두 개 실물 연결, 주소는 LPn 으로 분리.
- **[해소 10-23] 자세 모델의 Pi 4 배치** — 별도 서비스 `deskmate_tofposture`(CNN+GRU, `hub/atlas-tof`). `ingest.yaml tof.posture_source: service`, 없으면 MLP → 0x03 규칙 폴백. 실측 채점(macro-F1 0.456, 후보 17개 중 1위)으로 골랐다.
- **[해소] UART 프레임 TYPE** — 0x20 mmWave · 0x10 env · 0x03 자세 규칙 · 0x05 ToF 원본 8×8 · 0xF0 heartbeat. 구현·실수신 기준으로 확정(`data-spec.md` §13.1). 운영 경로는 **USB 직결**, GPIO UART2 는 대체 경로.
- **[해소] 제어 기기** — 스마트 플러그는 **채택하지 않는다**. iLink BLE 무드등 · Pico 2 W BLE 선풍기 · A2DP 스피커로 가고, LG ThinQ 공기청정기는 API 확인까지(FSM 연동은 예정).
- **[해소 11-02] 카메라 자세 판정(I7)** — **제품 경로에 카메라는 없다.** 제품 앱(`ui_demo_app`)에 카메라 기능이 없고 자세는 ToF 가 판정한다. `camsvc`·`camtest` 는 ToF 도착 전에 쓴 별도 개발 앱, `dataset/depth_teacher` 는 오프라인 라벨링 도구다. 보고서 v17 도 같은 기준으로 서술. (레포에서 개발용 코드를 지울지는 아래 열린 항목.)

### 열려 있는 항목
0. **[팀] 개발용 카메라 코드를 레포에서 지울지** — 제품 경로가 아닌 것은 위에서 정리됐다. 남은 선택은 `display/atlas/camsvc`(29파일)·`camtest`(48파일)를 지울지, 개발 기록으로 남길지다. 지워도 git 이력에는 남고, 제품 앱의 로그·설정 파일 경로 이름이 `camtest` 라 **경로 이름은 건드리면 안 된다**(보드 설치본이 그 경로를 읽는다).
4. **[팀] 새 디스플레이 기록** — 모델명·인터페이스(DSI/HDMI)·전원 경로를 `hardware.md`에 적기.
5. **[기본값] `C_focus` 부호** — 현재 "큰 값 = 집중 저하 증거". 바꾸려면 알려주기.
6. **[기본값] 개인화 저장소** — 로컬 파일(JSONL). opt-in·삭제 정책은 `privacy.yaml`·`docs/personalization-privacy-flow.md`.
7. **[기본값] RL 정책** — 맥락 밴딧 + 울타리. 세션당 개입 상한 6회·쿨다운 300 s.
8. **[사무국] 소스 공개 범위** — 전체 Public, 시크릿·라벨만 분리(기본값). 11-02 시크릿 스캔: 추적 파일·이력에 토큰·키·비밀번호 리터럴 없음.
10. **[기본값 10-01] 재시도 원인 게이트** — 첫 원인이 효과 없어 다른 원인을 시도할 땐 자동 대신 제안(`gate.retry_max: suggest`). 포스터 값 0.45/0.75 유지. 바꾸려면 알려주기.

---

## 6. FSM 추론 엔진(inference/) 개발 현황 — 박소연

**상태: ✅ 1단계 완료 · `main`** (11-02: hub 테스트 803 통과 · 9 skip · 1 xfail)

기준 설계: `docs/fsm-spec.md` (VER5, 5계층 18상태, C_fatigue/C_focus 이중 모니터링).

완료
- `config/fsm.yaml` — 임계값·컨텍스트 가중치·타이머(`score_period_sec: 10`)·게이트(0.45/0.75)·라우팅 전부 외부화.
- `inference/` — states(18상태) · types(SensorFrame/Signal 계약) · scoring(이중 신뢰도+재정규화) ·
  context(PC/MIXED/비PC + blend) · cause(dominant 라우팅) · engine(tick 루프·타이머·게이트).
- `tests/` — 전이·자세 시나리오·표시·리플레이·guard·개인화·제어까지 **803 통과 · 9 skip · 1 xfail**(11-02, Linux).
- 리플레이·데모 하네스 — `python -m deskmate_hub --replay <log.jsonl>` / `--demo`.
- Atlas 미리보기 API(`demo` 서브커맨드, HTTP 8765)와 `bridge` 서브커맨드(`hub/atlas` native service 용 라인 프로토콜).
- 실행: `cd hub && pip install -r requirements.txt && pytest tests/`

진행/예정
- ✅ 실센서 `SensorFrame` 연결 — USB 직결로 ToF·mmWave·환경 수신, 10-24 실기에서 IDLE → START 확인.
- ✅ hub 측 MQTT 발행(`state/phase` retain, `interaction/request`, `feedback/user` 구독).
- ✅ 세션 리포트·`session/report` 10 s 스냅샷·정량 지표(수용률·무응답률·되돌림률·정정률·회복 시간).
- ✅ `control/` ACTION_* → 실제 제어 — BLE 무드등·선풍기·스피커, 제어 진행 로그.
- ✅ 조합 조건(guard) — 다신호 AND 규칙(`docs/guard-spec.md`, PR #46). `config/fsm.yaml` `guards:` 에 선언.
- 🟡 2단계 모델 → 게이트 융합 — 지금은 **관찰 전용**. 융합은 실사용 라벨 축적 후.
- 🟡 MONITOR 의 개입 선택 — 맥락 밴딧 + 울타리(PR #73). 실사용 보상 축적은 결선 이후.

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
