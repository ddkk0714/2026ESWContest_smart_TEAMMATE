# DESKMATE AI 개발 브리핑 (참고용)

> 대상: Claude Code · Codex 등 AI CLI 로 이 저장소를 다루는 팀원
> 기준일: 2026-08-31

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
ESP32(센서)  ─┐
PC(키 타이밍) ─┼─► Raspberry Pi 4 : 융합 · FSM · 제어 판단 ─► 조명 · 환기 · 플러그
VL53L9CX ─────┘            │
                           └─► Raspberry Pi 5 : Atlas Flutter UI · 제안/정정 입력
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
| C2 | ToF 연결은 **Pi 4 MIPI CSI-2 직결 우선**. 1일 spike로 검증하고 실패 시 **ESP32 I2C 축소 경로**로 전환한다. |
| C3 | **Pi 4 = hub**(수집·융합·FSM·제어 판단), **Pi 5 = display**(Atlas Flutter UI·터치·스피커). 역할을 합치지 않는다. |
| C4 | Pi 5는 LG **AI Native OS Video Profile + Atlas Flutter**. Pi 4는 Headless 계열. |
| C5 | 가산점 요건상 **ESP32 · Pi 4 · Pi 5 3종 구성을 유지**한다. Pi 5로 통합하지 않는다. |
| C6 | 운영 경로에는 **ToF 특징값만** 흐른다. 축소 depth map은 **명시적 디버그/UI 모드에서만 최대 2Hz**. |
| C7 | **CRC-16은 UART binary frame 구간에만** 적용한다. MQTT/TCP JSON에는 애플리케이션 CRC를 넣지 않고 sequence·timestamp·스키마로 검증한다. |
| C8 | **Node-RED는 개발 모니터링·센서값 주입·로깅 전용**이다. 제거해도 운영 경로가 동작해야 한다. |
| C9 | **1단계 규칙 FSM은 2단계 TFLite 없이 단독 완전 동작**해야 한다. 분류기는 import 실패해도 hub가 죽지 않는 선택적 의존이다. |
| C10 | **호흡은 보조 신호**다. 8월 go/no-go 통과 전까지 `respiration_enabled: false`. 호흡 실패가 전체 판정을 흔들면 안 된다. |
| C11 | **임계값·가중치·타이머는 코드에 하드코딩하지 않는다.** 전부 `hub/deskmate_hub/config/*.yaml`. |
| C12 | 판정 사이클 ≤ 500ms, 신뢰도 계산 주기 30s, PC/MIXED/NPC 맥락 판정 윈도우 15분. |
| C13 | **Pi 5 앱은 Docker 안에서 실행되지 않는다.** Docker 컨테이너는 개발 PC의 크로스 빌드 환경이고, 산출물 `.ipk`가 Pi 5의 AI Native OS에 설치되어 네이티브로 실행된다. Pi 5에 Docker를 올리지 않는다. ([`../display/atlas/README.md`](../display/atlas/README.md)) |

---

## 3. 미결정 사항 — 에이전트가 혼자 정하면 곤란한 것

에이전트가 혼자 결정하면 안 되는 항목이다. 코드가 필요하면 **양쪽을 모두 수용하는 인터페이스**로 두고 결정을 유보한다.

| # | 미결정 항목 | 현재 상태 |
|---|---|---|
| D1 | ESP32↔Pi 4, Pi 4↔Pi 5 물리 통신 (UART / MQTT / 혼합) | **MQTT가 최우선 후보지만 미확정**. 논리 계약을 전송 기술과 분리해 둔다. |
| D2 | `C_focus` 의미·부호 (집중 저하 증거 유지 vs 집중도로 반전) | 현재 코드는 "큰 값 = 집중 저하 증거". |
| D3 | Pi 4 FSM 배포 런타임 (Raspberry Pi OS Python vs AI Native OS Headless native/bridge) | **실물 Pi 4의 OS와 Python 지원 여부 확인 대기**. |
| D4 | 개인화 저장소 (파일 / SQLite / 기타 로컬 DB) | 보존·삭제·opt-in 정책 미정. |
| D5 | 호흡 go/no-go 기준 (ToF / mmWave / 둘 다 / 비활성) | 8월 실측 대기. |
| D6 | 마이크 사용 여부 | 현재 제외. 명시적 활성화형 보조 입력만 검토 대상. |

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

배경 문서: [`architecture.md`](architecture.md), [`development-progress.md`](development-progress.md), [`hardware.md`](hardware.md), [`fsm-dev-plan.md`](fsm-dev-plan.md), [`submission.md`](submission.md)

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
- MQTT를 확정 사실처럼 코드·문서에 못 박는 것 → §3 D1 과 어긋남
- MQTT JSON에 CRC 필드를 추가하는 것 → §2 C7 과 어긋남
- TFLite import를 hub 시작 경로의 필수 의존으로 만드는 것 → §2 C9 와 어긋남
- 요청받지 않은 push·브랜치 전환·reset 을 수행하는 것 → 사고 나기 쉬운 지점
- 문서를 고치지 않고 계약(토픽·스키마·상태)만 바꾸는 것 → §4 와 어긋남

---

## 8. 현재 Atlas Pi 5 개발 상태 — 작업 재개용

- 브랜치 `feat/atlas-display-env`에서 Docker/WSL2 크로스 빌드, Flutter 테스트, release `.ipk` 생성,
  Pi 5 SSH 설치·실행까지 검증했다. 최신 상세 상태와 재현 명령은
  [`atlas-build-handoff.md`](atlas-build-handoff.md)를 기준으로 한다.
- 앱은 내장 데모와 Pi 4 HTTP 개발 어댑터를 지원한다. 내장 데모의 `자동 순환: ON/OFF` 버튼은
  터치 확인용이며 실제 Hub 연결 빌드에는 표시되지 않는다.
- Pi 5 주소는 DHCP이므로 문서의 마지막 IP를 고정값으로 가정하지 않는다. 자격증명·로컬 SSH 설정·
  Flutter custom device 설정은 저장소에 넣지 않는다.
- 현재 화면의 USB `0416:c168` `TSTP MTouch`는 부팅 때 열거된 뒤 `xhci-hcd.0` 오류로 분리되어
  input event 노드를 만들지 못한다. 이는 Flutter 버튼 문제로 확인된 것이 아니며, 화면 모델·전원·USB
  배선 확인이 먼저다. [`hardware-bringup.md`](hardware-bringup.md)의 안전 주의와 진단 순서를 따른다.
- 정확한 화면 배선도를 확인하기 전 별도 `5V+GND`와 USB VBUS를 동시에 연결하거나 반복 재연결하지 않는다.
- Pi 5 재부팅 뒤 DESKMATE 앱은 자동 실행되지 않는다. 자동 시작 등록 전에는
  `flutter-atlas run -d deskmate_pi5 --release`로 다시 실행한다.
