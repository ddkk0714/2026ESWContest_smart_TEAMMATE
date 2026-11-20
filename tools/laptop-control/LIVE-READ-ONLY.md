# WSL 노트북 전용 Fixture / Live Read-only

Live 모드는 Pi5 SSH·캡처·터치·앱 실행을 생성하지 않는다. Pi4 수신은 기존 devview의 `--read-only` 경로다. Cloud는 사용자가 `현재 상태 읽기`를 클릭할 때만 호출한다. 연결 및 2초마다의 snapshot은 메모리 읽기다.

## 환경과 실행 위치

모든 터미널에서 이 기능 worktree의 저장소 루트로 이동한다. 기존 Python3.12와 캐시로 준비한 영속 환경 예시:

```bash
cd "$HOME/dev/ESWContest_LG/2026ESWContest_smart_TEAMMATE-thinq-laptop-gateway"
PY="$HOME/.local/share/deskmate/thinq-gateway-venv/bin/python"
```

`thinqconnect==1.0.14`를 유지한다. 이 가상환경은 기존 uv 캐시의 패키지를 참조하므로 캐시를 제거하지 않는다. 환경이 없거나 SDK 버전이 다르면 임의 설치·업그레이드하지 않는다.

포트는 Fixture `18790/18791/18792`, 동시 실행하는 Live `18890/18891/18892`로 분리한다. 최종 단일 화면 주소를 `18791`로 사용할 때는 자신이 실행한 기존 Fixture Node 서버만 Ctrl+C로 종료하고 Live Node의 `--port`를 `18791`로 지정한다. Fixture 소스·백엔드를 삭제하지 않는다. 두 서버를 같은 포트에 중복 실행하지 않는다.

## A. Fixture — Cloud 0회

각각 별도 터미널:

```bash
"$PY" hub/atlas-tof/pc/devview/devview.py --fake --read-only --bind 127.0.0.1 --port 18790
```

```bash
"$PY" tools/laptop-control/thinq_host.py --fixture --allow-control --port 18792
```

```bash
node tools/laptop-control/server.mjs --mode fixture --port 18791 --sensor-fixture-port 18790 --thinq-port 18792
```

브라우저는 `http://127.0.0.1:18791/`. LOW/AUTO는 Fake SDK만 사용하며 물리 동작 검증이 아니다.

## B. Live Read-only — 별도 승인 후 사용자 실행

Pi4 주소는 읽기 전용 확인으로 확보한 현재 주소를 사용한다. IP를 추정하거나 Pi5 SSH 성공만으로 Pi4 성공을 판단하지 않는다.

터미널1: 실제 센서 읽기. 아래의 `PI4_HOST`는 사용자가 현재 확인한 주소를 입력한다.

```bash
read -r -p '확인된 Pi4 주소: ' PI4_HOST
"$PY" hub/atlas-tof/pc/devview/devview.py --pi4 "$PI4_HOST" --read-only --bind 127.0.0.1 --port 18890
```

Pi4 불통이어도 인증 및 ThinQ 카드는 독립 사용 가능하다. 실제 센서 미수신을 가짜 값으로 채우지 않는다. 프로필·제안 응답·MQTT 명령 발행은 --read-only로 차단한다.

터미널2: **사용자 본인의 신뢰된 대화형 터미널**에서만 실행한다. Codex 도구·채팅에 PAT를 넣지 않는다.

```bash
"$PY" tools/laptop-control/thinq_host.py --live-read-only --port 18892
```

Live Read-only는 최초 실행에서 사용자 전용 `~/.config/deskmate-thinq/client-id`에 UUIDv4를 한 번 만들고, 이후 실행은 같은 값을 읽는다. 디렉터리 권한은 `0700`, 파일은 `0600`이어야 하며 소유자·형식·권한이 다르면 값 생성이나 덮어쓰기 없이 중단한다. Client ID 값은 화면·로그에 출력하지 않는다. 이어 PAT는 사용자 터미널에서 숨김 입력한다. PAT는 인수·환경변수·파일·HTML에 전달하지 않는다. 숨김 입력을 지원하지 않는 터미널은 중단한다. 인증은 프로세스 메모리에만 있고 최대20분이며 종료·재시작 시 재공급해야 한다. 메모리는 프로세스 침해나 덤프에 대한 완전한 보호를 제공하지 않는다.

서버 시작·인증 세션 연결만으로 Cloud에 요청하지 않는다. 사용자 조회 승인 후 버튼을 누르면 기기 목록(최초1회)·Profile·State GET을 순차 수행한다. 이후 재조회는 고정한 기기의 Profile·State를 조회한다. 일치하는 모델이 정확히 한 대가 아니면 차단한다. 응답에서 실제 ID·계정·PAT는 공개하지 않는다.

터미널3: Pi4 주소를 위와 동일하게 지정한다. Pi4가 준비되지 않으면 `--pi4`를 생략해 ThinQ만 먼저 확인할 수 있다.

```bash
read -r -p '확인된 Pi4 주소 (미연결이면 Enter): ' PI4_HOST
node tools/laptop-control/server.mjs --mode live-read-only --port 18891 --sensor-port 18890 --thinq-port 18892 --pi4 "$PI4_HOST"
```

브라우저는 `http://127.0.0.1:18891/`. 최종18791로 전환할 때만 앞서 설명한 포트 교체를 적용한다.

1. Live Read-only·POST 금지 표시 확인.
2. 실제 Pi4 센서와 갱신 시퀀스 확인. 미연결이면 ThinQ와 별도로 보고.
3. ThinQ 팝업 → 인증 세션 연결. Fixture나 제어 가능 백엔드는 거부돼야 한다.
4. **실제 Cloud 조회에 대한 명시적 승인 후** 현재 상태 읽기 한 번 클릭.
5. API값·등급·필터·전원·운전·풍량·마지막 성공 GET(UTC)을 실제 기기와 대조.
6. PM2는 API 필드명이며 단위·측정 시각·실제 측정 최신성은 미확인. connection=unknown을 오프라인으로 단정하지 않는다.
7. LOW/AUTO 버튼은 비활성. 직접 HTTP로 prepare/execute/export를 시도해도 Node와 Python 양쪽에서 거부한다. 자동 재전송·자동 폴링·MQTT 발행 없음.

## Pi5 경유가 필요한 경우

기존에 검증한 SSH 키 파일·HostKeyAlias로 신원을 먼저 확인한다. 새 키 자동 승인은 금지한다. Pi5→Pi4 각 포트가 실제로 접근 가능한 경우에만 노트북 loopback 포워딩을 사용한다. 보드 네트워크·라우팅·서비스 설정을 변경하지 않는다.

```bash
# 검증된 현재 값으로만 설정. 비밀값이 아닌 공개 호스트 키 파일/기존 alias다.
read -r -p '기존 known_hosts 파일: ' TRUST_FILE
read -r -p '동일 키가 기록된 기존 HostKeyAlias: ' TRUST_ALIAS
read -r -p '현재 Pi5 주소: ' PI5_HOST
read -r -p '확인된 Pi4 유선 주소: ' PI4_LAN
ssh -N -T -o BatchMode=yes -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile="$TRUST_FILE" -o HostKeyAlias="$TRUST_ALIAS" \
  -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=2 \
  -L "127.0.0.2:1883:$PI4_LAN:1883" \
  -L "127.0.0.2:8771:$PI4_LAN:8771" \
  -L "127.0.0.2:8772:$PI4_LAN:8772" \
  -L "127.0.0.2:8765:$PI4_LAN:8765" "root@$PI5_HOST"
```

이 터널을 사용할 때 위의 PI4_HOST를 `127.0.0.2`로 지정한다. 기존 root SSH는 전송에만 사용하며 Native Service나 다른 패키지의 권한을 빌리지 않는다. loopback에만 bind하며 포트 충돌 시 다른 프로세스를 종료하지 말고 중단한다.

## 실패·종료·복구

- 연결 불가: 각 터미널의 고정 오류를 확인한다. Node 화면은 `127.0.0.1` URL로 연다. Cloud401/403은 인증 오류이며 Pi4 센서 장애나 기기 offline과 별개다.
- 세션 만료·브라우저 갱신 후 bootstrap 충돌: 인증 터미널을 Ctrl+C로 종료하고 같은 고정 Client ID로 사용자가 다시 공급한다. 자동 재시도하지 않는다.
- API 실패/stale: 현재값을 표시하지 않는다. PAT나 SDK 예외 전문을 로그에 출력하지 않는다. 로그는 고정 오류·HTTP 상태·시각만 확인한다.
- 종료: HTML 인증 세션 종료 → 자신이 실행한 인증·Node·devview·SSH 포워딩 터미널에서 각각 Ctrl+C. 보드 서비스는 종료하지 않는다.
- Fixture 복귀: Live Node를 Ctrl+C로 종료하고 A의 Node를 재실행한다. 기존 앱 설치나 설정을 바꾸지 않았으므로 IPK 복구는 필요하지 않다.

실제 Cloud 응답과 브라우저 렌더링은 이 문서만으로 검증됐다고 보지 않는다. 현재 계정의 기존 Client ID 확인과 실제 GET은 사용자의 별도 승인이 필요하다.
