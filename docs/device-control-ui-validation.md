# 가전 제어 UI 계약·Mock 소프트웨어 통합 검증 — 2026-10-19

작업 위치: 별도 `2026ESWContest_smart_TEAMMATE-device-control-20261019` worktree,
branch `feat/device-control-platform-20261019`, 시작/현재 HEAD `f13f085`.
기존 dirty UART/환경센서 worktree, firmware, UART 코드, 과거 display/atlas/app은 수정하지 않았다.
Git staging/commit/push, 실기 배포·재시작·실제 ThinQ 호출은 하지 않았다.

## 변경과 안전 조건

- DisplayState가 선택 필드 control/execution_mode/last_episode를 파싱한다. 기존 센서 null 계약 유지.
- 서버 interaction/request의 control_plan은 Dispatcher의 고정 계획을 복사한다.
  target/op/value/reason/command_id/advice/mode를 최신 state 계획과 비교한다.
- 환경 제안 수락은 active request_id/Hub boot_id/유효한 deadline, 30초 내 상태,
  suggest gate/awaiting_user, MQTT 연결을 모두 확인한다. 재연결 전후 ID를 검사하며
  클릭한 옛 카드를 새 카드 수락으로 바꾸지 않는다. correct는 제안을 소모하지 않는다.
- 실제 선택한 대상 ID·operation·원시 value·policy rule을 그대로 카드에 표시한다.
  CO2 안내와 냉방 제어가 동시에 있으면 둘 다 표시한다. CO2만 있고 환기장치가 없으면
  '직접 환기' 안내와 '안내 확인'만 제공한다. CO2를 공기청정기에 연결하지 않는다.
- accepted/executing/failed/timeout/cancelled/skipped와 확인된 성공을 구분한다.
  executed/undone/MONITOR를 성공으로 해석하지 않는다. 명령 결과와 오류는 last_episode로 남는다.
- 서버의 state_confirmed는 matching actual_value가 확인된 succeeded에서만 true다.
  이전 결과에 확인 필드가 없으면 UI는 '기기 상태 미확인'으로 표시한다. Mock은 항상 구분한다.
- ACTION_ENV의 raw auto gate로 완료 알림을 만들지 않는다. 휴식·자세 카드/정정/리포트,
  대시보드·센서·음악·상단 navigation과 기존 motion은 유지한다.
- 수동 BLE 밝기 > 환경 명령 > phase feedback. 대기 쓰기와 연결 후 각 프레임 직전에
  revision/수동 상태/활성 권한/만료를 검사한다. 환경 제어 후 phase가 밝기를 덮어쓰지 않는다.
  수동 우선은 앱 세션에서 유지하며 명시적 자동 피드백 OFF→ON만 이를 해제한다.
- undo_of_command_id가 마지막 램프 명령과 맞지 않거나, 수동 조작 이후이면 undo도 차단한다.
  Mock 명령은 BLE 실제 쓰기로 넘어가지 않는다.
- GATT 전송 완료는 executing/transport_only로 보고하고 actual_value를 만들지 않는다.
  상태 확인 경로가 없으면 Dispatcher의 기존 결과 timeout을 적용한다.

## 검증 범위와 재현

서버 시험은 실제 FSM/LiveHub/Policy/Dispatcher와 임시 loopback MQTT broker,
Mock 센서·Mock Gateway를 사용한다. 생산 FSM gate·가중치·타이머를 바꾸지 않으며
기존 Simulation의 시험용 evidence 가중치를 사용한다(실사용 추론 성능의 증거가 아님).

```sh
DESKMATE_UI_CONTRACT_OUTPUT=/tmp/deskmate-control-ui-e2e.json \
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:hub \
/tmp/deskmate-device-control-venv/bin/python -m pytest -q -ra \
  hub/tests tools/test_thinq_connect_probe.py
```

`test_control_ui_contract.py`가 실제 loopback MQTT를 경유한 제안/결과를 합성 fixture로 내보낸다.
저조도 수락, CO2 환기 안내, 고온 냉방 제안·거절, 연결 오류, 제안 만료,
Gateway 미실행 timeout/MONITOR를 포함한다. 날짜/UUID는 합성 표본이며 실기 로그가 아니다.
기본 replay fixture는 `display/atlas/ui_env_app/test/fixtures/control_e2e.json`이다.

Flutter는 기존 atlas-dev 컨테이너의 /tmp/deskmate-ui-validation 복사본에서 검사한다.
기존 /workspace bind mount의 소스·build cache는 변경하지 않는다. 의존 SDK/버전은 변경하지 않는다.

```sh
flutter analyze
flutter test --reporter expanded \
  --dart-define=DESKMATE_CONTROL_FIXTURE=/tmp/deskmate-control-ui-e2e.json
```

검증 구분:
1. Python: 실제 Hub/FSM + loopback MQTT + Mock Gateway + MockDisplay feedback 왕복.
2. Flutter: 같은 Python export를 실제 DisplayState/제안·결과 Widget에 입력하는 replay.
3. Flutter 전체 앱 Widget: 로컬 시험 MQTT transport가 export를 전달하고,
   MqttStateSource → 전체 화면 → 수락 → request_id feedback 발행 → MONITOR 결과 표시를 검증.
   1024×600에서 overflow/exception도 검사한다.
4. 위 replay 시험과 별도로 `control_flutter_live_scenario.py` + `control_live_mqtt.dart`는
   같은 실행 중인 실제 Hub/loopback broker에 Flutter를 연결한다. Docker stdin/stdout byte pipe가
   양쪽 loopback TCP를 연결하며 LAN listener/포트 공개/privileged network 설정을 만들지 않는다.
   Flutter 카드 수락 -> 실제 feedback/user -> Dispatcher -> Mock Gateway -> result -> MONITOR
   -> Flutter 확인 표시까지 하나의 연속 실행으로 검증한다. Mock clock은 UI envelope ts에만
   wall clock으로 매핑하고 생산 FSM/센서/제어 계획/ID는 바꾸지 않는다.
5. Pi5 실제 디스플레이·실제 센서/가전은 검증하지 않았다. Mock readback이 물리 동작의 증거는 아니다.

## 전체 변경 리뷰와 커밋 후보

PAT/개인키/실제 기기 ID·계정/개인 로그/빌드 산출물을 새 소스에 저장하지 않았다.
문서·테스트의 mock_* ID와 가짜 주소는 합성 fixture다. probe의 예외 문자열/PAT와 개인 별칭이
출력될 수 있던 경로는 출력 마스킹 및 예외 본문 비출력으로 보완했다. 실제 프로필의 자유 형식
필드까지 개인정보가 없다고 보장할 수는 없으므로 실제 조회 출력은 별도 검토해야 한다.
기존 ignored .pytest_cache/__pycache__/logs/hub/logs는 커밋 후보에서 제외하고 보존한다.
Flutter 캐시·시험 로그·SDK·IPK·sqlite journal은 후보에 포함하지 않는다. index는 비어 있다.

아래는 **제안만** 하며 실제 add/commit은 하지 않았다. 순서대로 적용해야 의존성이 맞는다.

### 1. `feat(thinq): add redacted read-only device discovery`

- tools/thinq_connect/__init__.py
- tools/thinq_connect/probe.py
- tools/thinq_connect/README.md
- tools/test_thinq_connect_probe.py

### 2. `feat(control): add disabled mock device control platform`

- hub/deskmate_hub/config/environment_control.yaml
- hub/deskmate_hub/control/devices.py
- hub/deskmate_hub/control/environment_policy.py
- hub/deskmate_hub/control/device_adapters.py
- hub/deskmate_hub/control/device_gateway.py
- hub/deskmate_hub/control/command_journal.py
- hub/deskmate_hub/control/dispatcher.py
- hub/deskmate_hub/live.py
- hub/atlas/tools/build_payload.py
- hub/tests/test_environment_control_policy.py
- hub/tests/test_device_gateway.py
- hub/tests/test_device_gateway_mqtt.py
- hub/tests/test_thinq_adapter.py
- hub/tests/test_device_control_packaging.py
- hub/tests/test_gateway_recovery.py
- hub/tests/test_control_integration_safety.py
- hub/tests/test_control_platform_e2e.py
- docs/device-control-platform.md
- docs/mqtt-topics.md

### 3. `feat(display): bind control cards and lamp feedback to server plans`

- display/atlas/ui_env_app/lib/control_state.dart
- display/atlas/ui_env_app/lib/environment_control_view.dart
- display/atlas/ui_env_app/lib/display_state.dart
- display/atlas/ui_env_app/lib/state_source.dart
- display/atlas/ui_env_app/lib/dashboard_view.dart
- display/atlas/ui_env_app/lib/intervention.dart
- display/atlas/ui_env_app/lib/main.dart
- display/atlas/ui_env_app/lib/feedback_policy.dart
- display/atlas/ui_env_app/lib/sensor_overview_page.dart (동작 변경 없는 문자열 lint 1곳)
- display/atlas/ui_env_app/test/control_state_test.dart
- display/atlas/ui_env_app/test/control_hub_e2e_test.dart
- display/atlas/ui_env_app/test/support/control_fixtures.dart
- display/atlas/ui_env_app/test/fixtures/control_e2e.json
- display/atlas/ui_env_app/test/app_navigation_test.dart
- display/atlas/ui_env_app/test/dashboard_view_test.dart
- display/atlas/ui_env_app/test/feedback_policy_test.dart
- display/atlas/ui_env_app/test/intervention_test.dart
- display/atlas/ui_env_app/test/mqtt_probe_test.dart

### 4. `test(control): verify mock Hub and Flutter control contract`

- hub/tests/test_control_ui_contract.py
- hub/tests/control_flutter_live_scenario.py
- display/atlas/ui_env_app/test/control_live_mqtt.dart
- docs/device-control-integration-review.md (앞선 서버 리뷰의 역사적 기록)
- docs/device-control-ui-validation.md (이번 결과·커밋 계획)

## 미해결/실기 전 조건

- 새 EnvironmentControlPolicy enabled=false, automatic_control=false, mode=mock 그대로다.
  Gateway의 Live 실행 차단과 ThinQ adapter allow_control=false도 유지한다.
- Pi4 제한 Python에는 socket/asyncio/sqlite/SDK가 없다. Hub의 정책·Dispatcher 경로만
  사용하며 host Gateway/journal/SDK는 별도다. 패키징 회귀 테스트로 import 분리를 검사한다.
- 구버전 서버가 control_plan을 제공하지 않으면 새 UI는 환경 제안 실행 수락을 차단한다.
  구버전 Pi5가 새 snapshot/확인 규칙을 이해하지 못하므로 실기 안전성은 버전 조합 검증이 필요하다.
- BLE 외부 버튼 조작 감지/상태 readback과 Pi5 재시작을 넘는 dedup·수동 우선 저장은 미구현이다.
  네트워크·디스크 ACL 및 운영 Gateway 배치, readback과 물리 동작의 차이도 실기 검증 대상이다.
- 이미 전송된 BLE 프레임은 취소할 수 없다. Mock Gateway journal의 재시작 안전성을
  Pi5 BLE의 메모리 cache에 그대로 적용됐다고 주장하지 않는다.
- 알려진 기존 자세 신호 XFAIL은 남아 있다. 이 작업에서 FSM 임계값/가중치/UART를 변경하지 않았다.
- PAT/기기 프로필 확인 없이 특정 ThinQ 모델 기능을 활성화하지 않는다. 공기청정기 CO2 제거 가정 금지.
- push/Draft PR은 로컬 검증 결과와 제한을 기재하는 형태로 준비 가능하나 별도 승인이 필요하다.


## 직접 연결 시험 실행

기존 atlas-dev의 임시 소스 복사본에 위 Dart driver를 준비한 뒤 다음 명령으로 실행한다.
일반 test suite는 `_test.dart`만 모으므로 이 local-only driver는 아래 명령에서만 수행한다.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:hub:hub/tests \
/tmp/deskmate-device-control-venv/bin/python hub/tests/control_flutter_live_scenario.py
```

첫 직접 연결에서는 Flutter의 기본 MQTT 3.1과 시험 broker의 3.1.1-only 차이를 발견했다.
MqttStateSource에 setProtocolV311을 명시하고 CONNECT `MQTT:4` 회귀 검사도 추가했다.
이는 기존 Mosquitto와 호환되며 새 topic/새 broker 구조를 제품에 도입하지 않는다.
시험 harness는 API 접수 이벤트가 아닌 terminal succeeded 도착까지 기다리게 수정했다.

최종 직접 연결 결과: passed, mode=mock, target=mock_lamp, operation=set_brightness,
value=70, adapter_calls=1, state_confirmed=true, hardware=false.
로그는 /tmp/deskmate-control-flutter-live.log에 있으며 커밋 후보가 아니다.

환경 결과 카드의 undo도 표시한 에피소드에 묶었다. feedback/user의 선택 필드
undo_command_ids를 UI/Hub가 모두 현재 에피소드와 대조하므로, 결과를 보는 동안 새 제안이나
새 명령이 생겨도 이전 터치가 새 제어의 되돌리기로 바뀌지 않는다. legacy 휴식/자세 feedback은 유지한다.

## 최종 검증 결과

- Python: **361 passed, 1 xfailed, 36 warnings** (39.09초).
  XFAIL은 기존 posture 단독 신호 재정규화 이슈이며 신규 실패/skip은 없다.
  warnings는 시험용 amqtt helper의 기존 설정/plugin DeprecationWarning이다.
- `flutter analyze`: **No issues found**.
- 전체 `flutter test`: **232 passed**. 새 Python export를 dart-define으로 지정하여 실행했다.
- 별도 직접 MQTT/Flutter driver: **1 passed**, Mock 밝기 70, adapter 1회 호출,
  같은 command_id의 확인 결과 및 MONITOR/Mock 표시까지 확인했다.
- `dart format` 및 `git diff --check` 통과. tracked 수정 16개 + untracked 파일 30개 = 46개 후보.
- index는 비어 있으며 staging/commit/push를 수행하지 않았다. HEAD는 계속 f13f085다.
- 기존 worktree의 tracked diff는 시작 때와 같은 UART 파일 2개(12 insertions, 2 deletions)로 보존됐다.
- 구형 display/atlas/app, firmware, UART/native C++/ToF, FSM engine/가중치/임계값은 변경하지 않았다.

호스트 검증 로그와 전체 diff:
- /tmp/deskmate-control-final-python.log
- /tmp/deskmate-control-flutter-analyze.log
- /tmp/deskmate-control-flutter-tests.log
- /tmp/deskmate-control-flutter-live.log
- /tmp/deskmate-control-flutter-live-result.json
- /tmp/deskmate-device-control-ui-review.patch

이는 로컬 Mock 전용 Draft PR의 검토 준비 상태다. 실제 push/PR 생성은 하지 않았다.
실기 배포·실제 ThinQ 제어 활성화의 승인을 의미하지 않으며, 위 미검증/잔여 조건은 그대로 남는다.
