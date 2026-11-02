# AS183HWWA 공기질 GET 관측·수정·재검증 — 2026-10-22~23

## 근거와 범위

사용자가 실제 LG AS183HWWA / API 모델 AIR_910604_WW에서 수행한 GET 결과의
요약 보고를 근거로 한다. Devices·Profile·State가 모두 HTTP 200이었다는 사용자 확인이다.
이 수정 작업에서 에이전트가 API를 재호출하거나 새 측정값을 얻은 것은 아니다.
계정·PAT·Client ID·기기 ID·원본 응답은 보존하지 않는다. 정확한 요청 시각·수신 간격은 제공받지 않았다.
10월 22일 최초 조회와 10월 23일 수정 후 재검증을 아래에서 구분한다.

기존 [WSL AUTO → LOW → AUTO 단독 제어 시험](thinq-as183hwwa-validation-20261022.md)과
이번 읽기 전용 공기질 GET은 별도 검증이다. 이번에는 POST·전원 OFF·풍량 변경을 하지 않았다.

## 2026-10-22 최초 GET: API 성공, 파싱 문제 발견

이날 API의 HTTP 200과 별개로 기존 파서는 아래 숫자를 unsupported_profile로,
monitoringEnabled 문자열을 invalid_value로 처리했다. 표의 마지막 열은 당시 수정 후
오프라인 예상 결과이며, 수정된 코드의 실기 성공은 다음 날 별도 확인했다.

| API 속성 | 보고된 Profile | 보고된 State | 수정 후 오프라인 예상 결과 |
|---|---|---|---|
| airQualitySensor.PM1 | number, 읽기 지원, 명시적 read range 없음 | 8 | pm1=8, valid=true |
| airQualitySensor.PM2 | 동일 | 8 | pm2=8, valid=true |
| airQualitySensor.PM10 | 동일 | 10 | pm10=10, valid=true |
| airQualitySensor.odor | 동일 | 1 | odor=1, valid=true |
| airQualitySensor.totalPollution | 동일 | 1 | total_pollution=1, valid=true |
| filterInfo.filterRemainPercent | 동일 | 96 | filter_remain_percent=96, valid=true |
| airQualitySensor.monitoringEnabled | enum, 읽기 지원, ON_WORKING / ALWAYS | ON_WORKING | monitoring_mode=ON_WORKING, valid=true |

표의 valid는 Profile 검증과 로컬 API 수신 freshness만 뜻한다. 물리적 새 측정이나
클라우드 값의 측정 신선도를 보증하지 않는다. 사용자 보고에 없는 전원 상태·등급·연결 상태를
추가로 추정하지 않는다. PM 단위, PM2의 정확한 물리적 의미, 측정 시각·주기는 미확인이다.

## 2026-10-23 수정 후 실제 GET 재검증 성공

사용자가 WSL 터미널에서 최신 worktree의 수정된 `air_quality.py`를 사용하는 단발 GET
재검증이 성공했다고 보고했다. 아래는 사용자 제공 실행 결과 요약이며, 에이전트가 같은
장치에 재조회한 결과는 아니다. 이번 마무리 작업에서도 실제 API를 추가 호출하지 않았다.

| 확인 항목 | 사용자 보고 결과 |
|---|---|
| Devices / Profile / State | 모두 HTTP 200 |
| GET_attempts | 3 |
| api_status | ok |
| all_required_fields_match | true |
| 필수 7개 항목 | API 값과 파싱값 일치, valid=true, status=ok |

| API 속성 | 수집 필드 | 실제 State 및 파싱값 |
|---|---|---|
| airQualitySensor.PM1 | pm1 | 15 |
| airQualitySensor.PM2 | pm2 | 20 |
| airQualitySensor.PM10 | pm10 | 27 |
| airQualitySensor.odor | odor | 1 |
| airQualitySensor.totalPollution | total_pollution | 2 |
| filterInfo.filterRemainPercent | filter_remain_percent | 95 |
| airQualitySensor.monitoringEnabled | monitoring_mode | ON_WORKING |

검증한 것은 **WSL → ThinQ Cloud GET → 수정된 읽기 수집 계층의 필수 필드 파싱 일치**다.
POST·전원 OFF·풍량/운전 모드 변경 시험이 아니다. Pi 4·MQTT·Pi 5·Gateway 종단 검증도 아니다.
다른 등급·전원 필드의 값이나 물리적 온라인 상태를 이 결과로 추가 확정하지 않는다.

PM 단위·PM2의 정확한 물리적 의미·센서 측정 시각/주기는 여전히 미확인이다.
API 수신 시각은 센서 측정 시각이 아니며, 3개 GET 성공은 새 센서 측정 발생을 증명하지 않는다.
10월 22일 fixture의 8/8/10·필터 96은 과거 재현 입력으로 유지하고, 오늘의 실측값으로
덮어써서 과거 테스트와 실측 이력을 혼합하지 않는다. 계정/인증정보와 불필요한 raw 응답은 기록하지 않는다.

## 발견한 문제와 수정

1. 기존 파서는 range/enum/list 등의 형태만 지원해, 읽기 지원이 명시된 `number` 값도
   범위가 없으면 unsupported_profile로 버렸다. number의 범위 생략/null을 허용하되
   유한 int/float만 받는다. bool·문자열 숫자·NaN·무한대·잘못된 타입은 거부한다.
   명시된 min/max는 계속 검사하고, 잘못되거나 일부만 있는 범위는 우회하지 않는다.
   음수 센서값 거부와 필터 백분율 0..100 검사는 유지한다.
2. 기존 monitoring_enabled는 boolean으로 잘못 정의돼 ON_WORKING을 거부했다.
   내부 키를 monitoring_mode로 고치고 읽기 Profile enum에 있는 문자열만 허용한다.
   ON_WORKING·ALWAYS를 true/false 또는 온라인/오프라인으로 변환하지 않는다.

현재 수집 계층은 MQTT/FSM/UI에 연결하지 않은 독립 라이브러리다. 저장소에서 옛 키의
사용처는 수집기·테스트·README뿐이었다. 따라서 중복 alias를 만들어 boolean 오해를 남기지
않고 키를 명시적으로 변경했다. 원본 API 경로는 그대로다. 옛 키를 사용하는 외부 로컬
소비자는 업데이트가 필요하며, 향후 전송 계약은 별도 설계·승인 대상이다.

필드별 독립 검증, last_good과 현재값 분리, monotonic 수신 age/stale,
unit=null·measured_at=null·measurement_freshness=unknown은 유지한다.
반복 GET 성공이나 값 동일/변화만으로 새 센서 측정 발생을 확정하지 않는다.

## 식별정보 없는 fixture와 오프라인 검증

`tools/thinq_connect/fixtures/as183hwwa_air_quality_observed_20261022.json`은 위 보고의
최소 재구성본이다. 읽기 지원을 mode=[r], 범위 생략을 value.r=null로 나타낸 테스트 표현이며,
전달받지 않은 원본 JSON의 정확한 배치까지 확인했다고 주장하지 않는다. 보고된 필드와 값만
담으며 기기 ID·기기 목록·토큰·개인정보·기타 미제공 속성은 없다.

오프라인 변형 테스트의 소수 값·명시적 범위·잘못된 enum·장애는 합성 입력이다.
실제 기기에 그런 상태를 만들거나 전원을 끈 것이 아니다.

검증 항목:

- number + 범위 생략/null + 정상 정수/실수
- 명시적 범위 경계·초과·잘못된 범위 차단 및 읽기 권한 유지
- bool·숫자 문자열·NaN·무한대·기타 비정상 타입 거부
- monitoring enum ON_WORKING/ALWAYS, 미지원 값·잘못된 타입 거부
- 한 필드가 잘못돼도 다른 정상 필드 유지
- last_good·stale·결측·복구 및 기존 SDK GET/오류/토큰 마스킹 회귀
- 기존 외부 GET 실행 도구의 오프라인 안전성 5개 회귀

이번 수정에서 Python 3.12.15 / thinqconnect 1.0.14로 실제 오프라인 검증했다.

| 범위 | 결과 |
|---|---|
| 수집기 기존 21 + 신규 10 | 31 passed |
| 보존 수동 시험 | 27 passed |
| 기존 probe | 7 passed |
| ThinQ adapter | 15 passed |
| 저장소 밖 GET 실행 도구 안전성 | 5 passed |
| 합계 | 85 passed (기존 75 + 신규 10) |

HTTP/socket 연결은 테스트에서 차단했다. fixture의 값과 수정된 파싱 결과가 일치함을
검증한 것이며, 수정 후 실제 기기를 다시 조회한 성공은 아니다. 사용자 보고의 실제
Devices/Profile/State HTTP 200과 이 테스트의 Fake HTTP 응답을 혼동하지 않는다.
저장소 안 테스트는 80개이며 외부 실행 도구 테스트 5개는 기존 `/tmp` 검증 도구의 회귀다.

저장소 루트에서 준비된 Python/SDK 환경으로 저장소 테스트를 재현할 수 있다:

```bash
PYTHONPATH=.:hub "$THINQ_TEST_PY" -B -m pytest -p no:cacheprovider -q \
  tools/thinq_connect/test_air_quality.py tools/thinq_connect/test_manual_trial.py \
  tools/test_thinq_connect_probe.py hub/tests/test_thinq_adapter.py
```

Ruff 검사·포맷, JSON/문법, 공백 검사도 수행했다. 실제 API 재검증은 수행하지 않았다.

## 남은 검증

- 기본 필수 필드의 실제 GET 재검증은 2026-10-23 사용자 보고로 완료. 다른 속성·모드의 추가 검증은 별도 승인 필요
- 실제 Profile의 미제공 범위·등급·단위와 PM2 의미 확인
- 전원·측정 모드별 클라우드 캐시와 측정 신선도는 별도 설계·검증 필요
- MQTT·Hub·Pi 5 통합 및 실제 가전 종단 제어는 이번 범위 밖
- PR #56의 UI·폰트·에셋, PR #57 및 다른 worktree는 변경하지 않음

## 후속 설계 검토: PoC와 모델 독립성

DeviceRegistry·Capability·ThinQConnectAdapter는 PR #57의 공통 기반으로 보존한다.
현재 읽기 수집기는 probe/SDK를 주입받지만, 단일 AS183HWWA 선택과 FIELDS는 PoC 매핑이다.
다중 가전 등록·라우팅·센서 융합·Live Gateway가 완성됐다고 해석하지 않는다.

이번 최소 보완에서는 음수/백분율 범위를 FieldSpec의 minimum/maximum으로 옮겼다.
AS183HWWA의 PM·냄새·오염도·필터 검사 결과는 유지하면서, 합성 다른 가전의 온도처럼
음수가 정상인 숫자는 명시된 Profile과 매핑에 따라 허용한다. enum의 허용값 역시 Profile을
따르며 AS183HWWA의 모드 이름을 모든 기기에 강제하지 않는다.

다른 가전 fixture는 순수 합성이며 지원 기종을 늘리거나 실제 장치와 연결한 것이 아니다.
개별 수집기 인스턴스의 오류/캐시 격리와 PoC의 미지원 기기 선택 차단도 검사한다.
현재 snapshot은 필드별 API 속성·valid/status·수신 age·last_good·미확인 단위/측정 시각을
유지한다. 향후 공개 논리 장치 ID·단위 및 의미 검증 상태·융합 폴백은 MQTT 연결 전에 별도 설계한다.
읽기 허용을 제어 Capability나 쓰기 권한으로 확대하지 않으며, 자동 제어와 Live 경로는 비활성이다.

추가 검증은 89 passed(기존 85 + 신규 4), Ruff·포맷·공백 검사 PASS다.
합성 다른 가전의 숫자/enum 매핑, 인스턴스별 오류/캐시 격리 및 미지원 기기 선택 차단을
확인했다. 이는 다른 LG 기기의 실기 연동 성공을 뜻하지 않는다. 실제 API 재호출·UI·FSM·Gateway 변경은 없다.

## 2026-10-23 마무리 오프라인 회귀

이번 로컬 커밋 준비에서 Python 3.12.15 / thinqconnect 1.0.14로 다시 실행한 결과다.

| 범위 | 결과 |
|---|---|
| 수집 파서·캐시·범용 매핑 | 35 passed |
| 보존 수동 시험(네트워크 차단) | 27 passed |
| probe | 7 passed |
| ThinQ adapter | 15 passed |
| 저장소 내 소계 | 84 passed |
| 저장소 밖 기존 GET 실행기 | 5 passed |
| 저장소 밖 단발 GET 실행기 | 4 passed |
| 전체 관련 검증 | **93 passed** |

이전 85는 숫자/monitoring 파서 수정 시점의 결과이며, 범용 매핑/격리 테스트 4개를
추가한 뒤 89가 됐다. 이번 93은 그 89와 별도로 검증하던 단발 GET 실행기 테스트 4개를
함께 실행한 수치다. 저장소 밖 실행기·테스트·로그는 커밋에 포함하지 않는다.
Ruff E4/E7/E9/F/I, Ruff format --check, 문법·JSON·공백 및 민감정보 검사를 통과했다.

HTTP/socket 연결은 오프라인 테스트에서 차단했다. 사용자 실기 성공과 Fake HTTP/합성
Profile 검증은 별도로 취급한다. 이번 테스트·기록·커밋 작업에서 ThinQ GET/POST를 호출하지 않았다.
