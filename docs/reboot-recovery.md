# 재부팅·장애 복구 자동화 (2026-10-06)

구현 위치는 `tools/atlas-recovery/`이다.

> **10-14 실기 결과: 현재 ATLAS 이미지에서는 부팅 자동 시작이 불가능하다.** 쓰기 가능한 UnitPath는 `/data/share/usr/lib/systemd/system`뿐인데, `/data`는 systemd가 부팅 유닛을 읽은 뒤에 마운트된다. 그래서 그 경로의 `.wants`는 무시된다(두 보드 재부팅 후 `inactive` 확인). 설치기는 이제 이 경로에 영구 설치를 거부한다. 부팅할 때마다 Pi 4에서 `sh /data/share/deskmate/recovery/install.sh pi4 --runtime-only`, Pi 5에서 `... pi5 --runtime-only`를 한 번 실행한다. 이후 감독·재시작은 정상 동작한다. 영구 등록은 ATLAS가 지원하는 방식을 LG 측에 확인해야 한다.
>
> 같은 날 Pi 4에서 확인한 것: hub 프로세스 강제 종료 → 약 11초 뒤 새 PID로 복구. broker 재시작 → hub MQTT 재연결, broker 프로세스 1개. UART 권한 `g0_a5015 660` 복구. Pi 5 앱이 AppManager로 기동. Pi 4에는 broker·hub 감독 서비스, Pi 5에는 디스플레이 기동 서비스를 설치한다. 두 보드 모두 30초 간격으로 복구 상태를 점검한다.

## 10-24 재부팅 뒤 Pi 4 허브 켜기

10-24 핫스팟(Pi 4 `172.20.10.3`)에서 두 번 재부팅했는데, 두 번 다 broker·hub가 꺼진 채로 올라왔다. Pi 5 앱의 "Pi 4 연결 점검"은 4단계(상태 수신)에서 `health/hub 도 받지 못함`으로 실패했다.

원인은 위 10-14 결과와 같다.

- hub(`com.deskmate.hub1`)는 D-Bus 온디맨드라 누가 부르기 전에는 뜨지 않는다.
- 부르는 유닛을 부팅에 걸 방법이 없다.
- 부팅 때마다 `serial-getty@ttyAMA0`(login·sh)가 hub가 읽을 UART(`/dev/serial0` → `ttyAMA0`)를 다시 잡는다.

root로 아래 세 줄을 돌려 복구했다.

```sh
sh /data/share/deskmate/bin/pi4-broker-start.sh
cd /data/share/deskmate/recovery && sh lifecycle.sh hub-prepare
busctl --system call org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus StartServiceByName su com.deskmate.hub1 0
```

`hub-prepare`는 다음을 한다. 마지막 줄이 hub를 깨운다.

- getty를 멈춘다.
- `ttyAMA0`을 `g0_a5015 660`으로 바꾼다.
- D-Bus 활성화 파일과 이름 소유 정책을 설치된 계정으로 맞춘다.

수 초 안에 확인된 것:

- `deskmate/health/hub`가 `online`.
- `deskmate/state/phase`가 10 s마다 들어옴(`source: fsm`).
- `wget -q -O - http://127.0.0.1:8765/health` = `{"status":"ok","state_ready":true,"mqtt":true}`.
- Pi 5 앱 로그 `state 수신 성공: MQTT 172.20.10.3:1883 seq=0 state=IDLE`.
- MQTT 확인 명령: `/data/share/deskmate/bin/mosquitto_sub -h 127.0.0.1 -t 'deskmate/#' -v -C 5 -W 20`(보드 busybox에는 `timeout`이 없다).

감독(hub가 죽으면 재시작)까지 원하면 위 세 줄 대신 `sh install.sh pi4 --runtime-only` 한 줄을 쓴다. 이미 손으로 띄운 broker가 있으면 같은 포트를 쓰니 먼저 멈춘다. 10-24에는 이 한 줄을 다시 돌려 보지 않았다.

함께 본 것:

- 저널은 휘발성(`/var/log/journal` 없음)이라 재부팅 전 hub 로그는 남지 않는다. hub가 직접 남기는 파일은 `logs/esm-*.jsonl`(ESM 기록)뿐이다.
- 이때 UART 수신은 0이었다(`DESKMATE UART rx=0`). ESP32 값이 안 들어오는 것은 이 절차와 별개다.
- Pi 5 앱의 점검은 hub가 아예 안 떴을 때도 "3. hub 생존"을 통과로 표시한다. health가 없으면 통과로 치기 때문이다(`display/atlas/ui_env_app/lib/mqtt_probe.dart`). 그 뒤 문구 "hub 는 떠 있는데 상태가 오지 않습니다"도 이 경우에는 맞지 않는다. 이때는 위 세 줄을 돌린다.

### ESP32를 USB로 붙였을 때 (10-24)

10-22부터 ESP32(`esp32dev_usb` 펌웨어)를 Pi 4 USB-A에 꽂아 쓴다. ATLAS에는 CH340 드라이버가 없어서 꽂아도 `/dev/ttyUSB0`이 안 생긴다. 그대로 두면 hub는 `/dev/serial0`(GPIO UART)만 기다리며 `rx=0`에 머문다.

hub가 USB를 읽게 한 방법:

1. `hub.env`의 `DESKMATE_UART_DEV`를 `/dev/ttyUSB0`으로 바꾼다. 보드에서 한 번만 하면 되고, 재부팅해도 남는다(10-24 변경, 원본은 `hub.env.bak_1024_serial0`).
2. 부팅할 때마다(그리고 ESP32를 다시 꽂을 때마다) hub를 깨우기 전에 CH340을 잡는다.

```sh
sh /data/share/deskmate/bin/pi4-broker-start.sh
/mnt/rawdata/deskmate_tof/ch340cfg auto 115200      # generic 드라이버에 붙이고 115200 8N1 설정 → /dev/ttyUSB0
cd /data/share/deskmate/recovery && sh lifecycle.sh hub-prepare   # ttyUSB0 를 g0_a5015 660 으로
busctl --system call org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus StartServiceByName su com.deskmate.hub1 0
```

순서가 중요하다. `hub-prepare`는 장치가 있을 때만 권한을 고친다. `ttyUSB0`은 처음에 `root:dialout`이라 hub 계정이 열지 못한다. 보드율 설정은 CH340을 다시 꽂거나 리셋하면 풀린다. ESP32를 다시 꽂았으면 `ch340cfg`와 `hub-prepare`를 다시 돌린다(hub는 10초마다 다시 연다).

10-24 17:09 확인한 것:

- 30초에 `DESKMATE UART rx=527 bytes=70913 discarded=20 crc_errors=0`. 버려진 것은 환경 센서 드라이버가 섞어 찍는 텍스트(`DHT22 read failed…`)다.
- `[bridge] uart frames=284 dropped=0 hb=25 grids=116`.
- FSM IDLE → START, `sensor_summary`에 ToF·mmWave가 찼다(`tof.sensors_ok=3`, `mmwave.distance_cm=35`, `heart_bpm=73`).
- Pi 5 앱 센서 화면에 mmWave 값(재실·움직임·거리·심박)이 나온다.
- 환경 값(CO₂·온도·습도·조도)은 ESP32 쪽 센서 오류로 비어 있다.

USB 중계기(`/mnt/rawdata/deskmate_tof/relay.sh`, `uart_relay_board`)와 hub는 같은 `ttyUSB0`을 동시에 열면 안 된다. 하나만 띄운다.

## 동작

- broker: Mosquitto를 foreground로 실행하고 systemd가 종료 시 재시작한다.
- hub: 설치된 서비스 소유자의 D-Bus 활성화 경로를 사용한다. 설치 파일의 UID·GID로 D-Bus 설정을 갱신하고 기존 hub.env 연결 설정을 보존한다.
- UART: 실제 장치가 나타나면 해당 serial-getty를 중지하고 hub 그룹에 660 권한을 부여한다. 장치가 없으면 FSM은 계속 실행한다.
- FSM: /api/state의 boot_id·seq와 data.source=fsm을 확인한다. 상태 갱신이 세 번 연속 없거나 native fallback만 발행되면 hub를 재시작한다. 기본 재시작 간격 제한은 120초이다.
- 디스플레이: ATLAS AppManager로 앱을 실행한다. 실행 앱 목록에서 정확한 앱 ID를 확인하고 앱이 사라지면 기동 서비스를 재시작한다.
- broker 연결 장애만으로 정상 FSM을 재시작하지 않는다. 수동으로 중지한 서비스는 watchdog의 try-restart가 다시 시작하지 않는다.
- 각 서비스의 재시작 제한에 도달하면 진단 후 수동 복구한다. 무한 재시작을 위해 제한을 해제하지 않는다.

## 설치 준비

기존 broker·hub IPK·Pi 5 앱을 먼저 설치한다. 이 디렉터리를 보드의 /data/share/deskmate/recovery에 복사한다. recovery.env.example을 recovery.env로 복사하여 보드별 ROLE, 설치 경로와 UART를 확인한다. 기존 hub.env가 있으면 그 설정이 우선한다. 설정 파일은 root 소유이며 비밀번호를 넣지 않는다.

기존 수동 broker 프로세스가 실행 중이면 기존 운영 절차로 종료한 뒤 설치한다. 동일 포트에 두 broker를 실행하지 않는다. 설치기는 관련 없는 프로세스를 일괄 종료하지 않는다.

ATLAS 루트 파일시스템은 읽기 전용일 수 있다. 설치기는 systemctl의 UnitPath에 포함되고 실제 쓰기 가능한 영구 디렉터리만 사용한다. 그런 경로가 없으면 영구 설치를 거부한다. OS remount나 임의 부팅 훅 수정은 하지 않는다. 그 경우 보드 이미지에서 지원하는 영구 서비스 등록 방식을 확인해야 한다.

## 보드 명령

Pi 4에서 root로:

```sh
cd /data/share/deskmate/recovery
sh install.sh pi4
sh lifecycle.sh status
```

Pi 5에서 root로:

```sh
cd /data/share/deskmate/recovery
sh install.sh pi5
sh lifecycle.sh status
```

systemd가 실제로 읽는 영구 경로를 지정하려면 --unit-dir PATH를 사용한다. --no-start는 등록 후 즉시 실행하지 않는다. --runtime-only는 /run/systemd/system에 시험 설치하며 재부팅하면 사라진다. 영구 자동 시작의 검증으로 취급하지 않는다.

```sh
sh install.sh pi4 --runtime-only
journalctl -u deskmate-hub.service -u deskmate-watch.service --no-pager -n 100
systemctl reset-failed deskmate-hub.service
systemctl start deskmate-hub.service
# 설치 때와 같은 역할·경로 옵션으로 제거; 설정과 데이터는 유지
sh install.sh pi4 --remove
```

기본 점검 실패 횟수·재시작 간격·감독 주기는 recovery.env에서 변경한다. systemd 서비스의 종료 재시작은 10초 간격이며 120초 안에 6회로 제한한다. systemd timer는 부팅 60초 후 시작한다.

## 검증과 남은 작업

PC 모의 명령으로 설치·제거, 미관리 unit 보호, 기존 설정 보존, FSM 갱신 정지와 HTTP 장애 복구, 재시작 간격 제한, 디스플레이 정확한 ID 비교, 수동 중지 존중, D-Bus 활성화 실패를 검증한다.

```powershell
python -m pytest tools/test_atlas_recovery.py -q
```

실기 완료 기준:

- 전원 재인가 후 Pi 4 broker·hub, Pi 5 앱이 자동 기동한다.
- Python FSM만 종료하거나 정지시켜 native 중계가 살아 있어도 복구한다.
- broker 재시작 뒤 MQTT가 재연결되고 중복 프로세스가 없다.
- UART를 분리·재연결하고 IPK를 재설치해도 실제 장치 권한과 서비스 소유자가 맞는다.
- 앱 종료 후 다시 실행되고, 점검 중 수동 stop은 유지된다.
- 인터넷 차단에서도 로컬 추론·화면·제어가 동작한다.
- 시작 실패를 반복해도 재시작 제한이 적용된다.

실제 ATLAS의 UnitPath, D-Bus·AppManager 응답 형식, 장치 권한과 부팅 순서는 보드에서 확인해야 한다. PC 테스트는 이 실기 검증을 대신하지 않는다.
