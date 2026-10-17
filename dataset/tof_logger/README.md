# VL53L8CX 지시 자세 수집기 (tof_logger)

목표: **L8(VL53L8CX 8×8) 하나로 자세를 판단하는 AI 모델**의 학습·평가 데이터를 모은다.
L9 는 센서 실장 폴트로 쓰지 못해서(`feat/vl53l9cx-rewrite` 기록) L8 로 간다.

```
L8 ─I2C─ ESP32 (env:tof_logger) ──USB 921600──► tof_log.py ──► data/sessions/<세션>/
          원본 8×8 + 규칙 판정(TofPosture)          + 지시 자세(정답)      frames.jsonl · meta.json
```

- **수집 모드 전용이다.** 운영 펌웨어(`env:esp32dev`)에는 들어가지 않는다. 원본 zone 배열은 USB 로
  개발 PC 에만 오고 MQTT·UART2 운영 경로로는 나가지 않는다(firmware README "원칙").
- 한 프레임에 **센서 원본 8×8 거리·상태값**, **같은 프레임의 규칙 판정**, **그때 안내 중인 지시 자세**가 같이 남는다.
  - 지시 자세가 정답이다. 규칙 판정은 비교 대상이자 약한 라벨이다.
  - 규칙 라벨만으로 학습하면 모델은 규칙의 오류까지 그대로 배운다. 그래서 **평가는 항상 지시 자세로 한다.**
- 카메라·마이크를 쓰지 않는다. 센서는 L8 하나다.

## 준비

### 1. 펌웨어

```sh
cd firmware/esp32_sensor_node
pio run -e tof_logger -t upload
```

**2026-10-16 부터 센서 두 개(VL53L8CX ×2)를 한 격자(16×8, 또는 8×16)로 붙여 기록한다.** 배선은 운영 펌웨어와 같다 —
`include/pins.h` 와 [`docs/breadboard-esp32-node.md`](../../docs/breadboard-esp32-node.md) (SDA 23 · SCL 18 · LPn A 32 / B 33 ·
NCS·SPI_I2C_N GND). 한쪽 센서만 올라오면 8×8 로 떨어지고, `w`·`h` 가 줄마다 들어 있으니 수집기는 그대로 돈다.
배치·방향은 `platformio.ini` 의 `-DDESKMATE_TOF_STACK_ROWS/FLIP_ROWS/FLIP_COLS/SWAP/COUNT` 로 바꾼다(`src/tof_logger/main.cpp` 위쪽).

부팅하면 센서 펌웨어(약 84 KB)를 올리느라 몇 초가 걸린다. 실패하면 `{"t":"tof_err","where":"init",...}` 를
보내고 3초마다 다시 시도한다. 수집기 화면에 `센서 오류: init` 으로 나온다.

### 2. PC

```sh
cd dataset/tof_logger
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 사용법

```sh
python tof_log.py --port COM5 --user soyeon --protocol --note "모니터 아래 20cm, 사람까지 60cm"
python tof_log.py --port COM5 --user soyeon --minutes 30        # 자유 기록(규칙 판정만, 정답 없음)
python tof_log.py --fake --user test --protocol --fast          # 하드웨어 없이 배관만 확인
python inspect_session.py data/sessions/<세션>                    # 품질·규칙 정확도·자세별 거리맵
python inspect_session.py data/sessions/<세션> --npz s.npz        # 학습용 배열
```

- **프로토콜**(`config.yaml`, 24단계 약 5분)은 화면 문구와 삑 소리로 안내한다. 문구는 `depth_teacher` 프로토콜과 같다.
  - 첫 단계는 빈 자리 배경 측정(ESP32 에 `g`)이고, 둘째 단계는 바른 자세 기준선(`b`)이다.
  - 각 단계 앞 3초는 자세를 바꾸는 시간이라 정답이 없다.
- `--note` 에는 **설치 위치·거리·높이**를 꼭 적는다. 장착이 바뀌면 같은 모델이 안 맞을 수 있어서다.
- Ctrl+C 로 멈춰도 그때까지는 저장된다.

## 처음 붙였을 때: 방향 확인

센서 zone 순서가 장면의 어느 쪽인지는 장착 방향에 따라 다르다. **10-16 부터 로그의 `d`·`st` 는 펌웨어가
이미 장면 방향으로 돌려 두 센서를 이어 붙인 격자**다(붙이려면 방향을 먼저 맞춰야 해서). hello 의 `flip_rows/flip_cols` 는
그래서 항상 false 다. 방향이 틀렸으면 한 센서 분(8×8 블록)씩 다시 뒤집어 쓸 수 있으니 데이터는 버리지 않는다.

1. 짧게 프로토콜을 한 번 돌리고 `inspect_session.py` 의 자세별 평균 거리맵을 본다.
2. 바른 자세에서 **머리(가까운 값)가 위쪽 가운데**에 있어야 한다.
   왼손 턱괴기는 센서에서 보면 **오른쪽 열**에 팔 기둥이 생겨야 한다.
3. 다르면 `--flip-rows 1`·`--flip-cols 0` 처럼 바꿔 가며 맞는 조합을 찾는다.
4. 찾은 값을 펌웨어 `DESKMATE_TOF_FLIP_ROWS/COLS`(센서별 8×8)·`DESKMATE_TOF_STACK_ROWS`(가로/세로 붙임)·`DESKMATE_TOF_SWAP`(A/B 자리)에 넣고 다시 굽는다.
   기본값(flip_cols=1, 가로 붙임, A 가 왼쪽)은 ST 예제의 표시 방식이고, 실측 확인 전이다.
   - 이 값은 온보드 규칙 판정이 쓰는 방향이다. 방향이 틀리면 규칙 판정(약한 라벨)도 틀린다.

## 파일

| 파일 | 내용 |
|---|---|
| `tof_log.py` | 수집 CLI (시리얼 / `--fake`) |
| `inspect_session.py` | 세션 점검, npz 내보내기 |
| `config.yaml` | 지시 자세 프로토콜 |
| `tofl/lines.py` | 펌웨어 줄 파서. 형식은 `firmware/.../src/tof_logger/TofLine.h` |
| `tofl/protocol.py` | 일정·라벨, 규칙 enum 대응(`RULE_CLASS`) |
| `tofl/session.py` | 세션 쓰기·읽기(방향·유효 zone 처리) |
| `tofl/recorder.py` | 소스 → 세션 루프 |
| `tofl/fake.py` | 가짜 L8. 배관 시험용이다. 장면 숫자는 실측이 아니고, rule 은 지시 자세를 그대로 돌려준다(일치율 100% 는 의미 없음) |

세션 형식(`frames.jsonl` 한 줄):

```json
{"t_host":1760000000.5,"seq":12,"ms":3456,"w":8,"h":8,"d":[...64],"st":[...64],
 "rule":{"present":true,"posture":"upright","conf":0.83,"baseline":true,"head_mm":612,"torso_mm":680,
         "drop_mm":4,"chin":0.0,"motion":0.05,"nods":0},
 "prompt":"upright","phase":"step","step":1}
```

`d` 는 mm 이고 -1 은 타깃 없음이다. `st` 는 VL53 target status 로, 5·9 는 유효, 6·10 은 대체로 유효, 255 는 타깃 없음이다.

## 테스트

```sh
python -m pytest tests -q                                  # 수집기 18개 (가짜 L8 전 구간 포함)
cd ../../firmware/esp32_sensor_node && pio test -e native -f test_tof_line   # 줄 형식 6개
```

펌웨어 줄 형식의 골든 문자열은 양쪽 테스트(`test_tof_line.cpp`, `tests/test_protocol_line.py`)에
같이 들어 있다. 형식을 바꾸면 둘 다 고친다.

## 아직 안 한 것

- **실기 확인.** 아직 L8 을 붙여 돌려 보지 않았다. 컴파일, 호스트 테스트, 가짜 소스 전 구간만 확인했다.
  처음 붙일 때 볼 것은 init 성공 여부, 15 fps, seq 갭, 방향이다.
- 학습 스크립트(`cam-teacher/train_l8.py` 의 MLP 를 실측 16×8 입력으로 옮기기)와 **Pi 4 허브 탑재**.
  **2026-10-17 결정: 자세 모델 추론은 ESP32 가 아니라 Pi 4 에서 한다.** 따라서 (1) ESP32 는 원본 격자를 UART 로
  Pi 4 에 보내야 하므로 `firmware/README.md` "원본 zone 미발행" 원칙과 프레임 규약(0x03/0x04)을 함께 고친다,
  (2) Pi 4 의 제한 Python 에는 numpy 가 없어 순수 Python 추론으로 가거나 네이티브 확장 ABI 를 보드에서 먼저 확인한다.
