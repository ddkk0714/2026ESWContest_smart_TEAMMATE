# DESKMATE AI 개발 브리핑 (참고용)

> 대상: Claude Code · Codex 등 AI CLI 로 이 저장소를 다루는 팀원
> 기준일: 2026-09-14 (노션 09-11 UART 실측 반영) · 결선 계획은 [`roadmap.md`](roadmap.md)

**이 문서는 참고 자료다. 개발 방식과 도구 사용법은 각자 자유다.**
누가 어떤 CLI 를 어떻게 쓰든 상관없고, 이 문서가 그걸 정하지 않는다.

AI 에이전트는 저장소 밖의 맥락을 모른다. 그래서 매번 같은 걸 되묻거나,
아직 미정인 항목을 혼자 결정해버리거나, 프라이버시 제약을 모른 채 코드를 만든다.
이 문서는 **그 맥락만** 한곳에 모아둔 것이다. 세션 시작할 때 읽히면 그런 사고를 줄일 수 있다.

| 절 | 내용 | 성격 |
|---|---|---|
| §2 확정 · §3 미결정 · §4 계약 문서 · §5 제약 | 프로젝트 사실 | **공유하면 좋은 부분.** 누가 작업하든 동일하다. |
| §6 작업 방식 · §7 자주 하는 실수 | 운영 예시 | **한 사람의 방식일 뿐.** 각자 편한 대로 바꿔 쓰면 된다. |

---

## 1. 프로젝트 한 문단

DESKMATE는 제24회 임베디드SW경진대회 스마트 가전 부문 팀 TEAMMATE의 작품이다.
ToF·mmWave·환경 센서·키스트로크 타이밍을 **로컬에서** 융합해 책상 작업 상태를 VER5 18개 내부 상태로 추론하고,
신뢰도에 따라 조명·환기·자세·휴식을 자동 실행하거나 사용자에게 제안한다.
**카메라·마이크를 쓰지 않는다. 센싱·추론·제어가 모두 로컬 장치에서 끝난다.**

```
ESP32(mmWave·환경) ─UART2(COBS+CRC-16)─┐
PC(키 타이밍) ──────Wi-Fi/MQTT─────────┼─► Raspberry Pi 4 : UART 디코딩 · 융합 · FSM · 제어 판단 ─► 플러그 · 조명 · 환기
VL53L9CX(경로 미결: Pi4 CSI-2 / ESP32 I2C)┘            │
                                                      └─MQTT(이더넷 직결)─► Raspberry Pi 5 : Atlas Flutter UI · 제안/정정 입력
```

| 계층 | 장치 | 디렉터리 |
|---|---|---|
| 센싱 | ESP32 | `firmware/` |
| 센싱 | PC 키스트로크 | `collector/` |
| 추론 | Raspberry Pi 4 hub | `hub/` |
| 출력 | Raspberry Pi 5 + Atlas | `display/` |
| 학습 | PC → TFLite | `ml/` |

---

## 2. 확정 사항 — 팀이 이미 정한 것

| # | 확정 내용 |
|---|---|
| C1 | ToF는 **VL53L9CX (54×42 zone)**. 다른 ToF 모델을 전제한 코드를 쓰지 않는다. |
| C2 | ToF 는 **스켈레톤 트래킹(V2V-PoseNet) 채택(2026-09-08)** 이되 **온보드 실시간은 기하 특징 7종**, 스켈레톤은 PC 오프라인 검증으로 제시한다. 연결 경로(Path A: Pi 4 CSI-2 / Path B: ESP32 I2C binning)는 **D7 미결** — 코드는 두 경로를 모두 수용하는 특징값 인터페이스로 둔다. |
| C3 | **Pi 4 = hub**(수집·융합·FSM·제어 판단), **Pi 5 = display**(Atlas Flutter UI·터치·스피커). 역할을 합치지 않는다. |
| C4 | Pi 5는 LG **AI Native OS Video Profile + Atlas Flutter**. Pi 4는 Headless 계열. |
| C5 | 가산점 요건상 **ESP32 · Pi 4 · Pi 5 3종 구성을 유지**한다. Pi 5로 통합하지 않는다. |
| C6 | 운영 경로에는 **ToF 특징값만** 흐른다. 축소 depth map은 **명시적 디버그/UI 모드에서만 최대 2Hz**. |
| C7 | **CRC-16은 UART binary frame 구간에만** 적용한다. MQTT/TCP JSON에는 애플리케이션 CRC를 넣지 않고 sequence·timestamp·스키마로 검증한다. |
| C8 | **Node-RED는 개발 모니터링·센서값 주입·로깅 전용**이다. 제거해도 운영 경로가 동작해야 한다. |
| C9 | **1단계 규칙 FSM은 2단계 TFLite 없이 단독 완전 동작**해야 한다. 분류기는 import 실패해도 hub가 죽지 않는 선택적 의존이다. |
| C10 | **호흡은 보조 신호**다. `respiration_enabled: false` 기본. ToF 호흡 추출은 제외했고 mmWave 호흡은 C17 기준으로만 쓴다. 호흡 실패가 전체 판정을 흔들면 안 된다. |
| C11 | **임계값·가중치·타이머는 코드에 하드코딩하지 않는다.** 전부 `hub/deskmate_hub/config/*.yaml`. |
| C12 | 판정 사이클 ≤ 500ms, **신뢰도 계산 주기 10s**(`score_period_sec`, 2026-09-14 확정 — 포스터 서술과 일치), PC/MIXED/NPC 맥락 판정 윈도우 15분. ingest 는 10s 마다 `SensorFrame` 을 만든다. |
| C13 | **Pi 5 앱은 Docker 안에서 실행되지 않는다.** Docker 컨테이너는 개발 PC의 크로스 빌드 환경이고, 산출물 `.ipk`가 Pi 5의 AI Native OS에 설치되어 네이티브로 실행된다. Pi 5에 Docker를 올리지 않는다. ([`../display/atlas/README.md`](../display/atlas/README.md)) |
| C14 | **Pi 4 ↔ Pi 5 는 이더넷 직결 + MQTT(Mosquitto on Pi 4, TCP 1883).** SSH 22 병행. display 는 `state/phase`(retain)·`interaction/request`·`display/message` QoS 1 구독, `feedback/user` 발행. |
| C15 | **ESP32 ↔ Pi 4 는 UART2.** ESP32 GPIO25(TX)/GPIO26(RX) ↔ Pi 4 GPIO15/GPIO14, 공통 GND. 115200 8N1 로 2026-09-11 실측 통과(목표 460,800 bps+ 는 미검증). 프레임은 COBS + 끝 `0x00` + CRC-16/CCITT-FALSE. ESP32 UART0 은 디버그 로깅 전용, UART1 은 C1001 전용. |
| C16 | **Pi 4 런타임은 AI Native OS Headless(ATLAS).** 이미지에 Python·컴파일러가 없으므로 `hub/atlas` native service IPK 가 `/restricted/python3` 로 FSM 을 실행한다. **UART 디코더(COBS/CRC)·MQTT 클라이언트는 그 C++ 서비스에 넣는다.** 보드에 개발 도구를 설치하지 않는다. |
| C17 | **mmWave 는 체동 중심 보조 신호.** 순간 호흡·심박, HRV, 센서 내장 수면 판정·`inBed`, 부팅 덤프 파형은 판정 근거로 쓰지 않는다. 호흡은 중앙값(120 s 창)+기준선 비교와 "호흡 소실 여부"만 증거로 쓴다. 자세 판별은 mmWave 가 하지 않는다. |
| C18 | **키스트로크 계약은 `collector/` 구현 기준으로 확정(2026-09-14).** 60 s 슬라이딩 윈도우 · 1 Hz · QoS 0 · `deskmate/sensor/keystroke`. 필드: `dwell_mean_ms` `dwell_std_ms` `flight_mean_ms` `flight_std_ms` `idle_ratio` `correction_rate` + `typing_active` `mouse_active` `input_active` `flight_cv` `mouse_event_rate` `window_s`. 키 간격 > 2 s 는 flight 통계 제외, ≥ 3 s 는 idle. **미입력 구간 = `typing_active=false`, `idle_ratio=1.0`** → hub 는 키스트로크 신호 미가용으로 재정규화. 세부 튜닝(공통 envelope 추가 등)은 이후. ([`data-spec.md`](data-spec.md) §6.4) |
| C19 | **환경 센서는 보정 없이 단일 센서 측정(2026-09-14).** CO₂ = SCD41, 온도·습도 = DHT22, 조도 = BH1750. SCD41 의 T/RH 출력은 전송·저장하지 않고, 센서 간 교차 보정도 하지 않는다. |
| C20 | **Pi 5 디스플레이 교체 완료, 터치 정상(2026-09-14).** 이전 `xhci-hcd.1` 사망 관찰은 이력. 터치 관련 블로커는 해제됐고 남은 것은 재부팅 자동 시작·4화면 구현이다. |

---

## 3. 미결정 사항 — 에이전트가 혼자 정하면 곤란한 것

에이전트가 혼자 결정하면 안 되는 항목이다. 코드가 필요하면 **양쪽을 모두 수용하는 인터페이스**로 두고 결정을 유보한다.

| # | 미결정 항목 | 현재 상태 |
|---|---|---|
| D1 | UART 프레임 **TYPE·스키마** (물리 계층은 C14·C15 로 확정됨) | mmWave TYPE 미배정(실험 펌웨어는 `0x01` 임시 사용 → ToF 디버그 TYPE 과 충돌). DrowsyDetector 출력을 state·evidence 만 보낼지 요약값까지 보낼지 미정. CRC 다항식·test vector 를 `data-spec.md` 에 기록해야 한다. |
| D2 | `C_focus` 의미·부호 (집중 저하 증거 유지 vs 집중도로 반전) | 현재 코드는 "큰 값 = 집중 저하 증거". |
| D3 | ~~신뢰도 계산 주기~~ | **해소(2026-09-14)** → C12. `score_period_sec: 10`. |
| D4 | 개인화 저장소 (파일 / SQLite / 기타 로컬 DB) | 보존·삭제·opt-in 정책 미정. 잠정 로컬 JSONL. |
| D5 | 호흡 go/no-go 기준 | ToF 호흡 추출은 제외(mmWave 로 이관). mmWave 호흡은 C17 기준으로만 사용. `respiration_enabled` 의 의미를 "호흡 소실 증거 사용 여부"로 재정의할지 미정. |
| D6 | 마이크 사용 여부 | 현재 제외. 명시적 활성화형 보조 입력만 검토 대상. |
| D7 | **ToF 연결 경로** Path A(Pi 4 MIPI CSI-2 풀해상도 54×42) / Path B(ESP32 I2C 1 MHz, 27×21 binning) | **2026-09-19 까지 1일 spike 로 결정.** 드라이버 미확보 시 Path B 자동 확정. Path A 채택 시 ESP32 는 mmWave·환경 전담. |
| D8 | ~~키스트로크 계약~~ | **해소(2026-09-14)** → C18. 구현 기준 확정, 세부 튜닝은 이후. 병합 PR: `feat/merge-pending`. |
| D9 | ~~SCD41·DHT22 역할 분리~~ | **해소(2026-09-14)** → C19. 보정 없음, 단일 센서 측정. |
| D10 | ~~Pi 5 터치 화면 유지/교체~~ | **해소(2026-09-14)** → C20. 교체 완료, 터치 정상. |
| D11 | 스마트 플러그 모델 | 로컬 제어 가능(클라우드 의존 없음) 모델 미선정. |
| D12 | 미머지 브랜치 정리 | **진행 중(2026-09-14)**: `feat/fsm-replay`(PR #3 `fsm-report` 포함)·`feat/collector-keystroke` 를 `origin/main` 위에 병합한 `feat/merge-pending` 을 푸시함. PR 머지 후 세 브랜치 삭제. |

전체 목록: [`requirements-spec.md`](requirements-spec.md) §9, [`data-spec.md`](data-spec.md) §16.

---

## 4. 계약 문서 — 구현보다 문서가 먼저

아래 네 문서는 **모듈 간 계약**이다. 구현을 바꾸기 전에 문서를 먼저 바꾸고, 같은 커밋/PR에 넣는다.

| 문서 | 내용 |
|---|---|
| [`requirements-spec.md`](requirements-spec.md) | MVP 기능·비기능 요구사항, 수용 기준, 확정/잠정/미결정 표시 |
| [`data-spec.md`](data-spec.md) | L0~L5 데이터 계층, 값·단위·유효성·시간·보정·융합 계약 |
| [`fsm-spec.md`](fsm-spec.md) | VER5 18상태 전이·임계값·이중 신뢰도 공식 |
| [`mqtt-topics.md`](mqtt-topics.md) | MQTT 채택 시의 topic·payload 매핑 (논리 계약의 전송 매핑) |

정합성 우선순위(충돌 시 위가 이긴다):

1. 프라이버시·보안 규칙과 사용자의 명시적 지시
2. `fsm-spec.md`와 실제 `SensorFrame`/`TickResult` 구현
3. `requirements-spec.md` · `data-spec.md`의 논리 계약
4. 통신 어댑터·UI 표시 스키마

배경 문서: [`roadmap.md`](roadmap.md)(결선 갭·주차 계획), [`architecture.md`](architecture.md), [`development-progress.md`](development-progress.md), [`hardware.md`](hardware.md), [`fsm-dev-plan.md`](fsm-dev-plan.md), [`submission.md`](submission.md)

---

## 5. 지켜야 할 제약 — 프로젝트 차원

### 프라이버시
- 키 **내용**을 수집·전송·저장하지 않는다. dwell·flight·idle·correction 통계만 다룬다.
- 카메라 영상, 상시 음성을 쓰지 않는다.
- 운영 중 ToF raw zone 배열을 전송·저장하지 않는다. 디버그 depth map은 명시적 플래그 + 단기 보존 후 삭제.
- 자가기록(ESM) 라벨과 개인 로그는 커밋하지 않는다.

### 보안
- 자격증명·토큰·API 키를 소스와 로그에 넣지 않는다. `.env` 또는 gitignore된 `secrets.yaml`만 사용한다.
- 데이터셋, 학습 모델, 빌드 산출물, SDK, 로그는 커밋하지 않는다.

### 안전
- 비가역 동작(전원 차단 등)은 사용자 확인 없이 실행하지 않는다.
- 불확실·오프라인·제어 실패 시 보수적으로 동작하고 UI에 표시한다.
- 신호가 충돌하면(예: ToF 노딩 + mmWave active) 자동 제어 대신 display에서 확인을 받는다.

### 참조 자산
- `reference/raspberrypi/` 아래 LG 제공 샘플은 **직접 수정하지 않는다.** 기능 코드로 옮겨 검토·수정한 뒤 쓴다.
- Atlas 빌드는 `display/atlas/compose.yaml`의 Docker(개발 PC) 안에서 하고, Docker 설정이 레포 외부 경로를 참조하지 않게 한다. 배포는 `flutter-atlas build atlas --ipk` → `flutter-atlas run -d <device_id>`로 Pi 5에 업로드·설치·실행한다.

---

## 6. 작업 방식 — 참고 예시

**아래는 규정이 아니다.** 한 사람이 쓰던 방식을 적어둔 것이니, 각자 맞는 대로 바꿔 쓰면 된다.
다만 팀 규칙(`README.md` 협업 규칙)에서 온 항목은 ★ 로 표시했다. 그건 도구와 무관하게 지켜야 한다.

### 시작 전
1. `git status`로 현재 브랜치와 미커밋 변경을 확인한다.
2. 수정할 파일과 목적을 먼저 선언하게 한다.
3. 이미 수정·스테이지된 파일이나 다른 사람이 작업 중인 파일은 확인 없이 건드리지 않게 한다.
4. 관련 계약 문서(§4)를 읽히고, 확정(§2)·미결정(§3)에 어긋나지 않는지 보게 한다.

### 진행 중
- 한 작업 = 하나의 기능 또는 버그 수정. 범위를 임의로 넓히지 않게 한다.
- 같은 파일에 다른 변경이 나타나면 편집을 멈추고 차이를 확인하게 한다. 덮어쓰기·자동 포맷·stash·restore·reset으로 해결하지 않게 한다.
- 대규모 포맷·이름 변경·폴더 이동은 기능 변경과 섞지 않고 분리한다.
- ★ MQTT topic/payload를 바꾸면 `mqtt-topics.md`를, FSM 상태·전이·임계값을 바꾸면 `fsm-spec.md`와 테스트를 함께 갱신한다.

### Git
- ★ **`main` 직접 push 금지.** `feat/<기능명>` 브랜치 → PR → 리뷰 후 머지.
- ★ 커밋 메시지: `feat(scope): 내용` / `fix(scope): 내용` / `docs(scope): 내용` / `chore(scope): 내용`
- ★ 데이터셋·학습 모델·자격증명은 커밋하지 않는다.
- 커밋 직전에 대상 파일만 명시적으로 스테이징하고 `git diff --cached --check`를 확인하면 무관한 변경이 섞이지 않는다.
- push·브랜치 전환·병합·리베이스·stash·reset은 명시적으로 요청할 때만 하도록 시켜두면 사고가 줄어든다.

### 검증
- ★ FSM 전이는 합성 입력 단위 테스트로 검증한다(실기기 없이 검증 가능한 유일한 부분).
  `cd hub && pip install -r requirements.txt && pytest tests/`
- 테스트를 실행할 수 없으면 이유와 재현 명령을 보고에 남기게 한다. 실행한 척하지 않게 하는 게 중요하다.

### 완료 보고 형식 (예시)
```
수정 파일 :
변경 요약 :
실행한 검증과 결과 :
커밋 해시 :
남은 위험 / 미검증 항목 :
```

---

## 7. 자주 하는 실수 — 참고

- ToF 원본 배열을 MQTT payload나 로그에 실어 보내는 것 → §2 C6 과 어긋남
- 임계값을 Python 상수로 박아 넣는 것 → §2 C11 과 어긋남
- ToF 연결 경로(Path A/B)를 한쪽으로 못 박은 코드를 만드는 것 → §3 D7 과 어긋남
- mmWave 순간 호흡·심박값을 FSM 입력으로 직접 쓰는 것 → §2 C17 과 어긋남
- Pi 4 보드에 Python 패키지·컴파일러를 설치하려는 것 → §2 C16 과 어긋남
- 별도 저장소의 ESP32 코드를 참조만 하고 이 레포로 옮기지 않는 것 → 통합 MVP 지연
- MQTT JSON에 CRC 필드를 추가하는 것 → §2 C7 과 어긋남
- TFLite import를 hub 시작 경로의 필수 의존으로 만드는 것 → §2 C9 와 어긋남
- 요청받지 않은 push·브랜치 전환·reset 을 수행하는 것 → 사고 나기 쉬운 지점
- 문서를 고치지 않고 계약(토픽·스키마·상태)만 바꾸는 것 → §4 와 어긋남

---

## 8. 현재 실기 개발 상태 — 작업 재개용

- **Pi 5 display**: Docker/WSL2 크로스 빌드, Flutter 테스트, release `.ipk`, Pi 5 SSH 설치·실행까지 검증(`main` 머지됨).
  대시보드 재설계·18상태 그래프·키스트로크 패널·오디오·MQTT 구독(`MqttStateSource`)까지 들어 있다.
  최신 상세 상태와 재현 명령은 [`atlas-build-handoff.md`](atlas-build-handoff.md)를 기준으로 한다.
- 앱은 내장 데모, Pi 4 HTTP 개발 어댑터(`DESKMATE_HUB_URL`), MQTT(`DESKMATE_MQTT_HOST`)를 지원한다. 둘 다 주면 MQTT 우선.
  내장 데모의 `자동 순환: ON/OFF` 버튼은 터치 확인용이며 실제 Hub 연결 빌드에는 표시되지 않는다.
- **Pi 4 hub**: `hub/atlas` native service IPK 가 제한 Python 으로 FSM 데모를 돌리고 HTTP 8765 를 연다.
  **hub 가 MQTT 를 발행하는 코드는 아직 없다.** 지금까지의 화면 MQTT 확인은 Node-RED(`tools/node-red-visualizer`)가
  `deskmate/state/phase`·`display/message` 를 broker 에 주입한 결과다.
- **ESP32**: C1001 mmWave 파서·5상태 DrowsyDetector·UART2 송신 펌웨어가 **별도 저장소**에 있다(이 레포 `firmware/` 는 비어 있음).
  09-11 에 Pi 4 `/dev/serial0` 까지 26 B 프레임 도달을 확인했다. Pi 4 측 디코더는 아직 없다.
- **미머지 브랜치 정리(2026-09-14)**: `origin/feat/fsm-replay`(PR #3 `fsm-report` 를 이미 포함)와 `origin/feat/collector-keystroke` 를
  `origin/main` 위에 병합한 **`feat/merge-pending`** 브랜치를 푸시했다(hub 53 passed·1 xfail, collector 8 passed).
  main 에 PR 머지되면 세 브랜치는 삭제한다. 그 뒤 `collector/`·`report.py`·`--report` 가 main 에 들어온다.
- **Pi 5 디스플레이는 교체됐고 터치가 정상 동작한다(2026-09-14).** `hardware-bringup.md`·`atlas-build-handoff.md` 의 xHCI 관찰은 이력이다.
- Pi 5 주소는 DHCP이므로 문서의 마지막 IP를 고정값으로 가정하지 않는다. 자격증명·로컬 SSH 설정·
  Flutter custom device 설정은 저장소에 넣지 않는다.
- 현재 화면의 USB `0416:c168` `TSTP MTouch`는 부팅 때 열거된 뒤 `xhci-hcd.0` 오류로 분리되어
  input event 노드를 만들지 못한다. 이는 Flutter 버튼 문제로 확인된 것이 아니며, 화면 모델·전원·USB
  배선 확인이 먼저다. [`hardware-bringup.md`](hardware-bringup.md)의 안전 주의와 진단 순서를 따른다.
- 정확한 화면 배선도를 확인하기 전 별도 `5V+GND`와 USB VBUS를 동시에 연결하거나 반복 재연결하지 않는다.
- Pi 5 재부팅 뒤 DESKMATE 앱은 자동 실행되지 않는다. 자동 시작 등록 전에는
  `flutter-atlas run -d deskmate_pi5 --release`로 다시 실행한다.
