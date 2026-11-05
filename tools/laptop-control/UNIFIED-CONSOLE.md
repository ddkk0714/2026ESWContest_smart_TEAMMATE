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
- 인증은 최대 40분, 실행 challenge는 30초다. 종료/재시작 시 PAT를 다시 입력한다.
- 키보드 수집 ON/OFF와 마우스 유지는 별도다. 현재 `--local-only` 시험 수집기는
  캡처만 검증하고 MQTT를 발행하지 않는다. 상단에 이 모드를 표시한다.

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
