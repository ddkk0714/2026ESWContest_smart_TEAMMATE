# DESKMATE — 프로젝트 개발 가이드

> 프로젝트 확정·미결정 사항과 제약은 [docs/agent-briefing.md](docs/agent-briefing.md)에 정리돼 있다. 세션 시작 문구 예시는 [docs/agent-kickoff-prompt.md](docs/agent-kickoff-prompt.md).
> **결선 목표(포스터 수준)까지의 갭과 주차별 계획은 [docs/roadmap.md](docs/roadmap.md)** 가 기준이다.

> 제24회 임베디드SW경진대회 · 스마트 가전 부문 · 팀 TEAMMATE
> ToF·mmWave·키스트로크·환경 센서를 융합해 작업 상태(시작·몰입·피로·종료)를 추론하고
> 조명·환기·자세·휴식을 자동 실행/제안하는 비침습 워크스페이스 가전.
> **카메라·마이크 미사용. 센싱·추론·제어 모두 로컬.**

이 문서는 팀 전체가 보는 **러프 개발 로드맵 + 모듈 현황 + 결정사항**이다. 기준일 **2026-09-14**.
상세 설계는 `docs/` 참조. (`README.md`=소개, `docs/architecture.md`, `docs/fsm-spec.md`,
`docs/mqtt-topics.md`, `docs/hardware.md`, `docs/data-spec.md`)

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
| `hub/inference/` FSM 1단계 | 박소연 | ✅ | 18상태 엔진, 테스트 46개(45 통과·1 xfail), 리플레이·데모 하네스, 엎드림·노딩 시나리오(PR #11). `score_period_sec: 10`. `report.py`·`--report` 는 `feat/merge-pending` PR 로 진입 중 |
| `hub/atlas/` Pi 4 native service | 공통 | ✅ | ARC IPK 로 제한 Python 을 감싸 FSM 실행, HTTP 8765 개발 API. **UART 디코더·MQTT 발행은 미구현** |
| `hub/ingest/` UART·MQTT 수신 | 이민혁 | ⬜ | 빈 패키지. W1 최우선 — UART 라인 + MQTT 키스트로크 → `SensorFrame` |
| `hub/features/` baseline 정규화 | 김태환 | ⬜ | 빈 패키지. 중앙값·MAD Modified z-score → `phi/delta` (포스터 G6 전제) |
| `hub/control/` 플러그·ThinQ | 조명희 | ⬜ | 빈 패키지. 스마트 플러그 기본 라인 |
| `hub/inference/` TFLite 2단계 | 조명희 | ⬜ | 선택적 의존. 라벨 축적 후 |
| `ml/` 학습→TFLite | 조명희 | ⬜ | 비어 있음 |
| `firmware/` ESP32 | 이민혁·김태환 | 🟡 | **C1001 파서·DrowsyDetector·UART2 송신이 별도 저장소에 있음** → 이 레포로 이관 필요. 환경 센서 미구현 |
| `display/` Atlas UI | 최민경 | 🟡 | 대시보드·18상태 그래프·키스트로크 패널·오디오·MQTT 구독, release IPK 배포. **디스플레이 교체·터치 정상(09-14)**. 리포트 화면·저자극 토글·자동 시작 없음 |
| `collector/` 키스트로크 | 최민경 | 🟡 | 구현·테스트 8개. `feat/merge-pending` PR 로 main 진입 중. **계약은 구현 기준 확정(09-14)**. hub 수신 없음 |
| `tools/` | 공통 | ✅ | Node-RED 모니터(MQTT 주입·관찰), Pi 4 SSH 탐색 스크립트 |
| ToF 파이프라인 | 김태환 | ⬜ | 경로 미결(Path A/B). 스켈레톤(V2V-PoseNet) 채택(09-08)했으나 코드 없음. 기하 특징 7종 폴백 설계만 |

---

## 4. 전체 개발 로드맵 (2026-09-14 재기준)

상세는 [docs/roadmap.md](docs/roadmap.md) §4. 여기서는 주차 목표만.

| 주차 | 기간 | 목표 | 통과 기준 |
|---|---|---|---|
| W1 | 09-15~09-21 | **mmWave 수직 슬라이스** — ESP32 코드 이관, Pi 4 UART 디코더, ingest(10 s 프레임), hub MQTT 발행, `feat/merge-pending` PR 머지 | C1001 앞 움직임이 Pi 5 화면에 반영, 정적 지속 시 FATIGUE_SUSPECT 진입 |
| W2 | 09-22~09-28 | ToF 경로 확정(09-19 spike)·기하 특징 7종, 환경 센서(단일 센서·보정 없음), 키스트로크 연결(확정 계약), 새 화면 배선 기록·자동 시작 | 4 신호 모두 `SensorFrame`·FSM `reasons` 에 등장 |
| W3 | 09-29~10-05 | 스마트 플러그 제어, 게이트 제안/자동 실동작, UI 4화면(리포트 신규), 자동 시작 | 센서→피로→제안→수락→플러그 ON→회복 1사이클 실기 재현 |
| W4 | 10-06~10-12 | baseline 정규화, 실측 로그 리플레이 튜닝, 2단계 TFLite(개념 실증), 스켈레톤 오프라인 검증 그림 | 정규화 전후 오판정 비교표 |
| W5 | 10-13~10-19 | Dock+Head 하우징 조립, 2시간 장시간 시험, 시연 시나리오 고정 | 사이클 ≤ 500ms, 재연결·온도 이상 없음 |
| W6~7 | 10-20~10-30 | 개발완료보고서·작품소개서·시연영상·저장소 공개 | `docs/submission.md` 체크리스트 |
| 발표 | 11-06 | 오프라인 발표 심사 | 실기 시연 |

**병목·의존성**
- W1 의 UART 프레임 계약(mmWave TYPE·스키마·CRC test vector)이 `ingest`·`firmware` 착수의 선행조건.
- ToF Path A/B 결정(09-19)이 W2 전체의 게이트. 실패 시 Path B 자동 확정.
- Pi 5 터치는 디스플레이 교체로 해소(09-14). 남은 시연 리스크 1순위는 ToF 경로.

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
- **[진행 09-14] 미머지 브랜치** — `feat/merge-pending`(fsm-replay + fsm-report + collector-keystroke) 푸시. PR 머지 후 세 브랜치 삭제.

### 열려 있는 항목
1. **[팀·09-19 마감] ToF 연결 경로** — Path A(Pi 4 MIPI CSI-2 풀해상도) vs Path B(ESP32 I2C binning). 1일 spike 후 결정, 실패 시 B.
2. **[팀] UART 프레임 TYPE·스키마** — mmWave TYPE 신규 배정(실험 펌웨어는 0x01 을 임시 사용 → ToF 디버그와 충돌), DrowsyDetector 출력(state·evidence vs 요약값 포함), 목표 속도 460,800+ 실측.
3. **[팀] 스마트 플러그 모델** — 로컬 제어 가능 모델(클라우드 의존 없음) 선정·구매. W2 안.
4. **[팀] 새 디스플레이 기록** — 모델명·인터페이스(DSI/HDMI)·전원 경로를 `hardware.md`·`hardware-bringup.md` 에 적기.
5. **[기본값] `C_focus` 부호** — 현재 "큰 값 = 집중 저하 증거". 바꾸려면 알려주기.
6. **[기본값] 개인화 저장소** — 로컬 파일(JSONL) 잠정. opt-in·삭제 정책은 W4 전.
7. **[기본값] RL 정책** — 고정 규칙. 로그 축적 후 활성, 세션당 개입 상한.
8. **[사무국] 소스 공개 범위** — 전체 Public, 시크릿·라벨만 분리(기본값).

---

## 6. FSM 추론 엔진(inference/) 개발 현황 — 박소연

**상태: ✅ 1단계 코어 동작 · `main` 머지됨**

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
- ⬜ 실센서 `SensorFrame` 연결 — `ingest/`(W1) 와 `features/` baseline(W4) 이 선행.
- ⬜ hub 측 MQTT 발행(`state/phase` retain, `interaction/request`, `feedback/user` 구독) — W1.
- 🟡 `report.py` 세션 리포트 — `feat/merge-pending` PR 머지 대기. 이후 리포트 화면(W3)과 계약.
- ⬜ `control/` ACTION_* → 실제 제어 — W3.
- ⬜ 2단계 TFLite 확신도 → 게이트 융합 — W4, 선택적.
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
