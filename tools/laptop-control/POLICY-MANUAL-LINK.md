# V1.1 제안 → 별도 수동 승인

## 최신 검증 구분 (2026-11-05)

- 실제 HTML의 LOW/AUTO·OFF/ON 수동 제어: 사용자 본체 확인 성공.
- 실제 Pi 4 활성 FSM 세션 합류 → 새 연속 재실 30초 → ON 제안 → 수락 →
  별도 변경 승인 → POST → GET 일치 → 사용자 본체 확인: 성공.
- OFF/AUTO 정책 제안의 실데이터 종단은 미검증이며 Fixture/Mock 결과만 있다.
- 자동 전원 제어는 비활성이다. 이번 시험과 PR 게시 준비에서 추가 Cloud 요청은 하지 않는다.
- 이후 절의 테스트 개수와 실기 미실시 표기는 각 개발 시점의 기록이다.
  최신 회귀 검증은 [SESSION-ATTACH.md](SESSION-ATTACH.md)를 참조한다.

기존 `feat/thinq-manual-power` worktree에서 진행한다. 기본 모드는 기존 동작을 유지하며
명시적인 `--policy-link` 프로세스만 새로운 연결을 사용한다.
18891 GET·19091 LOW/AUTO·19291 전원 수동 성공 경로와 운영 Pi 4/5는 변경하지 않는다.

## 경계

- 기존 AirPurifierPolicy와 YAML을 재사용한다. START 후 재실 30초 ON, 기존 FSM의
  600초 부재 timeout → IDLE OFF, BAD API 응답 지속 + LOW → AUTO 후보다.
  별도 재실 감지기·FSM 에피소드·밴딧·자동 전원 제어는 추가하지 않는다.
- Live에서 `connection=unknown`을 online으로 바꾸지 않는다. 새 수동 검토 모드에서만
  unknown에 대한 후보 표시를 허용하며, 실행 전 본체 온라인/외부 조작 없음 확인은 필수다.
  offline·결측·stale·미확인 등급에서 제어하지 않는다.
- 수락은 GET 상태 재검사와 기존 Session의 30초 일회 승인창만 준비한다. POST하지 않는다.
  최신 Pi 4 입력은 Node가 기존 읽기 전용 devview에서 다시 조회한다. HTML이 재실 입력을
  명령 인자로 위조할 수 없다. GET 대기 시간도 Hub 데이터 나이에 포함한다.
- 개별 승인 직전 Profile/State·대상·제안 조건·Hub 최신성·nonce·세션 수명을 재검사한다.
  API 접수·별도 GET readback·사용자 본체 확인은 각각 표시한다.
- AUTO 후보만 LOW→AUTO 독립 전환을 허용한다. 이 예외는 내부 정책 승인과 명시적인
  전송 경계 옵션이 모두 있어야 하며, 기존 19091/CLI의 LOW 후 AUTO 복원 계약은 유지한다.
- 부재 OFF는 관찰·수동 검토다. 응답 없이 30초 후 만료하면 건너뛰며 자동 실행/재시도하지 않는다.
  자동 OFF의 사전 동의 정책은 후속 작업이다.
- Live 제안 POST는 기본 차단한다. 별도 실기 승인 후 인증 프로세스에
  `--allow-policy-post`를 명시한 경우에도 사용자의 개별 변경 승인이 필요하다.
  이 옵션 없이 위조된 execute 요청이 들어와도 추가 GET/POST 없이 차단한다.
- 실패·timeout·readback 불일치 시 잠금을 유지한다. 자동 재전송/자동 복원은 없다.

## 지금 확인할 Fixture 화면

주소: `http://127.0.0.1:19491/`. 실제 PAT·Cloud·가전·보드 사용 없음.

1. ThinQ 팝업에서 인증 세션 연결 → 현재 상태 읽기 → 팝업 닫기.
2. V1.1 카드에서 재실 ON / 부재 IDLE OFF / BAD 지속 AUTO 시나리오 선택.
   이 버튼들은 합성 시간축이다. 실제 Pi 4 재실·부재를 검증하는 버튼이 아니다.
3. 거절하면 전송 없음. 수락하면 별도 변경 승인창만 열린다.
4. 두 조건 체크 → 변경 승인 → Fixture GET 일치 → Fixture 확인.
   결과는 실제 본체 동작의 증거가 아니다.

새 worktree 루트의 WSL 터미널 두 개에서 재실행할 수 있다.
기존 포트의 프로세스를 임의 종료하지 말고 이번에 시작한 Fixture만 대상으로 한다.

```bash
cd "$HOME/dev/ESWContest_LG/2026ESWContest_smart_TEAMMATE-thinq-manual-power"
~/.local/share/deskmate/thinq-gateway-venv/bin/python \
  tools/laptop-control/thinq_host.py --fixture --allow-control --power-control --policy-link --port 19492
```

```bash
cd "$HOME/dev/ESWContest_LG/2026ESWContest_smart_TEAMMATE-thinq-manual-power"
node tools/laptop-control/server.mjs --mode fixture --power-control true --policy-link true \
  --port 19491 --thinq-port 19492 --sensor-port 18990
```

18990은 기존 가짜 센서 iframe이다. 없어도 ThinQ Fixture 자체는 독립적으로 동작한다.
세션 20분, 각 ON/OFF/AUTO는 최대 한 번 실행한다. 다시 시험하려면 본인이 시작한
19492·19491만 Ctrl+C로 종료 후 새 세션으로 실행한다. 원본 서버를 종료하지 않는다.

## Live 기본 차단 실행

```bash
# 사용자 WSL 터미널: 기존 고정 Client ID 재사용, 제어용 PAT 숨김 입력
~/.local/share/deskmate/thinq-gateway-venv/bin/python \
  tools/laptop-control/thinq_host.py --live-manual --power-control --policy-link --session-minutes 40 --port 19392
# 별도 터미널: 기존 실제 센서 devview 18890 재사용
node tools/laptop-control/server.mjs --mode live-manual --power-control true --policy-link true \
  --session-minutes 40 --port 19391 --thinq-port 19392 --sensor-port 18890 --pi4 127.0.0.2
```

`http://127.0.0.1:19391/`에서 명시적인 GET 이후 실제 Pi 4 재실/FSM 입력으로 후보를
관찰한다. 중간 연결은 실제 Hub의 `source=fsm`과 일치하는 FSM/phase를 확인해 기존 활성
세션에 합류한다. 합류 후 새로 관측한 연속 재실 30초가 필요하며 START를 가짜로 만들지 않는다.
센서 입력이 끊기거나 오래되면 차단한다. 화면 polling은 GET을 자동 재실행하지 않는다.
기존 실제 MQTT 경로·인증/ACL·보드 시간차 문제를 해결했다고 주장하지 않는다.

이 실행 명령은 제안 POST 차단 모드다. 실제 제안 POST는 사용자 별도 승인과 현재 상태
재확인 후에만 허용한다. 실제 ON 제안 종단은 위 최신 검증 기록과 같이 확인했다.
종료는 새 인증 프로세스/Node만 Ctrl+C. PAT는 파일·환경변수·로그·HTML·MQTT에 저장하지 않는다.

ON 한 동작만 별도 승인한 시험은 인증 프로세스에 다음 제한을 추가한다.
기존 인증 세션이 실행 중이라면 해당 시험 프로세스만 직접 종료하고 새로 입력한다.

```bash
~/.local/share/deskmate/thinq-gateway-venv/bin/python \
  tools/laptop-control/thinq_host.py --live-manual --power-control --policy-link \
  --allow-policy-post --policy-stage POWER_ON --session-minutes 40 --port 19392
```

`--policy-stage POWER_ON`은 수락 처리와 POST 직전 모두 OFF/AUTO를 차단한다.
이 옵션도 자동 제어를 허용하지 않는다. 사용자가 제안 ID에 수락하고 30초 이내에
상태 확인과 별도 변경 승인을 마쳐야 한 번만 POST한다. ON 시 AUTO를 함께 쓰지 않는다.
기존 관측 세션에서 만료/거절된 제안을 되살리기 위해 재시작하지 않는다. 이번 실기는
사용자가 새 ON 시험을 명시적으로 승인하여 새로운 노트북 시험 세션으로 진행했다.

## 검증 기록 구분

호스트의 합성 SDK·localhost Node/Python·실제 Chrome Fixture에서 ON/OFF/AUTO 제안,
수락만으로 전송 없음, 거절, 두 조건 개별 승인, 중복·만료·Hub stale·상태 변경 차단,
readback 및 Fixture 확인 표시를 검증한다. 실제 Live GET/POST·보드 변경은 하지 않았다.

이번 결과: 관련 Python 회귀 175 passed, Node/HTML 26 passed, `git diff --check` 통과.
실제 Chrome에서 거절 및 ON/OFF/AUTO 수락 → 개별 승인 → Fixture readback까지 통과했다.

## Live 시험 세션 40분

위 Live 19391/19392 실행 명령에는 **각각 `--session-minutes 40`**이 반영돼 있다.
기존 18891/19091/19291/19491 프로세스는 재시작하거나 세션을 연장하지 않는다.
Python 인증 수명과 Node의 허용 수명을 모두 2400초로 맞추며, 서로 다르면 인증 연결을 거부한다.
인증 시작 40분 후에는 요청을 차단한다. 개별 제안·변경 승인은 계속 30초이며 연장하지 않는다.
`--allow-policy-post`가 없는 기본 실행은 Live 제안 POST를 계속 차단한다.

40분 후속 검증: 관련 Python 177 passed, Node/HTML 27 passed. 실제 Chrome에서
Live 모드·40분 안내·Fixture 숨김·인증 전 제어 차단·기존 실제 센서 프록시 조회를 확인했다.
실제 Cloud/가전 요청은 실행하지 않았다. Pi 4 입력이 stale이면 실기 제안 실행은 차단한다.

세션 합류 후속 검증과 제한은 [SESSION-ATTACH.md](SESSION-ATTACH.md)를 참조한다.
