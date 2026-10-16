# 개입 한 사이클 검증

2026-10-06: 사용자가 통신 점검 완료를 확인했다. 다음 단계인
착석 → 집중 → 피로 → 제안 → 응답 → 제어 → MONITOR → 회복 → 리포트를 검증한다.

## 완료한 소프트웨어 검증

`tools/test_intervention_mqtt.py`는 임시 loopback MQTT 브로커와 제품 `LiveHub`·`MqttSource`,
외부 MQTT mock 응답기를 연결해 아래 다섯 경로를 검사한다. 시연 프로파일의 타이머만
가상 시계로 진행하며, 제품 임계값·통신 계약·운영 설정은 변경하지 않는다.
실센서와 Pi 5 응답은 합성이므로 실기 통과를 의미하지 않는다.

```powershell
python -m pytest tools/test_intervention_check.py tools/test_intervention_mqtt.py -q
```

| 경우 | 확인한 결과 |
|---|---|
| accept | 일치하는 request_id 수락 뒤 설정된 팬·조명 명령, 성공 응답·actual_value, 회복, 리포트 수용률 1 |
| reject | 일치하는 질문 거절 뒤 제어 명령 없음, 회복, 리포트 수용률 0 |
| timeout | 화면 만료 응답 뒤 제어 명령 없음, 회복, 리포트 무응답률 1 |
| undo | 수락 실행 후 되돌리기 명령·성공 결과, mock 팬 OFF·조명 원래 값 |
| correct | REST 정정이 ESM·리포트에 기록, 같은 센서 입력의 대조군과 FSM 전이 일치 |

결과 누락·실행 실패·actual_value 불일치·잘못된 request_id·과거 세션 리포트·hub 재시작은
검증 실패로 처리한다. 과거 retained 상태와 리포트로 현재 실행을 통과시키지 않는다.

## 실센서 실행: 통신 점검 다음부터

`tools/intervention_check.py`는 상태·질문·피드백·제어 명령·결과·리포트만 구독하는 **읽기 전용** 도구다.
센서, 피드백, 제어 메시지를 발행하지 않고 raw 센서를 기록하지 않는다.
결과는 gitignore 대상 `logs/intervention/<시각>-<case>/`에 저장한다.

1. 같은 시점의 최신 hub·Pi 5 앱을 사용한다. 브로커·배선은 확인한 구성을 유지한다.
2. 실제 제어 기기 대신 mock으로 확인할 때만, 같은 브로커에서 `mock_plug.py`를 실행한다.
   실제 어댑터와 mock을 동시에 같은 제어 토픽에 붙이지 않는다.
3. 아래 감시 명령을 먼저 시작하고 새 hub 실행에서 착석한다. 각 경우는 **새 hub 실행**으로 나눈다.
   현재 리포트는 프로세스 실행 중 누적되므로 수락·거절 등을 한 실행에 섞으면 이 도구의 단일 경우 판정과 맞지 않는다.
4. Pi 5에서 응답하고 다시 타이핑해 회복한다. 회복 뒤 리포트 갱신을 확인한 다음 PC에서 Ctrl+C로 기록을 끝낸다.

```powershell
# mock 사용 시에만 별도 터미널. 실제 어댑터가 있으면 생략한다.
python tools/mock_plug.py --broker <PI4_IP>

# 실센서 + 보드 hub + Pi 5는 그대로 두고 관측한다(운영 타이머는 15분 이상 걸릴 수 있다).
python tools/intervention_check.py --broker <PI4_IP> --case accept --seconds 1200
```

센서 동작·빠른 시연 프로파일로 PC hub를 사용하는 방법은
[`field-checklist.md` §3](field-checklist.md#3-fsm-대표-경로-실센서)를 따른다.
판정기가 기대하는 환경 제안을 보려면 자세 개입 후 회복되지 않는 재시도 경로가 필요하다.
환경 제어가 처음부터 자동으로 실행된 경우를 이 도구의 제안 수락 통과로 간주하지 않는다.

| --case | Pi 5에서 할 일 |
|---|---|
| accept | 환경 제안 카드에서 적용할게요 |
| reject | 환경 제안 카드에서 괜찮아요 |
| timeout | 환경 제안 카드가 만료될 때까지 응답하지 않음(앱이 timeout 전송) |
| undo | 환경 제안을 수락하고 실행 확인 후 되돌리기 창 안에 되돌리기 |
| correct | 환경 제안 수락 + 지금 상태가 아니에요에서 쉬는 중 선택 |

자동 판정이 PASS여도 Pi 5 카드 표시·터치 동작과 실제 램프/팬 동작은 눈으로 확인해
현장 기록표에 별도로 적는다. mock PASS는 실제 기기 제어 성공을 의미하지 않는다.
디스플레이가 아예 꺼져 앱의 timeout을 보내지 않는 경우는 기존 만료 회귀 시험에서 별도로 다룬다.

## 결과 확인

`events.jsonl`은 토픽별 증거, `result.json`은 통과 여부와 실패 항목이다.
관측을 START 이후에 시작했거나 회복 전에 끝내면 부족한 증거를 실패로 표시한다.
종료 코드 0은 모든 조건 통과, 1은 실패 또는 증거 부족이다.

```powershell
# 기록 후 동일한 기준으로 재평가
python tools/intervention_check.py --events logs/intervention/<실행>/events.jsonl --case accept
# 현장 control.yaml의 대상·명령 값이 다르면 동일 설정을 지정
python tools/intervention_check.py --broker <PI4_IP> --case accept --control-config <control.yaml>
```

| 경우 | 자동 판정 | Pi 5 표시·터치 | 실제 기기 / mock | 증거 폴더 |
|---|---|---|---|---|
| accept | | | | |
| reject | | | | |
| timeout | | | | |
| undo | | | | |
| correct | | | | |

실측 증거가 채워지기 전까지 로드맵의 실센서 통과 항목은 미체크로 유지한다.

## 2026-10-06 마무리 구현

별도 로컬 검증 도구를 `feat/personalization-runtime` 작업 트리에 통합했다. 기존 다섯 응답 경로에 화면 무응답·제어 실패·제어 결과 누락을 추가하여 loopback MQTT에서 검증한다. 운영 임계값은 바꾸지 않고 시연 프로파일과 가상 시계로 시간을 진행한다.

- 제안 수락 시 명령 유효 시간을 실제 발행 시각부터 새로 계산한다. 제안 생성 때의 시간이 지나 있어도 유효한 수락을 실행할 수 있다.
- hub가 질문 만료를 처리하고 ESM에 source=hub, verdict=timeout을 한 번 기록한다. 화면에서 만료를 보내지 않아도 리포트 무응답률에 반영한다.
- 피드백·결과의 hub 수신 시각을 메모리에 보관한다. 기한 전에 도착해 다음 tick에서 처리된 응답은 유지하고, 늦은 수락·결과는 이미 만료된 상태를 되살리지 않는다.
- MONITOR로 넘어간 에피소드의 되돌리기도 매 tick 결과 타임아웃을 처리한다. 되돌릴 명령이 없으면 되돌림으로 집계하지 않는다.
- 성공 응답에 actual_value가 명시되어 있고 요청 값과 다르면 실패로 처리한다. actual_value 누락을 허용하는 기존 결과 수신 경로는 유지하지만, 현장 판정기는 일치하는 actual_value가 있어야 PASS로 인정한다.
- session/report.data.control_results에 forward와 undo를 나누어 total·executing·succeeded·failed·timeout·cancelled 수를 발행한다. 개별 기기·명령 ID·실제 값은 리포트에 추가하지 않는다.
- 판정기는 두 세션이 섞인 기록, 같은 ID의 다른 명령, 실패 결과 뒤 성공 재응답, 제어 결과 집계가 없는 리포트를 PASS로 인정하지 않는다. 동일 상태의 반복 tick과 동일 명령 재전송은 별개 세션·명령으로 세지 않는다.

화면 무응답 시험은 hub의 timeout 기록과 제어 미실행을 확인한다. 실제 화면의 timeout 응답이 관측되지 않으므로 이 기록을 현장 timeout 카드 시험 PASS로 대체하지 않는다. 제어 실패·결과 누락 시험에서는 MONITOR·RECOVERY로 진행하더라도 실제 제어 성공 판정은 FAIL이며, 리포트에 failed·timeout이 남아야 한다.

실행 명령:

```powershell
$env:PYTHONPATH='hub;ml;collector'
python -m pytest hub/tests/test_intervention_completion.py tools/test_intervention_check.py tools/test_intervention_mqtt.py -q
```

PC 검증은 실제 Pi 5 렌더링·터치, 실센서 입력, 실제 기기 제어 및 Pi 4 native MQTT 실행을 대신하지 않는다. 이 작업은 mock 기반 소프트웨어 사이클 완료이며 실기 항목은 미검증으로 유지한다.

MQTT 통합 시험은 PC에 amqtt 0.12.0·paho-mqtt 2.1.0이 필요하다. 없는 환경에서는 해당 시험이 skip될 수 있으므로 통합 검증 결과로 세지 않는다. 이번 PC에서는 두 의존성을 설치한 환경에서 실제 loopback 브로커 왕복 시험을 실행했다.

```powershell
python -m pip install -r tools/requirements.txt amqtt==0.12.0
```

수신 큐의 정정·수락 등 연속 응답은 같은 tick에서 순서대로 처리한다. 한 응답씩 지연시켜 아직 유효한 다음 응답을 만료시키지 않는다.

최종 PC 검증: hub·ML·dataset·collector·tools의 명시적 테스트 경로에서 **318 passed, 1 xfailed, 41 warnings**. xfailed는 기존 항목이고 warning은 TensorFlow/Keras 의존성의 deprecation 안내다. loopback MQTT 시험은 skip 없이 실행했다. 기록 판정 CLI는 합성 증거 5종에서 종료 코드 0, 빈 증거에서 종료 코드 1을 확인했다. 실제 기기 제어는 수행하지 않았다.
