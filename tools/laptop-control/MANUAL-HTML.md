# LOW/AUTO 수동 제어 HTML

검증된 CLI와 같은 `Session`·`HostSession`·`GuardedSdk`·`Transport`를 사용한다.
전용 모드 `live-manual`만 추가하며 18891 Live Read-only, 18991 Fixture와 별도로 실행한다.
Pi 4·Pi 5 서비스와 기존 CLI를 변경하지 않는다. 실제 HTML 제어 시험은 별도 승인 후 진행한다.

## 실행

기능 worktree 루트에서 사용자 소유의 신뢰된 WSL 터미널로 실행한다.

```bash
cd "$HOME/dev/ESWContest_LG/2026ESWContest_smart_TEAMMATE-thinq-laptop-gateway"
PY="$HOME/.local/share/deskmate/thinq-gateway-venv/bin/python"
"$PY" tools/laptop-control/thinq_host.py --live-manual --port 19092
```

- 기존 고정 Client ID 파일을 읽기만 하며, 없거나 권한·형식이 잘못되면 중단한다.
- 조회·제어 권한 PAT는 숨김 입력한다. HTML·Node·인자·환경변수·파일에 PAT를 넣지 않는다.
- Python은 `127.0.0.1:19092`에만 bind하고 세션은 20분이다. 시작만으로 Cloud를 호출하지 않는다.
- CLI와 HTML에서 같은 기기에 동시에 제어 시험을 하지 않는다.

Node가 아직 실행되지 않은 경우에만 별도 터미널에서:

```bash
cd "$HOME/dev/ESWContest_LG/2026ESWContest_smart_TEAMMATE-thinq-laptop-gateway"
node tools/laptop-control/server.mjs --mode live-manual --port 19091 \
  --thinq-port 19092 --sensor-port 18890 --pi4 127.0.0.2
```

주소: `http://127.0.0.1:19091/`. 센서 포트·주소는 기존 read-only devview와 SSH 터널을
재사용하는 예다. 없어도 ThinQ 시험은 독립적으로 가능하며 `--pi4`를 생략할 수 있다.
Node는 Pi 5 SSH·캡처·터치 프로세스를 만들지 않는다.
Fixture 또는 Read-only backend는 이 수동 화면의 인증 프로세스로 사용할 수 없다.
포트 충돌 시 다른 서버를 종료하지 말고 실행 상태부터 확인한다.

## LOW와 AUTO 개별 시험

1. Live Manual 표시를 확인하고 상단 `ThinQ 연결 상태` 팝업을 연다.
2. `인증 세션 연결` 후 `현재 상태 읽기`를 직접 누른다. 연결·메모리 snapshot 갱신은 Cloud GET이 아니다.
3. 대상 한 대, Profile LOW/AUTO 읽기·쓰기 지원과 POWER_ON/CLEAN/AUTO를 확인한다.
   GET 성공은 PAT 쓰기 권한·물리 온라인 상태를 입증하지 않으며 `connection=unknown`도 유지한다.
4. 별도 LOW 승인 후 `약풍으로 변경`을 누른다. 이 단계는 새 Profile/State GET과 승인 후보 생성이며 POST가 아니다.
5. 본체 온라인·외부 조작 없음 두 조건을 직접 확인해 체크하고, 30초 안에 `변경 승인`을 누른다.
6. POST는 LOW 한 번만 허용한다. API 접수와 별도 GET readback을 각각 결과 목록에서 확인한다.
7. 실제 본체 표시·풍량 변화가 확인된 경우에만 `실제 기기의 변경을 확인했어요`를 누른다.
8. 별도 AUTO 복원 승인 후 같은 절차로 진행한다. LOW 물리 확인이 없으면 AUTO 준비가 차단된다.

POST 응답·readback이 불확실하거나 timeout이면 제어 잠금을 유지한다. GET·화면 새로고침으로
서버의 실패 상태를 풀지 않는다. 중복 nonce와 이미 시도한 LOW/AUTO는 재전송하지 않는다.
종료·만료·브라우저 닫기는 이미 실행된 제어를 취소하지 않으며 자동 복원도 하지 않는다.
실패 후 새 세션으로 같은 명령을 반복하지 말고 먼저 실제 상태를 확인한다.

## V1.1 경계와 보존

- V1.1 카드·명령 관찰은 계속 shadow/Mock 전용이다. 후보 수신·페이지 polling으로 POST하지 않는다.
- `prepareWind(value)`와 기존 prepare/execute 경로를 사용자 수락 흐름에서 재사용할 수 있다.
  현재는 LOW/AUTO 수동 버튼만 연결했다. 독립적인 AUTO 정책 제안은 현재의 ‘동일 세션 LOW 확인 후 복원’
  제한과 다른 요구이므로, 그 연결은 별도 승인·안전 검토 전까지 활성화하지 않는다.
- 전원 ON/OFF·HIGH·자동 전원·밴딧·FSM 제어 에피소드는 연결하지 않는다.
- 기존 18891은 양쪽 서버에서 POST를 차단한다. 기존 18991은 Fixture 데이터·Mock 동작을 유지한다.
- 오프라인 시험의 synthetic SDK가 반환한 성공은 실제 Cloud·HTML 물리 제어 성공이 아니다.

종료는 사용자 본인이 실행한 19092 인증 터미널과 19091 Node만 Ctrl+C로 종료한다.
18891/18991 및 보드 서비스를 종료하지 않는다. 이 경로는 IPK 배포나 보드 보안 정책 변경이 필요 없다.

호스트 검증: Python 49 passed, Node/HTML 21 passed. synthetic SDK를 사용한 실제 localhost
Node→Python HTTP 시험에서 두 조건·nonce·중복·LOW/AUTO 순서·readback·물리 확인 분리와
응답 유실 후 GET으로 제어 잠금이 해제되지 않음을 확인했다.

후속 실기 결과: 2026-11-05 사용자가 별도 수동 HTML에서 실제 LOW와 AUTO 제어 모두
동작했음을 확인했다. CLI는 제공된 기록에서 API 접수·GET 일치·사용자 물리 확인까지 확인했고,
HTML은 사용자의 두 동작 성공 확인에 근거한다. HTML의 원본 API 응답·기기 식별자·민감 로그는
수집하지 않았다. 이 결과를 V1.1 정책의 실제 제어나 Pi 4 종단 제어 성공으로 해석하지 않는다.
