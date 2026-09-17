# atlas-hotspot-broker — Pi 4 MQTT broker + 휴대폰 핫스팟 개발 통신

교내 LAN 이 막히거나 이더넷 직결이 안 될 때, **Pi 4·Pi 5 를 같은 휴대폰 핫스팟에 붙이고
Pi 4 에서 Mosquitto 를 띄워** 개발용 MQTT 경로를 여는 절차다. 2026-09-17 에
ATLAS Platform 26.06.0-246 (Pi 4 Rev1.2 · Pi 5 Rev1.1) 에서 검증했다.

전제 · 제약 (실측)
- ATLAS 보드에는 `mosquitto` 가 없고 `opkg update` 는 LG 내부 feed(`kairos-art.lge.com`)만 바라봐서
  인터넷이 있어도 설치가 안 된다. → SDK 툴체인으로 **정적 크로스빌드**해서 올린다.
- `/restricted/python3` 은 SSH 셸에서 AppArmor 가 실행을 막고 `_socket` 도 없다. Node·Podman 없음,
  Docker 는 바이너리만 있고 데몬 비활성. → Python/컨테이너 브로커는 불가.
- ConnMan 1.44 의 `/var/lib/connman/*.config` 프로비저닝과 `connmanctl` 에이전트 입력은 이 빌드에서
  동작하지 않는다. `connman-adapter` 가 노출하는 **NetworkManager 호환 D-Bus** 로만 PSK 를 넘길 수 있다.
- eth0 가 기본 경로를 유지하므로 기존 LAN SSH 는 끊기지 않는다. 핫스팟 RTT 는 ≈370 ms.

## 파일

| 파일 | 실행 위치 | 역할 |
|---|---|---|
| `build_mosquitto.sh` | PC (docker) | `deskmate-atlas-dev:local` 컨테이너에서 mosquitto 2.0.20 을 aarch64 정적 빌드 → `dist/` |
| `board_wifi_connect.sh` | 보드 (BusyBox sh) | `<SSID> <PASS>` 로 핫스팟 접속. 이미 붙어 있으면 그대로 종료 |
| `deploy_broker.sh` | PC | Pi 4 `/tmp` 로 broker·클라이언트 복사 후 기동, Pi 5 에 pub/sub 복사 |
| `mosquitto.conf` | Pi 4 | `listener 1883 0.0.0.0`, 익명 허용, `user root`, 영속 없음 |

`dist/` 와 소스 tarball 은 커밋하지 않는다(`.gitignore`). SSID/비밀번호는 인자로만 넘기고 어디에도 기록하지 않는다.

## 절차

```bash
# 0) 한 번만: 정적 브로커 빌드 (컨테이너 이미지는 display/atlas/README.md 대로 준비)
tools/atlas-hotspot-broker/build_mosquitto.sh

# 1) 휴대폰 핫스팟 ON (2.4 GHz 권장 — 5 GHz 는 첫 인증이 가끔 타임아웃, 스크립트가 3회 재시도)
# 2) 두 보드에 접속 스크립트 복사·실행 (LAN SSH 경로 사용)
scp tools/atlas-hotspot-broker/board_wifi_connect.sh root@<pi4>:/tmp/
ssh root@<pi4> 'sh /tmp/board_wifi_connect.sh <SSID> <PASS>'    # → connected: 10.x.x.x/24
scp tools/atlas-hotspot-broker/board_wifi_connect.sh root@<pi5>:/tmp/
ssh root@<pi5> 'sh /tmp/board_wifi_connect.sh <SSID> <PASS>'

# 3) Pi 4 에 broker 기동 + Pi 5 에 클라이언트 복사
tools/atlas-hotspot-broker/deploy_broker.sh root@<pi4> root@<pi5>
#   → "broker listening on 1883", "local pub ok", "wlan0: <pi4-hotspot-ip>/24"
```

## 검증

```sh
# Pi 4
netstat -ltn | grep 1883                      # 0.0.0.0:1883 LISTEN
tail /tmp/mosquitto.log
# Pi 5 → Pi 4 (핫스팟 주소)
/tmp/mosquitto_sub -h <pi4-hotspot-ip> -t 'deskmate/#' -v &
/tmp/mosquitto_pub -h <pi4-hotspot-ip> -t deskmate/test -m ping
```

Pi 5 앱의 broker 주소는 `<pi4-hotspot-ip>:1883` (핫스팟 DHCP 라 바뀔 수 있음 → `ip -4 addr show wlan0`).

## 알려진 것 · 남은 것

- `/tmp` 기동이라 **재부팅하면 사라진다**. 영구화는 `hub/atlas` 처럼 native-service IPK 로 감싸 D-Bus
  activation 하거나 hub 서비스가 fork 하는 방식이 후보(미구현).
- 핫스팟 클라이언트가 0 이 되면 휴대폰이 핫스팟을 끄는 경우가 있다. `SSID .* not in scan results` 가 나오면 먼저 폰을 본다.
- `connmanctl disconnect` 뒤 ConnMan 서비스 목록이 비는 현상이 있어 스크립트가 wifi 를 껐다 켠다.
- ssh 로 보드에 `pkill -f mosquitto` 를 보내면 ssh 셸 자신이 죽는다. `pidof mosquitto` 를 쓴다.
