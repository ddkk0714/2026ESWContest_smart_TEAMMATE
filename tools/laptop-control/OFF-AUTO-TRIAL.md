# V1.1 OFF/AUTO 실기 준비

기준: PR #111 / `39bde3d`. 기존 ON 제안·수동 제어 코드는 그대로 재사용한다.
처음에는 OFF 안전성 회귀 테스트와 노트북 시연 전용 20초 모드를 추가했다.
이후 사용자가 임의의 10초 대신 최신 DESKMATE 빠른 시연 기준으로 통일하도록 요청해
현재는 명시적으로 선택한 **ThinQ 시연 모드에서 Hub 빠른 시연과 같은 5초 기준**을 사용한다.
Hub의 fast/normal 모드 자체는 참고 정보이며 제안 조건이 아니다.
Pi 4/5 운영 서비스와 FSM의 600초 임계값은 변경하지 않는다.

## 최신 기준: Hub 빠른 시연과 동일한 5초

- Pi 5 설치 메타데이터에서 DESKMATE 앱 `0.55.0`을 읽기 전용으로 확인했다.
  V54 UI(PR #113)와 주변기기 부재 타이머는 별개다.
- 실제 시간의 근거는 [Hub PR #114](https://github.com/ddkk0714/2026ESWContest_smart_TEAMMATE/pull/114)
  `808b4a9f936ff4b32dec45740d97bd26e619a42a`의
  [fsm.yaml](https://github.com/ddkk0714/2026ESWContest_smart_TEAMMATE/blob/808b4a9f936ff4b32dec45740d97bd26e619a42a/hub/deskmate_hub/config/fsm.yaml#L60)
  `demo_fast.absent_off_after_sec=5` 및 `live.py::_absent_off`의 fast 분기다.
  정상 모드의 프로파일 `control.absent_off.after_sec` 및 FSM의 IDLE 타이머와 구분한다.
- 이 Hub PR은 아직 미병합이다. 코드 전체를 병합·복원하지 않고 필요한 기준만 대조했다.
- 실제 최신 phase에서 `sensor_summary.demo_mode={speed:normal, enabled:true}`를 확인했다.
  ThinQ는 Hub 모드를 바꾸지 않는다. `--demo-absence`로 선택한 로컬 시연은
  Hub가 normal이거나 모드 메타데이터가 없어도 동일한 5초를 사용한다.
- 현재 Hub 메시지는 모드와 판정 주기를 제공하지만 부재 끄기 임계값 자체는 제공하지 않는다.
  따라서 검토한 Hub 코드의 5초를 로컬 YAML에 맞췄다. 향후 Hub 임계값 변경 시 재대조해야 하며,
  임의의 원격 설정값을 자동으로 조회·동기화한 기능으로 해석하지 않는다.

시연 전용 화면은 **`http://127.0.0.1:19691/`**(인증 19692)이다. 기존 19591은 그대로 둔다.
시연 인증 프로세스에 **`--demo-absence`**를 명시하면
YAML의 `demo_absence_off_sec=5`를 사용한다. Hub fast 전환은 요구하지 않는다.
옵션이 없으면 기존 FSM 부재 10분→IDLE 경로다.
시연은 실제 Hub의 검증된 재실 값을 사용하며 IDLE이나 센서값을 만들어 넣지 않는다.
먼저 새 재실 true·실제 활성 세션을 관측한 뒤 새 부재 메시지들로 5초 이상을 확인한다.
판정·MQTT 주기와 노트북 polling으로 제안 표시는 한 주기 정도 더 늦어질 수 있다.
주변기기 명령 발행, ThinQ 제안 생성, 사용자 승인과 실제 ThinQ OFF 완료가 동시에 일어난다고 보장하지 않는다.
멈춘 false 메시지를 반복 조회하는 것만으로 시간이 차지는 않는다.

```bash
~/.local/share/deskmate/thinq-gateway-venv/bin/python \
  tools/laptop-control/thinq_host.py --live-manual --power-control --policy-link \
  --demo-absence --session-minutes 40 --port 19692
# 별도 터미널, 같은 worktree에서 실행
node tools/laptop-control/server.mjs --mode live-manual --power-control true --policy-link true \
  --session-minutes 40 --port 19691 --thinq-port 19692 --sensor-port 18890 --pi4 127.0.0.2
```

이 명령도 **실제 POST 차단**이다. 실제 OFF는 별도 시험 승인과 기존 개별 변경 승인이 필요하다.
화면에 `OFF 판단용 부재 N/5초 · Hub 빠른 시연 켜짐/꺼짐/미확인(참고)`와 실제 FSM 상태를 함께 표시한다.
운영과 달리 이 OFF 후보는 FSM이 아직 FOCUS 상태여도 생길 수 있다.
시연 OFF는 세션 종료나 가짜 IDLE을 만들지 않으며 자동 ON/복원으로 이어지지 않는다.
연결 끊김·stale·부팅 변경 후에는 재실 true를 새로 확인해야 다시 시연을 시작할 수 있다.
Hub 모드 전환 자체는 이 명시적 로컬 시연의 타이머나 제안을 초기화하지 않는다.
Hub normal 상태에서도 기존 ON/AUTO 제안은 바꾸지 않는다.

향후 자동 OFF는 별도의 사전 동의 기능으로 검토한다. 동의한 시간·기기·세션 범위에서만
실행하고, 복귀·데이터 끊김·수동 조작·세션 종료 시 취소하며 POST는 한 번만 보낸다.
현재 자동 OFF/ON은 구현·활성화하지 않았다. 사용자의 부재로 물리 확인을 못한 경우
GET 일치와 본체 확인 미실시를 구분해야 한다.

### 실제 부재 표시와 OFF 판단용 카운터

카드에 **실제 Hub 재실 입력 / 수신 후 부재 경과**를 별도로 표시한다.
이 표시와 정책의 OFF 자격 카운터는 별개다. 정책은 새 재실 기준선·활성 세션을 추가로 요구한다.
이 표시는 fast/normal·ThinQ 인증 여부와 무관하고, 신선한 MQTT 표본의 present만 사용한다.
별도 SDK/Cloud 요청은 하지 않는다. 재실 복귀·연결 끊김·stale·Hub 부팅 변경 시 구간을 초기화하며,
재연결 전 부재 시간은 복원하지 않는다. 신선도 한계는 기존 YAML `hub_max_age_sec`를 사용한다.
정책의 OFF 판단 카운터·5초 기준·수락·개별 승인·기본 POST 차단은 그대로다.
표시만 수정할 때는 브라우저 새로고침으로 적용된다. 정책 코드 변경은 노트북 인증 서버를
재시작해야 하며 PAT를 숨김 재입력한다. 서비스 시작은 Cloud 요청을 실행하지 않는다.

최신 관련 회귀는 Python 337 passed, Node/HTML 34 passed이다.
Hub normal·메타데이터 누락 상태의 명시적 로컬 시연, 모드 변경 시 타이머 유지,
미승인 POST 차단 및 ON/AUTO 보존을 포함한다.
실제 5초 부재·ThinQ OFF POST는 미실시이며,
이번 작업에서 Cloud 요청·보드 변경·Commit/Push/Merge는 하지 않았다.

## 운영 OFF 조건과 복귀 처리 (시연 옵션 미사용)

- 로컬 FSM 기본 설정의 `absent_idle_sec=600`을 사용한다. 노트북에 별도 부재 타이머를 만들지 않는다.
- 실제 활성 세션을 먼저 관측한 뒤 `present=false`, FSM `IDLE`, `absent_timeout` 경계를 확인해야 한다.
  처음부터 IDLE인 입력, 경계를 놓친 재연결이나 retained 메시지를 보고 OFF 이벤트를 재구성하지 않는다.
- 실제 기기 전원이 ON이고 ThinQ GET이 유효·최신이어야 한다. `connection=unknown`은 계속 unknown이다.
- 제안과 개별 승인은 각각 기존 30초 제한을 유지한다. 복귀하여 `present=true`가 되면
  수락 전·수락 후 모두 OFF를 차단한다. 이미 만료한 제안은 GET/재연결로 다시 살리지 않는다.
- 따라서 자리로 돌아온 뒤 예전 OFF 제안을 승인하는 시험의 기대 결과는 **차단**이다.
  실제 OFF 성공 시험은 노트북을 센서 감지 범위 밖에서 조작하거나 다른 사람이 본체 확인을 돕는다.
- 수동 변경 우선 1800초, 연결 끊김·stale·기기 상태 변경·실패 시 차단을 유지한다.
  기다림을 줄이려고 FSM 전환, 재실값, 시각이나 TTL을 조작하지 않는다.

## 준비 화면 (Cloud/POST 자동 실행 없음)

이번 OFF 관찰용 HTML은 `http://127.0.0.1:19591/`이다. 인증 백엔드는 19592이며
기존 18891/19091/19291/19391/19491과 구분한다. 노트북 Node는 40분 옵션을 사용하고
실센서 devview 18890을 읽는다. 실제 Pi 4 경로가 끊겼으면 미연결로 표시하며 Fixture로 대체하지 않는다.

실센서 SSH 경로를 먼저 정상화하고 사용자가 명시적으로 GET을 시험할 때, 신뢰된 WSL 터미널에서:

```bash
cd "$HOME/dev/ESWContest_LG/2026ESWContest_smart_TEAMMATE-thinq-manual-power"
~/.local/share/deskmate/thinq-gateway-venv/bin/python \
  tools/laptop-control/thinq_host.py --live-manual --power-control --policy-link \
  --session-minutes 40 --port 19592
```

PAT는 숨김 입력하고 저장하지 않는다. 이 명령은 **정책 POST 차단** 모드다.
서버 시작·인증 연결만으로 Cloud를 호출하지 않는다.

1. 공기청정기 ON을 본체에서 확인한다. 외부 앱·예약·리모컨 조작을 중지한다.
2. 인증 세션 연결 → 현재 상태 읽기를 직접 눌러 ON을 확인한다.
3. V1.1 카드에서 실제 Pi 4 입력의 재실 true·활성 FSM·시퀀스 갱신을 먼저 확인한다.
4. 센서 감지 범위를 벗어나 `present=false`가 연속 유지되게 한다. 실제 FSM이
   부재 10분 후 IDLE로 전환하는지 관찰한다. 노트북 화면의 시간만으로 판정하지 않는다.
5. 최초 GET은 10분 전에 stale이 된다. IDLE 이후에도 부재를 유지한 채 **현재 상태 읽기**를
   직접 눌러 최신 ON을 확인한다. 자동 Cloud polling은 사용하지 않는다.
6. OFF 제안의 사유 `fsm_absent_timeout`을 확인하고, 30초 이내 수락하면 별도 승인창만 열린다.
   이 준비 세션에서는 실제 변경 승인/POST가 차단되어 있다.
7. 복귀·만료·stale로 차단되면 멈춘다. 조건을 완화하거나 제안을 재생하지 않는다.

실제 OFF POST는 사용자가 별도로 승인한 뒤에만 새로운 OFF 전용 시험을 준비한다.
그때 인증 명령에 `--allow-policy-post --policy-stage POWER_OFF`를 추가하여 다른 동작을 차단하고,
실제 유효 제안 수락 → 별도 상태 확인·변경 승인 → POST 1회 → GET readback → 사용자 본체 확인 순으로 한다.
이 문서는 해당 POST를 지금 실행하거나 만료한 제안을 재사용하라는 지시가 아니다.
실제 OFF 시험 승인은 부재 시험을 시작하기 전에 받는다. 새 인증 세션은 실제 활성 FSM·ON 상태를
먼저 확인한 뒤 새 부재→IDLE 경계를 관측해야 한다. 이미 IDLE인 상태에서 인증을 다시 시작해
과거 OFF 경계를 복원하거나, 관찰 시험에서 소비·만료한 제안을 재사용할 수 없다.

## AUTO는 이후 별도 시험

- 실제 최신 GET에서 전원 ON·청정 상태·풍량 LOW 및 `totalPollutionLevel=BAD`가 확인되어야 한다.
- 서로 다른 API 수신 보고가 최소 2회, 60초 이상 지속하고 보고 간격이 120초 이내여야 한다.
  수동 GET만 사용하며 반복 GET을 독립적인 센서 측정이라고 표현하지 않는다.
- unknown/stale/결측/미확인 등급·이미 AUTO이면 제안하지 않는다. CO₂로 BAD를 대신하지 않는다.
- 실제 공기질이 BAD가 아니라면 실기 시험을 보류한다. Fixture BAD를 Live에 주입하지 않는다.
- LOW로 바꾸는 동작도 별도 실제 제어 승인 대상이다. 사전 수동 변경 우선권을 지우지 않는다.
- AUTO 제안 역시 별도 수락·개별 승인·POST·readback·본체 확인을 거친다. 자동 복원은 없다.

## 회귀 테스트

추가한 OFF 테스트는 합성 SDK·가상 시간만 사용한다. 부재 600초 경계, POST 기본 차단,
별도 허용 시 일회 OFF, 수락 전후 복귀, 제안 만료 후 재사용 금지, 연결 끊김·stale·승인 만료를 검사한다.
실제 Pi 4 조건과 물리 OFF/AUTO 성공은 별도 실기 결과로 기록한다.

종료는 이번에 실행한 노트북 프로세스만 Ctrl+C로 한다. 토큰 폐기·프로세스 종료는
이미 전송한 기기 명령을 취소하거나 전원을 자동 복원하지 않는다.
