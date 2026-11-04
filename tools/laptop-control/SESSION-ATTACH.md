# V1.1 기존 활성 세션 합류

## 확인한 실제 계약

Pi 4 `deskmate/state/phase`의 비retained 실메시지에서 `node=hub`, `schema_version=1.0`,
`boot_id`, 증가하는 `seq`, `data.source=fsm`, `fsm_state=FOCUS_NPC`, `phase=focus`,
boolean `present`를 확인했다. 확인한 메시지에는 별도 세션 ID/활성 여부 필드가 없었다.
원문 메시지·boot ID 값·개인정보는 저장하지 않았다.

`hub/deskmate_hub/presentation.py`의 상태→phase 투영과 FSM 코드를 근거로,
IDLE/END를 제외한 START·작업·피로·회복 구간의 일치하는 상태만 활성 근거로 사용한다.
상태→phase 목록은 호스트 테스트에서 Hub의 실제 투영과 일치하는지 검사한다.
이는 기존 MQTT 인증/ACL 문제를 해결하거나 메시지에 암호학적 신원을 추가한 것은 아니다.

## 동작

- 기존 IDLE→START 감지는 유지한다.
- 최초 접속/재연결 시 최신 Hub 메시지의 source·FSM·phase가 일치하면 기존 세션에 합류한다.
  FSM 상태는 그대로이며 START 이벤트나 Hub 초기화를 만들지 않는다.
- 합류 후 PC에서 새로 관측한 재실 true 30초가 필요하다. 캐시 나이를 재실 시간에 보태지 않는다.
- 부재, MQTT 연결 끊김, 센서 뷰어 연결 실패, 오래된/역순/retained 메시지에서는 제안을 막는다.
  회복 후 다시 30초를 측정한다. IDLE/END는 기존 세션 종료로 처리한다.
- PC PhaseFeed가 보존하는 `source/phase`는 기존 Hub 메시지 필드다.
  HTTP 메타데이터 `mqtt_connected/mqtt_epoch`는 PC 수신 연결 상태이며 Hub 세션 ID가 아니다.
- 같은 정책 프로세스에서 같은 Hub 부팅의 연결만 끊겼다면 기존 세션 번호와 처리 기록을 유지한다.
  합류할 때마다 새 세션 번호를 만들거나 seen 기록을 지우지 않는다.
- 세션 ID가 없으므로, 끊긴 사이 IDLE→START가 있었는지는 추정하지 않는다. 이 경우 기존 기록을
  유지해 중복 ON을 억제한다. 새 세션을 놓쳐 제안이 생략될 수 있다.
- 정책 프로세스 자체를 종료한 뒤에는 메모리 중복 기록을 복구할 수 없다. 프로세스 재시작을
  넘어선 정확한 세션 식별/중복 보장에는 Hub의 안정적인 세션 ID 또는 별도 승인된 상태 보존이 필요하다.
- 기존 수동 조작 우선, 만료, OFF/AUTO, 대상 고정, 일회 nonce, readback 및 본체 확인을 유지한다.
  실제 POST는 기본 차단한다. 별도 승인한 시험 모드에서도 새 개별 변경 승인이 필요하며
  자동 제어는 계속 차단한다.

## 호스트 변경 범위

`AirPurifierPolicy`, PC `thinq_observer.PhaseFeed`, PC devview의 MQTT 연결 상태 전달,
Live Node의 읽기 실패 전달, HTML의 합류 진단·카드 위치만 변경했다.
Pi 4의 운영 코드/ELF/IPK, Pi 5 운영 앱, 서비스 설정, MQTT 브로커 설정은 변경하지 않았다.

코드 반영을 위해 노트북의 읽기 전용 뷰어 18890과 Live 19391/19392만 다시 실행했다.
인증은 사용자가 새 터미널에서 숨김 입력하며 토큰을 추출/파일 저장하지 않았다.
기존 18891/19091/19291/19491 서버는 유지했다.

## 검증

- 관련 Python 306 passed: 정상 START, 중간 FOCUS 합류, 합류 후 30초 미만 차단, 재연결 중복 차단,
  부재·종료·stale·retained·역순 차단, 기존 OFF/AUTO·수동 제어·PC devview 회귀 포함.
  ON 전용 시험의 OFF/AUTO 차단, 일회 ON, POST 직전 동작 범위 재검사 3개를 포함한다.
- Node/HTML 31 passed: 합류 진단·연속 재실 카운터, 팝업→제안 카드 이동, 기존 모드 보존 검사.
- 실제 Chrome에서 제안 카드가 센서 화면 위에 배치되고 팝업의 카드 보기 버튼으로 이동함을 확인했다.
- 실제 Pi 4 읽기 전용 경로: 새로운 PhaseFeed가 source/phase/연결 상태를 보존하는지 확인했다.
- 사용자가 실행한 실제 GET 캐시에서 `api_status=ok`, POWER_OFF 및 유효값을 확인했다.
  실제 정책 응답에서 `session_active=true`, `session_origin=attached`, 연속 재실 약 48초,
  `set_power/on` 제안 생성, `post_enabled=false`를 확인했다. 도구에서 Cloud를 재호출하지 않았다.
- 제안은 30초 후 만료된다. 만료나 조건 불일치는 기존 중복 정책에 따라 같은 관측 세션에서
  다시 제안하지 않는다.
- 후속 사용자 승인 ON 시험: 실제 Pi 4 활성 세션 합류 뒤 ON 제안의 별도 승인 경로를 실행했다.
  로컬 정책 결과에서 `operation=set_power`, `value=on`, `api_accepted=true`, `state_match=true`,
  `state_confirmed=true`, `actual_value=on`, `transport_performed=true`를 확인했다.
  본체 켜짐은 사용자가 직접 확인했고 `physical_evidence=user_attestation`으로 구분한다.
  자동 제어는 false이며 정책 OFF/AUTO 실기는 수행하지 않았다. 원본 응답·인증정보는 저장하지 않았다.

## Live HTML 확인

주소 `http://127.0.0.1:19391/`. 최신 화면은 V1.1 카드를 센서 iframe 위에 배치한다.
ThinQ 팝업의 `V1.1 제안 카드 보기`로도 이동할 수 있다.

공기청정기 OFF를 최초 상태로 조회한 뒤 전원/풍량을 변경하지 않는다.
카드의 `기존 활성 세션 합류`, 새로 측정하는 `재실 N/30초`, 최신 GET을 확인한다.
ON 제안이 보이면 30초 안에 수락하여 개별 승인창까지만 검증한다.
수락만으로 POST하지 않으며 실제 변경 승인은 별도 실기 승인 전까지 누르지 않는다.
GET은 API 수신 후 2분이 지나면 stale이므로 오래된 값을 현재 상태로 취급하지 않는다.
단순 재조회로 만료된 제안을 다시 실행 가능하게 만들지는 않는다.

## Draft PR 게시 전 전체 회귀

- Hub 전체: **835 passed, 3 skipped, 1 xfailed**.
- Gateway·정책·PC devview: **233 passed**.
- Node/HTML: **31 passed**. Python 구문 검사와 `git diff --check` 통과.
- 두 Python 영역을 한 프로세스로 수집하면 같은 이름의 `tools.thinq_connect`가
  저장소 버전과 독립 vendored 버전 사이에서 충돌하여 10개 수집 오류가 발생한다.
  이 제한은 미해결이며, 아래처럼 독립 프로세스로 각 영역 전체를 검증했다.
  테스트 삭제·skip 추가·assertion 변경으로 통과시키지 않았다.
- 의존성 deprecation 경고와 기존 skip/xfail은 실제 보드 검증으로 대체하지 않는다.
  게시 준비 과정에서는 Cloud/가전/운영 MQTT/보드에 접근하지 않았다.

검증된 Python 3.12·기존 의존성 환경에서 worktree 루트를 기준으로 실행한다.

```bash
PY="$HOME/.local/share/deskmate/thinq-gateway-venv/bin/python"
PYTHONPATH="$PWD/hub:$PWD/hub/tests" PYTHONDONTWRITEBYTECODE=1 \
  "$PY" -m pytest -q -p no:cacheprovider hub/tests
PYTHONPATH="$PWD/hub:$PWD/hub/tests:$PWD/tools/laptop-control" PYTHONDONTWRITEBYTECODE=1 \
  "$PY" -m pytest -q -p no:cacheprovider tools/laptop-control hub/atlas-tof/pc/devview/test_devview.py
node --test tools/laptop-control/server.test.mjs tools/laptop-control/thinq-ui.test.mjs
git diff --check
```
