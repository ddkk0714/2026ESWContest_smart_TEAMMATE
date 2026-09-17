# Pi 4 Atlas Hub service

Pi 4 Headless 프로파일에서 기존 Python FSM Hub를 실행하는 native-service IPK다.
SSH 셸의 기본 AppArmor 프로파일은 `/restricted/python3` 실행을 거부하므로,
시스템 정책을 변경하지 않고 Atlas 서비스 샌드박스 안에서 제한 Python을 실행한다.

## 빌드

저장소 루트가 `/workspace`로 마운트된 Atlas 개발 컨테이너(`deskmate-atlas-dev:local`, `display/atlas/compose.yaml`)에서
실행한다. SDK 환경 스크립트를 source 해야 cmake·크로스 컴파일러가 PATH 에 오르고, 컨테이너 Python 에 PyYAML·paho 가 있어야
payload 를 만든다 — 이 순서를 `tools/build_ipk.sh` 가 한다 (09-16 실보드 설치 확인).

```bash
# PC 저장소 루트에서 (Windows Git Bash 는 MSYS_NO_PATHCONV=1 와 "$(pwd -W)")
docker run --rm -v "$PWD:/workspace" -w /workspace deskmate-atlas-dev:local bash hub/atlas/tools/build_ipk.sh
# → hub/atlas/build/arm64/ipk/com.deskmate.hub1.ipk
```

ARC 0.5가 서비스 실행 파일 하나만 패키징하므로, 빌드 시 Hub를 실행 파일 뒤쪽의
zip payload로 포함한다. Atlas 제한 Python에는 HTTP/JSON 실행에 필요한 순수 표준
모듈도 다수 빠져 있어, 빌드 컨테이너의 동일한 Python 3.12 순수 표준 라이브러리를
함께 넣는다. 네이티브 확장과 개발·테스트 패키지는 포함하지 않는다. PyYAML이
요구하는 일부 모듈도 없으므로 `fsm.yaml`은 빌드 시 JSON으로 변환한다. 이 JSON은
빌드 산출물이며, FSM 임계값과 가중치의 원본은 계속
`hub/deskmate_hub/config/fsm.yaml` 하나뿐이다.

## 설치 및 실행 확인

`arc install` 은 내부적으로 scp + 보드의 `abusctl call com.atlas.PackageManager1 Install` 이다(컨테이너에서는 TTY 가 필요해
`arc devices add` 가 막히므로 PC 의 ssh 로 같은 일을 한다):

```bash
ssh atlas 'mkdir -p /tmp/arc' && scp hub/atlas/build/arm64/ipk/com.deskmate.hub1.ipk atlas:/tmp/arc/
ssh atlas 'kill $(pidof deskmate_hub_service) 2>/dev/null;
           abusctl call com.atlas.PackageManager1 Remove com.deskmate.hub1;
           abusctl call com.atlas.PackageManager1 Install com.deskmate.hub1 /tmp/arc/com.deskmate.hub1.ipk'
ssh atlas 'busctl --system call com.deskmate.hub1 / org.freedesktop.DBus.Peer Ping'   # D-Bus 활성화(HTTP 요청은 활성화 안 됨)
curl http://<PI4_IP>:8765/health        # {"status":"ok","state_ready":true,"mqtt":true|false}
ssh atlas 'journalctl -b --no-pager | grep "com.deskmate.hub1\["'
```

설치 위치는 `/data/share/usr/atlas/services/com.deskmate.hub1/` (읽기 전용 루트의 RW 오버레이), 실행 계정은 `u0_aNNNN`.
8765 HTTP API 는 Pi 5 화면과 보드 간 연동을 확인하는 개발용 어댑터다(Pi 4↔Pi 5 제품 경로는 MQTT).

## 런타임 설정 — `hub.env`

D-Bus 활성화는 환경변수를 넘길 수 없다. 서비스는 실행 파일 옆 `hub.env`(KEY=VALUE, `#` 주석)를 읽어 `setenv(overwrite=0)` 한다.
ARC 0.5 는 번들에 실행파일·serviceinfo·dbus 파일만 넣으므로 기본값(`files/hub.env`)은 payload zip 에 동봉되고 Python 이
`setdefault` 로 적용한다. 우선순위: 프로세스 env > 보드의 `<service_dir>/hub.env` > 동봉 기본값.

```sh
# 보드에서 브로커 주소만 바꾸기 (재빌드 없음)
ssh atlas 'printf "DESKMATE_MQTT_HOST=172.16.34.176\n" > /data/share/usr/atlas/services/com.deskmate.hub1/hub.env;
           kill $(pidof deskmate_hub_service)'    # 다음 D-Bus 호출/Ping 에 재기동
```

| 키 | 기본 | 뜻 |
|---|---|---|
| `DESKMATE_HUB_MODE` | `live` | `live` = UART+MQTT 실센서 → FSM, `demo` = 합성 시나리오(화면 시험) |
| `DESKMATE_MQTT_HOST` / `_PORT` | `172.16.34.176` / `1883` | 브로커. 비우면 MQTT 비활성 (FSM 은 UART 만으로 돈다) |
| `DESKMATE_MQTT_CLIENT_ID` | `deskmate-hub` | |
| `DESKMATE_UART_DEV` / `_BAUD` | `/dev/serial0` / `115200` | ESP32 UART2 |

## MQTT — C++ 네이티브 클라이언트 (2026-09-17)

보드의 `/restricted/python3` 에는 `_socket`·`_ssl`·`_random` 이 없다(빌드 시 `service_bridge` 가 모듈 가용성을 journal 에 찍는다).
그래서 **브로커 연결은 C++ 서비스가 갖고, Python 은 FSM 만** 라인으로 주고받는다. Atlas SDK 에 paho 헤더가 없어
`src/mqtt_client.cpp` 에 MQTT 3.1.1 클라이언트를 직접 두었다(CONNECT+LWT · SUBSCRIBE QoS0/1 · PUBLISH QoS0/1 retain ·
PINGREQ keepalive · 백오프 재접속·재구독 · 대기열 1000 건). QoS 2·TLS 는 계약에 없어 미지원.

```
브로커 ──► C++ 구독: deskmate/sensor/# (0) · feedback/user (1) · control/result (1)
              └─ stdin  "MQTT\t<topic>\t<payload>"  ──► Python  deskmate_hub/ingest/mqtt_lines.py → SensorCache
Python stdout "STATE\t{..}"   ──► C++ 발행 deskmate/state/phase        QoS1 retain
              "REQUEST\t{..}" ──►          deskmate/interaction/request QoS1
              "REPORT\t{..}"  ──►          deskmate/session/report      QoS1 retain
              "CMD\t{..}"     ──►          deskmate/control/cmd         QoS1
C++ 자체:      deskmate/health/hub  online(retain) / LWT offline(retain)
```

`/health` 가 `"mqtt":true` 면 브로커에 붙은 것이다. 호스트 테스트 `deskmate_mqtt_host_test` 는 패킷 벡터를 항상 검사하고,
`DESKMATE_TEST_BROKER=host:port` 가 있으면 실브로커와 왕복(구독→QoS1 retain 발행→수신)까지 확인한다(09-17 amqtt 통과).

## UART2 수신 (2026-09-16)

`src/uart_rx.cpp` 가 `/dev/serial0`(환경변수 `DESKMATE_UART_DEV`, `DESKMATE_UART_BAUD` 기본 115200)을 raw 모드로 열고,
`0x00` 경계로 COBS 해제 → CRC-16/CCITT-FALSE·헤더·길이(≤256 B) 검증 → 통과한 프레임만
`UART\t{"type":32,"seq":..,"ts_ms":..,"payload_hex":".."}` 한 줄로 Python 브리지 stdin 에 쓴다(HTTP 중계와 mutex 로 직렬화).
장치를 못 열면 10 s 마다 재시도하고 30 s 마다 수신/폐기/CRC 오류 통계를 stderr 에 남긴다 — 센서 없이도 FSM 은 돈다.
payload 해석은 Python(`deskmate_hub/ingest/uart_frame.py`)이 한다. 규약은 `docs/data-spec.md` §13.1.

Python 브리지 모드·MQTT 설정은 위 `hub.env` 절을 따른다. payload 의 paho 는 PC `python -m deskmate_hub run` 과 같은 코드를
쓰기 위한 동봉일 뿐, 보드에서는 import 되지 않는다(_socket 부재).

호스트 테스트: `cmake -DBUILD_TESTING=ON` 후 `ctest` 로 `deskmate_uart_rx_host_test`(CRC check value·COBS 벡터·프레임 파싱·CRC/LEN 손상 거부·
Python 코덱 공유 벡터·라인 형식). assert 가 아니라 명시적 check 라 NDEBUG 빌드에서도 검증된다.

PC 에 C++ 컴파일러가 없어도 `ziglang` 으로 크로스 빌드해 WSL 에서 돌릴 수 있다(09-16 통과 확인):

```bash
python -m pip install ziglang
python -m ziglang c++ -std=c++17 -target x86_64-linux-musl -static hub/atlas/src/uart_rx.cpp hub/atlas/src/uart_rx_host_test.cpp -o /tmp/t
wsl -d docker-desktop -- /tmp/t        # 또는 아무 리눅스 환경에서 실행 → "all checks passed"
python -m ziglang c++ -std=c++17 -target aarch64-linux-gnu -c hub/atlas/src/uart_rx.cpp -o /tmp/uart_rx.o   # Pi 4 타깃 컴파일 확인
```

`main.cpp` 는 sdbus-c++ 가 필요해 ARC 컨테이너에서만 빌드된다. ARC 실컴파일·설치·D-Bus 활성화·live 모드 FSM 은 09-16 실보드에서
확인했고, Pi 4 UART 수신은 ESP32 실배선으로 `rx=132 discarded=0 crc_errors=0` 을 봤다(`docs/codex-handoff-2026-09-16.md`).
남은 실보드 항목: `/dev/ttyS0` 접근 권한의 영구 부여(지금은 `chgrp`/`chmod 660` 임시), `serial-getty@ttyS0` 영구 비활성,
커널 `console=ttyS0` 제거, 브로커 연결 실측(PC 브로커 + 공유기 포트포워딩).
