# ThinQ Connect 조회와 별도 수동 시험

`probe.py`는 공식 `thinqconnect` Python SDK로 기기 목록, 기기 프로필, 현재 상태만 조회한다.
제어 API는 의도적으로 제공하지 않는다. DESKMATE 제어 어댑터와 정책이 확정되기 전 실제 가전을
잘못 조작하지 않기 위해서다.

## PAT 준비

1. [LG Smart Solution API Developer](https://smartsolution.developer.lge.com/)에 LG ThinQ 계정으로 로그인한다.
2. **Cloud Developer → Docs → ThinQ Connect → PAT (Personal Access Token)**에서 PAT를 발급한다.
3. 계정 국가가 대한민국이면 country code는 `KR`을 사용한다.
4. 클라이언트마다 고정된 UUID v4 하나를 사용한다. 실행마다 새 ID를 만들면 API 호출이 차단될 수 있다.

PAT를 채팅, Git, 명령행 인자에 붙여 넣지 않는다. 가장 안전한 기본 방식은 도구가 표시하는 숨김
프롬프트에 직접 입력하는 것이다. 기존 probe에는 THINQ_PAT 호환 경로가 있지만, 이 문서의 실제 수동 시험은 환경변수 PAT를
읽지 않고 숨김 입력만 사용한다. PAT를 파일·환경설정·환경변수·로그에 저장하지 않는다.

```bash
# Python 3.12.15 가상환경에서 실행한다(환경 준비는 아래 참고).
python -m pip install -r tools/thinq_connect/requirements.txt

export THINQ_COUNTRY=KR
export THINQ_CLIENT_ID='<한 번 생성해 계속 사용할 UUID v4>'
python -m tools.thinq_connect.probe list
python -m tools.thinq_connect.probe list --show-device-id
python -m tools.thinq_connect.probe inspect '<deviceId>'
```

공식 SDK에서 사용하는 읽기 API는 다음과 같다.

- `async_get_device_list()` → `GET /devices`
- `async_get_device_profile(device_id)` → `GET /devices/{deviceId}/profile`
- `async_get_device_status(device_id)` → `GET /devices/{deviceId}/state`

프로필의 writable property와 제어 옵션이 그 모델에서 실제 허용하는 기능이다. 제품 종류만 보고
전원, 목표 온도, 운전 모드가 모두 가능하다고 가정하면 안 된다.

PAT 없이 구조를 확인할 때는 fixture를 사용한다.

```bash
python -m tools.thinq_connect.probe --mock-file /tmp/thinq-devices.json list
```

fixture root는 `devices`, `profiles`, `states` 키를 가진 JSON 객체다.

## DESKMATE 연결 경계

Pi 4에 설치된 ATLAS `ThinqClient1` D-Bus 서비스는 보드 등록과 cloud message 전송 인터페이스다.
현재 확인된 인터페이스만으로는 개인 ThinQ 계정의 `GET /devices` 및 가전 프로필/제어 API와 같은
계약임을 입증할 수 없다. 실제 가전 조회는 우선 공식 ThinQ Connect SDK로 검증한다.

향후 어댑터는 `deskmate/control/cmd`를 구독하고, 실제 성공/실패를
`deskmate/control/result`로 돌려준다. 온도·습도·CO2·조도에서 명령을 결정하는 정책은 이 API
어댑터 밖에 두며 기존 FSM 임계값과 섞지 않는다. 조명은 ThinQ 프로필에서 지원이 확인되기 전까지
기존 BLE 램프 경로를 유지한다.


보안 보완(2026-10-19): CLI는 SDK 예외 본문/traceback을 출력하지 않는다. 출력의 alias,
계정 식별자, serial/MAC/SSID, credential 필드는 재귀적으로 가린다. 기기 ID는 기본 마스킹하며
`list --show-device-id`는 로컬 선택을 위한 명시적 예외다. 이 출력도 저장소에 커밋하지 않는다.
알 수 없는 프로필의 자유 형식 필드까지 개인정보가 없다고 보장할 수 없으므로 실제 조회 결과는
공유 전 별도 검토해야 한다. 이번 테스트에는 합성 기기/프로필만 사용했다.


## AS183HWWA 단독 실기 기록과 수동 시험 보존본

2026-10-22 사용자가 WSL에서 실제 AS183HWWA의 AUTO → LOW → AUTO 시험을 수행했고,
API 접수·GET State·본체 물리 확인을 각각 확인했다. [실기 기록](../../docs/thinq-as183hwwa-validation-20261022.md)을
따른다. 이는 사용자 제공 로그·물리 확인에 근거한 단독 시험이며, Gateway·Pi 4·Pi 5 종단 성공이 아니다.
기존 합성/Mock 검증 기록과 구분한다. 이 도구는 Hub/Mock Gateway에 import하거나 연결하지 않는다.

### 환경과 의존성

저장소 루트에서 Python 3.12.15 + thinqconnect 1.0.14를 사용한다.
`requirements.txt`는 이 SDK 버전을 고정하며 SDK 원본·venv는 저장소에 넣지 않는다.
시스템·PlatformIO Python을 변경하지 않는다. 기존 해당 환경이 있으면 그 Python을 재사용하면 된다.
다음은 uv로 사용자 영역에 **별도 환경이 필요한 경우만** 준비하는 예다.

```bash
THINQ_TEST_ENV="$HOME/.local/share/deskmate/thinq-manual-venv"
if [ ! -x "$THINQ_TEST_ENV/bin/python" ]; then
  ~/.local/bin/uv venv --python 3.12.15 --no-python-downloads "$THINQ_TEST_ENV"
fi
~/.local/bin/uv pip install --python "$THINQ_TEST_ENV/bin/python" -r tools/thinq_connect/requirements.txt
THINQ_TEST_PY="$THINQ_TEST_ENV/bin/python"
```

부모 Python 3.12.15가 이미 설치되어 있다는 전제다. 보존본은 검증 당시 버전과 다르면 중단한다.
`THINQ_TEST_PY`는 자격증명이 아닌 실행 파일 경로이며 기존 환경의 Python 경로를 지정해도 된다.

### 변경 없는 점검과 GET 사전 조회

```bash
"$THINQ_TEST_PY" -B -m tools.thinq_connect.manual_trial --check
export THINQ_CLIENT_ID="${THINQ_CLIENT_ID:-$("$THINQ_TEST_PY" -c 'import uuid; print(uuid.uuid4())')}"
env -u THINQ_PAT "$THINQ_TEST_PY" -B -m tools.thinq_connect.manual_trial --preflight
```

--check는 API를 호출하지 않는다. --preflight(옵션 생략 시에도 기본)는 GET 목록·Profile·State만
호출하며, PAT는 실제 대화형 터미널에서 숨김 입력한다. ID나 토큰 인자는 받지 않는다.
Client ID는 같은 터미널의 기존 값을 유지하고 없을 때만 생성한다. 터미널 재시작 후에도 기존
Client ID를 재사용하는 것이 좋으며 호출마다 새 UUID를 만들지 않는다.

DEVICE_AIR_PURIFIER / AIR_910604_WW가 정확히 한 대이고, reportable=true이며,
windStrength가 LOW·AUTO 읽기/쓰기를 지원하고 POWER_ON / CLEAN / AUTO일 때만 사전 점검을 통과한다.
controllable=null만으로 거절하거나 제어 권한이 있다고 판단하지 않는다.
모델·reportable·HTTP 성공만으로 실시간 온라인 상태를 보장하지 않는다.

### 별도 승인 후에만 가능한 실제 수동 시험

아래는 실제 기기를 변경할 수 있다. **각 재실행은 별도 실기 승인을 받은 뒤 사용한다.**
보존·오프라인 테스트 작업의 승인은 실행 승인이 아니다.

```bash
env -u THINQ_PAT "$THINQ_TEST_PY" -B -m tools.thinq_connect.manual_trial --execute
```

1. LOW 전 본체 온라인·POWER_ON/CLEAN/AUTO와 다른 사용자/예약/자동화 조작 부재를 확인한다.
2. `APPROVE AS183HWWA LOW ONLINE NO OTHER CONTROL`을 입력한다.
   Profile·State를 다시 검사하고 airFlow.windStrength=LOW 한 번만 보낸다.
3. API 접수와 GET LOW 확인은 물리 확인을 대신하지 않는다. 본체 확인 후에만
   `PHYSICAL LOW CONFIRMED`를 입력한다. 확인 불가이면 UNKNOWN으로 구분한다.
4. 외부 조작이 없고 실제로 시험 LOW 설정이 유지되는 경우에만
   `APPROVE AS183HWWA RESTORE AUTO NO EXTERNAL CHANGE`를 입력한다.
   재조회·설정 비교 후 AUTO 한 번을 보낸다.
5. 본체 복원을 확인한 경우에만 `PHYSICAL AUTO CONFIRMED`를 입력한다.
   State만 맞고 물리 확인이 없으면 full_success가 아니다.

windStrengthDetail, 전원, 모드, 다른 기기 및 다른 HTTPS host/endpoint로의 제어는 차단한다.
SDK 고수준 set_wind_strength()는 상세 속성을 우선할 수 있으므로 사용하지 않는다.
POST 응답 유실·오류 뒤에는 GET 확인만 수행하고 추가 POST/복원을 하지 않는다.
Ctrl+C/EOF/오류 시 자동 cleanup POST도 없다. LOW가 남았을 가능성이 있으면 본체/ThinQ 앱에서
확인하고 사용자가 복원을 판단한다. 기존 설정이 바뀌면 강제로 AUTO를 덮어쓰지 않는다.
GET 간격 약 10초·확인 기한 60초는 시험 설정이며 LG 센서/클라우드 갱신 주기가 아니다.
조회 사이의 외부 조작을 완전히 감지하거나 막지는 못한다.

### 오프라인 재현

```bash
"$THINQ_TEST_PY" -B -m unittest tools.thinq_connect.test_manual_trial -v
```

실제 SDK의 요청 조립과 오류 처리는 사용하지만, HTTP session과 가전·PAT·ID·물리 확인은 모두
합성 fixture다. 실제 HTTP/소켓 연결은 테스트에서 차단한다. 기존 19개 시나리오에 CLI 검사 8개를
추가했다. 이 결과는 실기 시험 재실행이나 운영 Gateway 검증이 아니다.
선택적으로 pytest가 설치된 개발 환경에서는 기존 probe·어댑터 회귀도 함께 실행할 수 있다.

```bash
PYTHONPATH=.:hub "$THINQ_TEST_PY" -B -m pytest -p no:cacheprovider -q \
  tools/thinq_connect/test_manual_trial.py tools/test_thinq_connect_probe.py hub/tests/test_thinq_adapter.py
```

계정/기기 ID·PAT·원본 로그는 fixture나 문서에 복사하지 않는다. 이 수동 시험은 Gateway·FSM·자동 제어를
활성화하지 않는다. PM 데이터 MQTT 수집과 Live 명령·journal 통합은 후속 승인 대상이다.
