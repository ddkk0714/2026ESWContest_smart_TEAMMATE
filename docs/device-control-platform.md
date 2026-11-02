# 가전 제어 기반 — 2026-10-19 로컬 구현

기준 `origin/main`: `f13f08563e39f68817ffc70d1363fd3bb0a22f75`.
이 변경은 로컬 코드와 합성 자동 테스트까지다. 실제 계정·장치·운영 브로커를 호출하거나
Pi 4/Pi 5에 배포하지 않았다. 실제 모델, capability binding과 실기 동작은 미검증이다.

## 제어 책임과 기본값

```text
SensorCache ENV (필드별 valid + 수신/원본 시각)
  -> EnvironmentControlPolicy.observe / plan (YAML)
  -> 기존 ControlDispatcher 에피소드
     FSM auto/suggest/none, 기존 request_id / accept / reject / expiry / undo
  -> 기존 control/cmd -> DeviceGateway -> DeviceAdapter
  <- 기존 control/result <- API 접수 후 별도 상태 조회로 확인
```

`config/environment_control.yaml`은 `enabled: false`, `mode: mock`,
`automatic_control: false`다. 이 마일스톤에서는 정책을 **mock 모드에서만** 켤 수 있다.
설정의 수치는 검증용 예시이며 제품 임계값이나 기존 FSM의 가중치/게이트를 변경하지 않는다.
기본 설정에서는 기존 Dispatcher·FSM·MQTT·제안 카드·iLink 경로가 그대로 동작한다.
기존 `config/control.yaml`의 enabled 값이나 legacy 동작을 전역 OFF로 바꾸지 않았다.

정책은 명령을 발행하지 않는다. Dispatcher에 `plan_commands`를 전달하면 legacy
`actions.environment`의 fan+lamp 묶음을 **대체**하고, 두 목록을 합치지 않는다.
새 정책의 AUTO는 `automatic_control: true`인 Mock 실험에서만 허용한다.
AUTO를 SUGGEST로 바꾸거나 NONE 게이트를 올리지 않는다. SUGGEST는 기존 수락이 필요하다.

현재 LiveHub의 선택적 정책 연결은 프로세스 내 기존 MockPlugAdapter에만 발행한다.
외부 `publish_control`을 사용하지 않으므로 Mock 명령이 실제 MQTT/기기에 섞이지 않는다.
Mock 정책 활성 시 state/phase의 control 요약에만 선택 필드
`execution_mode: mock`을 추가하여 합성 제어 결과임을 표시한다. Gateway는 이와 독립된 호스트 실행 구성 요소다. Dispatcher에서 나온 명령만 전달하는
통합 Fake MQTT 테스트가 있으며, 운영 서비스 자동 시작·계정 인증·Gateway 배포는 하지 않는다.

## 모듈

- `control/devices.py`: Capability, Device, DeviceRegistry. 명시적으로 등록된 역할·기능만 선택.
  기기 online, 최신 실제 상태, manual hold, 역할과 허용 값, Mock/Live 소유권을 검사한다.
- `control/environment_policy.py`: 센서별 임계값, 히스테리시스, 최소 지속, 쿨다운과 최신성.
  valid는 개별 co2/temp/humidity/lux flag로 검사한다. aggregate valid를 대체 사용하지 않는다.
- `control/device_adapters.py`: 네트워크 없는 MockDeviceAdapter와 공식 SDK 객체를 주입받는
  ThinQConnectAdapter. 일반 BLE/Wi-Fi 기기는 공통 DeviceAdapter 인터페이스와 Mock까지만 제공한다.
- `control/device_gateway.py`: 기존 cmd/result envelope, QoS1 중복, 만료, retained 거절,
  기기 소유권, 수동 조작 우선, 가습/제습 충돌과 명령 후 상태 확인.
- `atlas/tools/build_payload.py`: 새 YAML도 JSON으로 동봉해 제한 Python에서 PyYAML을 요구하지 않는다.
  보드 LiveHub 경로는 asyncio/socket/SDK를 import하지 않는다.

## 환경 정책

CO2는 `outside_air` 역할에만 연결한다. 외기 교환 장치가 없으면 명령 후보가 없고,
기존 env_suggest 카드에 따른 환기 제안/사용자 행동을 유지한다. 공기청정기로 CO2를 낮춘다고
가정하지 않는다. `air_cleaning` 규칙은 현재 차단하며, 실제 PM/공기질 입력과 검증된 기기
프로필·별도 정책 설계가 확보돼야 추가할 수 있다.

온도 상승은 cooling, 건조는 humidifying, 고습은 dehumidifying, 어두움은 lighting
역할을 찾는다. 등록된 적합한 기기가 없으면 다른 종류를 대신 제어하지 않는다.
가습/제습은 반대 기기가 켜져 있거나 상태가 불명확하면 차단한다. 진행 중인 반대 명령도
Gateway에서 차단한다. 히스테리시스 해제는 후보 제거이며, 별도 OFF 명령을 임의 생성하지 않는다.

센서 결측·invalid·NaN/Inf·불가능 범위·stale·미래 시각·시계 역행·표본 간 긴 공백은
지속 증거를 초기화한다. 임계값을 일정 시간 넘은 최신 증거만 계획에 사용한다.
제안 수락 직전에도 최신 센서·기기 상태를 확인하며, 이전 상태를 undo 값으로 보관한다.

기기 상태가 예상한 자체 명령과 다르게 바뀌면 사용자 또는 외부 제어로 간주하여
`manual_override_sec` 동안 제어/undo를 막는다. `mark_manual`도 제공한다.
실기에서는 실제 사용자 조작 알림/폴링을 이 API로 연결해야 한다.
현재 상태를 읽을 수 없거나 rollback 값이 지원되지 않으면 명령을 만들지 않는다.

## MQTT와 BLE 소유권

기존 `deskmate/control/cmd`, `deskmate/control/result` 필드와 envelope를 유지한다.
새 토픽이나 새 사용자 수락 채널은 만들지 않았다. registry의 내부 context, 모델별
외부 ID, PAT는 MQTT 결과로 내보내지 않는다.

Gateway.attach(client)는 운영자가 만든 **전용** paho-compatible MQTT client에
`control/cmd` QoS1 구독 콜백을 연결한다. 같은 client의 publish를 주입할 때는
기존 `control/result`에 JSON envelope를 QoS1, retain=false로 전송한다.
단일 이벤트 루프에서 `pump()`를 주기적으로 호출한다. 기존 Hub MQTT client의 콜백을 덮어쓰면 안 된다.
노트북 등 full Python 호스트를 전제로 하며, restricted Python을 Paho로 우회하지 않는다.
node=hub 검사는 인증이 아니다. 기존 브로커 ACL의 Hub-only 명령 발행 권한을 전제로 한다.

기존 Pi5 `desk_lamp`는 Gateway가 항상 무시한다(미등록이어도 실패 결과를 발행하지 않음).
다른 기존 어댑터 소유 대상은 `external_targets` 또는 `gateway_owned=false`로 제외한다.
BLE 프로토콜·Pi5 제안 카드·램프 피드백은 변경하지 않았다.

command_id는 현재 Gateway 세션에서 중복 실행되지 않는다. 같은 ID에 다른 내용을 실어도
실행하지 않는다. 메모리 한도에 도달하면 새 명령을 차단하며 기존 ID를 지우지 않는다.
통합 리뷰에서 Mock 전용 `journal_path`를 추가했다. 이 경로를 지정한 Gateway는
claim/intent를 SQLite에 먼저 저장하고 재시작 시 조회만 수행한다. 메모리 모드는 재시작 보호가 없다.
Live Gateway 실행은 현재 마일스톤에서 차단한다. 세부 실패·복구 의미는
[통합 리뷰](device-control-integration-review.md)를 따른다.
실패·timeout도 자동 재전송하지 않는다(POST timeout일 때 장치가 받았을 수도 있기 때문).
원래 명령이 상태 확인 중일 때 들어온 undo는 API 호출 없이 대기한다. 원래 변경이 확인된
뒤 같은 기기의 이전 값으로 되돌리며, 원래 상태가 미확인/만료면 undo 성공을 위조하지 않는다.

## ThinQ 모델 검증 경계

공식 SDK:
- https://github.com/thinq-connect/pythinqconnect/blob/main/thinqconnect/thinq_api.py
- https://github.com/thinq-connect/pythinqconnect/blob/main/thinqconnect/devices/connect_device.py

사용하는 메서드는 async_get_device_profile, async_get_device_status,
async_post_device_control이다. API 호출에는 SDK timeout과 외부 asyncio timeout을 적용한다.
계정 인증·PAT 발급·기기 프로필 조회는 이번에 실행하지 않았다.

ThinQConnectAdapter는 `allow_control=false`가 기본이다. Gateway도 기본 disabled,
Mock 모드, 자동 제어 OFF이며 현재 allow_live 값과 무관하게 Live 실행을 차단한다. 실제 모델용 operation은 하나도 등록하지 않았다.
실제 프로필에서 검증한 `property/resource/property`, 상태 경로, 논리 값↔wire 값 binding이
있고, 실행 시 다시 조회한 profile의 r/w mode와 허용 값·범위·step 검사를 통과해야 POST가 가능하다.
알 수 없는 프로필 구조나 미지원 기능을 추측하지 않는다.

SDK POST 응답 성공은 API 요청 접수이며 상태 확인 성공과 다르다. Gateway는 먼저 executing을
발행하고 이후 별도 GET state 값이 목표와 일치해야 succeeded를 발행한다. 조회 실패/지연은
성공으로 바꾸지 않고 기존 명령 기한 내에서만 조회한다. 기한 후 결과로 성공을 되살리지 않는다.
이미 원하는 상태였다면 쓰지 않고 완료하며, 내부 evidence에 already_satisfied와
write_attempted=false, api_accepted=false를 남긴다. 외부 결과 계약은 유지한다.
상태 조회 확인도 실제 팬 회전 등 물리 동작의 독립 검증을 대체하지 않는다.

인증 오류·offline·네트워크 timeout은 고정 오류 코드로 정리한다. SDK 예외의 headers,
PAT, 계정/기기 ID, 상세 에러 문자열을 로그나 MQTT 결과로 복사하지 않는다.

## UART 범위

기존 dirty worktree의 UART relay 소스는 이식하지 않았다. PR #55가 표준 ToF 0x05 grid를
직접 처리하므로 grid→v1 변환과 방향 플래그의 의미는 차후 단일 UART 리더 통합에서
재검토한다. 이번 가전 제어에서는 UART·펌웨어·자세 서비스를 변경하지 않는다.

## 로컬 테스트 및 남은 실기 검증

```bash
PYTHONPATH=.:hub /tmp/deskmate-device-control-venv/bin/python -m pytest -q \
  hub/tests tools/test_thinq_connect_probe.py
git diff --check
```

테스트 데이터는 합성 센서·Fake SDK 응답이다. MQTT 콜백 Fake 테스트와 별도로,
임시 127.0.0.1 amqtt 브로커에서 실제 pub/sub·재연결 시험을 한다. 운영 Pi4 브로커나 가전에는
연결하지 않는다. 정상/결측/invalid/stale, 지속·히스테리시스·쿨다운, 역할 선택,
수동 우선, 가습/제습 배제, 수락/거절/만료/undo, 중복·retained·만료 명령,
인증·timeout·profile 제한, 요청 접수와 상태 확인 구분, 제한 Python 패키징을 확인한다.

기기 확보 후에는 PAT를 숨김 프롬프트로 입력해 이식한 read-only probe로 KR 계정의 목록,
정확한 모델/기기 종류/프로필/상태를 확인한다. 가전마다 역할·operation binding을 검토하고
기존 BLE 대상 소유권, 사용자 수동 조작 감지, 실제 제어 지연·실패·undo와 재시작 중복 방지를
검증한 뒤 별도 승인을 받아 Live를 활성화해야 한다. 공기청정기는 별도 PM/공기질 근거 없이
이 환경 정책에 연결하지 않는다.

## 초기 구현 검증 기록

2026-10-19, 신규 worktree의 격리된 `/tmp/deskmate-device-control-venv`에서 실행했다.
Python 3.10 / pytest 9.1.1 / PyYAML 6.0.3 / paho-mqtt 2.1.0 /
NumPy 2.2.6 / amqtt 0.12.1. 기존 worktree의 가상환경·빌드 산출물은 사용하거나 수정하지 않았다.

- Hub 전체 + 이식한 ThinQ probe: 327 passed, 1 xfailed (skip 없음).
- 기존 XFAIL: `test_missing_signals_must_not_let_posture_alone_confirm`.
  자세 신호 단독 점수 재정규화에 관한 기존 기대 실패이며 이번 범위에서 바꾸지 않았다.
- 경고 4개: amqtt에서 기존 테스트 helper의 구형 설정·plugin 로딩에 내는 DeprecationWarning.
- Mock MQTT 왕복, 기존 MQTT reconnect, FSM·feedback·undo·환경·UART·BLE 회귀 포함.
- 실제 ThinQ SDK 네트워크 호출, 실제 가전 작동, 보드 배포/재시작은 미수행.
- `git diff --check` 통과. 생성 파일도 별도 공백 검사 및 기존 ThinQ 원본 4파일 바이트 비교 수행.

후속 통합 안전성 수정과 종단 시나리오는 [통합 리뷰](device-control-integration-review.md)에 기록한다.
