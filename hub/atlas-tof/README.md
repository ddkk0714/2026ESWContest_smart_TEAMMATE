# atlas-tof — Pi 4 ToF 자세 서비스 (VL53L8CX ×2 → PostureNet)

```
VL53L8CX ─I2C─ ESP32(bring-up / tof_logger 펌웨어) ─USB─► /dev/ttyUSB0 ┐
VL53L8CX ─I2C─ ESP32                               ─USB─► /dev/ttyUSB1 ┼─► deskmate_tofposture ─HTTP :8771─► hub · 앱 · curl
             (USB 가 안 잡히면) PC tof_live.py --forward ─TCP─► tcp:9001 ┘   센서마다 PostureNet(CNN→GRU, 72k 파라미터)
                                                                             10 Hz 스텝 → 센서별 확률 → log-softmax 합
```

**운영 경로(10-16 결정, ESP32 는 센싱만)**: ESP32 가 UART2 로 TYPE 0x05 "L8 원본 zone"(센서마다 10 Hz, 200 B)을 보내고,
hub/atlas 의 UART 디코더가 그 줄을 `127.0.0.1:9100` 으로 넘긴다(`TOFPOSTURE_PORTS=tcp:9100`, 한 연결에 두 센서, sensor_id 로 나눔).
hub 에 넣을 중계기·패치·시험 결과는 [`integration/hub-atlas/`](integration/hub-atlas/README.md). 위 그림의 USB·PC 경로는 개발용이다.

`dataset/tof_sim_v5`(PC 작업 폴더 `sw경진대회/tof-sim-v5` 와 같은 코드)에서 MuJoCo 합성 데이터로 학습한 **자세·졸음 분류기를 Pi 4 에서 실제로 돌리는** 서비스다.
ATLAS(Yocto, musl) Headless 에는 numpy·TFLite 런타임이 없고(ECO.AI 추론 애드온은 기본 이미지에 없음, 노션
「라즈베리파이 개발 프로세스」) 컴파일러도 없으므로, 모델 순전파를 **표준 C++ 만으로** 구현했다(`src/posture_net.h`).

라벨(모델 클래스 수만큼 앞에서부터): `upright 바른자세 · lean_forward 상체 앞으로 · recline 뒤로 젖힘 · face_down 엎드림 ·
chin_rest 턱괴기 · drowsy 꾸벅 졸음 · away 자리비움 · misplaced 위치 재조정 · forward_head 거북목`.
`misplaced` 는 자세가 아니라 "장치를 다시 놓으라"는 뜻이다. 7클래스 = 호 배치 전용, 8 = + misplaced, 9 = + 거북목(10-15 추가).

## 올리는 길 두 가지

| | A. 정적 실행 파일 (내일 바로) | B. IPK 서비스 (정식) |
|---|---|---|
| 빌드 | `tools/build_static_aarch64.sh` — Zig(clang+musl), PC·서버 어디서든 | Atlas 개발 컨테이너 `arc build hub/atlas-tof` |
| 올리기 | `tools/deploy-pi4.ps1 -Ip <PI4>` → `/mnt/rawdata/deskmate_tof` 에 복사·자가 시험·시작 | `arc install` + `tools/push-model.sh` + `tools/pi4-tof-activate.sh` |
| 실행 | root SSH 로 직접(`board/start.sh`). 노션 「ATLAS 보드 SSH 접속」 9-5: SSH 터미널 직접 실행은 문서가 안내하는 정상 절차 | D-Bus activation, 설치 계정 |
| 재부팅 뒤 | `start.sh` 다시 | `pi4-tof-activate.sh` 다시 (ATLAS 는 부팅 자동 시작 불가, `docs/reboot-recovery.md`) |

**A 를 묶어 둔 것**: `sw경진대회/rpi4_tof_deploy/`(레포 밖, 모델·바이너리 포함). 그 README 가 내일 순서다.
B 는 같은 소스다. sdbus-c++ 가 SDK 에 있으면 D-Bus 이름을 소유하고(hub 와 같음), 없으면 그 부분 없이 빌드된다(camsvc 와 같음).

```sh
# A: 정적 실행 파일
python -m venv zigenv && zigenv/bin/pip install ziglang==0.13.0
ZIG="zigenv/bin/python -m ziglang" bash hub/atlas-tof/tools/build_static_aarch64.sh
# B: IPK (atlas-dev 컨테이너, 레포 루트 /workspace)
unset LD_LIBRARY_PATH; source /opt/atlas-sdk-x86_64/environment-setup-armv8a-atlas-linux
arc build hub/atlas-tof          # -> hub/atlas-tof/build/arm64/ipk/com.deskmate.tofposture1.ipk
```

## 검증된 것 (보드 없이)

| 무엇 | 결과 |
|---|---|
| C++ 순전파 vs PyTorch (export 의 golden 240프레임, x86) | 최대 logit 오차 1e-5 ~ 2.5e-5, argmax 불일치 0 |
| **aarch64 정적 실행 파일을 qemu-aarch64 로** (10-15) | 같은 golden 최대 오차 1.2e-5 ~ 4.4e-5, 불일치 0. 리플레이·HTTP·TCP 입력·방향 뒤집기 동작 |
| 프레임당 시간 | x86 Xeon 단순 루프 약 385 µs. Pi 4 는 실측 필요(수 ms 예상, 센서 2개 × 10 Hz 면 여유) |
| 줄 파서 | tof_logger JSON(tof / tof_hello / tof_err), bring-up CSV(F / P / # / Ranging started / [ERR]), 부팅 로그·깨진 줄 |
| 보드 스크립트 | 서버에서 x86 빌드로 start/status/stop/selftest 셸 로직 확인, Windows tar→ssh 복사 md5 일치 |

**실기에서 아직 모르는 것**: (1) ATLAS 커널의 USB 시리얼(CH340·CP2102) 드라이버 — 없으면 TCP 입력으로 우회,
(2) bring-up 펌웨어의 정확한 줄 형식·보드율(펌웨어 문자열에서 복원, 자동 탐색), (3) 센서 zone 방향(`TOFPOSTURE_FLIP_*`),
(4) 펌웨어 15 Hz → 10 Hz 스텝(최신 프레임 사용), (5) 시뮬→실측 성능 차이. 실측 세션은 `tof_live.py --record` 로 남긴다.

## 모델 (10-15 선택)

후보 12개를 같은 시험 세션(현재 9라벨 시뮬, 한 장치의 두 센서, 무작위 배치 32세션 + 호 배치 16세션)으로 채점했다
(`dataset/tof_sim_v5/bench_deploy.py`). 두 센서 합침, 정확도 / macro-F1:

| 모델 | 라벨 | 무작위 배치 | 호 배치 |
|---|---|---|---|
| **ubai_random_fresh** (기본) | 8 | **0.811 / 0.775** ※ | 0.896 / 0.879 ※ |
| ubai_random_lowlr | 8 | 0.798 / 0.741 ※ | 0.873 / 0.859 ※ |
| ubai_random_rich | 8 | 0.772 / 0.720 ※ | 0.861 / 0.842 ※ |
| **ubai_v5** (대안, 학습 중) | 9 | 0.632 / 0.515 | **0.883 / 0.896** |
| ubai_arc_rich | 8 | 0.681 / 0.538 ※ | 0.902 / 0.883 ※ |
| ubai_h96_random · h128_random | 9 | 0.688 / 0.570 · 0.645 / 0.550 | 0.720 / 0.681 · 0.789 / 0.761 |

※ 8라벨 모델은 거북목 구간을 바른자세로 답하면 맞은 것으로 셈. 무작위 배치(실사용)에는 random_fresh, 정면 호에 고정한
시연에서 거북목까지 보여야 하면 v5. random_fresh 의 무작위 배치 약점은 상체 앞으로 0.61·졸음 0.60.

## 설정 (환경변수 또는 실행 파일 옆 `tofposture.env`)

| 이름 | 기본값 | 쓰임 |
|---|---|---|
| `TOFPOSTURE_PORTS` | (자동) | 콤마로 나눈 입력. 비우면 `/dev/ttyUSB*` `/dev/ttyACM*` 를 시작 때와 5초마다 찾아 최대 4개. `tcp:9001` = 그 포트로 PC 가 넘기는 줄, `tcp:9100` = hub/atlas 가 넘기는 UART 0x05 줄(운영), 보통 파일 = 리플레이(`녹화.jsonl@COM5` 로 포트 하나만) |
| `TOFPOSTURE_BAUD` | auto | 921600→115200→460800→230400 중 센서 줄이 읽히는 값(바이트는 오는데 3 s 동안 줄이 없으면 다음). 숫자로 고정 가능 |
| `TOFPOSTURE_MODEL` | `<exe>/models/posture_net.bin` | 가중치 (클래스 수·은닉 폭은 파일 헤더에서 읽음) |
| `TOFPOSTURE_FLIP_ROWS` / `_COLS` | 비움 | 1 이면 받은 격자를 위아래/좌우로 뒤집는다. **UART 0x05 표준 배치는 펌웨어가 이미 장면 방향으로 보내므로 비워 둔다**(flags 는 적용한 변환 기록). USB·PC 경로에서 장착 방향을 바꿀 때만 쓴다(`tof_live.py --show` 로 정함) |
| `TOFPOSTURE_HTTP` / `TOFPOSTURE_BIND` | 8771 / 0.0.0.0 | HTTP. 8765(hub)·8770(camsvc)과 겹치지 않게. 0 이면 끔 |
| `TOFPOSTURE_RATE_HZ` | 10 | 모델 스텝 주기 = 학습 프레임률. **바꾸면 안 된다** |
| `TOFPOSTURE_STALE_MS` | 1000 | 이보다 오래 조용한 센서는 offline(융합 제외, 돌아오면 GRU 리셋) |
| `TOFPOSTURE_FUSION` | logprob | `logprob`(log-softmax 합) 또는 `mean`(softmax 평균). 아래 "두 센서 합치기" |
| `TOFPOSTURE_SMOOTH_S` | 1.0 | 화면용 확률 EMA 시간상수 |
| `TOFPOSTURE_RESET_PERIOD_S` | 0 (끔) | N 이면 마지막 리셋(또는 시작)으로부터 N s 지나면 추론 전에 GRU h·EMA 를 비운다. 시뮬 36개에서 900 은 전체 효과가 없어 기본 끔. 아래 "GRU 리셋" |
| `TOFPOSTURE_AWAY_RESET_S` | 60 | ToF 규칙 재실(coverage)이 이 시간 동안 계속 false 면 리셋, 비어 있는 동안 반복. 재실 한 프레임이면 타이머 해제. 0 이면 끔 |
| `TOFPOSTURE_PRESENT_MIN_MM` / `_MAX_MM` | 150 / 1300 | 재실 coverage 에 세는 zone 의 거리 범위(mm) |
| `TOFPOSTURE_PRESENT_MIN_COVERAGE` | 0.06 | 두 센서 중 큰 coverage(status 5·6·9·10 이고 거리 범위 안인 zone / 64)가 이 이상이면 재실(64칸 중 4칸) |
| `TOFPOSTURE_STDOUT` | change | `change` 자세 바뀔 때 / `tick` 매 스텝 / `off` |

## API

- `GET /health` — `{"ok","model_loaded","rate_hz","ticks","online_sensors","present_rule","resets":{...},"sensors":[{name,online,frames,hz,age_ms,error}]}`
- `GET /api/state` — `{"schema":"deskmate/tof_posture/1","posture","posture_ko","index","conf","since_ms",
  "probs":{...}, "instant":{...}, "present","misplaced","online_sensors","present_rule","coverage":[s0,s1],
  "resets":{periodic,away,manual,last_reset_ms,since_reset_ms,period_s,away_s,present_min_mm,present_max_mm,present_min_coverage},
  "sensors":[{name,online,posture,conf,probs,frames,steps,reused,resets,age_ms,hz,has_status,coverage,rule,hello,info,error}],
  "model":{file,classes,rate_hz,fusion,flip_rows,flip_cols,labels}}`
  `posture` 는 1 초 EMA 를 거친 값, `instant` 는 이번 스텝 값. `present` = valid 이고 `away` 가 아님(모델).
  `present_rule`·`coverage` 는 모델과 무관한 ToF 규칙 재실(아래 "GRU 리셋"), `resets` 는 리셋 계기별 횟수와 마지막 리셋 시각(`ts_ms` 와 같은 단조 시계).
  `sensors[].rule` 은 펌웨어 온보드 규칙 판정(bring-up `P,` 줄 / tof_logger `rule.posture`)으로, 모델과 비교용이다.
- `POST /api/reset` — 모든 GRU 상태 0 + EMA 비움 (장치를 옮겨 놓은 뒤). `resets.manual` 이 늘고 마지막 리셋 시각이 갱신된다.

## GRU 리셋 (10-24)

GRU 는 은닉 상태를 세션 내내 끌고 가므로 한 번 틀린 자세에 갇히면 프레임이 바뀌어도 못 나올 수 있다. 세 계기로 모든 센서의
GRU h 와 융합 EMA 를 비운다(워밍업 없음, 다음 스텝은 그 스텝의 융합 확률로 시작). 어느 계기든 "마지막 리셋 시각"을 갱신한다.

| 계기 | 조건 | 설정 |
|---|---|---|
| 주기 | 매 스텝 추론 전에 "지금 − 마지막 리셋 ≥ N s" (사람이 앉아 있어도, 자세 중간이어도) | `TOFPOSTURE_RESET_PERIOD_S` **0 = 끔(기본)**, 켤 때 900 |
| 자리 비움 | ToF 규칙 재실이 N s 동안 계속 false. 비어 있는 동안 N s 마다 반복. 재실 한 프레임이면 타이머 해제 | `TOFPOSTURE_AWAY_RESET_S` 60 |
| 수동 | `POST /api/reset` | — |

- **왜**: PC 시뮬(MuJoCo + GRU, 10-24, 시뮬 근거·실측 전)에서 보드와 같은 융합·EMA 를 넣어 보니 배치가 나쁜 장면(시드 1)에서
  GRU 가 거북목(forward_head)에 갇혀 못 빠져나왔다(0.308→0.269). 900 s 주기 + 자리 비움 60 s 리셋을 넣으면 그 장면은 0.334(+0.065) 로 회복.
  서비스는 그 시뮬 정의(PC `GruPosture`)를 그대로 따른다.
- **주기 리셋 기본값이 0(끔)인 이유**: 최종 36개 장면에서는 900 s 주기 리셋이 좋은 배치(시드 2·3)에서 −0.038 손해라 전체 효과가
  없었다(0.595→0.592). 옵션은 남기고 실측에서 고착이 보이면 켠다. 자리 비움 리셋은 60 s 유지(단독 효과는 PC 시뮬이 따로 재는 중).
- **자리 비움 근거가 모델 출력 `away` 가 아니라 coverage 인 이유**: 융합 결과 `away` 를 쓰면 GRU 가 빈 의자를 사람으로 보는
  배치에서 리셋이 영영 안 걸린다. coverage = 센서별로 target_status 5·6·9·10 이면서 거리 150~1300 mm 인 zone 비율,
  두 센서 중 큰 값이 0.06(64칸 중 4칸) 이상이면 재실. 프레임마다 판정. 센서가 전부 offline 이면 상태는 이미 비어 있으므로
  자리 비움 타이머는 돌지 않는다(기존 동작: online 0 → EMA 전부 0, valid false).
- 리셋 때 stderr 에 한 줄(`tofposture: reset (periodic|away|manual) after … s`). 둘 다 0 이면 동작은 이전과 같다
  (호스트 테스트가 골든 240 프레임으로 확인).

hub FSM 연동은 `hub/deskmate_hub/ingest` 가 이 `/api/state` 를 읽거나(`tof_feature.posture`, `data-spec.md` §6.1),
C++ hub 서비스가 같은 프로세스로 합치는 두 길이 있다. 연동 방식은 팀 결정.

## 센서 펌웨어

| 펌웨어 | 줄 형식 | 보드율 | 비고 |
|---|---|---|---|
| **VL53L8CX I2C bring-up** (김태환, 플래시 이미지 `tof-bringup-full-4MB.bin`, 10-14 전달) | CSV `F,<ms>,<64 거리>[,<64 상태>]`, `P,<ms>,<present>,<posture>,…`(온보드 규칙 판정), `# …` 안내 | 미확인(자동 탐색) | 소스가 레포에 없어 **펌웨어 문자열에서 복원한 형식**. 8×8 @ 15 Hz, 행 flip 토글(`# rows flipped/normal`). 상태값 없이 64개만 오면 거리>0 을 유효로 본다 |
| `tof_logger` (`origin/feat/tof-logger`, `pio run -e tof_logger -t upload`) | JSON `{"t":"tof",...}` + `tof_hello` | 921600 | 상태값·flip 설정 포함 |

bring-up 이미지는 4 MB 전체 플래시 덤프(부트로더 0x1000·파티션 0x8000·앱 0x10000)라 0x0 에 그대로 굽는다
(`python -m esptool --chip esp32 --port COM5 write_flash 0x0 tof-bringup-full-4MB.bin`).

## 두 센서 합치기

모델은 센서 하나의 8×8 을 보도록 학습됐다(세션마다 두 센서 중 하나를 무작위로 골라 학습). 그래서 센서마다
모델을 하나씩 두고 결과를 합친다. 규칙은 `dataset/tof_sim_v5/eval_pair.py`·`fit_fusion.py` 로 같은 세션을 두 센서 뷰로
렌더해 골랐다(random 배치, ubai_random 4 h 체크포인트, 정확도):

| 규칙 | 24세션 | 48세션 4-겹 |
|---|---|---|
| 왼쪽 센서만 / 오른쪽 센서만 | 0.665 / 0.615 | 0.633 / 0.659 |
| softmax 평균 (`mean`) | 0.705 | 0.668 |
| log-softmax 합 (`logprob`, 기본) | 0.682 | 0.718 |
| 더 확신하는 쪽 | 0.697 | 0.659 |
| 둘 중 하나라도 맞으면 (상한) | 0.854 | — |

둘 다 단일 센서보다 낫고 차이는 세션 편차 안쪽이다. 상한과의 간격(약 0.15)은 합치는 규칙으로는 못 줄이고,
두 센서를 한 모델이 같이 보는 구조(학습 변경, 센서 배치에 묶임)의 몫이다. 센서를 4개로 늘려도 이 서비스는
그대로 돈다(포트 4개) — 다만 배치가 달라지면 모델을 다시 봐야 한다.

## 호스트 테스트 (보드·센서 없이)

```sh
cd hub/atlas-tof
# export 산출물을 test/ 에 (커밋 안 함): posture_net.bin, golden.txt, 선택 replay_s0.jsonl replay_s1.jsonl
cmake -S . -B build/host -DBUILD_TESTING=ON && cmake --build build/host && (cd build/host && ctest --output-on-failure)
# cmake 가 없으면
g++ -O2 -std=c++17 -o build/host_test src/host_test.cpp src/tof_line.cpp src/device_posture.cpp
build/host_test test/posture_net.bin test/golden.txt test/replay_s0.jsonl test/replay_s1.jsonl
# 서비스 자체를 파일 입력으로
TOFPOSTURE_PORTS=test/replay_s0.jsonl,test/replay_s1.jsonl TOFPOSTURE_MODEL=test/posture_net.bin \
TOFPOSTURE_EXIT_ON_EOF=1 build/deskmate_tofposture
# aarch64 실행 파일을 PC 에서: qemu-aarch64 build/aarch64/tofposture_selftest ... (conda-forge qemu-execve-aarch64)
```

리플레이 파일은 `dataset/tof_sim_v5/tools/sim_to_toflines.py --seed 7001 --placement random --out hub/atlas-tof/test` 로
만든다(시뮬 세션을 두 센서 뷰로, 펌웨어 줄 형식 + `label`). 실측 세션은 `tof_live.py --record` 로 같은 형식으로
남으므로 그대로 리플레이·채점할 수 있다(정답은 없음).

## 파일

| 파일 | 내용 |
|---|---|
| `src/posture_net.h` | 모델 순전파 (`tof::PostureNet` load/reset/step). 원본 `dataset/tof_sim_v5/tools/posture_net_ref.cpp` |
| `src/tof_line.{h,cpp}` | 펌웨어 줄 파서 — tof_logger JSON, bring-up CSV (라이브러리 없음) |
| `src/line_source.{h,cpp}` | tty(raw, 보드율 자동)·TCP(`tcp:<port>`)·파일 읽기 스레드, 끊기면 다시 연결 |
| `src/device_posture.{h,cpp}` | 센서 N개 × 모델, 10 Hz 스텝, 방향 보정, offline 처리, 융합, EMA, 상태 JSON |
| `src/http_server.{h,cpp}` | `/health` `/api/state` `/api/reset` |
| `src/main.cpp` | 설정(`tofposture.env`)·포트 자동 탐색(5초마다)·루프·stdout. sdbus-c++ 가 있으면 D-Bus 이름 소유 |
| `src/host_test.cpp` | 파서·골든·리셋 규칙(주기·자리 비움·끔=골든과 일치)·리플레이 테스트 (보드에서는 `tofposture_selftest`) |
| `board/start.sh` `stop.sh` `status.sh` `selftest.sh` `tofposture.env.example` | A 경로에서 보드에 함께 올리는 스크립트(BusyBox sh) |
| `tools/build_static_aarch64.sh` | A 경로 정적 빌드(Zig) |
| `tools/deploy-pi4.ps1` / `deploy-pi4.sh` | A 경로: PC → 보드 복사·자가 시험·시작 |
| `tools/pi4-tof-activate.sh` | B 경로: 재부팅·재설치 뒤 계정·D-Bus·USB 권한·env 정리 + 기동 |
| `tools/push-model.sh` | B 경로: export 폴더의 `posture_net.bin` 을 보드에 |
| `pc/tof_wall_noise.py` | 벽 잡음 측정. 안내 진행(`session`: 벽 45/50/60 cm 각 5분, `--with-person`·`--steps` 로 빈 자리 → 가만히 → 평소 작업, 소리·Enter), 중계기 `/raw` 원본 녹화 → 칸별 σ·느린 성분·끄덕임 대역·필터 비교, 거리별 시뮬 `L8Params` 맞춤, 벽 vs 사람 비교. `--pi5` 로 ssh 터널 자동. 절차 [`docs/WALL_NOISE_TEST.md`](docs/WALL_NOISE_TEST.md) |
| `files/` | ARC 패키징(serviceinfo, .apptype, D-Bus conf/service) |
