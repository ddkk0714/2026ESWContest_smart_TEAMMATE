# 가전 제어 통합 리뷰 및 Mock 종단 검증

작업 branch: `feat/device-control-platform-20261019`, 기준 HEAD `f13f085`.
기존 dirty worktree, UART·ESP32·Pi5 UI 소스와 실기 설정을 변경하지 않는다.
이번 결과는 코드/합성 시뮬레이션이다. 실제 ThinQ API, 실제 센서·가전, Pi4·Pi5 앱 실행으로
검증한 결과는 없다. 기존 구현 위에 안전성 수정만 누적한다.

## 발견 사항과 처리

| 심각도 | 문제 | 처리 |
|---|---|---|
| 높음 | command_id 기록이 메모리에만 있어 재시작 후 중복 POST 가능 | Mock용 SQLite write-ahead journal 추가. claim/POST intent를 먼저 commit하고 복구 시 GET만 수행 |
| 높음 | POST 응답 유실을 즉시 failed로 판단 | network timeout/error는 요청 결과 unknown + executing. 재전송 없이 GET으로 확인하거나 timeout |
| 높음 | Dispatcher가 발행하지 않은 제안 명령의 결과도 수용 가능 | sent_ts가 없는 명령 결과 거절. 새 정책 성공은 일치하는 actual_value 필수 |
| 높음 | 무가전 제안의 명령 목록이 비어 있으면 skipped로 끝나 카드가 한 tick 만에 닫힘 | advice-only 에피소드는 기존 SUGGEST/feedback/만료 경로에서 응답 대기. 수락해도 장치 명령 생성 안 함 |
| 높음 | UI가 환경 플래그로 행동을 추측해 수락 대상과 실제 계획이 달라 보일 수 있음 | 서버 계획은 에피소드에 고정하고 수락 후 재선택하지 않음. **UI 수정은 보류**, 아래 최소 수정 필요 |
| 높음 | UI가 FSM gate=auto만 보고 '환경을 조정했어요'를 표시. 정책 veto·실패·API 접수와 구분 불가 | gate는 그대로 유지. 서버는 실제 skipped/failed/timeout/confirmed 상태 제공. **UI 수정 전 Live 차단 유지** |
| 중간 | MONITOR 진입 즉시 결과 요약이 없어지고 error_code도 버려짐 | control.last_episode, command_id, error_code 추가. MONITOR는 성공 판정이 아니라 제어 에피소드 종료 |
| 중간 | legacy request_id 누락/atlas-display 수락이 새 가전 제안에도 적용됨 | 새 정책이 켜진 제안만 정확한 현재 request_id를 요구. 정책 OFF의 기존 HTTP 경로는 유지 |
| 중간 | 준비되지 않은 계획(지속시간·쿨다운 등)에 generic 적용 카드를 발행 | 새 정책에서 awaiting_user가 아닌 에피소드에는 interaction/request를 발행하지 않음 |
| 중간 | 기존 램프의 phase feedback이 환경 명령 후 recovery/idle에서 밝기를 다시 바꿀 수 있음 | 현 상태 확인만 수행. UI 수정 안 함. Gateway는 desk_lamp를 소유하지 않음. 추가 소유권/우선순위 정책 필요 |

## 실제 환경 데이터 및 FSM 계약

정책은 SensorCache.env의 `co2_ppm/co2_valid`, `temp_c/temp_valid`,
`humidity_pct/humidity_valid`, `lux/lux_valid`를 사용한다. UART ingest는 ts와 received를
Hub 수신 epoch로 채우며, MQTT source는 envelope ts를 보관한다. 정책은 수신과 원본 시각을
모두 검사한다. aggregate valid는 개별 센서 validity를 대신하지 않는다.

새 정책 freshness 15 s는 기존 ingest ENV freshness 30 s보다 보수적이다. 기본 ENV
polling 5 s, Hub tick 10 s와 모순되지 않지만, 표본 유실이 길어지면 새 계획을 차단한다.
온도·습도는 기존 FSM에서 environment.phi이고, CO2/저조도는 environment.delta다.
따라서 **고온만으로 곧바로 ACTION_ENV가 된다고 주장할 수 없다.** 정책은 기존 FSM 진입 후
후보를 선택하며 gate, 가중치, 임계값, 상태 전이는 바꾸지 않는다.

단일 계획 훅이 legacy fan+lamp 목록을 대체한다. AUTO 허용과 SUGGEST 수락은 별개다.
automatic_control=false이면 AUTO 요청에 명령을 만들지 않으며 이를 임의로 SUGGEST로
바꾸지도 않는다. 제안 수락은 기존 feedback/user와 정확한 카드 ID를 사용한다.
거절·무응답·invalid/stale 표본은 실행을 만들지 않는다. 수락 후에는 이미 제안한 target/op/value만
재검사하고, 다른 기기로 바꿔 실행하지 않는다.

## 재시작 및 응답 유실 설계

`control/command_journal.py`는 **full Python/WSL 호스트의 Mock 전용**이다. journal_path를
주면 SQLite synchronous=FULL과 단일 프로세스 파일 lock을 사용한다. 로컬 target/operation/value,
command_id, 기한, 요청/결과 근거만 보관한다. PAT·SDK header·실제 외부 device ID는 보관하지 않는다.

1. 명령 수신 claim을 commit한다.
2. 어댑터 POST 직전 intent와 이전 값을 commit한다.
3. API 접수/결과는 publish보다 먼저 저장한다.
4. 재시작 후 미완료 명령은 GET으로만 확인한다. 상태가 맞아도 '복구 조회에서 일치'라고 기록하며
   원래 POST가 실행됐다는 인과 관계를 위조하지 않는다. 기한이 지나면 timeout이다.
5. terminal 결과의 중복 명령은 저장된 결과만 다시 보낸다. 변경된 동일 ID는 실행하지 않는다.
6. 재시작 전 대기 중이던 undo는 재실행하지 않고 cancelled로 알린다. 불명확한 원래 동작을
   가정해 되돌리는 것도 피한다. 새 사용자 판단이 필요하다.
7. journal 손상·쓰기 실패·중복 소유는 실패로 닫으며, 기록 없이 실행하는 fallback은 없다.

이는 **at-most-once** 전략이다. crash 전에 claim만 저장되고 POST가 안 됐어도 자동 재전송하지
않으므로 일부 명령은 실행되지 않고 timeout이 될 수 있다. MQTT에서 Gateway가 받지 못한
명령도 Hub timeout으로 끝난다. exactly-once 물리 동작을 보장하지 않는다.
메모리 전용 Mock 테스트 모드는 여전히 가능하지만 재시작 보호를 주장하지 않는다.
Live Gateway 실행은 allow_live 값과 무관하게 현재 마일스톤에서 차단한다.

POST 응답이 유실되면 api_accepted=None/request_outcome=unknown이다. 이후 GET 값이 일치해야
succeeded로 끝내고, 불일치/조회 오류는 기존 deadline까지만 기다린다. 재POST는 하지 않는다.
GET 일치도 물리 팬 회전 등 독립 실험의 대체 증거는 아니다.

## Pi4 제한 런타임과 기본 상태

LiveHub는 policy/devices/Dispatcher의 순수 모듈만 import한다. Gateway, sqlite3, asyncio,
aiohttp, SDK, Paho는 보드 경로에서 import하지 않는다. 새 YAML은 기존 payload builder가
JSON으로 동봉한다. `_socket/_random/_ssl/asyncio/sqlite3/SDK`를 막은 subprocess에서도
정책 및 Mock Hub를 구성하고 계획하는 시험을 추가했다. 이는 호스트 모의 검증이며 실제 Pi4
배포 검증은 아니다. 기본 enabled=false와 자동 제어 OFF는 유지한다.

MockControlTransport는 로컬 E2E 시험에만 명시적으로 주입한다. 없으면 기존 프로세스 내
MockPlug를 유지한다. 외부 transport를 주입해도 actual production MQTT 경로를 자동 선택하지
않으며, Mock 태그와 Mock registry를 사용한다. 시험은 임시 127.0.0.1 브로커만 사용한다.
운영 실행 위치·파일 권한·재시작 정책·journal 경로 및 백업은 아직 배포하지 않았다.

## Pi5 UI 코드 리뷰 — 실행 검증과 구분

이번에 실제 Flutter를 실행하거나 UI 소스를 수정하지 않았다. 아래는 현재 Dart 코드의 정적 리뷰다.

| 항목 | 현재 코드의 지원 |
|---|---|
| 환경값 및 원인 | DisplayState.fromEnvelope가 센서값/cause/env_flags 파싱. interventionReason이 이유 문구 생성 |
| 대상 가전·동작·계획값 | DisplayState에 control 모델이 없으므로 파싱/표시 안 함 |
| 수락·거절·만료 | state_source가 request_id/expires_in_s를 기억하고 feedback/user로 전달. main._feedback와 SuggestionView 버튼 존재 |
| 처리 결과 | control/result를 구독하지 않으며, state/phase의 control 요약도 파싱 안 함. 전송 성공 snackbar는 가전 성공이 아님 |
| 자동 실행 알림 | InterventionTracker가 ACTION_* + gate=auto만 보고 생성. 실제 명령 veto/실패/Mock/조회 미확인을 구분 못 함 |
| 무가전 카드 | 기존 버튼은 '적용할게요'. manual_ventilation이면 실제 의미에 맞는 '확인했어요' 등의 문구가 필요 |

주요 위치:
- `display/atlas/ui_env_app/lib/display_state.dart`: DisplayState.fromEnvelope
- `.../state_source.dart`: _onUpdates, feedback, _handleControl
- `.../dashboard_view.dart`: SuggestionView, _environmentTitle
- `.../intervention.dart`: InterventionTracker.update, autoActionTitle
- `.../feedback_policy.dart`: FeedbackCoordinator.apply/accept/_executeLampCommand

특히 co2_high와 too_hot이 함께 있고 냉방 Mock만 등록된 경우 서버 계획은 mock_ac.set_power=on이다.
기존 _environmentTitle은 CO2를 우선해 '잠깐 환기해 볼까요?'라고 선택한다. 그 카드만 보고
수락하면 냉방까지 수락했다고 보기 어려우므로 **현재 UI 그대로 실제 가전 제어를 활성화하면 안 된다.**
서버 수락 계획 고정 테스트는 통과했지만, 사람이 보는 제안과의 일치는 아직 UI에서 검증하지 않았다.

최소 UI 변경 제안(이번에는 미수행):
1. DisplayState에 nullable ControlSummary/Command/Advice를 추가. 기존 payload에 없으면 안전한 fallback.
2. MqttStateSource에서 request_id와 해당 계획 스냅샷을 함께 유지. 새 topic/새 feedback 채널은 불필요.
3. SuggestionView에 실제 target/op/value와 manual advice를 표시. env_flags만으로 실행 내용을 추측하지 않음.
4. 실행 중/확인 성공/실패/timeout/skipped/Mock을 control 요약과 last_episode로 표시. MONITOR를 성공으로 해석하지 않음.
5. InterventionTracker의 자동 완료 알림은 raw gate만으로 생성하지 않고 실제 확인 상태를 반영.
6. command_id가 같은 카드/결과는 반복 렌더링·재실행하지 않음. 데이터가 없는 기존 메시지는 기존 안전한 문구 유지.
7. 기존 FeedbackCoordinator에서 phase/환경/수동 조작의 lamp 소유권 우선순위를 정한 뒤 수정.

기존 apply/accept는 ACTION_ENV의 즉각적인 phase feedback을 차단하고 BLE 프레임을 직렬화한다.
하지만 RECOVERY/idle 등 이후 phase는 램프를 다시 바꾸므로 의미상 우선순위까지 보장하지 않는다.
PC용 ilink_light도 따로 활성화하면 별도 phase writer가 될 수 있다(현재 기본 disabled).
두 컨트롤러의 실제 활성 상태는 이번에 장치에서 확인하지 않았다.

## 연속 소프트웨어 종단 시나리오

`test_control_platform_e2e.py`는 실제 LiveHub/FSM/mapping/Policy/Dispatcher/MQTT를 사용한다.
하드웨어와 Pi5 UI만 Mock이다. 합성 Sample을 SensorCache에 주입하고 실제 loopback broker에서
cmd/result/feedback/state/request가 왕복한다. UI 역할은 MockDisplay가 기존 request_id/verdict만
발행한다. FSM을 stub하거나 상태를 강제로 ACTION_ENV로 바꾸지 않는다.

시험 fixture만 fatigue npc={posture:.30,environment:.70,elapsed:0},
focus npc={posture:.50,environment:.50,elapsed:0}를 사용한다. 생산 YAML 파일·gate·timer는 변경하지
않는다. 고온 시험에는 CO2 증거를 함께 공급한다. 이 결과를 기본 가중치의 실사용 성능으로 주장하지 않는다.

| 순서 | 입력/응답 | 확인 결과 |
|---|---|---|
| 1 | 저조도 -> 수락 | mock_lamp 밝기 70 제안; 동일 command_id/target/op/value 1회 실행 -> 결과 succeeded -> MONITOR |
| 2 | CO2 상승, 환기장치 미등록 | 명령 없이 manual_ventilation 카드 대기; 수락 시 advised -> MONITOR, 장치 호출 없음 |
| 3 | 고온+CO2 -> 거절 | mock_ac 제안; 명령 발행·실행 없음 -> MONITOR |
| 4 | 고온+CO2 -> 수락, 연결 오류 | error_code=network_error/failed를 결과와 last_episode에서 확인 -> MONITOR |
| 5 | 기존 쿨다운 경과, 고온+CO2 -> 수락 | 같은 계획의 냉방 Mock 전원 on 1회 -> 확인 결과 -> MONITOR |
| 6 | 환경 개선 | 기존 FSM MONITOR -> RECOVERY -> FOCUS_NPC |

별도 연속 시험으로 Gateway 미실행 timeout, 전체 기기 미등록의 advice-only 거절,
누락/atlas-display/오래된 request_id의 수락 차단, 계획 미준비 시 카드 미발행도 확인한다.

## 남은 작업과 실제 공기청정기 검증

하드웨어 없이 가능한 남은 작업은 위 UI 최소 변경·위젯 테스트, 계획/request 연계 표시,
명령 상태 오류 UX와 BLE 우선순위 합의, 운영 Gateway의 배치/저장소/ACL 설계다.
UI 변경은 다음 승인을 받아 진행해야 한다. 현재는 서버 및 Mock 검증 단계다.

실제 공기청정기 확보 후 순서:
1. KR 계정/PAT를 로컬 숨김 입력하고 기존 read-only probe로 목록·모델·프로필·상태만 확인.
2. 기기 타입과 실제 읽기/쓰기 property·허용 값·range·step·인증 권한 검증. operation binding은 이 근거로 등록.
3. PM/공기질 데이터가 있는지, 최신성/validity를 판단할 수 있는지 확인. CO2 제거/외기 교환 기능으로 추정하지 않음.
4. 사용자 직접 조작의 이벤트/폴링, online/offline, 응답 지연, API 접수와 상태·물리 동작의 차이를 확인.
5. 별도 제어 승인 후 소규모 가역 명령으로 상태 확인·되돌리기·응답 유실·재시작을 실기 검증.
6. UI의 실제 제안/결과 표시와 권한, 운영 journal/ACL 검증 후에만 Live 활성화를 검토.
   공기질 근거 없는 공기청정기 자동 제어와 CO2 연동은 계속 금지한다.

## 실행 결과

최종 실행: `/tmp/deskmate-device-control-venv/bin/python` (Python 3.10).

```text
PYTHONPATH=.:hub python -m pytest -q -ra hub/tests tools/test_thinq_connect_probe.py
353 passed, 1 xfailed, 24 warnings in 30.33s
```

기존 327 pass에서 새 안전성/복구/종단 테스트 26개를 추가해 통과했다. skip은 없다.
기존 자세 신호 재정규화 XFAIL 1개는 그대로다. 경고 24개는 임시 broker의 기존 helper에서
나오는 amqtt 설정/plugin DeprecationWarning(시험 6개 × 4개)이며 이번에 숨기거나 변경하지 않았다.

- `test_gateway_recovery.py`: 12개 — 재시작, 응답 유실, journal 실패/lock, Mock→Live, Fake SDK.
- `test_control_integration_safety.py`: 8개 — 수락 전 결과 차단, advice/수락/거절/만료, 계획 고정, 수동 우선, 인증 미설정, 값 확인.
- `test_control_platform_e2e.py`: 5개 — 연속 실제 FSM/임시 MQTT 시나리오와 Gateway 부재·무가전·카드 ID·미준비 계획.
- `test_device_control_packaging.py`: 제한 Python 경로 테스트 1개 추가.

`outcome=executed/undone`은 기존 계약의 명령 발행 여부다. 실제 성공은 각각의 command.status와
검증된 actual_value로 판단한다. MONITOR나 outcome 문자열만으로 기기 동작 성공을 표시하면 안 된다.

전체 실행 로그: `/tmp/deskmate-control-integration-tests-20261019.txt`.
전체 현재 변경 diff(신규 파일 포함): `/tmp/deskmate-control-integration-review-20261019.patch`.
원래 worktree의 미커밋 UART/ThinQ 파일과 환경센서 worktree의 상태는 보존됐으며, 새 worktree의
UI·firmware·UART·FSM/ingest YAML 변경은 없다. Git staging/commit/push와 장치 배포는 수행하지 않았다.

## 후속 기록

위 UI 미수정·미검증 설명은 앞선 서버 리뷰 시점의 기록이다. 후속 승인에 따라 UI 계약·BLE
우선순위를 로컬에서 수정했고, 현재 결과와 정확한 Mock/Widget 경계·커밋 후보는
[`device-control-ui-validation.md`](device-control-ui-validation.md)를 따른다. 실제 Pi5 배포나
실제 가전 제어는 여전히 수행하지 않았다.
