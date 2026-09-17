# DESKMATE Codex 인수인계 — 2026-09-16

## 현재 완료 상태

- SSH 복구: `ssh atlas` = `root@172.16.34.146`, 키 인증 정상.
- Pi 4 `/dev/serial0 -> ttyS0` 확인. `serial-getty@ttyS0`는 현재 부팅에서 중지함.
- ESP32 COM8 USB JSON 정상: mmWave 1 Hz, env 5 s.
- GND 재연결 뒤 Pi 4 UART 독점 캡처 성공: 8초 432 B.
  - COBS 경계 `0x00`
  - TYPE `0x20` mmWave, `0x10` env, `0xF0` heartbeat 확인.
- UART2 frame 송신 및 Pi 4 C++ `uart_rx` 구현은 작업 트리에 있음.
- Node-RED control/cmd·result, session/report, state baseline/control 패널 작업이 작업 트리에 있음.
- Claude `origin/feat/hub-ingest-live`의 hub live ingest 18개 파일을 반영하고 커밋함.
  - 커밋: `d55bb37 feat(hub): integrate live UART ingest bridge`
  - 테스트: `45 passed, 1 xfailed`
- Docker Desktop 실행 경로:
  `D:\SW임베디드경진대회_LG\Toolchains\DockerDesktop\Docker Desktop.exe`
- Docker 이미지 `deskmate-atlas-dev:local`, ARC 0.5 빌드 성공.
- live ingest 포함 IPK 빌드 및 Pi 4 재설치 완료.
- Pi 4 D-Bus 활성화 명령:
  `ssh atlas 'abusctl introspect com.deskmate.hub1'`
- `/health`와 `/api/state` 응답 확인.

## 현재 정확한 문제

Pi 4 서비스 디렉터리에 아래 `hub.env`를 작성했지만 현재 로컬 `hub/atlas/src/main.cpp`가 이를 읽지 않아 Python bridge가 계속 demo 모드로 시작한다.

```text
DESKMATE_HUB_MODE=live
DESKMATE_UART_DEV=/dev/serial0
DESKMATE_UART_BAUD=115200
```

`/api/state`에 합성 `scenario`, `pc-collector`, `co2_ppm:720`, `lux:410`이 보이면 아직 demo 모드다.

## 다음 채팅의 첫 작업

1. `hub/atlas/src/main.cpp`에 `hub.env` 로더를 추가한다.
   - `<fstream>` include
   - 실행 파일 옆 `hub.env`의 `KEY=VALUE`를 읽어 `setenv(..., overwrite=0)`
   - `startHub()` 전에 호출
   - 공백과 `#` 주석을 trim하는 Claude 커밋 `793364c` 구현을 참고하는 것이 안전함.
2. 아래로 IPK 재빌드한다.

```powershell
docker run --rm -v "${PWD}:/workspace" -w /workspace deskmate-atlas-dev:local bash -lc "python3 -m pip install -q --break-system-packages pyyaml && source /opt/atlas-sdk-x86_64/environment-setup-armv8a-atlas-linux && arc build hub/atlas"
```

3. IPK 업로드·재설치 후 `hub.env`를 다시 작성한다.
4. `abusctl introspect com.deskmate.hub1`로 활성화하고 `/api/state`에서 합성 scenario가 사라지고 UART null 센서 상태가 반영되는지 확인한다.
5. 그 다음 C++ native MQTT를 구현한다.

## MQTT 확인 사항

- Pi 4 런타임에는 `/usr/lib/libpaho-mqtt3a.so.1.3.13` 등이 있음.
- Atlas Docker SDK에는 Paho 헤더와 cross-link 개발 라이브러리가 없음.
- 제한 Python에는 `_socket`이 없어 `hub/deskmate_hub/ingest/mqtt_source.py`의 Python paho는 실보드에서 사용 불가.
- 목표 구조:
  `C++ MQTT subscribe -> MQTT\t JSON -> Python FSM`,
  `Python STATE/REQUEST/REPORT/CMD -> C++ MQTT publish`.
- 브로커 목표: PC `192.168.50.91:1883`, 보드는 ASUS WAN `172.16.34.176:1883`로 접속.
- ASUS 포트포워딩 규칙: TCP 외부 1883 -> `192.168.50.91:1883`.

## 주의

- 작업 트리에 펌웨어, Atlas UART, Node-RED의 미커밋 변경이 남아 있다. 삭제·reset 금지.
- 사용자 파일 `993C31405D25C08720.mp3`, `Claude outputs/`는 건드리지 않는다.
- `serial-getty@ttyS0` 중지는 재부팅 시 원복될 수 있다.
- 커널 cmdline에는 `console=ttyS0,115200`가 남아 있다. 영구 제거는 보드 부팅 설정 변경이므로 별도 검증 후 진행한다.

## 2026-09-16 후속 작업 결과

- `hub/atlas/src/main.cpp`가 실행 파일 옆 `hub.env`를 시작 시 읽도록 수정했다.
  - 빈 줄과 `#` 주석을 무시하고 `KEY=VALUE` 양쪽 공백을 제거한다.
  - `setenv(..., overwrite=0)`을 사용하므로 이미 설정된 프로세스 환경변수가 우선한다.
- Atlas 제한 Python에는 PyYAML이 없으므로 빌드 시 `ingest.yaml`도 `ingest.json`으로 변환해 ELF zip payload에 포함하도록 수정했다. `fsm.json`과 `ingest.json` 포함을 모두 확인했다.
- 재빌드 산출물: `hub/atlas/build/arm64/ipk/com.deskmate.hub1.ipk`.
- 검증 결과:
  - Hub Python: `45 passed, 1 xfailed`
  - C++ UART host test: 통과
  - Pi 4 UART: `rx=66 -> 132`, `discarded=0`, `crc_errors=0`
  - `/health`: `state_ready=true`
  - `/api/state`: live `boot_id=09ff5e74`, 합성 `scenario`/`pc-collector` 없음, UART mmWave 요약 반영
- `abusctl introspect com.deskmate.hub1`는 서비스 활성화에는 성공하지만 객체를 export하지 않아 `UnknownObject`를 출력한다. 프로세스와 HTTP API는 정상이다.

### Atlas 배포 시 반드시 유지할 조건

- `hub.env`는 서비스 계정이 읽을 수 있어야 한다. 확인된 설정은 다음과 같다.

```sh
chown u0_a5010:g0_a5010 /data/share/usr/atlas/services/com.deskmate.hub1/hub.env
chmod 640 /data/share/usr/atlas/services/com.deskmate.hub1/hub.env
```

- 이 보드에서는 `/etc/group`이 읽기 전용이어서 `addgroup u0_a5010 tty`가 실패한다.
- 실보드 검증을 위해 아래 임시 권한을 적용했다. 재부팅하면 원복된다.

```sh
chgrp g0_a5010 /dev/ttyS0
chmod 660 /dev/ttyS0
```

- 영구 배포 전 Atlas 공식 장치 권한/udev 방식으로 `/dev/ttyS0` 접근을 부여해야 한다. `serial-getty@ttyS0` 영구 비활성 및 커널 `console=ttyS0,115200` 제거도 아직 남아 있다.
- PC의 `opkg install --force-reinstall` 직접 호출은 읽기 전용 `/usr` 때문에 경고와 함께 실패했다. 이번 검증은 IPK에서 새 실행 파일을 추출해 `/data/share/usr/atlas/services/com.deskmate.hub1/deskmate_hub_service`에 교체했다. 정식 배포에는 Atlas `arc install` 환경을 사용한다.