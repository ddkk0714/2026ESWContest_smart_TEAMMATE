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
화면과 보드 간 연동을 확인하는 개발용 어댑터이며 최종 통신 방식은 확정하지 않는다.

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
