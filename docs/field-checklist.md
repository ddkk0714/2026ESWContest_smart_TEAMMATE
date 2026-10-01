# 실기 체크리스트 — 통신부터 시연까지

> 기준일 2026-10-01. 대상: PR #28~#33 에서 "실기 확인 필요"로 남긴 항목 전부 + 통합 MVP 1사이클.
> 위에서부터 순서대로 한다. **앞 단계가 안 되면 뒤 단계는 볼 수 없다.** 칸마다 "확인 방법"과 "기대 결과"를 적었고, 막히면 "안 될 때"를 본다.
> 결과는 맨 아래 §9 기록표에 적어 PR 코멘트로 남긴다. 노션의 실측 기록이 원본이므로 숫자(rx·crc·지연)는 노션에도 옮긴다.

---

## 0. 준비 (PC, 10분)

### 0-1. 주소 메모

| 이름 | 값 | 찾는 법 |
|---|---|---|
| `<PI4_IP>` | 교내 LAN `172.16.34.x` 또는 핫스팟 주소 | 교내: `ssh atlas`(안 되면 `atlas-ip.ps1`). 핫스팟: §0-1a `tools/find-boards.ps1` → `ssh atlas-hs` |
| `<PI5_IP>` | DHCP 또는 핫스팟 주소 | 핫스팟: §0-1a → `ssh pi5-hs`. 이더넷 직결이면 Pi 5 쪽 링크 주소 |
| `<PC_IP>` | PC 의 같은 망 주소 | PowerShell `ipconfig` |

주소는 저장소·문서에 고정값으로 적지 않는다(이 표는 현장 메모용).

### 0-1a. 휴대폰 핫스팟에서 보드 찾기 (2026-10-01 확인, 1분)

교내 LAN 이 없을 때는 **PC·Pi 4·Pi 5 를 모두 같은 휴대폰 핫스팟**에 붙이고 그 망으로 점검한다.

1. PC Wi-Fi 를 그 핫스팟으로 바꾼다. **CloudflareWARP 같은 VPN 은 끈다**(사설 주소 접속을 가로챈다).
2. 보드 찾기 + ssh 별명 갱신:
   ```powershell
   powershell -ExecutionPolicy Bypass -File tools/find-boards.ps1 -UpdateSshConfig
   ```
   → `Pi 4 10.x.x.x ssh22=True`, `Pi 5 10.x.x.x ssh22=True` 가 나오면
   ```powershell
   ssh atlas-hs     # Pi 4
   ssh pi5-hs       # Pi 5
   ```
3. 동작 원리와 함정
   - 보드는 **ping 에 답하지 않는다.** 스크립트는 ping 을 "이웃 표 채우기"로만 쓰고, MAC 으로 보드를 고른다
     (Pi 4 wlan0 `dc:a6:32:85:f3:73`, Pi 5 wlan0 `88:a2:9e:3c:cc:b4`). 둘 다 호스트 이름이 `atlas` 라 이름으로는 구분이 안 된다.
   - 주소가 바뀌면 SSH 가 "Host key verification failed" 를 낸다. 스크립트는 예전 교내 LAN 주소로 저장해 둔 키
     (`HostKeyAlias`)로 검증하므로, **키가 같으면** 경고 없이 붙는다. 키가 정말 다르면 보드가 재설치된 것이니 확인 후 갱신한다.
   - ATLAS 는 **재부팅하면 핫스팟 접속을 복구하지 않는다.** 보드가 안 보이면 보드 화면/콘솔에서 다시 붙이거나
     `tools/atlas-hotspot-broker/board_wifi_connect.sh <SSID> <PASS>`(PR #18 워크트리, SSID·비밀번호는 저장소에 적지 않는다).
   - 스크립트가 못 찾으면 IPv6 링크로컬로 직접: `ssh root@fe80::dea6:32ff:fe85:f373%<Wi-Fi ifIndex>`(Pi 4).
   - 핫스팟 경유 지연은 Pi 5 → Pi 4 약 40 ms(10-01). 점검에는 충분하고, 시연은 이더넷 직결이 기준이다(1-1).

### 0-2. PC 도구

```powershell
cd D:\SW임베디드경진대회_LG\worktrees\display-intervention      # PR #33 브랜치(또는 머지 후 main)
python -m pip install -r tools/requirements.txt                   # paho-mqtt (리허설 쓰면 amqtt 도)
python -m pytest hub/tests tools ml -q                            # 출발 전 PC 시험 통과 확인
```

- [ ] 시험이 모두 통과한다 (hub 157 passed·1 xfailed, tools·ml 41 passed 근처)

### 0-3. 감시 창 하나 띄워 두기

이후 모든 단계에서 이 창을 보며 확인한다. 아무것도 발행하지 않는 읽기 전용 도구다.

```powershell
python tools/mqtt_watch.py --broker <PI4_IP>
# 필요한 것만: --only state,request,feedback,control,report
```

---

## 1. 통신

### 1-1. 네트워크·SSH

| | 확인 방법 | 기대 결과 |
|---|---|---|
| - [ ] Pi 4 SSH | `ssh atlas "uname -a; uptime"` | 응답이 온다 |
| - [ ] Pi 4 ↔ Pi 5 이더넷 | `ssh atlas ping -c 3 <PI5_IP>` | 손실 0% |
| - [ ] PC ↔ Pi 4 | `ping <PI4_IP>` | 응답 |
| - [ ] Pi 4 ↔ Pi 5 유선 주소 대역 | `ssh atlas-hs "ip -4 addr show eth0"` / `ssh pi5-hs "ip -4 addr show eth0"` | 두 eth0 이 **같은 대역**. 10-01 에는 Pi 4 `169.254.x`(자동), Pi 5 `172.16.34.198`(옛 교내 주소)로 달라 직결 핑 100% 손실 → 두 보드에 같은 대역 고정 주소를 주고 브로커 주소를 그쪽으로(시연 전 필수) |

안 될 때: IP 가 바뀌었으면 `atlas-ip.ps1`. 핫스팟 망이면 IPv6 링크로컬 `ssh root@fe80::dea6:32ff:fe85:f373%<ifIndex>`(메모 `atlas-board-ssh`). 보드는 BusyBox 라 GNU 옵션(`head -8` 등)이 안 먹는다.

### 1-2. 브로커 (Pi 4 Mosquitto)

```bash
ssh atlas sh /data/share/deskmate/bin/pi4-broker-start.sh
```

| | 기대 결과 |
|---|---|
| - [ ] 스크립트 출력 | `브로커 pid=…` 와 `1883 LISTEN` |
| - [ ] 감시 창 | `[watch] <PI4_IP>:1883 연결, 구독: …` |

안 될 때: `브로커 바이너리가 없다` → `worktrees/atlas-hotspot-broker/tools/atlas-hotspot-broker/dist/` 를 `/data/share/deskmate/bin` 으로 복사. 로그는 `/data/share/deskmate/mosquitto.log`. 프로세스를 죽일 땐 `pkill -f` 금지(ssh 셸이 같이 죽는다) → `kill $(pidof mosquitto)`.
재부팅하면 다시 돌려야 한다(자동 시작 없음).

### 1-3. ESP32 단독 (USB 로 먼저)

ESP32 를 PC USB 에 꽂고 펌웨어부터 확인한다. PR #28(수신 전용 파서) 이후 **재플래시가 필요**하다.

```powershell
cd firmware/esp32_sensor_node
pio run -t upload          # 안 되면 BOOT 누른 채 EN 한 번 누르고 다시
pio device monitor -b 115200
```

| | 기대 결과 |
|---|---|
| - [ ] 부팅 로그 | `DESKMATE ESP32 sensor node starting` → `C1001 ready` (늦게 붙으면 `C1001 ready (late init)`) |
| - [ ] mmWave JSON 1 Hz | `{"t":"mmwave",…,"present":true,…}` 가 1초마다 |
| - [ ] 환경 JSON 0.2 Hz | `{"t":"env",…,"co2_ppm":…,"co2_valid":true,…}` 5초마다, 값이 `null` 아님 |
| - [ ] C1001 선 뽑았다 꽂기 | `C1001 retry backoff -> …` 후 다시 `ready`, 그동안 다른 줄은 계속 나온다(루프가 굶지 않음) |

안 될 때: `C1001 initialization failed` 가 계속이면 C1001 전원·TX/RX 교차 확인. 환경값이 계속 `null` 이면 SCD41·BH1750 I2C 배선·DHT22 핀.

### 1-4. ESP32 → Pi 4 UART

ESP32 를 Pi 4 에 연결한다: ESP32 GPIO25(TX) → Pi 4 GPIO15(RX), GPIO26(RX) ← GPIO14(TX), **GND 공통**.

```bash
scp hub/atlas/tools/pi4-uart-check.sh atlas:/tmp/
ssh atlas sh /tmp/pi4-uart-check.sh          # 진단만
ssh atlas sh /tmp/pi4-uart-check.sh --fix    # getty 정지·권한·보드율까지 손봄
```

| | 기대 결과 |
|---|---|
| - [ ] 노드 | `/dev/serial0 -> ttyAMA0` (09-18 실측) |
| - [ ] 원시 캡처 | 8초 동안 0 B 가 아니다 |

안 될 때: 0 B → 배선·GND·ESP32 송신. 바이트는 오는데 hub 가 rx=0 → 보드율·getty 가 핀을 나눠 먹는 중(`--fix`).

### 1-5. Pi 4 hub 서비스 — **PR #33 로 IPK 재빌드 필수**

PR #33 에 보드 전용 수정(`statistics` → 순수 `median`, 제한 Python 에 `_random` 없음)이 있다. 이전 IPK 로는 확인하지 않는다.

```bash
# Atlas 개발 컨테이너(저장소 루트 = /workspace)
cd /workspace && arc build hub/atlas
arc install <생성된 ipk> -d deskmate_pi4
# 설치·재부팅 뒤 항상 (브로커가 Pi 4 자신이면 127.0.0.1)
scp hub/atlas/tools/pi4-hub-activate.sh atlas:/tmp/
ssh atlas DESKMATE_MQTT_HOST=127.0.0.1 sh /tmp/pi4-hub-activate.sh
ssh atlas busctl --system status com.deskmate.hub1
curl http://<PI4_IP>:8765/health
```

| | 확인 방법 | 기대 결과 |
|---|---|---|
| - [ ] hub.env | activate 스크립트 출력 | `DESKMATE_HUB_MODE=live`, `DESKMATE_MQTT_HOST=127.0.0.1` |
| - [ ] 서비스 기동 | `busctl … status` | 오류 없이 응답 |
| - [ ] health | `curl …/health` | `mqtt=true` |
| - [ ] UART 통계 | 서비스 로그 30초 주기 | `rx>0`, `crc_errors=0`, `discarded` 거의 0 (09-18 기준 rx=71·crc 0) |
| - [ ] 상태 발행 | 감시 창 | `deskmate/health/hub (retain) online`, `deskmate/state/phase` 가 **10초마다**, `seq` 가 1씩 연속 |
| - [ ] **import 오류 없음** | 서비스 로그 | `ModuleNotFoundError`·`ImportError` 없음 (특히 `_random`) |

안 될 때: 재설치·재부팅 뒤 미기동/`rx=0` 은 거의 항상 activate 스크립트를 안 돌린 것(uid·`User=`·`own`·serial 권한 4가지). SSH 셸에서 서비스 실행 파일을 직접 돌리면 AppArmor 가 막는다 — 반드시 D-Bus 활성화로.
참고: 보드 경로는 UART 값을 MQTT 센서 토픽으로 다시 내보내지 않는다. 센서 값은 `state/phase` 의 `sensor_summary` 로 본다.

### 1-6. PC 키스트로크 수집기

```powershell
python -m collector --broker <PI4_IP>
```

| | 기대 결과 |
|---|---|
| - [ ] 감시 창 `deskmate/sensor/keystroke` | 타이핑 중 `typing=True`, 손 떼고 60초 뒤 `typing=False` |
| - [ ] **키 값 미수집** | `python tools/mqtt_watch.py --broker <PI4_IP> --only sensor --raw --sensor-every 1` 로 원문을 보며 아무 글자나 쳐 본다 → payload 에 친 글자가 **없다**(타이밍 통계만) |
| - [ ] hub 반영 | `state/phase` 의 `sensor_summary.keystroke` 가 생긴다 |

### 1-7. Pi 5 앱 ↔ 브로커

앱 설치·실행은 `docs/atlas-build-handoff.md`(release IPK, `flutter-atlas run -d <device> --release`).

| | 확인 방법 | 기대 결과 |
|---|---|---|
| - [ ] 첫 연결 | 앱 실행 → 시작 점검 화면 | 브로커·hub 점검 통과 후 대시보드 |
| - [ ] 주소 저장 (PR #30) | 연결 탭 → **주소 변경** → `<PI4_IP>` | 재빌드 없이 연결. 앱을 껐다 켜도 그 주소 유지 |
| - [ ] 상태 수신 | 대시보드 | 감시 창의 `fsm_state` 와 화면 국면이 같다 |
| - [ ] 피드백 왕복 | 아무 카드·정정 버튼 | 감시 창에 `deskmate/feedback/user  응답 …` |
| - [ ] 정상 시 깨끗함 (PR #31) | 화면 상단 | 경고 배지·상태 줄이 **없다** |

### 1-8. 통신 장애와 복구 (PR #30·#31)

하나씩 해 보고 원상 복구한 뒤 다음으로 간다.

| | 하는 일 | 기대 결과 |
|---|---|---|
| - [ ] 브로커 끈 채 앱 시작 | `ssh atlas 'kill $(pidof mosquitto)'` 후 앱 재시작 | 시작 점검에서 ② 브로커 응답 실패 + `pi4-broker-start.sh` 안내. 브로커 켜고 **다시 시도** → 통과 |
| - [ ] hub 끈 채 시작 | hub 서비스만 내리고 앱 재시작 | ③ 또는 ④ 안내 |
| - [ ] 실행 중 브로커 끊김 | 앱 켜 둔 채 브로커 kill → 30초 뒤 다시 기동 | 15초 안에 배지·상태 줄, 브로커 켜면 자동 재연결 후 사라짐. 진단 로그 `<앱 설치 경로>/camtest.log` 에 `MQTT 자동 재연결 #n` |
| - [ ] 상태 줄 닫기 | 상태 줄 탭해서 닫음 | 같은 문제로는 다시 안 뜸. ESP32 선을 뽑는 등 **새 문제**면 다시 뜸 |
| - [ ] 전체 리로드 | 연결 탭 → **전체 리로드** | 화면 유지한 채 재연결 |
| - [ ] 데모로 계속 | 브로커 끈 채 시작 → 데모로 계속 | 데모 화면. 앱 재시작하면 다시 실제 연결 시도 |
| - [ ] 진단 로그 | `camtest.log` | 시작 점검 결과가 기록돼 있다 |
| - [ ] hub 는 화면 없이도 돈다 | Pi 5 앱 종료 | 감시 창 `state/phase` 계속 10초마다, seq 연속 |

---

## 2. 센서 값이 판정까지 닿는가

감시 창 `deskmate/state/phase` 줄(`present=… co2_ppm=… flags=…`)과 Pi 5 화면을 같이 본다.

### 2-1. mmWave (PR #28·#29)

| | 하는 일 | 기대 결과 |
|---|---|---|
| - [ ] 재실 | 앉기 / 자리 비우기 | `present=True` / `False` (몇 초 지연은 정상) |
| - [ ] 체동 | 크게 움직이기 / 가만히 | 화면 체동 값 오르내림 |
| - [ ] 심박·호흡 | 가만히 1분 | 값이 뜨고, 락온이 풀리면 `*_valid=false` 로 **"0" 대신 빈칸** |
| - [ ] 졸음 상태 | 엎드려 정지 2~3분 | `drowsy_state` 가 `DROWSY` 쪽으로 (센서 내장 판정) |
| - [ ] 선 분리 | C1001 또는 ESP32 UART 선 뽑기 | 요약에서 `present`·`mmwave` 필드가 **사라진다**("없음"이 아니라 "모름"), hub 는 계속 발행 |

### 2-2. 환경 (PR #33 W2)

| | 하는 일 | 기대 결과 |
|---|---|---|
| - [ ] 값 범위 | 그대로 | CO₂ 400~1000 ppm, 20~28 °C, 30~60 %, 수백 lx 근처 |
| - [ ] 어두움 | BH1750 을 손으로 가림 10초 | `flags=too_dark`, 화면 이유 "조명이 어두워요 (… lx)" |
| - [ ] CO₂ 상승 | SCD41 에 숨을 몇 번 불기(측정 주기 5초) | `co2_ppm` 상승, 세션 중 +200 ppm 넘으면 `co2_rising`, 1150 ppm 넘으면 `co2_high` |
| - [ ] 더움 | DHT22 를 손으로 감싸기 1~2분 | 28 °C 를 넘으면 `too_hot`(쾌적 상한 26 °C + 여유) |
| - [ ] 센서 하나 뺌 | 환경 센서 선 분리 30초 | 해당 값·플래그만 사라지고 FSM 은 그대로 |

---

## 3. FSM 대표 경로 (실센서)

운영 타이머(`fsm.yaml`)는 기준선 5분·피로 확정 3분 등이라 한 바퀴가 15분 이상 걸린다. 두 경로 중 하나를 고른다.

**A. 제품 경로(보드 hub, 운영 타이머)** — 시간이 있을 때. 1-5 상태 그대로.

**B. 빠른 경로(PC hub, 시연 타이머)** — 보드 hub 는 내리고, ESP32 를 PC USB 에 꽂는다.

```powershell
python tools/uart_mqtt_bridge.py --port COM<n> --broker <PI4_IP>                  # ESP32 USB → MQTT 센서 토픽
cd hub; python -m deskmate_hub run --broker <PI4_IP> --config deskmate_hub/config/fsm.demo.yaml --log-dir logs/field
python tools/mock_plug.py --broker <PI4_IP>                                       # 제어 명령에 응답할 모의 플러그
```

| | 하는 일 | 기대 결과 (감시 창 `fsm_state`) |
|---|---|---|
| - [ ] 시작 | 빈자리 → 앉기 | `IDLE` → `START` → (기준선) → `CONTEXT_DETECT` → `FOCUS_*` |
| - [ ] 몰입 | 타이핑 | `FOCUS_PC` 유지, 화면 몰입 국면 |
| - [ ] 피로 | 키보드 놓고 엎드려 정지(+가능하면 CO₂ 올리기) | `FATIGUE_SUSPECT`(노란 경고) → `FATIGUE` → `CAUSE_ANALYSIS` → `ACTION_*` |
| - [ ] 회복 | 일어나 다시 타이핑 | `MONITOR` → `RECOVERY` → `FOCUS_*` |
| - [ ] 리플레이 재현 | `python -m deskmate_hub --replay logs/field/frames-<stamp>.jsonl --config deskmate_hub/config/fsm.demo.yaml` | 같은 전이 순서가 출력된다 |

---

## 4. 개입 화면·제어 (PR #33 W1·W3·재시도 게이트)

실센서로 개입을 정확한 시점에 일으키기 어렵다. **화면 흐름은 합성 시나리오로** 본다: PC 에서 리허설을 띄우고 Pi 5 앱을 PC 브로커로 돌린다.

```powershell
python tools/rehearsal_local.py --scenario demo --host 0.0.0.0     # 약 5분, 브로커 <PC_IP>:18832
python tools/mqtt_watch.py --broker <PC_IP> --port 18832 --only state,request,feedback,control,report
```

Pi 5 앱 연결 탭 → 주소 변경 → `<PC_IP>` 포트 `18832`. (끝나면 `<PI4_IP>`:1883 으로 되돌린다.) 동선·시각은 `docs/demo-scenario.md` §3.

> **화면이 국면을 따라 바뀌지 않는 건 정상이다.** 상태 탭은 기본으로 대기 위젯에 고정되고(자동 순환 꺼짐),
> 자동 알림·제안 카드만 위로 올라온다(멘토 피드백: 평소엔 위젯, 필요할 때만 카드). 국면별 화면을 보려면
> 상태 탭의 자동 순환을 켜거나 **개발자 탭**을 쓴다.
>
> **화면만 빨리 보려면(1~2분)**: hub·시나리오를 끄고 `python tools/ui_step_sim.py --broker <PI4_IP>` —
> 앱이 받는 메시지를 10초 간격으로 보낸다(대기 → 몰입 → 피로 의심 → 자세 자동 → 환경 자동 → 환경 제안 → 회복 → 리포트).
> 화면 응답은 콘솔에 찍힌다. 제어 명령까지 보려면 위 리허설(hub 포함)로.
>
> **개별 화면 하나씩**: 앱 **개발자** 탭 — 버튼마다 그 화면으로 고정, 오른쪽 미리보기로 바로 확인.
> 여기서 누른 응답은 hub 로 보내지 않는다.

| | 시점(대략) | 하는 일 | 기대 결과 |
|---|---|---|---|
| - [ ] 대기 위젯 | 0:00 | — | 시계·환경, "모든 처리는 이 기기 안에서 이뤄져요" |
| - [ ] 환경 이유 | 0:50~ | — | 몰입 화면 환경 카드에 "방이 더워요" |
| - [ ] 자동 알림 | 3:10 | 읽기만 | "자세를 바꿀 때라고 알렸어요" · 이유 · 확신도 %, **확인**·**되돌리기** 버튼 |
| - [ ] 알림 유지 | ~3:40 | 손대지 않음 | 상태가 바뀌어도 30초 남아 있다가 사라진다 |
| - [ ] 제안 카드 | 3:50 | 읽기 | "환경을 잠시 조정해볼까요?" · 이유(CO₂ 높음·상승·더움) · "n초 뒤 닫혀요" |
| - [ ] 수락 | 3:50~4:20 | **적용할게요** (거절은 **괜찮아요**) | 감시 창 `응답 accept … ms` → `명령 vent_fan.set_power='on'`·`desk_lamp.set_brightness=70` → `결과 … succeeded` |
| - [ ] 회복·리포트 | 4:10~ | 리포트 탭 | 집중 시간·피로 1회·개입 2회, 감시 창 `리포트 … metrics={"suggest_accept_rate": 1.0, …}` |

같은 리허설을 세 번 더 돌려 다른 응답을 본다.

| | 하는 일 | 기대 결과 |
|---|---|---|
| - [ ] 거절 | 제안 카드에서 **괜찮아요** | `응답 reject`, 제어 명령 **없음**, 이후 회복까지 간다 |
| - [ ] 무응답 | 제안 카드를 그냥 둔다 | 30초 뒤 카드가 닫히고 `응답 timeout`, 제어 명령 없음, hub 가 바로 다음으로 진행(180초 기다리지 않음) |
| - [ ] 되돌리기 | 수락 후 15초 안에, 또는 자동 알림에서 **되돌리기** | `응답 reject` → `명령 vent_fan.set_power='off' gate=undo`·`desk_lamp …=40 gate=undo` |
| - [ ] 정정 | 대기 화면 **지금 상태가 아니에요**(제안 카드에선 **상태가 달라요**) → "쉬는 중이에요" | `응답 correct → REST`, **화면 국면은 그대로**(판정 불변), 리포트 `correction_count` +1 |
| - [ ] 데모 소스에선 정정 없음 | 데모로 계속 상태 | 정정 버튼이 안 보인다 |

ESM 라벨 파일: 리허설은 `hub/logs/rehearsal/esm-<boot_id>.jsonl` 에 한 줄씩 생긴다(`python tools/evaluate_sessions.py hub/logs/rehearsal/esm-*.jsonl` 로 집계).
- [ ] 보드 hub 에서도 생기는지: `ssh atlas "find / -name 'esm-*.jsonl' 2>/dev/null"` — 없으면 쓰기 실패 로그(`[esm] 라벨 기록 실패`)만 있어야 하고 hub 는 계속 돈다

---

## 5. 블루투스 (PR #32, D-Bus 라 실기에서만)

| | 하는 일 | 기대 결과 |
|---|---|---|
| - [ ] 권한 | 처음 블루투스 검색 → 권한 요청 허용 | 허용하면 검색이 자동으로 이어진다 |
| - [ ] 일체형 | KLZS-L1 검색 | **한 번에 연결** 버튼 → 누르면 스피커·램프 모두 저장 |
| - [ ] 자동 재연결 | 스피커·램프 연결 후 앱 재시작 | 검색 없이 자동 연결, 연결 탭 두 줄 "정상" |
| - [ ] 램프 재연결 | 램프 전원 껐다 켬 | 다음 상태 전환 때 조명이 다시 바뀜(연결 탭 램프 줄 끊김 → 정상) |
| - [ ] 잊기 | 연결 탭에서 잊기 → 앱 재시작 | 자동 연결 안 함 |

---

## 6. 카메라 자세 (PR #29·#31, I7 결정 전 개발용)

| | 하는 일 | 기대 결과 |
|---|---|---|
| - [ ] 늦게 기동 | camsvc 를 앱보다 늦게 기동 | 30초 안에 자세 화면이 카메라로 전환, 연결 탭 자세 줄 "정상" |

I7(카메라 사용 여부) 팀 결정 전까지 시연·제출 빌드 포함 여부는 정하지 않는다(`docs/security-privacy.md` §6).

---

## 7. 시연 리허설 (통합 MVP 1사이클)

- [ ] `docs/demo-scenario.md` §5 무대 전 체크 전부
- [ ] 실센서로 피로 → 제안 → 터치 수락 → (모의/실 플러그) ON → MONITOR → RECOVERY 1회 (3장 B 경로 + 4장 수락)
- [ ] 인터넷 끊고(공유기 WAN 분리 또는 핫스팟 데이터 끔) 위 1회 다시 — 동작이 같다
- [ ] Node-RED 를 끈 상태에서도 같다

## 8. 오래 돌리기·성능 (시간 날 때)

| | 하는 일 | 기대 결과 |
|---|---|---|
| - [ ] 2시간 연속 | 1-5 상태로 두기 | `state/phase` seq 끊김 없음, 서비스 재시작 없음 |
| - [ ] 메모리·온도 | `ssh atlas top -b -n 1 \| head -20`, 온도는 보드 도구 | hub 메모리가 계속 늘지 않는다 |
| - [ ] 판정 주기 | 서비스 로그 tick 간격 | 10초 ± 1초 (PC 측정 p95 0.56 ms — 보드는 더 느려도 500 ms 안) |

---

## 9. 기록표

PR 코멘트에 붙이고, 숫자는 노션 실측 페이지에도 옮긴다.

| 단계 | 결과(통과/실패/보류) | 메모(실패 증상·숫자) |
|---|---|---|
| 1-1 네트워크 | | |
| 1-2 브로커 | | |
| 1-3 ESP32 USB | | C1001 ready 까지 걸린 시간: |
| 1-4 UART | | 원시 캡처 바이트: |
| 1-5 hub 서비스 | | rx= / crc_errors= / discarded= |
| 1-6 키스트로크 | | |
| 1-7 Pi 5 앱 | | |
| 1-8 장애 복구 | | 재연결까지 걸린 시간: |
| 2-1 mmWave | | |
| 2-2 환경 | | |
| 3 FSM 경로 | A / B | 도달 상태: |
| 4 개입 화면 | | accept/reject/timeout/undo/correct: |
| 5 블루투스 | | |
| 6 카메라 | | |
| 7 시연 1사이클 | | |
| 8 장시간 | | |
