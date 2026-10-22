# Pi 4 ToF 자세 모델 배포 묶음 (2026-10-17)

VL53L8CX 센서를 ESP32 로 읽고, 시뮬레이션으로 학습한 자세 모델을 **Raspberry Pi 4(ATLAS AI Native OS Headless)에서 실제로 돌리는** 묶음이다.
Atlas SDK 컨테이너도, 보드에 Python 도 필요 없다. SSH 로 `/mnt/rawdata` 에 올려 직접 실행한다.
노션 「ATLAS 보드 SSH 접속」 9-2·9-5 의 "모델·실행 파일은 scp 로 쓰기 가능 경로에, SSH 터미널에서 직접 실행" 절차와 같다.

```
VL53L8CX ─I2C─ ESP32(bring-up 펌웨어) ─USB─► Pi 4 /dev/ttyUSB0 ─► deskmate_tofposture ─HTTP :8771─► curl · hub · 앱
                                     (USB 가 안 잡히면) PC tof_live.py --forward ─TCP─┘   센서마다 모델 1개(10 Hz), 두 센서 확률 합침
운영(예정): ESP32 ─UART2 TYPE 0x05─► hub/atlas ─TCP 127.0.0.1:9100─┘   (아래 "운영 경로")
```

## 10-15 판에서 바뀐 것

- **모델을 9 라벨로 바꿨다.** 거북목(`forward_head`)까지 구분한다. 기본 = 무작위 배치 학습 ubai_r9_lowlr, 대안 = 정면 호 시연용 ubai_a9_demo.
- **실기 결과 반영**: 하드웨어 담당자가 10-15 판을 실제 Pi 4 에서 돌렸다(자가 시험 PASS, 10 Hz, 서비스 4,300 s). 다만 ESP32 의
  CH340(1a86:7523) USB 드라이버가 ATLAS 에 없어 USB 직결은 안 됐다 → 5번(PC 경유) 또는 운영 경로(UART2).
- **운영 경로 입력**: ESP32 가 UART2 로 보내는 TYPE 0x05(L8 원본 zone)를 hub/atlas 가 넘겨 주면 `TOFPOSTURE_PORTS=tcp:9100` 하나로 두 센서를 받는다.
  방향은 펌웨어 flags 대로 자동, frame_seq 로 유실·중복·재부팅을 가른다.
- `status.sh` 가 꽂힌 USB 장치와 커널의 USB 시리얼 드라이버를 보여 준다(CH340 대신 쓸 RP2040·ESP32-S3 판단용).
- PC 녹화(`tof_live.py --record`) 새 형식을 보드 서비스가 리플레이할 수 있다(`녹화.jsonl@COM5`).

## 받은 사람이 먼저 볼 것

- **필요한 것**: Windows 10/11 PC(기본 탑재 `ssh`·`tar`·PowerShell 만 씀), Pi 4 에 SSH 가 되는 같은 망,
  보드 IP(기본 `172.16.34.146`, DHCP 라 바뀔 수 있음). PC 에서 센서를 미리 보려면 Python 3.10 이상.
- **압축은 경로 아무 데나** 풀어도 된다(한글 경로 확인함). 풀고 나서 이 폴더에서 명령창(cmd 또는 PowerShell)을 연다.
- **올리기는 한 줄**: `deploy-pi4.bat 172.16.34.146` — 보드 확인 → 복사 → 자가 시험(PASS) → 서비스 시작.
  `.bat` 은 PowerShell 실행 정책(인터넷에서 받은 스크립트 차단)을 이 실행에서만 비켜 가려고 둔 것이다.
- **SSH 가 비밀번호를 물으면** 그냥 Enter(보드 root 는 빈 비밀번호). 여러 번 물을 수 있다. 그래도 거절되면 이 보드에
  SSH 키가 등록된 PC 에서 올린다(`ssh root@<IP>` 가 바로 되는 PC).
- 다 되면 `curl.exe http://172.16.34.146:8771/api/state` 에 자세가 나온다. 자세한 순서·문제 해결은 아래 "올리는 순서".

## 들어 있는 것

| 경로 | 무엇 |
|---|---|
| `pi4/` | **이 폴더째 보드의 `/mnt/rawdata/deskmate_tof` 로 복사한다** |
| `pi4/deskmate_tofposture` | 서비스 실행 파일. aarch64 정적 링크(어떤 보드 라이브러리에도 기대지 않음), 550 KB |
| `pi4/tofposture_selftest` | 보드 자가 시험(모델이 PyTorch 와 같은 답을 내는지 + 시뮬 세션 재생) |
| `pi4/models/posture_net.bin` | **기본 모델** — ubai_r9_lowlr, 무작위 배치 학습, 9 라벨(거북목·위치 재조정 포함) |
| `pi4/models/a9_demo/posture_net.bin` | 대안 모델 — ubai_a9_demo, 정면 호 배치 학습, 9 라벨(위치 재조정은 사실상 안 냄) |
| `pi4/start.sh` `stop.sh` `status.sh` `selftest.sh` | 보드에서 쓰는 스크립트(BusyBox sh) |
| `pi4/tofposture.env.example` | 설정. 처음 `start.sh` 가 `tofposture.env` 로 복사하고, 이후 재배포해도 `tofposture.env` 는 안 덮는다 |
| `deploy-pi4.bat` | **Windows 에서 이것만 쓰면 된다** — 아래 `.ps1` 을 실행 정책과 무관하게 부른다 |
| `deploy-pi4.ps1` / `deploy-pi4.sh` | PC → 보드 복사 + 자가 시험 + 시작을 한 번에 (`.sh` 는 Linux·macOS·Git Bash) |
| `pc/tof_live.py` | PC 에서 센서·모델 확인(형식 `--echo`, 방향 `--show`, 보드로 넘기기 `--forward`) |
| `pc/posture_net*.onnx` | 위 두 모델의 ONNX(PC 확인용, ATLAS ECO.AI 경로 후보) |
| `esp32/tof-bringup-full-4MB.bin` | ESP32 펌웨어(김태환 님 VL53L8CX bring-up, 4 MB 전체 플래시)와 출력 형식 메모 |
| `source/hub-atlas-tof/` | 서비스 소스·빌드 스크립트(팀 레포 `hub/atlas-tof` 사본). 실행 파일을 다시 만들 때만 필요 |
| `score_1017_data6cd.json`, `score_1017_data.json`, `SHA256SUMS` | 아래 모델 점수의 원자료(두 벌의 시험 세션), 파일 체크섬 |

## 모델

후보 6개를 같은 시험 세션으로 채점해 골랐다. 한 장치의 두 센서를 같은 동작으로 렌더한 처음 보는 세션이고, 거북목 경계를 고친 시뮬(6cd987e1)
기준이다(옛 시뮬 d560dbd2 로도 순위가 같다). 두 센서 합침, 정확도 · macro-F1:

| 모델 | 라벨 | 무작위 배치 32세션 (실사용) | 정면 호 배치 16세션 (시연) |
|---|---|---|---|
| **ubai_r9_lowlr** (기본) | 9 | **0.840 · 0.812** | 0.883 · 0.887 |
| **ubai_a9_demo** (대안) | 9 | 0.667 · 0.549 | **0.948 · 0.942** |
| ubai_r9_fresh | 9 | 0.789 · 0.777 | 0.930 · 0.940 |
| ubai_m9_mixed | 9 | 0.813 · 0.758 | 0.930 · 0.939 |
| 10-16 기본이던 random_fresh 최종본 (8 라벨) | 8 | 0.799 · 0.731 (거북목을 바른자세로 쳐 주면 0.842 · 0.825) | 0.829 · 0.786 |

- **기본(r9_lowlr)** 자세별 재현율, 무작위 배치: 바른자세 0.93 · 엎드림 0.99 · 턱괴기 0.93 · 자리비움 0.91 · 젖힘 0.80 · **위치 재조정 0.80** ·
  상체 앞으로 0.77 · **거북목 0.59 · 졸음 0.57**. 정면 호에서는 거북목 0.48 · 졸음 0.85 로 대안보다 약하다.
- **대안(a9_demo)** 정면 호 재현율: 바른자세 0.96 · 상체 앞으로 0.91 · 젖힘·엎드림·턱괴기·자리비움 1.00 · 졸음 0.97 · **거북목 0.78**.
  호 배치로만 학습해 무작위 배치나 잘못 놓인 장치에는 쓰면 안 된다(위치 재조정 0.00).
- **어느 걸 쓰나**: 사용자가 장치를 아무 데나 놓는 실제 조건이면 기본. 시연처럼 장치를 정면에 고정하고 거북목·졸음을 보여 줘야 하면 대안
  (`tofposture.env` 에 `TOFPOSTURE_MODEL=models/a9_demo/posture_net.bin` 한 줄 추가 후 `start.sh`). 둘 사이가 필요하면 r9_fresh(묶음에는 없음).
- **시뮬 점수는 실제 성능이 아니다.** 실제 센서 잡음·반사율은 보정 전이다. 실측 녹화로 다시 재야 한다.

## 올리는 순서

**0. 준비** — Pi 4 전원·LAN, PC 는 보드에 SSH 되는 망(보드 기본 `172.16.34.146`, DHCP 라 바뀔 수 있음 → 노션 「ATLAS 보드 SSH 접속」 3번 포트 스캔).
ESP32 에 bring-up 펌웨어가 없으면 굽는다(COM 번호는 장치 관리자에서 확인):
```powershell
python -m pip install esptool
python -m esptool --chip esp32 --port COM5 --baud 460800 write_flash 0x0 esp32\tof-bringup-full-4MB.bin
```

**1. PC 에서 센서부터 확인** (보드 문제와 센서 문제를 섞지 않으려고). ESP32 를 PC 에 꽂고:
```powershell
cd pc
python -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python tof_live.py --ports COM5 --echo     # 받은 줄 그대로: F,<ms>,... 가 보이면 형식·보드율 OK
.\.venv\Scripts\python tof_live.py --ports COM5 --show     # 1초마다 8×8 거리(cm)
```
`--show` 로 **방향**을 정한다. 센서 시야의 위쪽에 손을 대면 **위쪽 줄**이, 센서에서 볼 때 왼쪽에 대면 **왼쪽 열**이 가까워져야 한다.
반대면 `--flip-rows` / `--flip-cols` 를 붙여 맞는 조합을 찾고, 그 값을 4번에서 보드에 넣는다. 그다음 옵션 없이 돌리면 자세가 찍힌다.

**2. 보드에 올리기** (이 폴더에서)
```powershell
.\deploy-pi4.bat 172.16.34.146
```
보드 확인 → 복사 → `selftest`(PASS 가 나와야 함) → 서비스 시작까지 한다. 다시 올려도 보드의 `tofposture.env`(설정)는 유지된다.

**3. ESP32 를 Pi 4 USB 에 꽂고 확인**
```powershell
ssh root@172.16.34.146 sh /mnt/rawdata/deskmate_tof/status.sh
curl.exe http://172.16.34.146:8771/api/state
```
`/dev/ttyUSB0`(또는 `ttyACM0`)이 보이면 서비스가 5초 안에 잡는다(5초마다 새로 꽂힌 포트를 찾아 최대 4개까지 붙인다).
**10-15 실기에서는 ESP32 DevKit 의 CH340 이 꽂혀도 드라이버가 없어 노드가 안 생겼다** — 그때는 5번으로 간다.
`status.sh` 의 "꽂힌 USB 장치"·"이 커널의 USB 시리얼 드라이버" 에 `cdc_acm` 이 보이면 RP2040 USB-UART 브리지나 ESP32-S3 내장 USB 는 `ttyACM` 으로 잡힌다.
`/health` 의 `online_sensors` 가 1 이상이고 센서별 `hz` 가 15 근처면 정상이다.

**4. 방향 보정·모델 바꾸기** — 보드의 `/mnt/rawdata/deskmate_tof/tofposture.env` 를 고치고 `sh start.sh`.
`TOFPOSTURE_FLIP_ROWS/COLS` 는 비우면 자동(UART 0x05 는 펌웨어 flags 대로, USB·PC 경로는 안 뒤집음), 0/1 이면 강제다.
```sh
ssh root@172.16.34.146 "cd /mnt/rawdata/deskmate_tof && sed -i 's/^TOFPOSTURE_FLIP_COLS=.*/TOFPOSTURE_FLIP_COLS=1/' tofposture.env && sh start.sh"
```

**5. USB 가 안 보일 때** — ATLAS 커널에 ESP32 USB 칩(CH340·CP2102) 드라이버가 없을 수 있다(`status.sh` 의 커널 메시지로 확인).
그러면 센서는 PC 에 꽂고 줄만 보드로 넘긴다. **모델은 그대로 Pi 4 에서 돈다.**
```sh
# 보드: tofposture.env 의 TOFPOSTURE_PORTS=tcp:9001,tcp:9002 로 바꾸고 sh start.sh
```
```powershell
.\.venv\Scripts\python tof_live.py --ports COM5,COM6 --forward 172.16.34.146:9001,172.16.34.146:9002
```

**재부팅하면** 프로세스는 사라진다(파일은 남는다). `ssh root@<IP> sh /mnt/rawdata/deskmate_tof/start.sh` 를 다시 돌린다.
ATLAS 는 `/data` 가 늦게 마운트돼 부팅 자동 시작이 안 된다(`docs/reboot-recovery.md`, 10-14 실측).

## 운영 경로 (UART2, 펌웨어·hub 준비되면)

팀 결정(10-16): ESP32 는 센싱만, 모델은 Pi 4. ESP32 가 UART2 로 TYPE 0x05 "L8 원본 zone"(센서마다 10 Hz, 200 B)을 보내고,
hub/atlas 의 UART 디코더가 그 줄을 `127.0.0.1:9100` 으로 넘긴다. 이 서비스는 `tofposture.env` 에 `TOFPOSTURE_PORTS=tcp:9100` 한 줄이면 된다
(두 센서가 한 연결로 오고 센서 번호로 나뉨, `/api/state` 에 `tcp:9100#s0`·`#s1`).

- hub 쪽 변경(중계기·패치)은 팀 레포 `hub/atlas-tof/integration/hub-atlas/`. hub/atlas 는 팀 공용이라 hub 담당이 적용·빌드한다.
- 보드율 460800 전환은 펌웨어와 **같은 날** 한다(한쪽만 바꾸면 모든 프레임이 끊김). 날짜는 김태환 님과 Pi 4 담당이 정한다.
- 서비스가 0x05 를 읽는 규칙: 거리 0 = 타깃 없음, 방향은 flags b2/b3 대로, frame_seq 차이 0 은 중복(버림)·2..10 은 짧은 유실(진행)·그 밖은 그 센서 리셋,
  valid=0(새 측정 없음)이 1 s 넘으면 그 센서는 판정에서 빠진다(`/api/state` 의 `stale`).

## 결과 읽기

```
GET  /health      {"ok","model_loaded","rate_hz","ticks","online_sensors","sensors":[{name,online,frames,hz,age_ms,error}]}
GET  /api/state   {"posture":"upright","posture_ko":"바른자세","conf","since_ms","probs":{...},"instant":{...},
                   "present","misplaced","online_sensors","sensors":[{name,online,posture,conf,frames,hz,rule,...}],"model":{...}}
POST /api/reset   모든 센서의 모델 상태(GRU)를 0 으로 — 장치를 옮겨 놓은 뒤
```
- `posture` 는 1초 이동평균을 거친 값, `instant` 는 이번 0.1초 값이다.
- `sensors[].rule` 은 ESP32 펌웨어의 온보드 규칙 판정(`P,` 줄)이다. 모델과 나란히 비교할 수 있다.
- 서비스 stdout(`tofposture.out`)에는 자세가 바뀔 때마다 위 JSON 이 한 줄씩 남는다.

## 확인된 것과 아닌 것

| 확인 | 결과 |
|---|---|
| **실제 Pi 4 (10-15 판, 하드웨어 담당자)** | 자가 시험 PASS(골든 1.24e-5), 실시간 10 Hz 60 s, 서비스 :8771 4,300 s. CH340 USB 드라이버 없음 |
| 이 판의 aarch64 실행 파일을 qemu-aarch64 로: 모델 vs PyTorch 골든 240프레임 | 최대 오차 2.4e-5(기본)·7.0e-5(대안), 판정 불일치 0 |
| 같은 실행 파일로 시뮬 세션 재생(두 센서, 226초) | PASS. 합친 정확도 0.99(기본)·1.00(대안) — 이 세션은 바른자세·턱괴기 위주 |
| UART 0x05 입력(중계기 → 서비스, 두 센서 30 s, frame_seq 0xFFFF 순환 포함) | 600줄 전부, 리셋·유실·중복 0, 각 10 Hz. 펌웨어 교차 벡터 바이트 일치 |
| 서비스: HTTP·TCP 입력·방향 뒤집기·bring-up CSV·tof_logger JSON 형식 | 동작 |
| 보드 스크립트(start/status/stop/selftest) | 서버에서 x86 빌드로 셸 로직 확인 |
| Windows `deploy-pi4.ps1` 의 tar→ssh 복사 | 바이너리 md5 일치 |

**아직 모르는 것**: 이 판(9 라벨 모델·UART 0x05 입력)의 실제 Pi 4 실행, ATLAS 커널의 `cdc_acm` 유무, bring-up 펌웨어의 상태값 포함 여부,
센서 장착 방향, 실측 정확도(실측 녹화가 아직 없다). bring-up 펌웨어는 15 Hz 로 보내고 모델은 10 Hz 로 최신 프레임만 쓴다.

## 다시 만들기

- 모델: `dataset/tof_sim_v5/export.py --run <RUN>` → `posture_net.bin`·`golden.txt`·`posture_net.onnx`. 후보 비교는 `dataset/tof_sim_v5/bench_deploy.py`.
- 묶음: 서버에서 `hub/atlas-tof/tools/assemble_bundle.sh <기본 export> <대안 export> <대안 이름> <출력>`(ARM 빌드·자가 시험),
  PC 에서 `hub/atlas-tof/tools/package_bundle.py`(배포 스크립트·도구·펌웨어·소스·체크섬·zip).
- 실행 파일: `source/hub-atlas-tof/tools/build_static_aarch64.sh` (Zig 0.13, `pip install ziglang`, Linux·WSL). 소스는 같은 폴더 `src/`(팀 레포 `hub/atlas-tof`).
- 정식 서비스(IPK, D-Bus 활성화)로 올리는 길은 `hub/atlas-tof/README.md`. 같은 소스다.

## 2026-10-22 ESP32 ↔ Pi 4 USB 직결 (GPIO UART2 대신)
- 연결: ESP32 USB-C → Pi 4 USB-A. 전원도 Pi 4 USB 에서. 핫스팟 So 에서 Pi 5 = 172.20.10.10(바뀜), Pi 4 는 Pi 5 뒤 랜선(172.16.34.199, `ssh pi4-via-pi5`).
- 막혔던 것: ATLAS 커널(6.12.25-v8)에 **ch341 드라이버가 없어** CH340(1a86:7523)이 꽂혀도 /dev/ttyUSB 가 안 생김(있는 건 cp210x·ftdi_sio·pl2303·generic·cdc_acm).
- 푼 방법(보드에서 확인): ① `echo "1a86 7523" > /sys/bus/usb-serial/drivers/generic/new_id` → /dev/ttyUSB0 생김(재부팅 전까지 유지)
  ② generic 드라이버는 칩에 보드율을 안 쓰므로 `/dev/bus/usb/BBB/DDD` 에 usbfs 제어 전송으로 115200·8N1 을 직접 씀(`relay/ch340.h`, ch341.c 와 같은 값.
  WRITE_REG 0x9A 는 wValue=레지스터 쌍 0x1312/0x2518, wIndex=값 — 순서를 바꾸면 보드율이 안 바뀐다).
- 올린 것(`/mnt/rawdata/deskmate_tof/`): `uart_relay_board`(장치 `auto`: ttyUSB0~3 → ttyACM0~3 → ttyAMA0, 열 때마다 CH340 이면 설정, 10 s 무데이터면 다시 열기,
  옛 판은 `uart_relay_board.1017.bak`), `relay.sh`(기본 auto, USB 노드 getty 정리), `ch340cfg`(진단: `./ch340cfg auto 115200`).
- 펌웨어: `firmware/esp32_sensor_node` 새 환경 **esp32dev_usb**(DESKMATE_USB_FRAMES=1, fw 0x0124). UART2 와 같은 COBS 프레임을 USB 로도 낸다.
  USB 로는 JSON 진단·한 글자 명령이 없다(tools/sensor_monitor.py 는 esp32dev 로 구워야 함). 굽기: ESP32 를 PC 에 꽂고 `pio run -e esp32dev_usb -t upload --upload-port COMn`.
  부팅 뒤 첫 프레임까지 ≈34 s(C1001 없으면 begin 대기 + ToF init). PC 에서 포트를 기본값으로 열면 DTR/RTS 때문에 다운로드 모드(`boot:0x1`)로 멈추니
  pyserial 은 dtr=False·rts=False 로 열고 RTS 펄스로 리셋한다.
- 결과: relay.log `ch340 version=0x31 baud=115200`, type5 증가, /health online_sensors 1(tcp:9100#s1 ≈5 Hz), /raw 격자. 센서 A(0)는 PC·Pi 4 전원 모두에서 미기동(mask=2) — 배선 확인 필요.
- PC 뷰어: `pc/view.ps1`(ssh 터널 8771·8772 + http.server 8790 + 브라우저, `posture_view.html?base=&raw=`). 터널 없이 직접 닿으면 `-Direct <IP>`.
- 주의: 팀 hub(deskmate_hub_service)는 /dev/serial0 만 읽고 지금 안 떠 있음. USB 경로에서 hub 에도 프레임을 주려면 중계기가 hub 로 복사해야 한다(미구현). 중계기와 hub 가 같은 tty 를 열면 바이트가 갈린다.
