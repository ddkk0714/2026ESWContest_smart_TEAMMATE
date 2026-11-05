# DESKMATE ThinQ Gateway — Claude Code 인수인계

작성 기준: 2026-11-05. 과거 Codex 대화 없이 이어받기 위한 코드·Git·시험 상태 기록이다.
이번 인수인계에서는 기능 개발, 운영 보드 접근/배포, 실제 Cloud GET/POST를 수행하지 않았다.

## 0. 먼저 알아야 할 결론

- 이어받을 브랜치는 **`feat/thinq-claude-handoff`**다. PR #111의 후속 로컬 작업을 별도 브랜치에 보존했다.
- 단일 노트북 화면은 **http://127.0.0.1:19891/** 이다. 이 주소는 각 개발자 본인의 로컬 서버다. GitHub를 받았다고 팀원 PC에서 서버가 자동 실행되지는 않는다.
- 실제 GET, CLI/HTML LOW→AUTO, HTML 수동 OFF→ON, V1.1 ON 제안 종단은 이전 사용자 실기 확인이 있다. 이 새 브랜치 자체의 실기 재검증과는 구분한다.
- 최신 후속분에는 수집기 토글, 재연결 중계, 통합 콘솔, 사전 동의형 자동 OFF 시험 코드와 본체 확인 UI 복구가 **이미 구현**돼 있다. 같은 기능을 새로 만들지 말 것.
- **일반 정책 설정 `automatic_power=false`, 자동 ON 없음, 기본 무승인 POST 없음.** 별도 opt-in 자동 OFF 코드는 존재하지만 사전 동의 후 1회 시험용이다. 실기·보안 검증을 마친 운영 자동 제어가 아니다.
- Pi 4 내부 ThinQ 실행/정책 배포, Pi 5 제품 제안 카드와 실제 ThinQ의 종단 연결, 노트북 없는 독립 실행은 미완료다.
- 먼저 고칠 제품성 문제는 **같은 동작을 인증 세션당 1회로 제한하는 시험 설계**와 **서버의 구체적 거절 사유가 사라지는 오류 표시**다. 안전 검사를 지워서 해결하지 말 것.

## 1. 읽기 순서와 Git 관계

1. 저장소 루트 [AGENTS.md](../../AGENTS.md), [개발 브리핑](../../docs/agent-briefing.md), [개발 규칙](../../docs/development-rules.md).
2. [데이터](../../docs/data-spec.md), [MQTT](../../docs/mqtt-topics.md), [FSM](../../docs/fsm-spec.md), [로드맵](../../docs/roadmap.md), 루트 `CLAUDE.md`가 있으면 그 문서도 읽는다.
3. [PR #110](https://github.com/ddkk0714/2026ESWContest_smart_TEAMMATE/pull/110) → [PR #111](https://github.com/ddkk0714/2026ESWContest_smart_TEAMMATE/pull/111) → 이 문서/현재 소스.

| 대상 | 이번 원격 조회 결과 | 의미 |
|---|---|---|
| `main` | `1cdb3ae4a887591832ebd5044ed2ce6b3701a779` | 최신이라고 제공받았던 예전 SHA와 구분. 현재 조회 시점 기준 |
| PR #110 / `feat/thinq-laptop-gateway` | `d9018f4858127f929761f52a3530bd3e97a21f09`, open Draft, base `main` | GET/LOW·AUTO, HTML, MQTT 관찰·캐시·Mock, V1.1 초기 정책 |
| PR #111 / `feat/thinq-manual-power` | `39bde3da5cdb343f99fea4ef109927eae5b40bf0`, open Draft, base #110 브랜치 | 수동 전원, 제안→개별 승인, 활성 세션 합류, 20/40분 인증 |
| 이 브랜치 | 위 #111 HEAD에서 직접 분기 | 이미 공개된 선행 코드를 중복 커밋하거나 #110/#111을 수정하지 않음 |

조회 당시 main과 #111의 공통 조상은 `27b8fc5` 계열이며 각자 6개/2개 커밋을 가진다.
main에는 이미 PR #104(V48)와 #108 개발자 화면 라벨 변경이 포함됐다.
이 브랜치는 최신 main을 Merge/Rebase하지 않았다. 별도 main 통합 회귀가 필요하다.
공통 조상 이후 main 변경은 주로 `display/atlas/ui_demo_app/`, `docs/reboot-recovery.md`,
`hub/atlas-tof/pc/devview/index.html`, `tools/atlas-recovery/bringup.sh`다.
코드 경로가 겹치지 않아도 UI/Hub 의미적 호환성을 보장하지 않는다.

`feat/demo-script` 계열/PR #105는 #107에서 되돌린 이력이 있다. 통째로 복원/병합하지 않는다.
이 브랜치에는 그 되돌림을 취소하는 변경이 없다. 최신 브랜치·PR 상태는 다음 작업 전에 다시 조회한다.

### 별도로 보존된 예전 작업

`1ee37c61afad66cc343cd2ca57f05c3611a19922`는 과거 로컬 ThinQ 브리지 커밋이다.
기존 ThinQ integration worktree에 보존돼 있으나 이 브랜치의 조상이 아니며, 현재 원격 브랜치 HEAD보다 로컬 1커밋 앞섰다.
그 커밋의 `tools/thinq_connect/read_service.py`, `read_api.py`, `observation_mapping.py`는
**현재 브랜치의 해당 루트 경로에 존재하지 않는다.** 원격에서 이 SHA가 조회 가능하다고 가정하지 말 것.
이번 브랜치에는 검증된 후속 `ApplianceCache`, `thinq_bridge.py`와 vendored 파서/Transport가 있다.
과거 원본이 꼭 필요하면 소유자에게 별도 보안 검토 후 공유를 요청한다. SDK·원본 관측 로그를 일괄 복사하지 않는다.

## 2. 프로젝트와 실제 역할

DESKMATE는 비침습 센서 융합으로 책상 작업 상태/집중·피로를 **추정**하고 환경 조정·휴식을 제안한다.
직접 측정한 사실, 모델 추정, 가전 API가 보고한 상태, 사용자의 물리 확인을 혼동하지 않는다.

```text
ESP32 ToF/mmWave/CO₂/온습도/조도 → UART COBS/CRC → Pi 4 Native relay + Python Hub
Windows 키·마우스 타이밍 특징 → MQTT ────────────────────┘
Pi 4 센서 융합/FSM/개인화 → MQTT state/phase → Pi 5 ATLAS Flutter
                                        └→ 노트북 read-only devview → HTML/V1.1 shadow 정책
LG 공기청정기 ↔ ThinQ Cloud HTTPS ↔ 노트북 SDK/검증 계층 ↔ Node ↔ HTML
```

| 기능 | 실제 소스 위치 |
|---|---|
| ESP32/Pico 펌웨어, UART | `firmware/`, `hub/atlas/src/`, `hub/deskmate_hub/ingest/` |
| 센서 캐시/매핑, FSM, 개인화 | `hub/deskmate_hub/ingest/{cache,mapping}.py`, `inference/`, `personalize/`, `personalization/`, `config/` |
| Pi 4 Native/ToF | `hub/atlas/`, `hub/atlas-tof/` |
| Pi 5 제품 UI | `display/atlas/ui_demo_app/`, `display/atlas/ui_env_app/` — 설치본 대응은 별도 확인 |
| Windows collector | `collector/{__main__,capture,features,publisher,discover,control}.py`, `control.ps1`, `autostart.ps1` |
| 노트북 센서/FSM/ToF 화면 | `hub/atlas-tof/pc/devview/{devview.py,index.html}` |
| 노트북 통합 화면/중계 | `tools/laptop-control/{index.html,server.mjs,thinq-ui.mjs,collector-control.mjs}` |
| 인증/읽기/제어 경계 | `thinq_host.py`: `HostSession`, `GuardedSdk`; `thinq_vendor/poc/session.py`: `Session`; `thinq_vendor/tools/thinq_connect/manual_trial.py`: `Transport`, `DeviceIO` |
| 별도 CLI | `tools/laptop-control/manual_control.py`, `--manual`, 필요 시 `--power-control` |
| 노트북 정책 | `hub/deskmate_hub/control/air_purifier_policy.py`, `config/air_purifier_policy.yaml` |
| 관측값/명령 계약 | `thinq_bridge.py`, `thinq_observer.py`, `hub/deskmate_hub/ingest/{appliance,appliance_display,mqtt_lines}.py` |
| 정책 제어 플랫폼 | `hub/deskmate_hub/control/{dispatcher,device_gateway,device_adapters,gateway_service}.py` |

일부 최신 자료가 센서/설치 버전을 다르게 설명한다. 최신 소스와 실보드 증거를 우선 대조한다.
운영 카메라·키 내용·상시 음성·일반 운영 ToF raw 저장은 확장하지 않는다.

### MQTT 계약과 아직 연결되지 않은 부분

- 기존 Envelope의 `schema_version`, `node`, `boot_id`, `seq`, `ts`, `data`를 보존한다.
- ESP32 센서/키스트로크: `deskmate/sensor/*`; 키스트로크는 `deskmate/sensor/keystroke`.
- Hub 재실/FSM: `deskmate/state/phase`. root `data.present`, `fsm_state`, `source=fsm`, 일치하는 `phase`를 확인한다.
- 사용자 제안/피드백: `deskmate/interaction/request`, `deskmate/feedback/user`.
- 제어: `deskmate/control/cmd`, `deskmate/control/result`. TTL/중복/retained/target/gate를 검사한다.
- ThinQ 관측: `deskmate/sensor/appliance/{source}/{logical_device_id}`. 논리 ID만 사용하며 실제 Device ID/PAT를 넣지 않는다.
- `ReadSnapshot.export`는 Envelope 생성일 뿐 실제 publish가 아니다. `CommandObserver`는 명령 관찰일 뿐 POST 권한이 아니다.
- `ApplianceCache`는 출처별로 분리한다. ThinQ PM을 ESP32 env에 덮어쓰거나 FSM 입력에 자동 투입하지 않는다.
- `LiveHub.tick_once`의 `sensor_summary.appliances`는 추론 후 화면용 투영이다.
- `test_mqtt_contracts.py`의 localhost broker → Hub cache → 화면용 투영 및 Dispatcher → Mock Gateway → result는 호스트 종단 시험이다.
- Native `mqtt_client.cpp`의 retained appliance 차단은 코드/과거 호스트 시험 근거이며 설치 ELF 반영을 보장하지 않는다.
- **운영 MQTT의 ThinQ 실측 publish / cmd 수신→실제 POST / 실제 result publish는 아직 미완성·미실기다.**
  `gateway_service.py`는 Mock 전용이며 live `air_cleaning` 차단을 임의로 제거하지 않는다.
- 기존 브로커의 인증·ACL·TLS와 명령 약 15초 타임아웃, 인간 승인 30초, Cloud 지연의 정합성은 별도 과제다.
  과거 익명 평문 1883 설정을 안전하다고 가정하지 않는다.

## 3. ThinQ 구현과 비밀정보 경계

검증 대상 제품은 LG AS183HWWA, API 모델 식별은 `AIR_910604_WW`다. 제품 모델명은 실제 개별 Device ID가 아니다.
기존 공식 Python SDK **`thinqconnect==1.0.14`**를 사용한다. SDK 자체/바이너리는 vendoring하지 않는다.
재사용 출처와 수정 범위는 [thinq_vendor/SOURCE.md](thinq_vendor/SOURCE.md)를 확인한다.

```text
사용자 PAT 숨김 입력 → Python HostSession의 메모리
HTML → same-origin + 로컬 키 검사 Node → 별도 세션 키로 Python localhost
Session → DeviceIO(Profile/State/모델 검증) → GuardedSdk(일회 permit) → Transport(HTTPS 경계) → Cloud
```

- PAT는 `getpass` 입력과 인증 프로세스 메모리에서만 사용한다. 채팅/argv/env/파일/HTML/MQTT/로그에 넣지 않는다.
- Node의 브라우저 키, Python 세션 키, 명령 nonce는 PAT와 다르지만 모두 공개/로그 출력하지 않는다.
- 고정 Client ID는 저장소 밖 `~/.config/deskmate-thinq/client-id`를 읽는다. 디렉터리 0700/파일 0600,
  소유자·일반 파일·단일 링크·symlink 금지·UUIDv4를 검증한다. Live Manual은 없을 때 새로 만들지 않고 중단한다.
- 팀원 PC에는 원본 사용자 Client ID/PAT가 없다. 복사 요청/자동 생성 없이 계정 소유자와 공급 절차를 따로 확정한다.
- 인증 수명은 기본 20분, 명시적 `--session-minutes 40`으로 40분이다. Node/Python 설정이 같아야 한다.
- 개별 제어 challenge/제안은 **30초**다. 인증이 40분이어도 개별 승인이 40분인 것은 아니다.
- GET은 사용자가 누르는 단발 Devices/Profile/State 조회다. 화면 polling은 캐시/정책 조회이며 Cloud 자동 polling이 아니다.
- Transport는 선택된 HTTPS 호스트/경로/기기/정확한 payload, TLS 검증, redirect 금지와 일회 permit을 유지한다.
- 풍량은 `airFlow.windStrength=LOW/AUTO`, 전원은 `operation.airPurifierOperationMode=POWER_OFF/POWER_ON`만 허용한다.
  ON이 AUTO를 강제로 설정하지 않는다. `set_power: off`를 `set_power_off`와 혼동하지 않는다.
- POST는 새 Profile/State, 모델 capability, 현재 baseline, 세션/nonce/승인 만료, 대상 일치를 재검사한다.
- POST 접수 → 별도 GET readback → 사용자 본체 확인은 독립 증거다. 종료/PAT 폐기가 이미 보낸 명령을 취소하지 않는다.
- 메모리 저장도 프로세스 침해·메모리 덤프에 대한 완전한 보호를 제공하지 않는다.

### 모드

| 모드 | 데이터/동작 |
|---|---|
| Fixture | Fake SDK와 합성 응답. Cloud 0회, Fake 승인/제어. 기본 안전 시험 |
| Live Read-only | 실제 PAT의 수동 GET만. POST/export 등 제한. `18891`은 과거 이 모드 |
| Live Manual | 직접 LOW/AUTO·전원 버튼 + 상태 재검사·각 동작 개별 승인 |
| Live Policy | 별도 SDK가 아니라 `--policy-link`를 켠 Live Manual의 정책 제안 handoff. 수락만으로 POST 안 함 |
| 통합 Manual+Policy | 새 `--combined-manual` opt-in. 직접 수동과 제안을 한 화면에 두되 기존 개별 승인 유지. 기존 policy-only의 수동 차단은 그대로 |
| 사전 동의 자동 OFF | 별도 `--auto-off` capability + HTML 본체 확인/사전 동의 + 새 재실 기준선 후 1회 OFF. 자동 ON/반복 OFF 기능 아님 |

## 4. 실기 검증 — 증거 수준

민감 원본 응답/개인 로그를 공개 저장소에 보존하지 않았다. 아래 사용자 보고와 코드/호스트 증거를 구분한다.

| 항목 | 근거와 판정 |
|---|---|
| Devices/Profile/State GET, 공기질 표시 | 사용자 제공 비식별 결과: 각 HTTP 200, `api_status=ok`, `all_required_fields_match=true`; 18891 실제 표시 성공 보고 |
| CLI LOW→AUTO | 사용자 제공 터미널 기록: 각 API 접수, GET 상태 일치, 각 본체 확인 문구. `manual_control.py` 경로 |
| HTML LOW→AUTO | 사용자 실기 성공 확인. 코드/Fixture는 승인·readback·중복 차단 재현. 현재 새 브랜치에서 재실행한 것은 아님 |
| HTML 수동 전원 OFF→ON | 사용자 실기 성공 확인. 이후 최신 통합 화면의 최초 수동 OFF도 실제 꺼졌다고 보고 |
| 실제 Pi 4 phase 수신 | 이전 읽기 전용 관측에서 non-retained seq 증가와 present 변화, devview→정책 입력 확인. 현재 보드 동작을 보장하지 않음 |
| 활성 세션 중간 합류 | 실제 FOCUS 계열 상태에서 START를 놓쳐 ON 제안이 없던 문제를 수정. PR #111 코드/회귀 + 이후 사용자 ON 종단 확인 |
| V1.1 ON 제안 종단 | 사용자: 실제 재실→ON 제안→수락→개별 승인→실제 ON 성공. PR #111에 API/readback/사용자 attestation 분리 근거 기록 |
| V1.1 OFF/AUTO 제안의 실제 종단 | **미완료**. Fixture/Mock 회귀와 준비만. 수동 OFF 성공을 OFF 정책 성공으로 바꾸지 말 것 |
| 사전 동의 자동 OFF | 후속 코드/Mock 있음, 실제 자동 OFF 성공 증거 없음. 기본 비활성 |
| 실제 Windows collector 토글 | 이전 Windows Chrome(headless)에서 OFF→ON→reload ON→OFF→reload OFF, 실제 리스너 상태/마우스 유지 확인. MQTT 미발행 local-only |
| 현재 통합 콘솔 전체 실물 회귀 | 일부 사용자 수동 OFF 보고 외 전체 재검증 미실시. 본체 확인 누락·반복 조작 제약이 남음 |
| Pi 4 내부 정책·Pi 5 제품 카드·PC 없는 구조 | 미배포/미검증. 현재 노트북 shadow 정책과 다름 |

PM1/API PM2/PM10, 냄새·오염도·필터·운전·풍량은 Profile 기반 검증 필드만 표시한다.
API `PM2`를 PM2.5로 임의 번역하거나 단위를 붙이지 않는다. API 수신 시각은 실제 센서 측정 시각이 아니다.

## 5. V1.1 정책의 현재 계약

정책은 기존 FSM 밖의 독립 클래스다. 기존 `frame.present`와 세션 경계만 소비한다.
전원은 밴딧에 맡기지 않고 기존 `air_fan_up` HIGH arm/환경 개인화를 활성화하지 않는다.
제안/수락/거절은 기존 Dispatcher의 단일 FSM 제어 에피소드와 별도다.

| 설정/조건 | 현재 동작 |
|---|---|
| `automatic_power=false` | 운영 자동 전원 기본 비활성, 자동 ON 없음 |
| 재실 30초 + 활성 세션 + 전원 OFF | ON 제안 |
| 실제 부재 timeout→FSM IDLE + 이전 활성 세션 + 전원 ON | OFF 제안. 운영 FSM 부재 기준 600초를 로컬에서 조작하지 않음 |
| 실제 BAD 응답 + LOW | 최소 서로 다른 API 보고 2회, 60초 지속, 보고 간격 120초 이하에서 AUTO 제안. 임계값은 실측 정책 확정값 아님 |
| Hub 최신성 30초 / 제안 TTL 30초 | 단절/stale/만료 차단 |
| 수동 변경 우선 1800초 | 외부 전원/풍량 변화 관측 시 hold. 이를 지워 시연을 강행하지 않음 |
| 명시적 `--demo-absence` | 노트북 시연 전용 부재 5초. 운영 FSM/보드 주변기기 설정을 바꾸지 않음 |

시연 5초는 기존 Hub 빠른 시연 코드의 기준을 대조한 값이다. **실제 Hub가 fast일 필요는 없다.**
처음 잘못 추가했던 fast 필수 조건은 제거했다. `sensor_summary.demo_mode`는 현재 참고 표시다.
시연 OFF는 새 실제 재실 true·활성 세션을 관측한 다음 새 false 표본들이 5초 이상 지속해야 한다.
멈춘 false 표본을 화면에서 반복 읽어 시간을 채우지 않는다. MQTT 주기로 제안이 더 늦게 보일 수 있다.
별도 표시용 ‘수신 후 부재 경과’와 제어 자격 카운터는 서로 다르다.

- 기존 IDLE→START 감지는 유지한다. 중간 연결은 `source=fsm`, 알려진 `fsm_state`, 정확한 `phase` 매핑이 맞는 최신 입력만 attach한다.
- START를 생성하거나 FSM을 초기화하지 않는다. attach 후 실제 새 연속 재실 30초가 필요하다.
- retained, 중복·역순 seq, 이전 boot, 미래/잘못된 age, 단절·epoch 변화·stale는 제어 근거로 쓰지 않는다.
- reconnect는 같은 정책 프로세스의 `seen` 기록을 유지한다. 안정적인 Hub session ID가 없어 프로세스 재시작을 넘는 중복 보장은 없다.
- 제안은 만료/거절/무효화되면 같은 관측 세션에 자동 재생성하지 않는다. `already_handled_this_session`은 실제 POST 성공을 뜻하지 않는다.
- `connection=unknown`은 SDK State에 확정 온라인 근거가 없어 유지한다. GET 성공을 online으로 바꾸지 않는다.
  manual review는 후보 표시만 허용하며 실제 변경에는 사용자의 본체 온라인·외부 조작 없음 확인을 요구한다.
- 일반 미확인/결측/stale/검증되지 않은 등급에서는 관련 제어를 금지한다. CO₂는 기존 환기 판단용이며 BAD 대신 쓰지 않는다.
- 익명 MQTT의 `source=fsm`/seq 필드는 암호학적 신원 증명이 아니다. 인증/ACL/TLS가 미확인인 환경의 자동 제어를 운영 안전성이 입증된 것으로 취급하지 않는다.

### 사전 동의형 자동 OFF의 한계

사용자가 후속 작업에서 자동 OFF 시험을 요청하여 `automatic_off.py`를 추가했다.
이는 ‘아무 승인 없는 자동 ON/OFF’가 아니라 **명시적 CLI capability와 UI 사전 동의 후 단일 OFF**다.
일반 기본값은 비활성이고, 현재 Gateway의 opt-in 설정과 실제 armed 상태는 별개다.
`AutomaticOff`는 같은 Session/GuardedSdk를 쓰며 대상/boot/epoch/세션을 고정하고
POST 직전에 로컬 뷰어 18890의 실제 재실/FSM을 다시 읽는다. 복귀·단절·stale·만료·수동 의도·오류 시 취소한다.
브라우저 tick에 의존하므로 브라우저 종료/절전 중 실행을 보장하지 않는다. 이미 전송한 OFF는 취소할 수 없다.
반복 ON/OFF, 백그라운드 상시 운영, Pi 4 배포, 보안 완결성을 이 코드가 제공한다고 해석하지 않는다.

## 6. HTML와 재현 실행

[UNIFIED-CONSOLE.md](UNIFIED-CONSOLE.md)가 최신 단일 화면 안내다.
**기존 프로세스가 있으면 먼저 소유/명령/CWD/포트를 확인하고, 모르는 프로세스를 종료하지 않는다.**
팀원 PC에는 소유자의 가상환경·자격증명·프로세스·SSH 키·보드 경로가 자동 이전되지 않는다.

| 포트 | 역할/현재 의미 |
|---|---|
| 19891 / 19892 | 단일 통합 HTML/Node / 별도 Python 인증. 앞으로 사용할 주소 |
| 18890 | 읽기 전용 센서 devview. ThinQ 인증과 독립 |
| 19591 / 19691 | 이번 소유자 실행 환경에서 19891 이동 안내로 전환. 기존 제어 API는 410 |
| 18791,18991,19491 | 과거 Fixture 시험 주소. 소스/시험은 유지, 실행 중이라고 가정 금지 |
| 18891 | 과거 Live Read-only GET |
| 19091,19291 | 과거 LOW/AUTO, 전원 수동 시험 |
| 19391 | 과거 V1.1 Live 제안 승인 |
| 19692 | 과거 인증 포트. 최신 19892와 혼동하지 말 것 |

### 게시 준비 시 로컬 상태 관측

읽기 전용으로 19891 Node 응답과 `combined_manual=true`를 확인했다. 당시 인증은
`credential_session_required`였고 Windows collector 상태 파일은 오래되어 현재 실행을 확인하지 못했다.
과거 Chrome 시험 성공을 현재 수집기가 켜져 있다는 뜻으로 바꾸지 않는다. 서비스 재시작은 하지 않았다.
소유자 PC의 프로세스는 원래 개발 worktree 기준이며, 이 인수인계 worktree는 게시/시험용 별도 사본이다.
포트 표는 구성 설명이고 모든 프로세스가 현재 실행 중이라는 목록이 아니다. 보드 상태는 이번에 조회하지 않았다.

### 6.1 처음에는 Fixture만

저장소 루트에서 기존 Python 3.12 가상환경을 `PY`로 지정한다. 아래 경로는 placeholder다.
PAT/Client ID를 환경변수에 넣는 예시가 아니다.

```bash
PY=/path/to/existing/python3.12
"$PY" tools/laptop-control/thinq_host.py --fixture --allow-control --power-control --policy-link \
  --session-minutes 40 --port 19992
```

다른 터미널:

```bash
node tools/laptop-control/server.mjs --mode fixture --power-control true --policy-link true \
  --session-minutes 40 --port 19991 --thinq-port 19992 --sensor-port 19990
```

센서 fake 화면까지 필요하면 `devview.py --help`와 [LIVE-READ-ONLY.md](LIVE-READ-ONLY.md)의
기존 fake 모드를 확인해 19990에 실행한다. Fixture에는 PAT를 입력하지 않는다.
위 포트는 재현용 예시이며 다른 프로세스가 점유하면 덮어쓰지 않는다.

### 6.2 Live는 소유자의 별도 승인 이후

Node 시작은 기기 제어가 아니다. 별도 인증 터미널에 PAT를 사용자가 직접 입력한다.

```bash
node tools/laptop-control/server.mjs --mode live-manual --power-control true \
  --policy-link true --combined-manual true --session-minutes 40 \
  --port 19891 --thinq-port 19892 --sensor-port 18890 --pi4 127.0.0.2
```

```bash
"$PY" tools/laptop-control/thinq_host.py --live-manual --power-control --policy-link \
  --combined-manual --allow-policy-post --session-minutes 40 --port 19892
```

이는 실제 POST capability가 있는 명령이므로 승인 없이 실행하지 않는다. 준비/GET과 실제 제어 승인은 별개다.
최근 소유자 시연 설정은 여기에 `--demo-absence --auto-off`를 명시적으로 추가한 것이지만,
인수인계받았다고 그 동의가 팀원 PC/계정/기기까지 자동 승계되지 않는다.
필요하면 승인된 읽기 전용 명령부터 사용한다: `--live-read-only --session-minutes 40 --port <FREE_AUTH_PORT>`;
Node도 `--mode live-read-only`로 같은 수명·포트를 맞추고 power/policy/combined 옵션은 사용하지 않는다.

### 6.3 Pi 4 연결과 중계

보드 IP는 DHCP/핫스팟마다 바뀐다. 개인 IP/계정·네트워크 정보를 문서에 고정하지 않는다.
검증된 호스트 키만 사용한다. `StrictHostKeyChecking=no`나 새 키 자동 승인은 금지한다.
Pi 5 경유가 필요하고 승인된 경우 기존 `sensor_forward.py --help`와 [LIVE-READ-ONLY.md](LIVE-READ-ONLY.md)를 따른다.
이 도구는 기존 운영자 SSH로 loopback 포워딩만 하며 Native 계정/보안 정책을 빌리지 않는다.
네트워크 오류는 5→10→20→최대30초 재시도하고 키/인증/포트 오류는 중단한다. 기존 임시 2시간 자동 종료는 제거했다.
WSL 종료·노트북 절전·새 IP·보드 장애까지 해결하는 도구는 아니다. SSH 실행 중만으로 MQTT 정상이라고 판단하지 않는다.
뷰어는 `devview.py --pi4 127.0.0.2 --read-only --bind 127.0.0.1 --port 18890` 경로이며
기존 names/misjudge 개인 파일을 덮어쓰지 않도록 별도 경로를 확인한다.

종료/재시작은 본인이 실행한 해당 터미널만 Ctrl+C. Python 인증 재시작은 PAT·nonce·검증 이력을 잃는다.
Node 재시작은 인증 bootstrap 캐시를 잃을 수 있어 인증도 새로 연결해야 한다.
`start.ps1`/`connect-thinq.ps1`은 기존 Docker/Pi5 화면 모드이고 최신 WSL 통합 실행 옵션과 같지 않다.
개발 Docker를 재생성하거나 Pi 5 운영 앱을 다시 설치하지 않는다.

## 7. 키스트로크 토글 — 이미 구현, 남은 것은 실기 연동 검증

독서·필기·설계처럼 타자를 안 쓰는 작업을 위해 사용자가 키보드 수집을 선택한다.
이 기능은 이번 요청 전에 구현됐다. 새로 구현할 계획으로 되돌리지 않는다.

- HTML 상단 ON `키스트로크 수집 중 / 수집 끄기`, OFF `키스트로크 수집 중지됨 / 수집 켜기`.
- `InputCapture.set_keyboard_enabled`가 실제 keyboard Listener를 stop/join하고 새 ON 때 새 Listener를 만든다.
- 마우스 Listener는 유지한다. 키 이벤트/눌림 버퍼를 비우고 세대를 변경해 이전 콜백을 거부한다.
- `collector/control.py`의 per-user 파일 잠금으로 중복 실행을 거부한다.
- Windows의 `%LOCALAPPDATA%\DESKMATE\capture-control` 요청/상태 파일로 제어한다.
  상태는 PID·heartbeat·실제 Listener·ACK만 포함하며 키 원문/PAT를 포함하지 않는다.
- Node `collector-control.mjs`→PowerShell `collector/control.ps1`가 상태/요청을 처리한다.
  POST는 기존 same-origin/화면 키 보호를 적용하고 실제 ACK 후에만 화면을 바꾼다.
- heartbeat 5초 초과/프로세스 부재/상태 조회 실패에서는 임의 ON이 아니라 미연결로 표시한다.
- OFF payload는 `valid=false`, `keyboard_capture_enabled=false`, `capture_status=user_disabled`이며
  keyboard 수치/`typing_active`를 보내지 않는다. `typing_active=false`나 idle_ratio=1로 위조하지 않는다.
  실제 마우스 `mouse_active/mouse_event_rate/input_active`는 유지한다. 계약은 `docs/mqtt-topics.md`.
- 기존 Hub 매핑은 키 신호 unavailable, fatigue는 valid=false를 무효 표본으로 취급한다.
  과거 Hub 윈도 이력을 소급 삭제하거나 운영 FSM/개인화를 바꾸지는 않았다.
- 실제 Chrome(headless) ON/OFF·새로고침 확인을 했지만 당시 수집기는 `--local-only`였다.
  **운영 Pi 4 키스트로크 MQTT 수신 변화는 하드웨어 분리로 미실시**다.
- 정상 실행은 기존 `python -X utf8 -m collector --broker auto`; 로컬 시험은 `--local-only`.
  후자는 MQTT 탐색/발행·특징 로그 저장을 하지 않는다. 수집기 프로세스 ON과 키보드 캡처 ON은 다르다.
- 자동 시작 소스는 `collector/autostart.ps1`: 현재 사용자 로그인 작업, MultipleInstances IgnoreNew.
  조사 시 등록된 task/기존 collector가 없었고 이번에 자동 시작을 등록/변경하지 않았다.
- 키 객체는 pynput callback에서 분류/중복 방지를 위해 메모리에서만 처리한다. 키 내용/좌표 원문을 파일·로그·MQTT에 쓰지 않는다.
- 기본 기존 collector는 설정 파일이 없으면 키보드 ON이다. 기존 OFF 선택은 다음 실행에도 유지한다.
  다른 PC에서는 실행 전 사용자 동의/기존 자동 시작/venv/파일 ACL을 확인해야 한다.
- per-user 제어 파일의 NTFS ACL·제3자 쓰기 가능성에 대한 별도 보안 검토는 완료로 간주하지 않는다.

Windows Python 3.12 전용 venv에 기존 `collector/requirements.txt`를 설치해 시험했다.
시스템 Python·Codex 설정·인증정보·기존 venv는 변경하지 않았다. 팀원 환경에는 이 venv가 없다.

## 8. 알려진 문제 — 다음 담당자가 반드시 먼저 볼 것

### 8.1 첫 OFF 뒤 ON/LOW/재차 OFF가 막힘

사용자는 통합 화면 최초 OFF 실제 동작을 확인했지만 본체 확인 버튼을 누르지 않았다고 명시했다.
Mock에서 OFF→ON/LOW는 `previous_physical_confirmation_required`, 재차 OFF는 `stage_forbidden`을 재현했다.
실제 서버는 모든 StopTrial을 `precondition_or_approval_failed`로 압축하므로 그 순간의 정확한 내부 사유를
로그만으로 확정하지 못했다. 이를 실제 API 장애/권한 부족으로 단정하지 않는다.

- 본체 확인 없이는 다음 전원 동작 등 단계가 막힌다. OFF 상태에서는 LOW의 POWER_ON/CLEAN 조건도 충족하지 않는다.
- `Session.ctx.attempted`와 `Transport.attempted` **두 곳**에 같은 값의 세션당 1회 제한이 있다.
  이것은 임의 반복 조작 앱이 아니라 시험 단계 설계다. 한 곳만 지우면 해결되지 않는다.
- 일반 반복 수동 조작으로 확장하려면 ‘새 사용자 의도/새 nonce’와 ‘실패 명령 자동 재전송’을 구분하는 설계/회귀가 필요하다.
  이미 전송한/결과 불확실한 명령의 기록을 지우거나 새 ID로 재전송하지 말 것.
- 실제 이전 기기 상태와 승인 흐름을 확인하기 전 장치/PAT 문제로 보고하지 않는다.

### 8.2 본체 확인 버튼이 사라짐 — UI 복구 추가

브라우저의 `thinqVerified` 상태가 없으면 버튼을 못 찾을 수 있어 새 복구 영역을 추가했다.
새로고침→인증 연결→‘마지막 성공 동작’ 선택→사용자가 실제 본체를 보고 확인 버튼을 누른다.
선택은 확인 완료가 아니다. 기존 서버가 마지막 verified 동작과 일치하는 요청만 받아들이며
이 요청은 추가 Cloud GET/POST를 만들지 않는다. 틀리면 재선택 가능하다.
자동 attestation이나 backend 잠금 해제는 하지 않는다. 최신 수정 후 사용자의 실제 복구 성공은 아직 재확인하지 않았다.

### 8.3 오류/세션/센서 표시

- Session.safe가 상세 차단 사유를 압축한다. whitelist 사유·계층·안전한 상태 표시가 후속 과제다.
- 제어 timeout/응답 유실/상태 불일치에서는 backend와 UI가 잠금 상태를 유지한다. 성공한 새 GET만으로 잠금을 풀지 않는다.
- 캐시 TTL(기본 120초)이 지나면 현재값을 숨긴다. 마지막 성공 수신과 센서 측정 시각을 구분한다.
- 제안 30초가 지나면 다시 나타나지 않을 수 있다. 수락 기록이 없는데 `already_handled_this_session`일 수도 있다.
- 센서 화면 멈춤은 과거 SSH 단절/임시 중계의 2시간 만료가 실제 원인인 사례가 있었다.
  모든 멈춤을 그 원인으로 가정하지 않는다. viewer MQTT 연결, seq/age, tunnel, 보드 상태를 나눠 조사한다.
- 임시 구주소 19691에는 새 HTML만 읽히고 새 수집기 API가 없어 미연결로 보인 사례가 있었다.
  단일 19891로 정리하고 구서버는 제어 API를 받지 않는 이동 안내로 바꿨다.

### 8.4 설치본·시간·Native 한계

과거 설치 Pi 4 ELF/내장 Python에는 보드 전용 Python 8개/YAML 7개 보존 기록이 있고,
다른 비교 시점에는 공통 AST 6개 차이도 기록됐다. 기준/대상이 다른 수치를 동일한 비교로 합치지 않는다.
원본 ELF 소스 대응·동등 빌드·정확한 복구가 확인되지 않아 Pi 4 Native 통합 배포는 No-Go였다.
이전 보드 백업 기반 953 passed/2 failed 기록의 두 실패는 ThinQ 미적용 원본에서도 재현됐다고 보고됐으며,
현재 브랜치의 다른 테스트 집합 835 passed를 그 두 문제의 해결 증거로 쓰지 않는다.
설치 V48 및 이후 사용자 보고 V54/V55 등은 시간별 기록이며 현재 보드 버전을 여기서 확정하지 않는다.
동일 앱 ID/버전 재설치의 UID·데이터 보존/복구를 가정하지 않는다.
과거 보드 간 수백 초 시간 차이와 MQTT 보안 문제는 미해결로 취급한다. local monotonic 도입이 보드 시계를 동기화한 것은 아니다.
Pi 5 ThinQ Native PoC의 PackageManager UID 재할당·D-Bus/AppArmor/Python 제약도 해결 완료가 아니다.
남아 있던 진단 정책 원복 여부는 별도 실보드 확인 사항이다. 새 Native 서비스를 만들거나 재설치하지 않는다.

## 9. 시험 재현과 마지막 검증

아래는 오프라인 Fixture/Mock/localhost 시험이다. 운영 브로커 주소/실제 PAT를 넣지 않는다.
Hub 전체와 Gateway를 **서로 다른 Python 프로세스**로 실행해야 한다.
둘을 한 pytest 프로세스에서 수집하면 과거 `tools.thinq_connect` 이름 충돌로 collection error가 발생했다.
이름 충돌은 해결하지 않았고 assertion/skip을 바꿔 숨기지 않았다.

```bash
# 저장소 루트, 기존 Python 3.12 + 프로젝트 검증 의존성
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/hub" "$PY" -m pytest -q -p no:cacheprovider hub/tests

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/hub:$PWD/hub/tests:$PWD/tools/laptop-control" \
  "$PY" -m pytest -q -p no:cacheprovider tools/laptop-control hub/atlas-tof/pc/devview/test_devview.py

node --test --test-concurrency=1 tools/laptop-control/collector-control.test.mjs \
  tools/laptop-control/server.test.mjs tools/laptop-control/thinq-ui.test.mjs \
  tools/laptop-control/legacy-redirect.test.mjs

git diff --check
```

Windows collector의 Fake Listener 시험은 **Windows Python**에서 `python -X utf8 -B -m pytest -q -p no:cacheprovider collector/tests`.
WSL에서 실제 Windows 키보드 수집이 된다고 가정하지 않는다. 테스트는 실제 키 입력/운영 publish를 만들지 않는다.
대표 회귀: `test_mqtt_contracts.py`, `test_policy_session_attach.py`, `test_policy_manual_link.py`,
`test_demo_absence.py`, `test_automatic_off.py`, `test_unified_console.py`, `test_physical_recovery.py`,
`collector/tests/test_capture_control.py`, `test_keyboard_capture_contract.py`.

최종 코드 커밋/인수인계 검증 결과는 이 절 아래 ‘게시 검증 기록’에 기록한다.
이전 #110(1012 Python/21 Node), #111(Hub835+Gateway233/31 Node), 이후 부분 361/44 등은
각 시점·범위의 기록이다. 다른 범위를 단순 합산하거나 최신 전체 검증으로 바꾸지 않는다.
Native MQTT 19 checks, Flutter 360 등 과거 결과도 이번 인수인계에서 재실행한 결과가 아니다.

### 게시 검증 기록 (이 인수인계 브랜치)

검증 코드 커밋은 **`852e160fb1489681de28872b3e6573e35d9ea96c`**이다.
부모는 PR #111의 `39bde3da5cdb343f99fea4ef109927eae5b40bf0`, 검증된 tree는
`b7f88a97b6f1cdcabfe3fd2c58a27c7c3c9cd73b`다. 이후 인수인계 커밋은 문서/README 안내만 추가한다.

| 이번 새 worktree에서 실제 실행 | 결과 |
|---|---|
| Hub 전체, 별도 Python | **835 passed, 3 skipped, 1 xfailed** |
| Gateway·정책·devview, 별도 Python | **289 passed** |
| Windows collector Mock | **25 passed** |
| Node/HTML·구주소 API 차단, 순차 실행 | **44 passed** |
| `git diff --check` / 명시적 staged 검사 | 통과 |
| 실제 Cloud·운영 MQTT·보드 시험 | **수행하지 않음** |

기존 amqtt deprecation 등 경고는 유지했다. 코드 assertion/skip을 약화하지 않았다.
한 번의 병렬 Node 시험은 임시 localhost 포트 충돌(EADDRINUSE)이 있었고 동일 시험을
`--test-concurrency=1`로 실행해 통과했다. 테스트를 지우거나 포트 보안 검사를 우회하지 않았다.
이전 실제 Chrome(headless) 키보드 ON/OFF/새로고침 검증 및 통합 화면 확인은 원래 개발 worktree의 기록이다.
새 인수인계 사본에서 실제 Chrome·Cloud·하드웨어를 반복 실행하지 않았다.

보안 검사는 새 staged 파일 전체와 새 커밋의 blob/commit 이력을 대상으로 한다.
PAT/세션 키/실제 UUID·Device ID/MAC/개인 경로/SDK·IPK·캐시·로그·백업을 추가하지 않는다.
기존에 공개된 네트워크 예시는 새 사본에서 플레이스홀더로 마스킹했지만 선행 공개 이력은 재작성하지 않는다.
기존 10개 worktree와 원본 후속 소스·백업은 보존한다. 새 브랜치는 main에 병합하지 않는다.

## 10. 다음 우선순위와 변경 금지

1. 이 브랜치/문서/시험·성공 경로를 보존하고 팀원 환경을 읽기 전용으로 조사한다.
2. 키스트로크를 중복 구현하지 말고 **기존 토글의 실제 Pi 4 MQTT OFF/ON 수신·마우스·FSM 비회귀**를 검증한다.
   local-only 수집기와 운영 publish 모드를 혼동하지 않는다. 반복 수동 제어/거절 사유 UI도 우선 검토한다.
3. OFF 제안은 Fixture로 재현 후 실제 재실·FSM/최신 ThinQ 상태로 사용자 승인 종단을 검증한다.
   운영 600초와 명시적 시연 5초를 구분하며 자동 OFF는 별도 승인·보안 검토 전 활성화하지 않는다.
4. AUTO는 실제 BAD와 LOW가 확인되는 때만 시험한다. Fake BAD를 Live에 넣지 않는다.
5. 연결 복구, 만료/중복/외부 수동 변경/응답 유실·장시간 갱신을 검증한다.
6. 충분한 근거와 복구 경로를 확보한 뒤 Pi 4 중심 독립 ThinQ 서비스/제품 Pi 5 카드 통합을 설계한다.

main 직접 push/임의 Merge/Rebase/Reset/기존 PR 종료/타 worktree 덮어쓰기 금지.
팀 AGENTS.md와 사용자 작업물/백업을 보존한다. 운영 Pi Hub/FSM/개인화/MQTT/UART/ToF/BLE/Flutter,
AppArmor/D-Bus/계정/시계/자동 시작은 별도 승인 없이 변경하지 않는다.
실제 GET·POST·자동 제어·보드 배포·PAT 공급은 인수인계 문서가 실행 승인을 대신하지 않는다.
결선 직전에는 기능 확장보다 검증된 수동 시연과 기존 시스템 안정성을 우선한다.
