# Pi 4 Atlas Hub service

Pi 4 Headless 프로파일에서 기존 Python FSM Hub를 실행하는 native-service IPK다.
SSH 셸의 기본 AppArmor 프로파일은 `/restricted/python3` 실행을 거부하므로,
시스템 정책을 변경하지 않고 Atlas 서비스 샌드박스 안에서 제한 Python을 실행한다.

## 빌드

저장소 루트가 `/workspace`로 마운트된 Atlas 개발 컨테이너에서 실행한다.

```bash
cd /workspace
arc build hub/atlas
```

ARC 0.5가 서비스 실행 파일 하나만 패키징하므로, 빌드 시 Hub를 실행 파일 뒤쪽의
zip payload로 포함한다. Atlas 제한 Python에는 HTTP/JSON 실행에 필요한 순수 표준
모듈도 다수 빠져 있어, 빌드 컨테이너의 동일한 Python 3.12 순수 표준 라이브러리를
함께 넣는다. 네이티브 확장과 개발·테스트 패키지는 포함하지 않는다. PyYAML이
요구하는 일부 모듈도 없으므로 `fsm.yaml`은 빌드 시 JSON으로 변환한다. 이 JSON은
빌드 산출물이며, FSM 임계값과 가중치의 원본은 계속
`hub/deskmate_hub/config/fsm.yaml` 하나뿐이다.

## 설치 및 실행 확인

```bash
arc devices add -d deskmate_pi4 --ip=<PI4_IP> --user=root
arc install <생성된-ipk> -d deskmate_pi4
ssh rpi4 'busctl --system status com.deskmate.hub1'
curl http://<PI4_IP>:8765/health
```

`busctl` 접근은 D-Bus activation으로 서비스를 시작한다. 8765 HTTP API는 Pi 5
화면과 보드 간 연동을 확인하는 개발용 어댑터다(Pi 4↔Pi 5 제품 경로는 MQTT).

## UART2 수신 (2026-09-16)

`src/uart_rx.cpp` 가 `/dev/serial0`(환경변수 `DESKMATE_UART_DEV`, `DESKMATE_UART_BAUD` 기본 115200)을 raw 모드로 열고,
`0x00` 경계로 COBS 해제 → CRC-16/CCITT-FALSE·헤더·길이(≤256 B) 검증 → 통과한 프레임만
`UART\t{"type":32,"seq":..,"ts_ms":..,"payload_hex":".."}` 한 줄로 Python 브리지 stdin 에 쓴다(HTTP 중계와 mutex 로 직렬화).
장치를 못 열면 10 s 마다 재시도하고 30 s 마다 수신/폐기/CRC 오류 통계를 stderr 에 남긴다 — 센서 없이도 FSM 은 돈다.
payload 해석은 Python(`deskmate_hub/ingest/uart_frame.py`)이 한다. 규약은 `docs/data-spec.md` §13.1.

Python 브리지를 실센서 모드로 띄우려면 서비스 환경에 `DESKMATE_HUB_MODE=live`(선택: `DESKMATE_MQTT_HOST`)를 준다.

### 시연 프로파일 (2026-10-24)

`hub.env`(`pi4-hub-activate.sh` 가 만드는 서비스 환경 파일)에 한 줄을 더하고 서비스를 다시 띄우면 Python 브리지가 기본 fsm·ingest 위에
`deskmate_hub/config/profiles/<이름>.yaml` 의 `fsm:`·`ingest:` 절을 깊은 병합으로 얹는다(`deskmate_hub/profile.py`). 줄을 지우면 기본으로 돌아온다.

```
DESKMATE_PROFILE=demo-a
```

(`demo-b` 도 같은 식. 줄 끝 주석은 쓰지 않는다 — `main.cpp loadEnvironmentFile` 은 `#` 으로 시작하는 줄만 건너뛰고 값 안의 `#` 은 값으로 본다.)

빌드 때 `tools/build_payload.py` 가 profiles/*.yaml 을 `profiles/<이름>.json` 으로 payload 에 넣으므로(ingest.json 과 같은 방식) 보드의 제한 Python 은
YAML 없이 읽는다. 모르는 이름이면 브리지가 바로 종료하고 stderr 에 있는 이름을 찍는다. 적용되면 시작 로그에 `[profile] DESKMATE_PROFILE=demo-a — …` 한 줄이 남는다.
**두 프로파일은 시연 전용(시뮬 근거)이다 — 실측 성공 기록에 쓰지 않는다.**
빌드 시 `paho-mqtt` 가 설치돼 있으면 payload 에 동봉된다(`tools/build_payload.py`).

## 자세 서비스 판정 폴링 (2026-10-23)

`src/tof_service_poll.cpp` 가 별도 자세 서비스 `deskmate_tofposture`(PostureNet GRU, HTTP 8771)의 `GET /api/state` 를
`DESKMATE_TOF_SERVICE_PERIOD_MS`(기본 1000) 마다 받아 `TOFSVC	<json 한 줄>` 로 Python 브리지 stdin 에 쓴다(UART·MQTT 줄과 같은
mutex). 브리지의 제한 Python 은 `_socket` 이 없어 직접 못 읽기 때문이다. Python 쪽(`deskmate_hub/ingest/tof_service.py`)이 이를
`tof_service` 표본으로 두고 `ingest.yaml tof.posture_source: service` 면 FSM posture 신호로 쓴다. 환경변수:
`DESKMATE_TOF_SERVICE_HOST`(127.0.0.1) · `DESKMATE_TOF_SERVICE_PORT`(8771, 0 = 끔) · `DESKMATE_TOF_SERVICE_PATH`(/api/state) ·
`DESKMATE_TOF_SERVICE_TIMEOUT_MS`(500). 서비스가 없으면 첫 실패만 로그하고 주기마다 다시 붙어 본다 — 그동안 hub 는 MLP/0x03 규칙 판정으로 돈다.
0x05 격자를 서비스로 넘기는 중계(`src/uart_relay.cpp`, tcp:9100)와 짝이다.

호스트 테스트: `cmake -DBUILD_TESTING=ON` 후 `ctest` 로 `deskmate_uart_rx_host_test`(CRC check value·COBS·라인 형식),
`deskmate_uart_relay_host_test`, `deskmate_tof_service_poll_host_test`(HTTP 파싱·mock 서비스 폴링·재시도), `deskmate_mqtt_host_test`.

실보드 검증 완료(2026-09-18): `rx=71 discarded=0 crc_errors=0`, `/health` `mqtt=true`,
`/api/state` 에 실측 temp 26.4 · humidity 53.8 · lux 392.


## 재부팅·재설치 후 기동 (2026-09-18 실측)

```bash
scp hub/atlas/tools/pi4-hub-activate.sh atlas:/tmp/
ssh atlas sh /tmp/pi4-hub-activate.sh
```

재부팅·IPK 재설치 때마다 네 가지가 날아가고, 넷 다 증상이 `rx=0` 또는 서비스 미기동이다.

| 날아가는 것 | 증상 |
|---|---|
| 서비스 계정 uid (설치할 때마다 재할당, 실측 `u0_a5010`→`u0_a5015`) | 계정을 박아 둔 설정이 전부 어긋남 |
| D-Bus `.service` 의 `User=` | `Cannot do system-bus activation with no user`. `User=root` 도 `Failed to setup environment correctly` 로 거절되므로 **실제 설치 계정**이어야 한다 |
| `.conf` 의 `own` 정책 | IPK 가 넣는 파일은 root 에만 열려 있어 서비스가 버스 이름을 못 가짐 |
| `/dev/serial0` 노드 권한 (`root:tty 0620` 으로 원복) | 서비스가 노드를 못 열어 UART 수신 0 |

`hub.env` 도 재설치 때 사라진다. 스크립트가 없으면 만들고, 있으면 건드리지 않는다.

## UART 수신이 `rx=0` 일 때

30 s 주기 로그가 `rx=<프레임> bytes=<원시 바이트>` 를 함께 찍는다. 이 둘로 원인이 갈린다.

| 로그 | 뜻 | 볼 곳 |
|---|---|---|
| `open failed: ... (Permission denied)` | 서비스 계정이 노드를 못 연다 | `chgrp`/`chmod 660` |
| `bytes=0` | 선에 아무것도 오지 않는다 | 배선·공통 GND·ESP32 송신 |
| `bytes>0 rx=0` | 오는데 프레임이 안 선다 | 보드율, getty·콘솔과의 분할 수신 |

`open` 로그에 `/dev/serial0` 이 실제로 가리킨 노드가 같이 남는다. Pi 4 기본은
`ttyS0` 이고, `ttyAMA0` 으로 바뀌어 있으면 부팅 설정이 변한 것이라 `ttyS0` 앞으로
해 둔 권한·getty 정지가 적용되지 않는다.

보드에서 한 번에 점검하려면:

```bash
scp hub/atlas/tools/pi4-uart-check.sh atlas:/tmp/
ssh atlas sh /tmp/pi4-uart-check.sh          # 점검만
ssh atlas sh /tmp/pi4-uart-check.sh --fix    # getty 정지·권한·보드율까지
```

## Reboot recovery (2026-10-06)

Automatic startup units and fault recovery scripts are implemented; board installation and reboot verification remain pending. See [deployment guide](../../docs/reboot-recovery.md).
