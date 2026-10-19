# 독립 Mock DeviceGateway — Linux 호스트용

이 진입점은 합성 기기의 소프트웨어 시험 전용이다. Pi4/Pi5 배포, 실센서 E2E,
ThinQ 계정·실가전 제어를 검증한 것으로 해석하지 않는다.
기존 `DeviceGateway.attach()/pump()/poll()/close()`와 `CommandJournal`을 사용한다.
Hub/FSM/사용자 수락/Dispatcher를 대신하거나 UART를 열지 않는다.

## 시작과 종료

일반 Ubuntu/Python 3.10 이상에서 저장소 root 기준으로 실행한다.
별도 가상환경에 필요한 의존성만 준비한다. SDK·시스템 패키지 설치는 이 서비스의 작업이 아니다.

```bash
python3 -m venv /tmp/deskmate-mock-gateway-venv
source /tmp/deskmate-mock-gateway-venv/bin/activate
python -m pip install 'paho-mqtt>=2.0,<3' 'PyYAML>=5.4'

PYTHONPATH=hub python -m deskmate_hub.control.gateway_service \
  --mock --enable \
  --config hub/deskmate_hub/config/mock_gateway.yaml \
  --broker-host 127.0.0.1 --broker-port 18883 \
  --journal "$HOME/.local/state/deskmate/mock-gateway/commands.db"
```

- `PYTHONPATH=hub`: 이 저장소의 Hub 패키지를 로딩한다.
- `--mock`: Mock 전용 실행을 명시한다. 생략하면 네트워크·journal 생성 전에 거절한다.
- `--enable`: config의 `enabled: false`를 이번 프로세스에서만 덮어쓴다.
  config를 true로 만들어도 `--mock`는 항상 필요하다.
- `--config`: YAML 설정 경로. mode=mock, automatic_control=false만 허용한다.
- `--broker-host/--broker-port`: config보다 우선하는 연결 대상. 기본은 localhost:18883이다.
- `--journal`: 절대 경로 SQLite 파일. config의 journal 경로보다 우선한다.
  명시적 경로가 없으면 시작하지 않는다.

기본 실행이나 `--mock`만으로는 시작하지 않는다. 자동 서비스 등록도 하지 않는다.
`Ctrl+C` 또는 해당 Gateway PID에 `kill -TERM <PID>`로 정상 종료한다.
MQTT disconnect/네트워크 스레드 정지 후 SQLite와 독점 lock을 닫는다. journal은 삭제하지 않는다.
SIGKILL·전원 손실 뒤에도 SQLite 복구와 OS 파일 lock 해제를 이용한다.
로그는 고정된 Mock 수명주기 코드만 출력하며 payload, 기기 ID, 주소, 인증값, 예외 본문을 출력하지 않는다.
ThinQ SDK, PAT, aiohttp는 필요하지 않다. SQLite/fcntl/asyncio는 Linux Python 표준 라이브러리다.

## 개발 브로커와 Pi4 연결 분리

기본 `profile: localhost`는 127.0.0.1/::1/localhost 외 주소를 거절한다.
로컬에 이미 준비된 MQTT 브로커를 사용한다. 예를 들어 별도 개발 Mosquitto의 config는 다음처럼
loopback에만 listener를 둔다. 이 문서는 실행 예시이며 보드 브로커 설정을 변경하지 않는다.

```conf
listener 18883 127.0.0.1
allow_anonymous true
persistence false
```

이 config를 `/tmp/deskmate-mock-mosquitto.conf`로 준비했다면 별도 터미널에서
`mosquitto -c /tmp/deskmate-mock-mosquitto.conf`로 실행할 수 있다.
실제 테스트는 기존 amqtt helper가 선택한 임시 loopback 포트만 사용한다.

Pi4 연동은 **별도 승인 및 사전 점검 후** 로컬 config 복사본에 `profile: pi4`,
현재 확인한 broker 주소/포트와 다음 항목을 설정한다. 과거 DHCP 주소를 기본값으로 사용하지 않는다.

```yaml
broker:
  profile: pi4
  host: <현재 확인한 broker DNS 또는 IP>
  port: <TLS MQTT 포트>
  username_env: DESKMATE_MOCK_MQTT_USERNAME
  password_env: DESKMATE_MOCK_MQTT_PASSWORD
  tls_ca: /absolute/path/to/broker-ca.pem
```

비밀번호를 YAML/실행 인자/채팅에 넣지 않고 로컬 숨김 입력 또는 비밀 관리 도구로
환경변수에 전달한다. TLS CA 검증을 생략하거나 `tls_insecure`를 사용하지 않는다.
현재 Pi4의 익명 TCP 1883이 이 조건을 만족한다고 가정하지 않는다.
인증·TLS가 없는 기존 브로커는 이 원격 profile로 바로 실행할 수 없다.
loopback SSH 터널 같은 대안도 승인·접근제어 검토 후 별도로 결정한다.

브로커 ACL은 최소한 다음 소유권을 보장해야 한다.

- Hub만 `deskmate/control/cmd` publish, 사용자 UI만 기존 feedback/user publish.
- Gateway는 control/cmd subscribe와 control/result publish만 허용.
- 결과 publish 권한을 받은 다른 controller와 target 소유권이 겹치지 않도록 한다.

`node: hub`는 인증 수단이 아니다. MQTT ACL은 topic 수준이므로 target별 payload 소유권은
Gateway의 등록 목록과 controller별 ACL·신뢰 경계로 함께 관리해야 한다.
동일 broker에서 같은 mock target들을 여러 journal/Gateway가 동시에 소유하면 안 된다.
기존 topic이나 Hub 수락/게이트 계약을 변경하지 않는다.

## Mock 명령과 상태

등록은 `mock_gateway.yaml`의 `devices` 목록으로 한다. target은 `mock_*`만 허용한다.
기본 목록은 mock_lamp, mock_vent, mock_cooling이며 실제 모델/외부 기기 ID는 사용하지 않는다.
서비스는 등록 target이면서 `data.execution_mode: mock`인 명령만 core Gateway로 전달한다.
marker 누락/live/기존 desk_lamp 등의 명령은 무시하여 기존 BLE controller와 경쟁하지 않는다.
Mock 명령이라고 표시해도 node/gate/만료/operation 검증은 기존 Gateway가 다시 수행한다.

- subscribe: `deskmate/control/cmd`, QoS1.
- publish: `deskmate/control/result`, QoS1, retain=false.
- 결과 node는 `device-gateway-mock`. 기존 envelope와 command_id/status/actual_value를 유지한다.
- 같은 ID/같은 내용은 저장된 결과만 재발행한다. 바뀐 내용의 동일 ID는 무시한다.
- retained·만료 명령은 실행하지 않는다. AUTO는 항상 비활성이다.
- 요청 접수와 readback 상태 확인을 구분한다. Mock 상태 확인은 물리 기기 작동의 증거가 아니다.
- 실제 Hub의 신규 EnvironmentControlPolicy 기본 비활성은 바꾸지 않는다.
  Mock Hub 실험에서만 기존 MockControlTransport를 통해 명령을 연결한다.
  일반 Hub를 시작하는 것만으로 이 서비스까지 계획이 자동 연결되지는 않는다.

## journal과 장애 처리

명령 claim/실행 의도/최신 결과는 기존 SQLite journal에 동기식 저장한다.
생성 디렉터리는 현재 사용자 소유 0700, DB·lock은 0600이다. 기존 디렉터리/파일이 공유 권한이거나
심볼릭 링크면 거절하며 자동 chmod/chown하지 않는다. 프로세스 umask=0077로 SQLite 부속 파일도 보호한다.
같은 journal은 한 프로세스만 사용한다. 경로·저장 공간·디스크 오류 시 fail-closed로 종료한다.

`$HOME/.local/state/...`처럼 영속 디스크를 사용하면 재부팅 후에도 명령 이력을 유지할 수 있다.
`/tmp`, tmpfs, 컨테이너 임시 계층은 재부팅/재생성 뒤 보존을 보장하지 않는다.
journal은 중복 방지 이력이므로 성공 명령이 있어도 임의 삭제·용량 제한을 위한 ID 제거를 하지 않는다.
core의 1024개 명령 한도에 도달하면 새 명령을 거절한다. 시연 전 여유 용량을 확인하고,
계속 운영할 보관·교체 정책은 별도 설계해야 한다.

네트워크 재연결은 1~30초 backoff이고 매번 다시 구독한다.
연결이 끊기면 새 대기 명령은 실행하지 않고 진행 중 결과 조회/만료 처리만 한다.
기기 명령 POST는 재시도하지 않는다. 재접속·프로세스 재시작 시 저장된 **결과만** 재발행한다.
result는 중복될 수 있으며 Hub는 command_id와 원래 기한으로 판정한다.
종료 시 결과 전송 완료를 무기한 기다리지 않으며, 저장된 결과는 다음 시작에서 다시 알린다.

Paho의 메모리 수신 queue는 journal이 아니다. 수신 ACK 뒤 claim 전 프로세스가 죽거나,
연결 중단 중 MQTT 세션이 사라지면 명령이 누락되어 Hub timeout이 될 수 있다.
명령을 무조건 재발행하거나 안전성을 위해 새 ID를 만들지 않는다.
미완료 claim은 재시작 후 readback으로만 확인하고 기한이 지나면 timeout 처리한다.
Mock adapter의 기기 상태 자체는 메모리이며 재시작 시 initial_state로 돌아간다.
완료된 명령 결과 재발행은 새 기기 동작이 아니다. 미완료 상태가 복구 후 맞지 않으면 재실행하지 않는다.
Hub가 먼저 timeout한 결과를 뒤늦게 succeeded로 뒤집지 않는 기존 Dispatcher 규칙을 유지한다.

## 검증 경계와 배치

독립 프로세스 테스트: 시작/종료(SIGINT·SIGTERM)/SIGKILL 후 복구, 로컬 MQTT 왕복·재연결,
중복·retained·만료·AUTO/live 차단, journal 독점/복구, Gateway 종료와 네트워크 단절 시 Hub timeout.
기존 Hub–MQTT–Flutter–Mock Gateway 회귀는 별도 기존 시나리오로 재실행한다.
생산 설정·실센서·실가전·Pi4/Pi5 실기 동작은 이 시험에 포함하지 않는다.

시연은 일반 Ubuntu 노트북이 가장 빠르다(기존 venv/로그 확인이 쉽고 절전·연결 해제에 유의).
장기 시연은 별도 일반 Linux 소형 호스트가 적합하다(영속 디스크·전용 운영 환경 필요).
ATLAS Pi4 제한 Python에는 socket/SDK/journal 의존성을 가정하지 않는다.
Pi5도 full Linux Python을 별도로 확인하기 전 Gateway 호스트로 간주하지 않는다.

## 로컬 검증 기록 (2026-10-19)

기능 브랜치 `feat/device-control-platform-20261019`, HEAD `95b30fa` 위의 미커밋 변경을 시험했다.
최신 main을 이 브랜치에 merge/rebase하지 않았다.

- Python Hub 전체 + ThinQ probe Mock: **389 passed, 1 xfailed** (80개 amqtt 관련 폐기 예정 경고).
  기존 XFAIL은 `test_missing_signals_must_not_let_posture_alone_confirm`이며 변경하지 않았다.
- 새 진입점/프로세스 시험 **28개** 포함. 실제 Hub/FSM의 저조도 제안 수락 → 동일 command_id/
  target/operation/value/deadline → 독립 Gateway → readback 결과 → MONITOR를 추가 확인했다.
  Hub 시계는 기존 fixture의 가속 합성 시계이며 실제 사용자 시간/센서 검증이 아니다.
- Flutter 전체 **232 passed**, 정적 분석 **No issues found**.
- 새 Python export를 Flutter에 재생한 제어 계약 **9 passed**.
- 기존 실시간 Hub–MQTT–Flutter Widget–Mock Gateway 시나리오 통과: 제어 1회,
  readback 확인, Mock 결과 표시. 이 회귀 드라이버의 Gateway는 기존 프로세스 내 인스턴스이며,
  독립 프로세스의 실제 Hub 왕복은 앞의 별도 Python 통합 시험에서 검증했다.
- Flutter는 기존 개발 컨테이너의 `/tmp` 사본에서 시험했다. 보드/LAN/ThinQ 실계정은 접근하지 않았다.

## 최종 리뷰와 독립 프로세스 E2E (2026-10-19)

위 초기 검증 뒤의 추가 리뷰 기록이다. HEAD `95b30fa`와 브랜치를 그대로 유지했다.
기존 6개 미커밋 파일에 테스트 하네스 `hub/tests/control_process_e2e.py` 및
`hub/tests/test_control_process_e2e.py`를 추가했다. 실제 staging/commit/push/PR 수정은 하지 않았다.

발견·보완한 문제:

- CONNACK 거절을 명시적으로 처리하고 SUBACK 실패 시 대기만 하던 경로를 수정했다.
  연결/구독 거절은 명령 실행 없이 종료 코드 1로 종료하며 MQTT·journal을 정리한다.
  TCP 접속 실패·기존 연결 단절은 기존 1~30초 backoff로 재시도한다.
- JSON의 NaN/Infinity 및 오버플로 숫자를 journal claim 전에 버린다.
  잘못된 한 메시지가 SQLite의 allow_nan=false 저장 오류로 서비스를 종료시키지 않는다.
- journal 상위 경로의 심볼릭 링크, DB/lock의 hardlink, SQLite sidecar의 링크·권한도 검사한다.
  기존 파일의 권한·소유자는 바꾸지 않는다.
- E2E 하네스는 실제 MQTT에서 확인한 MONITOR 완료 이력을 보관한다.
  Flutter가 결과를 확인하는 동안 FSM이 다음 상태로 이동해도 이미 확인한 완료를 놓치지 않는다.
  FSM을 고정하거나 전이를 바꾸지 않는다. summary의 fsm_state는 관측한 완료 단계이며,
  current_fsm_state는 검사 종료 직전 실제 상태다.

### 재현

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:hub \
  python -m pytest -q -ra hub/tests tools/test_thinq_connect_probe.py

# 별도 Hub/Gateway + 실제 localhost MQTT + headless feedback 하네스
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:hub:hub/tests \
  python hub/tests/control_process_e2e.py run

# 기존 atlas-dev 내부 /tmp에 준비한 앱 사본에서 Flutter 위젯 클라이언트까지 연결
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:hub:hub/tests \
  python hub/tests/control_process_e2e.py run \
    --flutter-app /tmp/deskmate-gateway-review/display/atlas/ui_env_app
```

마지막 명령은 기존 개발 컨테이너의 명시적 `/tmp` 앱 사본을 요구한다. 자동 Pi5 배포나
새 컨테이너 생성은 하지 않는다. 사본은 ui_env_app과 pubspec의 경로 의존 camtest 소스로 준비하고
그 사본에서 `flutter pub get --offline`을 수행한다. 시험 후 이번 `/tmp` 앱 사본을 정리했다.

### 확인 결과와 대체 요소

- Python 전체 **400 passed, 1 xfailed**. 기존 자세 관련 XFAIL과 amqtt 폐기 예정 경고는 유지했다.
  Gateway 단위/독립 프로세스 시험 38개 및 별도 Hub/Gateway headless E2E 1개를 포함한다.
- Flutter 전체 **232 passed**, 정적 분석 **No issues found**.
- 별도 Hub PID 19663, 별도 Gateway PID 19661, 별도 Flutter 테스트 launcher PID 19693으로
  실제 localhost MQTT E2E 통과. PID는 해당 실행의 관측값이며 재사용 대상이 아니다.
- Flutter의 실제 MqttStateSource/DisplayState/EnvironmentControlSuggestion 위젯이
  요청을 파싱하고 버튼을 누른 뒤 feedback/user를 발행했다. request_id가 서버 활성 제안과 일치했다.
- Hub는 실제 LiveHub/FSM/EnvironmentControlPolicy/ControlDispatcher를 테스트 하네스에서 기동했다.
  센서·기기 입력은 합성이고, prewarm 구간만 가속 합성 시계로 재생한 후 wall clock을 사용했다.
  기존 Simulation과 같은 시험용 증거 가중치를 사용하며 제품 YAML/가중치/게이트를 수정하지 않았다.
- Gateway는 실제 gateway_service 모듈의 __main__을 별도 프로세스에서 실행했다.
  테스트 전용 Mock 어댑터 래퍼가 원본 control()에 그대로 위임하면서 호출 횟수만 기록한다.
  응답·상태 확인 결과를 대신 생성하지 않는다.
- MQTT command 1개, Mock adapter control 호출 1회, mock_lamp.set_brightness=70,
  journal의 api_accepted/state_confirmed 및 succeeded 결과, Flutter의 MOCK 결과 표시와
  MONITOR 메시지를 확인했다. command_id/target/operation/value/deadline을 전송 중 바꾸지 않았다.
- 컨테이너 양쪽 loopback TCP는 기존 테스트의 stdin/stdout byte pipe로 연결했다.
  LAN listener, 실제 Pi4 broker/ThinQ 계정, UART/ESP32, Atlas native launcher, Pi5 실제 화면은 검증하지 않았다.
  일반 pytest의 headless E2E만 Flutter interaction을 feedback 하네스로 대체하며 별도 Flutter 실행은 실제 위젯을 쓴다.

### 운영 전 남은 조건

Pi4 접속은 별도 승인 뒤 현재 주소, 인증 계정, CA/호스트명 검증, topic ACL과 controller 소유권을
확인해야 한다. MQTT 3.1.1 PUBACK에는 publish ACL 거절 이유가 없으므로 SUBACK 통과만으로
result publish 권한을 입증할 수 없다. 실제 broker에서 권한별 왕복 확인이 추가로 필요하다.
TLS/실제 broker 인증 설정 자체는 이번 로컬 가짜 거절 응답 시험으로 검증한 것이 아니다.

기본 enabled=false/mode=mock/automatic_control=false와 Hub 정책의 비활성 기본값은 유지한다.
영속 private journal과 단일 소유자가 필요하며, PC/Gateway 종료 시 Hub의 기존 timeout을 유지한다.
Mock 상태는 재시작 시 초기화되므로 journal 결과 재발행을 실제 가전 상태로 해석하지 않는다.
이 변경은 `feat(control): add opt-in standalone mock gateway service` 후속 커밋 후보이며,
실기 배포·실제 제어 및 main 병합 승인을 의미하지 않는다.
