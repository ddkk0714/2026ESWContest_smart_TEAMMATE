# Pi 4 ToF 자세 서비스 운영 (deskmate_tofposture)

기준일 2026-10-17 실기 · 소스 `hub/atlas-tof/` · 자세 모델 후보 B(`CLAUDE.md` §5 열린 항목 11)

ESP32 가 VL53L8CX 두 개의 원본 8×8 을 UART `0x05` 로 Pi 4 에 보내면, Pi 4 의 `deskmate_tofposture` 가 센서마다
PostureNet(CNN+GRU, 9 라벨)을 10 Hz 로 돌려 두 센서 확률을 합치고 HTTP `:8771` 로 낸다.
보드에 Python 이 없어도 되는 aarch64 정적 실행 파일이다.

```
VL53L8CX ×2 ─I2C─ ESP32 ─UART2 0x05─► Pi 4 /dev/ttyAMA0 ─► [UART 를 읽는 프로세스 하나] ─ UART\t{...} 줄 ─► 127.0.0.1:9100
                                                              ├ hub/atlas (uart_relay, 운영 경로)               │
                                                              └ uart_relay_board (hub 없이, 개발·시연)           ▼
                                                                                         deskmate_tofposture :8771 ─► curl · 앱 · posture_view.html
```

## 1. 올리기

배포 묶음(`hub/atlas-tof/tools/package_bundle.py` 로 만든 zip)을 풀고:

```powershell
.\deploy-pi4.bat <Pi4 IP>      # 보드 확인 → /mnt/rawdata/deskmate_tof 로 복사 → selftest(PASS) → 시작
```

다시 올려도 보드의 `tofposture.env` 는 안 덮는다. Pi 4 가 Pi 5 뒤 유선에만 있으면 ssh 별칭(ProxyJump)을 IP 자리에 넣으면 된다.

## 2. 입력 경로 — UART 는 한 프로세스만 읽는다

**`/dev/ttyAMA0` 를 두 프로세스가 같이 열면 바이트가 둘로 갈려 둘 다 프레임을 못 세운다.**
10-17 실기: hub 와 `uart_relay_board` 를 같이 띄우자 COBS·CRC 오류가 급증하고 자세 서비스 센서가 0 개가 됐다.
아래 중 **하나만** 쓴다.

| 경로 | 띄우는 법 | 언제 |
|---|---|---|
| **hub 중계 (운영)** | hub IPK 에 `uart_relay` 가 들어간 판(PR #44). `DESKMATE_TOF_RELAY_PORT=9100` 기본 | hub 가 도는 정상 운영 |
| **보드 중계기** | `sh /mnt/rawdata/deskmate_tof/relay.sh` (`stop`·`status`). hub 프로세스는 내려 둘 것 | hub 없이 자세만 볼 때, 시연 |
| **PC 경유** | 보드 env `TOFPOSTURE_PORTS=tcp:9001`, PC `python pc/tof_live.py --ports COM5 --forward <Pi4>:9001` | ESP32 를 PC USB 에 꽂고 bring-up·tof_logger 펌웨어를 쓸 때 |

서비스 쪽 설정은 앞의 둘 다 `TOFPOSTURE_PORTS=tcp:9100` 한 줄이다(두 센서가 한 연결로 오고 `tcp:9100#s0`·`#s1` 로 나뉜다).

`uart_relay_board` 는 덤으로 HTTP `:8772/raw` 에 센서별 마지막 원본 프레임(거리 mm·상태 64칸·frame_id·flags)을 낸다.
자세가 이상할 때 센서가 무엇을 보는지 확인하는 용도다.

## 3. 방향 — 서비스에서 뒤집지 않는다

펌웨어(`TofArray`)가 ST 의 zone 순서를 장면 방향(row 0 = 위, col 0 = 센서 기준 왼쪽)으로 **이미 돌려서** 보낸다.
`0x05` 의 `flags`(b0 flip_rows, b1 flip_cols)는 "적용한 변환" 기록이지 "받는 쪽이 뒤집으라" 는 뜻이 아니다(data-spec §13.1).

- `tofposture.env` 의 `TOFPOSTURE_FLIP_ROWS`·`TOFPOSTURE_FLIP_COLS` 는 **비워 둔다**. 서비스는 표준 배치를 안 뒤집는다.
- 10-17 에 중계기가 이 flags 를 "뒤집어라" 로 옮겨 좌우가 거울상이 된 일이 있었다(고침). `/api/state` 의 `sensors[].flip_cols` 가 `false` 인지 본다.

## 4. 보기

- `curl http://<Pi4>:8771/api/state` — `posture`(1 초 이동평균)·`instant`·`probs`·센서별 판정·`online_sensors`
- `curl http://<Pi4>:8771/health` — 센서별 `online`·`hz`(≈5)·`age_ms`
- **실시간 화면** `hub/atlas-tof/pc/posture_view.html` — 자세·확률 막대·센서 상태·60 초 띠 + 두 센서 원본 8×8 거리 격자.
  `file://` 로 열면 브라우저가 요청을 막으므로 그 폴더에서 `python -m http.server 8790 --bind 127.0.0.1` 뒤
  `http://127.0.0.1:8790/posture_view.html`. 서비스·중계기 모두 CORS `*` 라 보드 주소만 맞추면 된다.

## 5. 재부팅 뒤

ATLAS 는 `/data` 가 늦게 마운트돼 자동 시작이 안 된다. 손으로:

```sh
ssh root@<Pi4> sh /mnt/rawdata/deskmate_tof/start.sh
ssh root@<Pi4> sh /mnt/rawdata/deskmate_tof/relay.sh     # 보드 중계기 경로일 때만
```

## 6. 증상별 원인

| 증상 | 원인 | 확인·해결 |
|---|---|---|
| `online_sensors` 0, 서비스 로그의 `lines` 는 늘어남 | 줄 형식을 못 읽음 | 옛 서비스는 `payload` 첫 바이트 1(옛 v1)만 읽고 `"key": "v"` 처럼 콜론 뒤 공백이 있으면 못 읽었다. 이 판은 표준 배치와 공백 둘 다 읽는다 |
| `relay.log` 의 `bad` 가 계속 늘고 센서가 오락가락 | UART 를 두 프로세스가 읽음 | `for p in /proc/[0-9]*; do ls -l $p/fd 2>/dev/null \| grep -q ttyAMA0 && cat $p/comm; done` 로 하나인지 본다. hub 는 D-Bus 요청이 오면 다시 뜰 수 있다 |
| 좌우가 반대로 판정 | 두 번 뒤집힘 | §3. `sensors[].flip_cols` 가 `true` 면 env·중계기를 본다 |
| `misplaced`(위치 재조정)만 계속 | 장치가 사람을 시야 가운데에 못 둠 | `/raw` 격자에서 사람(가까운 칸)이 아래 몇 줄에만 있으면 장치가 위를 본다. 아래로 기울이거나 낮춘다. 기본 모델은 무작위 배치로 학습해 장치가 어긋나면 일부러 이 라벨을 낸다 |
| `hz` 가 5 보다 한참 낮음 | 프레임 유실 | `seq_gaps`·`resets`, 중계기 `bad`. 보드율·배선 |

## 7. 아직 모르는 것

- 실측 정확도. 시뮬 점수(무작위 배치 0.84 · 0.81)는 실제 성능이 아니다. 지시 자세 실측 세션으로 다시 재야 한다.
- 잡음 기준선(`docs/tof-noise-baseline.md`)은 지그가 나온 뒤 잰다.
- hub 중계 판 IPK 의 실기 설치(그때 보드 중계기는 내린다).
