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

## AS183HWWA 읽기 전용 공기질 수집 계층

`air_quality.py`는 일반 Linux Python 호스트용 라이브러리다. 기존 probe의
`ThinQReadApi`와 `summarize_device()`를 재사용하며 SDK client를 외부에서 주입한다.
생성/import만으로 네트워크를 사용하지 않고, 호출자가 `poll()`을 명시적으로 호출할 때만
기기 목록·Profile·State의 GET 메서드를 사용한다. CLI, PAT 입력/저장, 자동 polling,
MQTT 발행, 제어 어댑터/Gateway 연결은 추가하지 않았다. 2026-10-23 사용자가 WSL에서
수정된 수집기의 단발 GET을 실행해 필수 7개 항목의 API/파싱값 일치를 확인했다.
에이전트는 마무리 작업에서 실제 API를 추가 호출하지 않았다. 상세 범위는 아래 검증 기록을 따른다.

대상은 `DEVICE_AIR_PURIFIER` / `AIR_910604_WW` / `reportable=true`가 정확히 한 대인 경우로
제한한다. 선택된 ID는 인스턴스 수명 동안 고정하며 출력·로그에는 넣지 않는다.
`controllable=null`이나 POWER_ON/CLEAN/AUTO를 읽기 전제조건으로 사용하지 않는다.
전원 OFF 상태도 조회할 수 있지만, 그때의 공기질 값이 새로운 측정인지는 확인하지 못한다.

| 출력 그룹 | 필드 | 확인된 API 경로 |
|---|---|---|
| measurements | pm1, pm1_level | airQualitySensor.PM1, PM1Level |
| measurements | pm2, pm2_level | airQualitySensor.PM2, PM2Level |
| measurements | pm10, pm10_level | airQualitySensor.PM10, PM10Level |
| measurements | odor, odor_level | airQualitySensor.odor, odorLevel |
| measurements | total_pollution, total_pollution_level | airQualitySensor.totalPollution, totalPollutionLevel |
| device_state | power | operation.airPurifierOperationMode |
| device_state | monitoring_mode | airQualitySensor.monitoringEnabled |
| maintenance | filter_remain_percent | filterInfo.filterRemainPercent |

필드 존재 근거는 [사용자 제공 실기 기록](../../docs/thinq-as183hwwa-validation-20261022.md)이다.
후속 GET 사용자 보고에서 숫자 속성의 `type=number`·범위 생략과 monitoring enum을 확인했다.
아직 제공받지 않은 수치 범위·다른 등급 허용값·단위는 확정하지 않는다.
특히 `pm2`는 API `PM2`를 보존한 이름이며 PM2.5/µg/m³로 자동 변환하거나 라벨링하지 않는다.
`monitoring_mode`는 `ON_WORKING` / `ALWAYS` 등 Profile이 허용하는 측정 모드 문자열이다.
boolean이나 네트워크 온라인 여부로 해석하지 않는다. 기존 미공개 수집 키
`monitoring_enabled`를 대체했으며 원본 API 경로는 유지한다. 현재 Hub·MQTT·UI 소비자는 없어
그 계약은 변경하지 않았다. 별도 로컬 소비자가 옛 키를 사용했다면 이름 변경을 반영해야 한다.

각 필드는 `value`, `valid`, `status`, `received_at`, `api_age_sec`, `last_good`를 독립적으로 갖는다.
Profile의 해당 속성에 읽기 모드 `r`이 있어야 하며 number/range/enum/list 형태로 읽기 허용값을 검증한다.
`type=number`이면 범위가 없거나 null이어도 유한 int/float를 허용한다. 명시된 범위가 있으면
계속 검사하고, 잘못된 범위 정의를 범위 생략으로 처리하지 않는다. 모르는 Profile 구조는
`unsupported_profile`로 차단한다.
유한한 숫자만 받고 문자열 숫자·bool을 수치로 변환하지 않는다. 음수 센서값과 0..100을 벗어난
필터 백분율은 거부한다. 나머지 상한·등급값은 응답 Profile 기준이며 임의의 건강 임계값은 없다.
필드 하나가 missing/invalid/out_of_range여도 다른 정상 필드는 계속 사용한다.

- `valid=true`는 **Profile 검증 + API 수신 신선도**만 뜻한다. 실제 새 측정이나 모든 센서 정상의 보장이 아니다.
- `measured_at=null`, `unit=null`, `measurement_freshness="unknown"`을 유지한다.
  현재 API에서 확인된 측정 시각과 공식 단위가 없으므로 수신 시각으로 채우지 않는다.
- `max_receipt_age_sec`는 호출자가 반드시 지정한다. 장치 측정 주기나 FSM 임계값이 아닌 로컬 수신 freshness 설정이다.
  경과 시간은 monotonic clock으로 계산한다. 예제 테스트의 30초는 합성 테스트 설정이며 운영 기본값이 아니다.
- `snapshot()`은 I/O 없이 age/stale을 다시 판정한다. stale·실패·결측이면 현재 `value=null`, `valid=false`다.
  이전 정상값은 `last_good`의 value/received_at/api_age_sec에만 남으며 현재값으로 대체하지 않는다.
- 같은 응답을 다시 받아도 API 수신 시각만 갱신한다. 센서 측정 시각·측정 변화는 증명하지 못한다.
- `api_status`와 `connection`은 구분한다. GET 성공은 `api_status=ok`, `connection=unknown`이다.
  SDK의 확인된 오류 코드 1222만 `offline`으로 판정하고, 인증·타임아웃·기타 실패는 unknown으로 둔다.
- 요청별 timeout을 적용하고 자동 재시도하지 않는다. 실패 후 다음 명시적 poll에서 복구 가능하다.
  복구 시 이전 값이 아니라 새 응답의 필드별 검증 결과로 갱신한다.
- API 예외·헤더·원본 응답·알 수 없는 필드는 공개 결과에 넣지 않는다.
  SDK 1.0.14의 DEBUG URL 로그도 수집 요청의 실행 context에서만 억제한다.
  전역 로그 레벨과 다른 작업의 로그는 바꾸지 않는다. 외부 호출자는 SDK 객체나 원본 예외를 별도로 로그하지 않아야 한다.

수집 API는 아래 형태다. **실제 client로 poll을 호출하는 GET 실측은 별도 승인 후 수행한다.**

```python
collector = AirQualityCollector(api, max_receipt_age_sec=configured_receipt_age)
snapshot = await collector.poll()  # on-demand GETs; api/session is caller-owned
safe_data = snapshot.to_dict()     # measurements / device_state / maintenance
snapshot = collector.snapshot()    # no I/O; ages current values
```

기존 ESP32 환경 데이터·SensorCache·sensor_summary·FSM·MQTT 계약은 변경하지 않았다.
향후 Hub 연동에서는 측정 시각 미확인, 출처와 필드별 유효성을 보존해야 하며, 이 객체를 기존
SensorFrame으로 바로 취급해서는 안 된다. 공기청정기 PM 값을 CO₂ 대체값으로 쓰지 않는다.

오프라인 검증(준비된 Python 3.12.15 / SDK 1.0.14 환경 사용):

```bash
PYTHONPATH=.:hub "$THINQ_TEST_PY" -B -m pytest -p no:cacheprovider -q \
  tools/thinq_connect/test_air_quality.py tools/thinq_connect/test_manual_trial.py \
  tools/test_thinq_connect_probe.py hub/tests/test_thinq_adapter.py
```

테스트는 합성 Profile/State만 사용한다. 합성 range/enum 허용값을 해당 모델의 실제 스펙으로
해석하지 않는다. HTTP/socket 연결을 차단한 상태에서 부분 결측·범위 이상·오프라인·인증 실패·
타임아웃·stale·복구 및 실제 SDK의 GET 요청 조립을 검증한다.

10월 22일 최초 파싱 문제와 10월 23일 실제 GET 재검증 성공은 [공기질 GET 기록](../../docs/thinq-as183hwwa-air-quality-get-20261022.md)에 구분해 기록했다.
`fixtures/as183hwwa_air_quality_observed_20261022.json`은 사용자 보고의 최소 재구성본이며
원본 응답·기기 ID·PAT를 포함하지 않는다. 기존 합성 테스트의 임의 범위와 구분한다.

## 모델 독립형 플랫폼 방향과 현재 PoC 경계

최종 목표는 여러 LG ThinQ 가전의 Profile·Capability에 기반한 연동 플랫폼이다.
AS183HWWA는 첫 실기 검증 장치다. 현재 `AirQualityCollector`의 모델 선택과 `FIELDS`는
그 PoC의 명시적 매핑이며, 이 클래스가 모든 ThinQ 가전을 지원한다는 뜻이 아니다.
다른 기기가 발견돼도 임의로 선택하거나 지원 기능을 추측하지 않는다.

- 공통 기반: 공식 SDK와 기존 probe의 GET 인터페이스를 재사용한다. timeout·오류 마스킹·
  수신 시각/monotonic age·필드별 검증/캐시 처리는 모델명과 독립적으로 설계한다.
- 속성 매핑: API resource/property, 값 종류와 의미상 min/max는 `FieldSpec`에 둔다.
  PM은 음수 불가, 필터 백분율은 0..100이지만 이를 모든 가전의 숫자에 일괄 적용하지 않는다.
  합성 온도처럼 음수가 정상인 속성은 Profile과 그 매핑이 허용하면 유지한다.
- Profile enum은 해당 기기의 읽기 허용값으로 판단한다. ON_WORKING/ALWAYS를 다른 기기의
  공통 모드로 강제하지 않는다. 단위·PM2 의미·측정 시각도 기기별 확인 전에는 미확인이다.
- 제어: PR #57의 DeviceRegistry/Capability/ThinQConnectAdapter와 Dispatcher/사용자 수락
  경로를 유지한다. 이번 읽기 number 허용 규칙을 쓰기 명령의 허용 범위로 전용하지 않는다.
  무범위 읽기 지원은 제어 권한·제어 Capability 검증을 뜻하지 않는다.
- 센서 융합은 후속 단계다. Registry의 공개 논리 ID와 속성 경로를 출처로 묶고, 단위/의미
  확인 여부·필드별 유효성·측정/수신 시각을 유지해야 한다. 현재 객체는 API 출처와 속성 경로만
  제공하며 다중 기기 Registry 라우팅·융합·FSM 폴백까지 구현한 상태는 아니다.
- 데이터가 부족하면 수집기에서는 현재값을 null/invalid로 두고 last_good을 구분한다.
  향후 소비자는 필수 신호가 없을 때 판단을 보류해야 하며, 집계 valid 하나로 대신하지 않는다.

검증은 (1) AS183HWWA 실제 API 시험, (2) 다른 가전의 합성 Profile을 통한 파서 재사용 검사,
(3) Pi 4–MQTT–Pi 5–Gateway–실가전 종단 시험으로 구분한다.
`fixtures/synthetic_other_appliance_profile.json`의 속성·값·모드는 순수 합성이며
다른 LG 모델의 실제 지원 기능이나 연결 성공을 뜻하지 않는다.
현재 인스턴스 간 캐시/오류 격리는 테스트하지만 다중 기기 운영 통합은 아직 수행하지 않았다.

AS183HWWA 필수 7개 항목의 실제 GET 파싱은 2026-10-23 사용자 보고로 검증됐다.
다른 속성·모드·장애 복구 및 장치/시스템 통합의 실측 검증은 여전히 남아 있다.
새 가전 전체 지원이나 Live 제어는 추가하지 않는다. Flutter는 수정하지 않으며, PR #56이
실제로 병합된 뒤 최신 main의 디자인·모션·내비게이션과 라이선스 승인 자산을 기준으로 최소 연동한다.
