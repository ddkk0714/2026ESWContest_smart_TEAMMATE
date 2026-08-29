# DESKMATE 개발 진행 현황

> 기준일: 2026-08-29
> 목적: 제품 방향, 현재 구현 상태, 다음 의존성을 한 화면에서 관리한다. 개인 이름과 담당자 표기는 이 문서에서 제외한다.

## 1. 제품 지향점

- **초개인화**: 개인별 baseline과 시간대별 패턴을 이용해 피로·집중·환경 개입 기준을 조정한다.
- **공간 이해**: ToF·mmWave·환경 센서를 융합해 사용자 상태와 주변 환경을 함께 해석한다.
- **프라이버시 우선**: 카메라·마이크의 상시 수집 없이, 센싱·추론·제어를 로컬에서 처리한다. 마이크는 상시 감지가 아닌 명시적 명령/가전 제어 검토 항목으로만 둔다.
- **양방향 가전 연동**: 명령 전송뿐 아니라 가전 상태(예: 온도·동작 상태)와 사용자 피드백을 받아 다음 판단에 반영한다.
- **사용자 주도 자동화**: 불확실한 판정은 display에서 제안·확인·정정을 받고, 피드백을 개인화 데이터로 축적한다.

## 2. ATLAS 보드 배치

### 결정

**Raspberry Pi 5(8GB)의 LG AI Native OS Video Profile에서 ATLAS display 앱을 실행한다.** Raspberry Pi 4는 FSM hub 역할을 유지하되 실제 OS와 Python 배포 가능 여부는 별도로 확인한다.

| 구분 | Raspberry Pi 4 hub | Raspberry Pi 5 display + ATLAS |
|---|---|---|
| 주 역할 | 통신 broker/adapter 후보, 센서 수집, 전처리 결과 통합, 센서 퓨전, FSM, 제어 판단 | 터치 UI, 제안·정정 입력, 스피커 알림, 상태/리포트 표시 |
| 우선 기술 | Python 서비스 우선, AI Native OS Headless일 경우 native bridge 검토, MQTT 최우선 후보 | AI Native OS Video Profile, Atlas Flutter 앱, D-Bus display 장치 API |
| 장애 영향 | 센서·판정·제어의 핵심 경로 | 화면·사용자 상호작용만 제한, hub의 안전한 기본 동작은 유지 |

### 결정 근거

1. ATLAS Flutter 샘플과 Docker 개발환경은 사용자 대면 앱을 빠르게 구현하는 데 적합하다.
2. display에는 터치와 스피커가 있어, 불확실한 졸음/리듬 동작을 질문하고 수락·거절·정정받는 제품 경험을 완성할 수 있다.
3. Pi 4의 센서 융합·FSM을 ATLAS UI와 분리하면 UI 오류·업데이트가 실시간 판정 경로에 영향을 주지 않는다.
4. LG 기술교육 자료는 Pi 5 Video Profile의 Atlas Flutter·D-Bus 앱 개발과 Pi 4 Headless Profile 구성을 공식 제공 경로로 설명한다.

### 이 결정으로 확정되는 인터페이스

- **Pi 4 → Pi 5**: `deskmate/state/phase`, 제안 카드, 알림·리포트 데이터
- **Pi 5 → Pi 4**: `deskmate/feedback/user`의 수락·거절·정정, 터치 기반 세션 제어
- **Pi 5 내부**: Atlas Flutter UI와 스피커·터치 장치 연동
- **Pi 4 내부**: 센서 입력 유효성·순서·시간 판정, 융합, FSM, 제어 명령 결정. UART 사용 구간만 CRC-16, MQTT JSON CRC 미사용

### 바로 할 일

- [x] `display/atlas/app/`에 Atlas Flutter 앱 골격과 `state/phase` 호환 모델을 구현한다.
- [x] 개발용 HTTP 어댑터로 display 수락·거절을 Pi 4 FSM 실행기에 되돌린다.
- [ ] 개발 PC Docker에서 Flutter 테스트와 첫 release `.ipk` 빌드를 완료한다.
- [ ] Pi 5를 SSH custom device로 등록하고 debug/release 실행 로그를 확보한다.
- [ ] 실기기에서 display가 꺼져도 Pi 4 hub가 계속 동작하는지 통합 테스트한다.

> **확인 근거:** LG 기술교육 자료의 제공 기기 구성은 Raspberry Pi 5(8GB)+AI Native OS Video Profile, Raspberry Pi 4B+Headless Profile, ESP32이며 Flutter 앱은 Pi 5 Video Profile에서 개발한다. 프로젝트 Pi 4에 실제 설치된 OS와 Python 지원 여부는 별도 확인 대상이다.

## 3. 시스템 흐름

```text
ESP32 센서 노드 ── 통신 후보(MQTT 우선 검증) ──> Pi 4 hub (수집 · 융합 · FSM)
PC 키보드 타이밍 ──────────────────> │
                                       ├── 상태/제안 ──> Pi 5 display
LG 가전 상태 <── 양방향 MQTT/API ───> └── 제어 명령 ──> LG 가전·스마트 플러그
                                                ↑
                                      사용자 수락·거절·정정 피드백
```

## 4. 분야별 현황

| 분야 | 확정 방향 | 현재 상태 | 다음 산출물 / 의존성 |
|---|---|---|---|
| 보드 간 통신 | 논리 데이터 계약과 전송 기술을 분리 | MQTT 최우선 후보, UART/MQTT/혼합 최종 결정 대기 | Pi 4↔Pi 5 양방향 최소 통신으로 채택 여부 검증 |
| 센서 통신·전처리 | 운영은 특징값, 디버그는 축소 depth map 최대 2Hz | VL53L9CX·mmWave·환경·키 특징/단위/유효성 명세 | Pi 4 MIPI CSI-2 1일 spike, 실패 시 ESP32 I2C 축소 경로 |
| 센서 보정·퓨전 | ToF와 mmWave, 환경 센서를 맥락별로 융합 | SEN0623·SEN0536·SZH-EK070 역할, baseline, 졸음/리듬 규칙 명세 완료 | 실제 거치 데이터 E2E 리플레이 검증 |
| FSM·불확실성 처리 | FSM 우선, 불확실하면 사용자 확인 | 18상태 FSM, 신뢰도 게이트, 사용자 feedback 토픽·테스트 존재 | 불확실도 기준과 제안 카드 UX, 정정 라벨 스키마, 실제 센서 리플레이 검증 |
| 개인화·AI | 개인 baseline·시간대 패턴을 이용한 단계적 개인화 | 2단계 TFLite 및 RL은 선택적 설계 단계 | 개인화 feature/label·보존 기간·동의 흐름, 로컬 DB 결정 |
| 가전 연동 | 제어와 상태 수신을 모두 반영 | hub→control 명령 토픽 초안 존재 | ThinQ/API 분석 결과, 가전 상태 수신 schema, 실패·재시도·수동 복구 정책 |
| display·UI | 개발 PC Docker에서 arm64 `.ipk` 크로스 빌드 후 Pi 5 AI Native OS에서 네이티브 실행 | Atlas Flutter 대시보드, 내장 데모, Pi 4 HTTP 상태·수락/거절 연결 구현. 첫 `.ipk` 미생성 | Docker/WSL2 준비, release `.ipk`, Pi 5 SSH 실행 로그·터치 검증, 정정 UI, 최종 통신 adapter |
| PC 연동 | 키 입력 내용은 수집하지 않고 타이밍 특징만 사용 | 키스트로크 payload 계약 존재 | collector 구현, PC 상태 UI/Stream Deck 연동 범위 및 권한 모델 |
| CAD·브랜딩 | 접이식 힌지·데스크테리어형 제품 경험 | 저장소 내 구현 산출물 없음 | 패키징 치수·열 설계·ToF 배치 제약, Figma UI 흐름, 프로토타입 사진 |
| 명세·품질 | 요구사항과 데이터 계약을 구현보다 먼저 고정 | 요구사항 명세서·데이터 명세서 작성 완료, MQTT·FSM 명세 존재 | 시험 시나리오·수용 기준 구체화, 미결정 항목 해소 |

## 5. 이번 우선순위

1. **논리 계약 검토**: 요구사항·데이터 명세의 미결정 항목과 `C_focus` 의미를 결정한다.
2. **Pi 4↔Pi 5 최소 데모**: 구현된 합성 입력→FSM→HTTP 미리보기→상태 UI→피드백을 실기기에서 검증하고 MQTT 채택 여부를 판단한다.
3. **불확실성 UX 연결**: `FATIGUE_SUSPECT` 또는 충돌 신호에서 display가 질문하고 `feedback/user`가 FSM에 반영되는 최소 흐름을 구현한다.
4. **Pi 5 UI MVP**: 개발 PC Docker에서 `.ipk`를 만들고 Pi 5에 SSH 설치·실행해 화면·터치·로그를 검증한 뒤 최종 통신 adapter를 연결한다.
5. **양방향 제어 PoC**: 제어 명령 1종과 가전 상태 수신 1종을 끝까지 연결하고 실패 시나리오를 시험한다.

## 6. 결정이 필요한 항목

| 항목 | 선택지 / 확인할 내용 | 결정 기준 |
|---|---|---|
| mmWave 역할 분담 | 졸음 동작·환경 감지에 사용할 모듈별 신호와 신뢰도 | ToF와 중복되지 않는 정보량 |
| 보드 간 물리 통신 | ESP32↔Pi 4 및 Pi 4↔Pi 5의 UART / MQTT / 혼합 | #3 팀 결정 대기. MQTT는 최우선 후보 |
| Pi 4 FSM 런타임 | Raspberry Pi OS Python / AI Native OS Headless native bridge | 실물 OS·Python 지원·배포 방식 |
| 개인화 저장소 | 로컬 파일·SQLite·Node-RED DB | 개인정보 최소화, 백업·삭제 가능성 |
| 마이크 기능 | 제외 유지 / 명시적 활성화형 보조 입력 | 프라이버시와 데모 효과 |
| 가전 상태 수신 | ThinQ API·로컬 브리지·스마트 플러그 중 PoC 대상 | 실제 접근 가능 API와 데모 안정성 |

## 7. 완료 기준

- 센서 패킷은 스키마 버전·시퀀스·타임스탬프로 유효성을 판정하고 오류·중복·지연을 로그로 남긴다. UART frame은 CRC-16, MQTT JSON은 별도 CRC 없이 검증한다.
- Raw 개인 데이터와 실제 키 입력은 운영 중 전송·저장하지 않는다. 축소 depth map은 명시적 디버그/UI 모드에서 최대 2Hz로만 사용한다.
- Node-RED는 개발 모니터링·센서값 주입·로깅에만 사용하며 중단되어도 핵심 경로가 동작한다.
- 센서 융합 결과가 불확실하면 자동 제어 대신 display에서 사용자 확인을 받는다.
- 가전 제어는 명령 발행, 실행 결과 수신, 실패 시 안전한 복구까지 하나의 시나리오로 검증한다.
- 개인화 기능은 opt-in, 데이터 보존 기간, 삭제 방법이 정해진 뒤에만 활성화한다.

## 8. 관련 문서

- AI 개발 공통 브리핑: [agent-briefing.md](agent-briefing.md)
- CLI 시작 공통 문구: [agent-kickoff-prompt.md](agent-kickoff-prompt.md)
- 요구사항 계약: [requirements-spec.md](requirements-spec.md)
- 데이터 계약: [data-spec.md](data-spec.md)
- 통신 계약: [mqtt-topics.md](mqtt-topics.md)
- FSM 계약: [fsm-spec.md](fsm-spec.md)
- FSM 구현 계획: [fsm-dev-plan.md](fsm-dev-plan.md)
- 하드웨어·열 설계 참고: [hardware.md](hardware.md)
- Pi 5 Atlas 개발 환경: [../display/atlas/README.md](../display/atlas/README.md)
- Pi 4→Pi 5 실기 연결 순서: [hardware-bringup.md](hardware-bringup.md)
- LG 스마트 가전 기술교육: 저장소 외부 로컬 `개발자료/스마트 가전_기술교육 (1).pdf` (Git 미포함)
- 전년도 수상팀 공개자료: 저장소 외부 로컬 `개발자료/제23회ESWC_동방예의지국_발표자료_공개용 (1) (1).pdf` (Git 미포함)
