# 독립 수동 LOW/AUTO 시험 CLI

`manual_control.py`는 기존 Session·HostSession·GuardedSdk·Transport를 재사용한다.
포트나 MQTT 연결을 만들지 않으며 18891 Live Read-only와 18991 Fixture 서버를 유지한다.
기본 실행은 GET 사전 점검만 수행한다. `--manual`은 사용자 명령을 기다리며 LOW/AUTO 각각
별도의 정확한 승인 문구를 입력해야 POST가 가능하다.

2026-11-05 사용자가 제공한 실행 기록에서 LOW/AUTO 각각의 API 접수·GET 일치와
사용자의 본체 물리 확인을 확인했다. 최종 State는 POWER_ON/CLEAN/AUTO이며 세션은 종료됐다.
이는 노트북 CLI 경로의 실기 결과로, HTML 또는 Pi 4 정책 종단 제어 성공을 의미하지 않는다.

## 실행

사용자 본인의 신뢰된 WSL 터미널에서:

```bash
cd "$HOME/dev/ESWContest_LG/2026ESWContest_smart_TEAMMATE-thinq-laptop-gateway"
PY="$HOME/.local/share/deskmate/thinq-gateway-venv/bin/python"
"$PY" tools/laptop-control/manual_control.py --manual
```

기존 `~/.config/deskmate-thinq/client-id`를 형식·소유자·0700/0600 권한 검사 후 읽기만 한다.
없거나 잘못되면 새 ID 생성 없이 중단한다. 기기 조회·상태 조회·제어 권한이 있는 PAT는
터미널에서 숨김 입력하며 파일·환경변수·명령 인자·HTTP·로그에 전달하지 않는다.
세션은 20분이며 종료·만료 후에는 같은 프로세스에서 승인 권한을 복구하지 않는다.

시작하면 대상 한 대 선택, Profile, State GET으로 사전 점검한다.
`status=ready`는 LOW/AUTO Profile·POWER_ON/CLEAN/AUTO 조건을 확인했다는 의미이며
PAT의 실제 쓰기 권한이나 기기의 물리 온라인 상태를 GET으로 입증한 것은 아니다.
이후 명령 프롬프트에서 기다린다. 자동 조회·POST·복원은 없다.

## 첫 LOW 시험

1. 실제 본체 온라인·전원 ON·청정·AUTO 상태와 다른 조작/예약이 없음을 확인한다.
2. 사용자의 명시적인 LOW 시험 승인 후에만 명령 `LOW`를 입력한다.
3. 새 Profile/State를 검증하고 표시한 30초 안에 다음 승인 문구를 직접 입력한다.

   `APPROVE AS183HWWA LOW ONLINE NO OTHER CONTROL`

4. 전송 직전 Profile/State·세션·승인 만료·설정 불변을 재검사한 뒤 LOW POST를 최대 한 번 수행한다.
5. `post_response.api_accepted`와 `get_readback.state_match`는 각각 별도의 결과다.
   두 결과가 성공이어도 아직 물리 동작 확인은 아니다.
6. 본체 표시·풍량을 직접 확인한 경우에만 `PHYSICAL LOW CONFIRMED`를 입력한다.
   미확인이라면 `UNKNOWN`을 입력해 종료한다. 자동 복원하지 않는다.
7. LOW 확인 후 CLI는 명령 대기로 돌아온다. AUTO는 별도 사용자 승인 전까지 입력하지 않는다.

## AUTO와 실패 처리

AUTO는 같은 세션에서 LOW의 API 접수·GET 일치·사용자 물리 확인을 마친 뒤에만 가능하다.
명령 `AUTO`도 별도 승인 문구와 새 Profile/State·만료 검사를 거친다.
LOW/AUTO를 세션당 한 번씩만 전송하며, 결과 유실·timeout·불일치·만료·외부 변경 시 시험을 종료한다.
재시작으로 직전 실패 명령을 다시 보내지 말고 먼저 실제 상태를 확인한다.
프로세스를 재시작하면 이전 LOW 확인 이력을 복구하지 않는다.

`GET`은 사용자가 요청한 단발 조회, `QUIT` 또는 Ctrl+C는 종료다.
종료·PAT 폐기는 이미 실행된 LOW를 취소하지 않는다. 필요한 복원은 상태 확인 후 별도로 판단한다.
전원 ON/OFF·HIGH·V1.1 자동 제어는 이 CLI에 구현하지 않는다.
터미널에는 허용된 상태 요약·접수 여부·readback·사용자 확인만 표시하며 원본 API 응답은 출력하지 않는다.
