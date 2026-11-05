# DESKMATE 단일 통합 콘솔

사용 주소는 **http://127.0.0.1:19891/** 하나다. 이전 실행 중이던 19591/19691은
GET 화면 이동만 안내하고 기존 제어 API는 410으로 거절한다. 소스·기존 검증 기록은 삭제하지 않는다.

## 연결 구성

- HTML/Node 19891 → ThinQ 인증 프로세스 19892 (동일 통합 모드)
- HTML → 실제 Windows collector 상태/키보드 리스너 ON/OFF
- HTML → 읽기 전용 센서 뷰어 18890 → 기존 검증된 SSH 중계 → Pi 4
- 실제 센서가 끊기면 미연결/stale로 표시한다. Fixture를 실제 데이터로 대신하지 않는다.
- Fixture 소스/시험은 유지하지만 이 실측 콘솔에 Fixture 응답을 섞지 않는다.

## 실행 (해당 기능 worktree의 WSL)

```bash
node tools/laptop-control/server.mjs --mode live-manual --power-control true \
  --policy-link true --combined-manual true --session-minutes 40 \
  --port 19891 --thinq-port 19892 --sensor-port 18890 --pi4 127.0.0.2
```

별도의 신뢰할 수 있는 터미널에서 기존 ThinQ SDK 가상환경의 Python으로:

```bash
python tools/laptop-control/thinq_host.py --live-manual --power-control \
  --policy-link --combined-manual --allow-policy-post --demo-absence --auto-off \
  --session-minutes 40 --port 19892
```

이 명령은 검증된 기존 환경에서만 사용한다. 서버/수집기를 중복 실행하지 않는다.
PAT는 이 터미널에서 숨김 입력한다. 고정 Client ID는 기존 사용자 전용 파일에서 읽고
새로 생성하거나 출력하지 않는다. HTML에서 인증 연결 → 현재 상태 읽기를 직접 누른다.
프로세스 시작·인증 연결 자체는 Cloud 조회/제어를 하지 않는다.

## 함께 제공하는 기능과 안전 조건

- ThinQ 수동 GET, LOW/AUTO, 전원 OFF/ON: 기존 Session/GuardedSdk를 사용한다.
- 수동 버튼과 V1.1 제안 수락은 각각 새 Profile/State 확인·일회 nonce·개별 변경 승인을 거친다.
- API 접수, GET readback, 사용자의 본체 확인은 별도다. 실패/timeout 시 재전송·복원하지 않는다.
- 통합 모드는 명시적으로 켜야 한다. 기존 정책 전용 모드의 직접 수동 호출 차단은 유지한다.
- `--policy-stage` 제한 시험과 구분한다. 통합 모드에서 자동 제어는 여전히 OFF에만 한정한다.
- 자동 OFF는 별도 본체 확인·사전 동의 버튼을 눌러야 시작한다. 기본 비활성이다.
  현재 시연 기준은 실제 연속 부재 5초와 새 MQTT 표본이며, 자동 ON/복원은 없다.
- 수동 제어 준비는 자동 OFF 사전 동의를 취소한다. 이미 전송된 명령은 취소할 수 없다.
- 인증은 `--session-minutes` 20/40/60분(기본 60), 실행 challenge는 30초다. 종료/재시작 시 PAT를 다시 입력한다.
- 통합 모드의 자동 실행은 아래 **시연 자동 모드**로 바뀌었다(11-06). 위 두 줄의 1회 OFF 규칙은 `--policy-stage` 시험에만 남는다.
- 키보드 수집 ON/OFF와 마우스 유지는 별도다. 현재 `--local-only` 시험 수집기는
  캡처만 검증하고 MQTT를 발행하지 않는다. 상단에 이 모드를 표시한다.

## 반복 조작과 결과 불확실 (feat/thinq-repeat-control, 11-05)

통합 모드(`--combined-manual`)와 정책 연결(`--policy-link`)에서는 같은 값을 다시 보내는 것이
**새 사용자 의도**다. 의도마다 새 nonce·개별 승인·POST 최대 1회이고, `--policy-stage` 제한 시험과
CLI 수동 시험은 예전처럼 값별 1회·본체 확인 관문을 유지한다.

- 한 인증 세션에서 ON → OFF → ON, LOW ↔ AUTO 반복 가능. POST 사이 최소 5초.
- 본체 확인은 증거 기록이다. 다음 조작의 관문이 아니다(직전 새 Profile/State 기준선 검사가 그 역할).
- POST 뒤 상태가 바로 안 맞으면 **GET만** 최대 3번 더 읽는다(1.5·1.5·2초). POST는 다시 보내지 않는다.
- 그래도 불확실하면 새 조작을 잠근다. 본체를 확인한 뒤 화면의 **상태 다시 확인**을 누르면
  새 GET 한 번으로 읽은 상태를 현재 상태로 기록하고 잠금을 푼다. 불확실했던 명령은 다시 보내지 않는다.
- 막힌 이유는 정해진 목록(`session.py` `PUBLIC_REASONS`)의 `reason`으로 화면에 보인다.
- V1.1 정책은 자기 명령이 GET으로 확인되면 그 변화를 수동 조작으로 세지 않는다(예전: 30분 보류).
  정책이 끈 OFF가 확인되면 한 자리 비움이 끝난 것으로 보고, 다음에 앉는 사람에게 ON을 다시 제안한다.

## 시연 자동 모드 (11-06)

통합 모드에서 **한 번 동의하면 인증 세션(최대 60분) 동안 V1.1 제안을 묻지 않고 실행**한다.
제안 하나는 콘솔 버튼과 똑같은 길(prepare → POST 1회 → GET 확인)로 간다.

- 자리 비움 5초 → 전원 OFF · 다시 앉아 30초 → 전원 ON · LOW에서 공기질 나쁨 지속 → AUTO.
- 동의는 자리 복귀·FSM 세션 경계(자리 비움 → IDLE)·뷰어 재연결·수동 버튼에도 유지된다.
  **자동 모드 끄기**, 인증 만료, 기기 변경 때만 풀린다.
- OFF는 POST 직전 실제 부재를, ON은 실제 재실을 다시 읽어 확인한다. 재전송·복원 없음.
  결과 불확실이면 기존처럼 잠그고 기다린다(동의는 유지).
- 스냅숏이 오래되면 자동 모드가 GET을 직접 한 번 읽는다(20초 간격 이상).
- 실기(11-06 01:25, Pi 4 hub demo-b 빠른 시연 + 실제 공기청정기): 자리 비움 9초에 OFF
  `state_match`, 복귀 30초 뒤 ON `state_match`, 자동 실행 2회. 본체 동작은 사용자가 눈으로 확인.

## 현재 검증 범위

통합 모드의 수동 네 단계·승인/중복/기존 모드 차단은 Fake SDK 회귀로 검증했다.
이전의 실제 GET/CLI·HTML 수동 제어/ON 제안 성공과 이번 통합 모드의 실기 성공을 혼동하지 않는다.
현재 보드 분리로 Pi 4 수신·정책 종단·자동 OFF 물리 시험은 미실시다.
보드 프로그램·계정·D-Bus/AppArmor·운영 MQTT·자동 시작 설정은 바꾸지 않았다.

## 종료와 복구

새로 실행한 Node·ThinQ 인증 터미널만 Ctrl+C로 종료한다. 사용자 선택의 collector와
센서 중계는 따로 관리하며 임의 재시작하지 않는다. PAT 종료는 이미 보낸 명령을 취소하지 않는다.
인증 입력창을 잘못 닫으면 같은 19892 인증 명령으로 다시 열고 인증 연결을 누른다.
원격 Commit/Push/Merge나 실제 Pi 설치는 이 실행 정리의 일부가 아니다.
