# AS183HWWA ThinQ 단독 실기 검증 — 2026-10-22

## 증거 출처와 범위

이 문서는 사용자가 제공한 WSL 실행 로그 요약·최종 JSON과 **사용자의 실제 본체 물리 확인**을
근거로 작성했다. 기록 보존 작업에서 API를 다시 호출하거나 시험을 재실행하지 않았다.
원본 민감 로그·PAT·계정·외부 device_id는 수집하거나 저장하지 않는다.

**확인한 경로:** 사용자 WSL → ThinQ Connect SDK → LG ThinQ Cloud → AS183HWWA.
API 요청 접수, 별도 GET State 확인, 사용자 물리 확인을 구분해 검증했다.
Pi 4 Hub·MQTT·Gateway·Pi 5 UI 종단 제어가 성공했다는 의미는 아니다.

## 시험 환경

| 항목 | 당시 보고된 환경 |
|---|---|
| 실물 | LG 공기청정기 AS183HWWA — 사용자 확인 |
| API 기기 종류 | DEVICE_AIR_PURIFIER |
| API 모델명 | AIR_910604_WW |
| 호스트 | 사용자 WSL Ubuntu |
| Python / SDK | 3.12.15 / thinqconnect 1.0.14 |
| Python 관리 도구 | uv 0.13.5 |
| 작업 브랜치 / 사전 확인 HEAD | feat/device-control-platform-20261019 / 62eb1d9 |
| 관련 PR | Draft PR #57 — 이 문서는 PR 변경·병합 승인을 의미하지 않음 |
| 인증 | PAT를 터미널 숨김 입력으로 사용. 값은 보존하지 않음 |

실물 모델과 API 모델은 이번에 선택한 등록 기기에 대한 사용자 확인이다.
모든 AS183HWWA의 API 모델명이 항상 동일하거나 두 이름이 공식적으로 일대일 대응한다는
일반화는 하지 않는다. 정확한 요청·응답 시각, 지연 수치 및 전체 raw 응답은 제공받지 않았다.

## 실제 조회로 확인한 Profile·State

기기 목록·Profile·State는 사용자 보고상 모두 HTTP 200이다.
목록 요약의 controllable=null은 필드 누락과 구별되지 않으므로 제어 불가 판정에 사용하지 않았다.

| Profile 경로 | 읽기 / 쓰기 | 보고된 허용 값 |
|---|---|---|
| operation.airPurifierOperationMode | r / w | POWER_ON, POWER_OFF |
| airPurifierJobMode.currentJobMode | r / w | SLEEP, CLEAN |
| airFlow.windStrength | r / w | LOW, MID, HIGH, POWER, AUTO |
| airFlow.windStrengthDetail | r / w | OFF, LOW, MID, HIGH, POWER, AUTO |
| airQualitySensor.PM1 / PM1Level | r | 실제 응답의 농도·등급. 범위·단위는 이 기록에서 확정하지 않음 |
| airQualitySensor.PM2 / PM2Level | r | 동일 |
| airQualitySensor.PM10 / PM10Level | r | 동일 |
| airQualitySensor.odor / odorLevel | r | 실제 제공 속성 |
| airQualitySensor.totalPollution / totalPollutionLevel | r | 실제 제공 속성 |
| airQualitySensor.monitoringEnabled | r | 해당 기기의 보고된 Profile 기준 |
| filterInfo.filterRemainPercent | r | 조회 당시 96% |

PM·냄새·오염도 값도 실제 State에서 수신했다는 사용자 확인이 있다. 이 문서에는 전달받지 않은
농도 수치를 만들어 넣지 않는다. PM2는 ThinQ 통합 구현에서 PM2.5로 매핑되지만, **이 기기의
공식 API 단위·원본 Profile unit 및 측정/클라우드 갱신 주기는 추가 확인 대상**이다.
시험 프로그램의 GET 간격은 센서의 실제 갱신 주기를 뜻하지 않는다.

## 사용자 실행 절차와 관측 결과

당시 저장소 밖의 `/tmp/deskmate-as183hwwa-manual-20261022.py`를
`/tmp/deskmate-thinq-get-20261022/bin/python`으로 실행했다.
관련 오프라인 테스트는 `/tmp/test_deskmate_as183hwwa_manual_20261022.py`였다.
이 경로는 과거 이력이며 아래 보존본 실행에 필요하지 않다.

1. GET 사전 점검으로 대상이 정확히 한 대인지, windStrength의 LOW·AUTO 읽기/쓰기 지원,
   POWER_ON / CLEAN / AUTO 기준 상태를 확인했다. `preflight_passed`가 출력됐다.
2. 사용자가 `--execute` 모드를 실행하고, 제어용 PAT를 숨김 입력했다.
   LOW 직전에 명시적 승인 문구를 입력했다.
3. `airFlow.windStrength=LOW` 요청 후 `API_request_accepted`가 출력됐다.
4. GET State에서 POWER_ON / CLEAN / LOW, 상세 풍량 LOW를 확인했다.
5. 사용자가 실제 본체의 약풍 변경을 확인하고 `PHYSICAL LOW CONFIRMED`를 입력했다.
6. 별도 복원 승인 후 `airFlow.windStrength=AUTO` 요청이 접수됐다.
7. GET State에서 POWER_ON / CLEAN / AUTO, 상세 풍량 AUTO를 확인했다.
8. 사용자가 실제 본체의 자동 풍량 복원을 확인하고 `PHYSICAL AUTO CONFIRMED`를 입력했다.

| 단계 | power | job | windStrength | windStrengthDetail | 증거 |
|---|---|---|---|---|---|
| 시작 | POWER_ON | CLEAN | AUTO | AUTO | preflight_passed 및 사용자 제공 State 요약 |
| LOW 변경 후 | POWER_ON | CLEAN | LOW | LOW | API_request_accepted → GET → 본체 확인 |
| AUTO 복원 후 | POWER_ON | CLEAN | AUTO | AUTO | 별도 승인 → API 접수 → GET → 본체 확인 |

요청 속성은 **airFlow.windStrength 하나**였다. State의 windStrengthDetail도 바뀐 것을
관측했지만 상세 속성에 별도 POST한 것은 아니다. 전원·운전 모드는 변경하지 않았다.
POST의 정확한 HTTP status는 제공된 요약에 없으므로 HTTP 200/202 등을 임의로 기록하지 않는다.

사용자가 제공한 최종 로그:

```json
{
  "event": "trial_complete",
  "api_requests_accepted": true,
  "state_restored_AUTO": true,
  "physical_LOW_confirmed": true,
  "physical_AUTO_confirmed": true,
  "full_success": true
}
```

추가 자동 cleanup POST는 없었다. 이 결과는 **이 한 번의 AUTO → LOW → AUTO 단독 시험 성공**이다.
다른 풍량·운전 모드, 자동 제어, 연속 구동, 장애 복구 성공으로 확대 해석하지 않는다.

## 코드·테스트 보존과 재현

- 보존 코드: `tools/thinq_connect/manual_trial.py`.
- 보존 테스트: `tools/thinq_connect/test_manual_trial.py`.
- SDK 고정 의존성: `tools/thinq_connect/requirements.txt`.
- 실행·환경 준비: [ThinQ 도구 README](../tools/thinq_connect/README.md).
- 기존 probe를 직접 import한다. 외부 helper나 사용자 홈·임시 경로를 실행 의존성으로 사용하지 않는다.
- 기존 19개 오프라인 시나리오를 유지하고 CLI·숨김 입력·외부 cwd 검증을 추가했다.
- 시험 중 사용한 원본은 변경하지 않고 보존본을 만들었다. 아래 SHA256은 **보존 시점에 현존한
  임시 파일**에서 계산했으며, 사용자가 실기 실행 시 별도로 채집한 체크섬은 아니다.

```text
manual source: f0310b8602bbe593c95fe9f5ec44a3e7f0e7f87aab8e125dad12614b4c29333f
offline tests: e7cd4ffbd1ff8248b8b948fed0b290c3987ee2bf628bbb1ef32d19929b31ea65
```

보존본의 동작 안전장치:

- 기본/default와 --preflight는 GET 전용, --check는 API 요청 없이 환경만 확인.
- --execute도 LOW·AUTO 각각 정확한 승인 문구가 필요하며, 재실행에는 별도 실기 승인이 필요.
- 대상·KR HTTPS host·endpoint·payload를 제한. windStrengthDetail, 다른 기기, redirect 차단.
- 실행 직전과 복원 직전에 Profile·State를 재조회해 설정 변경 시 중단.
- POST 응답 유실·타임아웃 뒤에는 GET만 수행하고 추가 POST·자동 복원을 하지 않음.
- API 접수, State 일치와 사용자 물리 확인을 독립적으로 기록.
- PAT는 숨김 입력만 사용하고 환경변수 THINQ_PAT는 사용하지 않음. 파일·원본 로그 저장 기능 없음.

HTTP 성공만으로 최신 온라인 상태를 확정하지 못한다. 조회와 POST 사이 외부 조작을 완전히
차단하는 원자적 비교도 제공하지 않는다. 따라서 물리 온라인 확인과 외부 조작 부재에 대한
사용자 확인이 필요하다. 같은 값으로 돌아온 외부 조작이나 조회 사이의 조작은 감지하지 못할 수 있다.
취소·불명확한 응답 뒤에는 실제 기기를 확인하고 필요한 복원을 사용자가 판단해야 한다.

## 이번 보존 작업의 오프라인 검증

2026-10-22 보존 작업에서 아래를 실제 실행했다. 실제 API 요청·제어는 수행하지 않았다.
실행 환경: Python 3.12.15, thinqconnect 1.0.14, pytest 8.4.2, Ruff 0.14.1.
검사 도구는 격리된 개발 가상환경에만 추가했고 시스템·PlatformIO Python을 변경하지 않았다.

| 검사 | 실행 결과 |
|---|---|
| unittest 보존본 | 27개 PASS: 기존 19개 + CLI 8개 |
| pytest 관련 회귀 | 49 passed: 수동 시험 27 + 기존 probe 7 + ThinQ adapter 15 |
| --check | 버전·SDK import·저장소 probe import PASS, API 호출 없음 |
| Ruff E4/E7/E9/F/I | PASS |
| Ruff format --check | 2 files already formatted |
| 원본/보존본 AST 비교 | 경로 제거·main import 재연결 외 기존 시험 안전성 함수/클래스 및 원래 테스트 19개 본문 동일 |
| 민감정보 정적 검색·검토 | PAT/JWT/개인키/계정 이메일·실제 기기 ID·원본 민감 로그 미포함. 테스트 자격증명·대상은 합성 문자열 |
| 공백 검사 | git diff --check와 신규 파일 공백·EOF 검사 PASS |
| 기존 기능 보존 | probe·ThinQ adapter·Gateway 소스가 HEAD와 바이트 동일. hub/display/firmware diff 없음 |

재현 명령은 저장소 루트에서 준비된 Python 3.12.15 가상환경을 사용한다.

```bash
"$THINQ_TEST_PY" -B -m unittest tools.thinq_connect.test_manual_trial -v
PYTHONPATH=.:hub "$THINQ_TEST_PY" -B -m pytest -p no:cacheprovider -q \
  tools/thinq_connect/test_manual_trial.py tools/test_thinq_connect_probe.py hub/tests/test_thinq_adapter.py
"$THINQ_TEST_PY" -B -m tools.thinq_connect.manual_trial --check
ruff check --no-cache --select E4,E7,E9,F,I tools/thinq_connect/manual_trial.py tools/thinq_connect/test_manual_trial.py
ruff format --check --no-cache tools/thinq_connect/manual_trial.py tools/thinq_connect/test_manual_trial.py
git diff --check
```

unittest와 pytest의 27개는 같은 테스트이므로 합산 76개로 세지 않는다.
새 수동 시험 테스트는 실제 SDK의 요청 조립/오류 변환만 사용하고 session·응답을 합성한다.
실제 aiohttp 요청과 socket connect를 차단하며, 요청 유실·재전송 방지·물리 확인은 모의 검증이다.
전체 Hub·Flutter 테스트는 이 보존 작업에서 재실행하지 않았다. 이 결과는 과거 실기 재실행이나
운영 Gateway/장애 복구 검증이 아니다. Git staging·commit·push와 실기 배포도 수행하지 않았다.

## 미검증 범위와 다음 읽기 전용 단계

아직 Pi 4 Hub 제어, 실제 MQTT Gateway 연결, Pi 5 실기 수락/결과 표시, Live journal·재시작,
장애 복구 및 FSM에 의한 실제 제어는 검증하지 않았다. 기존 Mock Gateway, Hub·Flutter 제어,
기존 default OFF·자동 제어 OFF 설정은 이번 보존 작업에서 변경하지 않는다.

다음은 **읽기 전용 공기질 MQTT 연동 설계·구현을 별도 승인받은 뒤** 진행한다.

1. full Python Linux 호스트에서 한 대만 조회. 읽기 전용 PAT·SDK GET·poll 주기를 분리 관리.
2. PM/냄새/필터와 기기 상태, 조회 수신 시각, 확인 가능한 경우 측정 시각·단위·유효성을 정규화.
   HTTP 200이나 조회 수신 시각을 센서의 새 측정 시각으로 취급하지 않는다.
3. Gateway → Hub 수집 계약을 기존 코드와 대조해 정하고 `docs/mqtt-topics.md`·테스트에 반영.
   control/result를 상시 센서 텔레메트리 통로로 사용하지 않는다. 이 단계에서 topic을 확정하지 않는다.
4. Hub가 state/phase의 data.sensor_summary에 표시용 요약을 만들고 Pi 5가 기존 경로로 표시.
   ESP32 CO2·온습도·조도와 source/최신성/유효성을 구별하고 누락을 0으로 바꾸지 않는다.
5. 연결 끊김·stale·미지원 필드·재연결을 Mock으로 검증한 뒤 실제 읽기 전용 종단을 확인.

CO2 상승 자체를 공기청정기 풍량 변경 근거로 사용하지 않는다. PM 기반 판단·제어 정책,
Live Gateway·journal, 사용자 수락/취소/복원 계약은 후속 설계이며 이번 단계에서 활성화하지 않는다.
