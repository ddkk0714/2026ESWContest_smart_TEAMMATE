# firmware — ESP32 센서 말단 노드

담당: 이민혁 (펌웨어 · MQTT) · 김태환 (센서 인터페이싱 · ToF 전처리)

도크 내부에 1대 배치되어 mmWave·환경 센서를 읽고 노드 단에서 1차 전처리(이동평균·이상치 제거·졸음 FSM)한 뒤
**UART2 바이너리 프레임(COBS + CRC-16/CCITT-FALSE)** 으로 Pi 4 에 보낸다. 2026-09-11 에 C1001 → ESP32 → Pi 4 `/dev/serial0` 도달을 실측했다.
VL53L9CX 는 연결 경로(Path A: Pi 4 CSI-2 / Path B: ESP32 I2C 1 MHz binning)가 09-19 결정 대기이며, Path B 일 때만 이 노드에 붙는다.

> **현재 상태(2026-09-15):** `esp32_sensor_node/`에 PlatformIO 프로젝트, 공식 DFRobot C1001 라이브러리 기반
> `C1001Passive`, 5상태 `DrowsyDetector`, UART0(USB) JSON 출력이 들어왔다. 환경 센서는 MVP 스텁이며 5초마다
> 모든 값을 `null`, valid 플래그를 `false`로 출력한다.
>
> **2026-09-16 추가:** UART2 로 `docs/data-spec.md` §13.1 잠정 규약의 바이너리 프레임을 보낸다 —
> `transport/frame.cpp`(헤더·CRC-16/CCITT-FALSE·COBS·`0x00` 종료), `include/frame_types.h`(TYPE 0x20 mmWave 1 Hz ·
> 0x10 환경 0.2 Hz(스텁이면 valid_bits 0) · 0xF0 하트비트 1 Hz). 빌드 플래그 `-DDESKMATE_UART2_TX=1`(기본 켬),
> `-DDESKMATE_FIRMWARE_VERSION`. Python 코덱(`hub/deskmate_hub/ingest/uart_frame.py`)과 test vector 로 교차 검증했다.
> **`pio run` 실빌드와 실보드 UART 검증은 아직 안 했다.** 환경 센서 실제 드라이버(SCD41·BH1750·DHT22)는 미구현.
>
> **2026-09-29 갱신:** 위 두 항목은 이후 해소됐다(09-16 실보드 플래시·UART 실수신, PR #21 환경 드라이버).
> C1001 은 벤더 폴링 대신 **수신 전용 논블로킹 파서**(`sensors/c1001/C1001Passive.cpp`)로 읽는다. 능동 보고를
> 하나도 버리지 않고, 보고가 2 s 넘게 끊기면 질의로 깨운다(`nudgeIfSilent`). 초기화 전에는 300 ms `probe()` 로
> 생존을 확인하고, 센서가 없으면 10 s 부터 최대 120 s 까지 백오프하며 다시 붙는다. 졸음 판정 임계값은 실측 근거
> 주석과 함께 `DrowsyDetector.h` 에 있고, 시연용 확정 시간 단축은 `-DDESKMATE_DROWSY_HOLD_SCALE_PCT=<퍼센트>` 하나다.
> 호스트 테스트: `pio test -e native` (졸음 판정 12 · 환경 검증 9). 이 PC 처럼 gcc 가 없으면 Docker 로
> `docker run --rm -v "<펌웨어 경로>:/fw:ro" python:3.11 bash -c "apt-get -qq update && apt-get -qq install -y g++ && pip -q install platformio && cp -r /fw /tmp/fw && cd /tmp/fw && pio test -e native"`.

## 배선·UART 할당 (실측)

| 채널 | 용도 | 핀 |
|---|---|---|
| UART0 (`Serial`) | 디버그 모니터 · `log2file` 로깅 · 구간 마커 입력 | USB |
| UART1 (`Serial1`) | C1001 mmWave, 115200 8N1 | GPIO16 RX / GPIO17 TX |
| UART2 | Pi 4 링크, 115200 8N1 (목표 460,800+) | GPIO25 TX → Pi 4 GPIO15(물리 10) / GPIO26 RX ← Pi 4 GPIO14(물리 8) |
| I2C0 (`Wire`) | SCD41 `0x62` · BH1750 `0x23`, 100 kHz | GPIO21 SDA / GPIO22 SCL |
| I2C1 (`Wire1`) | **VL53L8CX ×2**, 1 MHz. A `0x54`(펌웨어가 바꿈) · B `0x52` | GPIO23 SDA / GPIO18 SCL · LPn A GPIO32 / B GPIO33 · NCS·SPI_I2C_N = GND |
| GPIO27 | DHT22 DATA | |

전체 배치와 핀 충돌 정리(76EHwan/ESP32_VL53L8 의 LPn 16·SPI_I2C_N 17 은 C1001 과 겹침): [`docs/breadboard-esp32-node.md`](../docs/breadboard-esp32-node.md).

GPIO16/17 은 WROVER 계열에서 PSRAM 용이다. 현재 `esp32dev`(WROOM)라 무관하지만 보드를 바꾸면 핀을 옮긴다.
공통 GND 필수. Pi 4 와 5V 를 직접 연결하지 않는다. 업로드가 안 되면 BOOT 를 누른 채 EN 을 한 번 누른다(`Wrong boot mode` = 업로드 모드 진입 실패).

## 원칙

- **ToF 원본은 센서별 8×8 단위로 Pi 4 까지만 간다(2026-10-17 표준).** 자세 AI 모델은 Pi 4 에서 돌리기로 해(허브 안 MLP 인지 별도
  서비스인지는 실측 성능으로 정한다 — `docs/data-spec.md` §6.1 후보 A/B), 이 노드는 VL53L8CX 원본 8×8 거리·상태를 **센서마다 한
  프레임씩** UART `0x05` 로 Pi 4 에 넘긴다(같은 frame_id·TS, 5 Hz). 두 센서는 바깥 8° 조준이라 시야가 대부분 겹쳐 16×8 로 붙이면 한
  장면이 아니기 때문이고, Pi 4 는 센서별로 추론해 융합한다. `TofArray` 의 16×8 스티칭은 규칙 추정기(`0x03`)·USB 표시용 내부 근사다.
  원본은 유선 UART 와 Pi 4 메모리 안에서만 다루고 MQTT·로그·화면으로는 나가지 않는다. 축소 depth map(`0x01`)은 명시적 디버그/UI
  모드에서만 최대 2Hz 로 허용하는 별개 예약이다.
- 모든 프레임에 `SEQ`·`TS` 를 넣는다(유실 감지·Pi 4 슬라이딩 윈도 정렬).
- UART binary frame 에만 CRC-16 을 적용한다. Pi 4 이후 MQTT JSON 에는 별도 애플리케이션 CRC 를 넣지 않는다.
- mmWave 순간 호흡·심박값은 보내더라도 Pi 4 판정 근거가 아니다. 체동 이동평균(τ=15 s)·심박 중앙값(120 s)·각성 기준선(τ=600 s)·졸음 FSM state/evidence 가 계약 대상이다.

## 프레임 규약 (계약 확정 전 — `docs/data-spec.md` 가 우선)

```
[SOF 1B][VER 1B][TYPE 1B][SEQ 2B][LEN 2B][TS 4B][PAYLOAD N][CRC16 2B]   → COBS 인코딩 + 끝 0x00
```

| TYPE | 내용 | 주기 | 상태 |
|---|---|---|---|
| 0x01 | (디버그) binning depth map | ≤ 2 Hz | 예약·미구현. 09-11 실험 펌웨어의 mmWave 임시 사용은 0x20 으로 옮겨 해소. 운영 원본 격자는 0x05 라 충돌 없음 |
| 0x03 / 0x04 | ToF `tof_feature`(26 B) / 자세 요약(4 B) — `src/sensors/tof/TofPayload.h` | 5 Hz / 1 Hz | **10-16 구현.** `docs/data-spec.md` §13.1. posture 는 노드 규칙 판정(Pi 4 MLP 의 폴백) |
| 0x05 | ToF 원본 8×8 센서 프레임 `tof_grid` — 헤더 8 B(grid_w·grid_h·sensor·sensors·frame_id·sensor_mask·flags·reserved) + dist i16×64 + status u8×64 = 200 B. 센서마다 하나, 같은 frame_id | 5 Hz × 센서 수 | **10-17 구현.** 단위는 센서별 8×8(시야 겹침·LEN ≤ 256 B). 근거·골든 hex 는 `docs/data-spec.md` §13.1 |
| 0x10 | 환경 묶음 (SCD41 CO₂ 2B + DHT22 T 2B + RH 2B + BH1750 lux 2B = 8B). SCD41 T/RH 는 보내지 않음(보정 없음·단일 센서, 09-14 확정) | 0.2 Hz | 미구현 |
| **미배정** | mmWave 졸음 state + evidence + 요약값 (10~16 B) | 1 Hz | **TYPE 배정 필요** |
| 0xF0 | 상태/하트비트 | 1 Hz | |

## 구조 (목표)

```
esp32_sensor_node/
├── platformio.ini
├── include/          설정 헤더 (노드 ID · 핀 · 임계값)
└── src/
    ├── main.cpp
    ├── sensors/      c1001 (파서·DrowsyDetector) / scd41 / bh1750 / dht22 / vl53l9cx(Path B)
    ├── preprocess/   이동평균 · 이상치 제거 · 특징 추출
    └── transport/    uart2 프레이밍(COBS · CRC-16) · 하트비트
```

## 개발 환경

PlatformIO (Arduino 프레임워크) 또는 ESP-IDF.
`platformio.ini` 에 보드 · 라이브러리 의존성을 고정한다.

ESP32 는 Pi 4 와 유선이므로 Wi-Fi 자격증명이 기본 경로에 필요 없다. 필요해지면 `include/secrets.h.example` 를 복사해 `include/secrets.h` 로 쓰고 커밋하지 않는다.

## ToF 자세 추정 (Path B 전처리)

`src/preprocess/tof/TofPosture.{h,cpp}` — 멀티존 거리 프레임 → roadmap §4-4 기하 특징 7종 →
`docs/data-spec.md` §6.1 `tof_feature`(posture enum · `motion_score` · `head_delta_mm` · `nod_rate_hz`).
Arduino 의존이 없는 순수 C++ 다. **10-17 부터 자세 모델 판정은 Pi 4 몫이고 이 규칙 판정은 비교·폴백용이다** — 원본 격자는 `0x05` 로
Pi 4 에 가고, Pi 4 의 모델(후보 A 허브 `posture/tof_mlp.py` / 후보 B `deskmate_tofposture`, 미확정)이 자세를 낸다.
끄덕임·졸음은 시간 현상이라 계속 여기서 판정해 `0x03` flags 로 보낸다.

- **자세:** 바른자세 · 상체 앞으로 · 뒤로 젖힘 · 엎드림 · 턱괴기 · 꾸벅 졸음 · 자리비움.
  정의는 팀에 이미 검증된 두 판정을 ToF 물리량으로 옮겼다 — `display/atlas/camsvc/src/posture_pose`
  (자세 범주·판정 순서·히스테리시스·1.5 s 유지)와 `dataset/depth_teacher`(머리 동역학만으로 졸음).
  8×8·54×42 ToF 로 앉은 자세를 다룬 공개 데이터셋은 찾지 못했다.
- **격자 무관.** VL53L9CX 54×42 원본, binning 축소(24×20 · 8×6 …), VL53L8CX 8×8 을 같은 코드로 처리한다.
  RAM 은 `DESKMATE_TOF_MAX_ZONES`(기본 2268 → 약 35 KB)로 정한다. 객체는 전역·정적으로 둔다.
- **흐름.** zone 상태값 판정(5·9, 선택 6·10) → 3프레임 중앙값 · 짧은 결측 유지 → 빈 자리에서 배운 배경 대비
  전경 분리 → 특징 7종 + 머리·몸통 거리, 머리 낙차, 팔 기둥 → 앉은 뒤 처음 조용한 10 s 를 "바른 자세" 기준선으로
  → 기준선 대비 분류 → 1.5 s 유지.

| 자세 | ToF 판정 (기준선 대비, on/off) | 출처 |
|---|---|---|
| 턱괴기 | 머리 바로 아래부터 끊김 없이 가슴보다 60 mm 이상 가까운 열이 상체 행의 60%/40% 이상, 머리는 엎드림만큼 안 내려감 | camsvc "턱괴기는 팔의 사실" |
| 뒤로 젖힘 | 머리 거리 1.11배/1.05배 이상 | camsvc 어깨 폭 0.90/0.95배 ↔ 거리 비율 |
| 엎드림 | 머리 꼭대기 하강 135/75 mm 이상 (행 변화 × 세로 시야각 × 거리) | camsvc 머리 낙차 0.35/0.20 어깨 폭 × 380 mm |
| 상체 앞으로 | 머리 80 mm 이상 가까워짐 | depth_teacher lean_forward 5 cm |
| 꾸벅 졸음 | 끄덕임(0.2~1.5 s 에 40 mm 이상 떨어지고 1.5 s 안에 60% 복귀, 몸통은 80 mm 이내) 60 s 안 2회, 또는 끄덕이듯 떨군 채 5 s 정지. 5 s 유지 | depth_teacher drowsiness |

- **임계값**은 전부 `TofPostureConfig` 에 있다. 출처를 옮긴 값도 카메라→ToF 환산 가정(어깨 폭 380 mm, 회전 반지름 15 cm)이
  들어가 있으니 depth_teacher 라벨과 동시 녹화로 실측 보정해야 한다.
- **호스트 테스트:** `pio test -e native -f test_tof_posture` (28개 — 배경 분리 · 기준선 · 6자세 · 판정 순서 ·
  타이핑 팔은 턱괴기 아님 · 1.5 s 유지 · 결측/튐 내성 · 끄덕임 1회는 졸음 아님 · 2회·떨군 채 정지는 졸음 ·
  상체 숙임·거북목은 끄덕임 아님 · 격자 4종 동일 판정 등).

**2026-10-16: 센서 연결·UART·hub 매핑까지 들어갔다(브랜치 `feat/tof-node-hw`, 실기 미검증).**

- `src/sensors/tof/TofArray.{h,cpp}` — VL53L8CX 두 개(ST STM32duino 2.1.0)를 `Wire1` 에서 읽어 센서별 8×8(`sensorDistance(i)`, 0x05 용)로
  두고, 규칙 추정기용으로만 한 격자로 붙여 `TofPostureEstimator` 에 넣는다(두 시야가 대부분 겹쳐 스티칭은 근사 — 10-17 표준). 부팅 때 B 의 LPn 을 눌러 둔 채 A 를 0x54 로 바꾸는 주소 배정, 죽은 센서 5 s 재시도,
  한쪽만 살면 8×8 로 축소. 배치·방향은 `-DDESKMATE_TOF_STACK_ROWS/FLIP_ROWS/FLIP_COLS/SWAP/COUNT`.
- `src/sensors/tof/TofPayload.{h,cpp}` — TYPE 0x03(26 B, 5 Hz)·0x04(4 B, 1 Hz) 인코더. 호스트 테스트 `test_tof_payload` 4개가
  hub `test_uart_frame` 와 같은 golden hex 를 고정한다. 하트비트 0xF0 에 `tof_ok` 1 B 가 붙었다(펌웨어 0x0120).
- `main.cpp` — ToF 폴링·UART 송신·USB `{"t":"tof_feature"}` 1 Hz·부팅 상태 줄(`VL53L8CX x2: ok_mask=3 grid=16x8`)·
  USB 명령 `b`(기준선)·`g`(배경)·`r`(초기화). `SET_LOOP_TASK_STACK_SIZE(16 KB)` — ST 드라이버가 스택을 4 KB 쓴다.
- hub: `ingest/uart_frame.py` 디코더, `SensorCache.tof`, `ingest.yaml` `tof:` 섹션으로 posture 신호(mmWave 대체는 ToF 가 낡았을 때만),
  `sensor_summary.tof`, Pi 4 C++ 네이티브 요약에도 `tof.posture`.

**2026-10-17: 원본 격자 UART 전송(TYPE 0x05) — 자세 AI 모델은 Pi 4 에서 돈다.**

- `TofPayload.{h,cpp}` `encodeTofSensorFrame` — `TofArray::sensorDistance(i)/sensorStatus(i)`(센서별 장면 방향 8×8)를 센서마다
  200 B 프레임으로 싼다. `main.cpp` `writeTofGridUart` 가 새 표본이 생긴 뒤 5 Hz(`kTofGridPeriodMs`)로 살아 있는 센서만 보내며,
  프레임마다 같은 `TS`·`frame_id`, SEQ 는 별도 카운터(`tof_grid_sequence`), `flags` 에 적용한 FLIP/STACK/SWAP 을 싣는다.
  펌웨어 버전 0x0121. 호스트 테스트 `test_tof_payload` 6개(골든 프레임 hex 2개는 hub `test_uart_frame` 와 공유).
- Pi 4 쪽 후보 A(이 커밋에 구현): `hub/deskmate_hub/ingest/uart_frame.py` `TofGridAssembler` 가 조립해 `SensorCache.tof_grid` 에 두고,
  `hub/deskmate_hub/posture/tof_mlp.py` 가 numpy 없는 순수 Python MLP(192→64→10, `dataset/tof_sim/train_mlp.py` 가중치를
  `export_mlp_json.py` 로 변환)로 자세를 낸다. 0x03 규칙 판정은 MLP 가 못 낼 때의 폴백으로 유지.
- Pi 4 쪽 후보 B(이 저장소엔 아직 없음): 별도 서비스 `deskmate_tofposture`(HTTP 8771, `dataset/tof_sim_v5` PostureNet CNN+GRU,
  센서당 (2,8,8) 입력·GRU 상태 유지·두 센서 융합)가 판정하고 `hub/atlas` 는 0x05 를 tcp:9100 으로 중계만 한다.
  **둘 중 무엇을 쓸지는 미확정이며 실측 세션 정확도로 정한다.** 둘 다 센서별 8×8 추론 + 융합이 전제다(후보 A 는 `tof_mlp.py` 가
  센서별 기준선·추론 뒤 log-softmax 합).

**아직 안 한 것: 실기.** 두 센서 주소 배정·15 Hz ×2 유지·격자 방향·임계값은 보드에 붙여 봐야 안다.
처음 켤 때 볼 것은 `docs/breadboard-esp32-node.md` "처음 켤 때 보는 것". 0x05 에 대해 실기에서 볼 것:
Pi 4 `UART	{"type":5,...}` 라인이 5 Hz × 2(센서 수) 로 들어오는지(`uart_rx` CRC 드롭 카운트 0), hub `UartStats.grids` 가 초당 5 씩
느는지(프레임 유실이면 `TofGridAssembler.discarded` 증가), 115200 에서 mmWave·환경 프레임이 밀리지 않는지(밀리면 Pi 4 링크
460800 로 올리거나 `kTofGridPeriodMs` 를 늘린다), 한쪽 센서만 살 때 그 센서 번호로 프레임 1개만 가는지(`sensors` 1), 센서별 8×8 의
방향(`flags` 의 FLIP/SWAP)이 모델 학습 방향과 같은지(손 들기로 확인, `dataset/tof_logger/README.md` "방향 확인").

**2026-10-17 실측 — Pi 4 허브 IPK 를 `feat/tof-node-hw`(382e821) 기준으로 재빌드·재설치해 0x05 조립을 보드에서 확인.**
ESP32 펌웨어 0x0121, Pi 4 `/dev/serial0 → ttyAMA0` 115200(getty 는 failed 상태, 노드는 `g0_a5015` 660), 서비스 계정 u0_a5015,
빌드는 WSL docker `atlas-dev:latest` 에서 `pip3 install --break-system-packages pyyaml paho-mqtt` →
`source /opt/atlas-sdk-x86_64/environment-setup-armv8a-atlas-linux` → `arc build hub/atlas`(SDK 환경을 안 읽으면 `spawn cmake ENOENT`).
30 s 리포트 4회 연속(사람 없이, 센서 둘 다 살아 있는 상태 `sensors_ok 3`):

- C++ `uart_rx`: `rx` 516 → 982 /30 s(전체 ≈15.5 프레임/s), **`discarded=0 crc_errors=0`**.
- hub 브리지(`[bridge] uart …`, 이 커밋부터 30 s 주기·0x05 카운터·MLP 판정 포함): **`parts` +238~239 /30 s ≈ 7.9 /s = 센서당 ≈4 Hz**
  (5 Hz 가 아닌 것은 펌웨어 타이머 위상 문제 — 아래 실측(2)에서 고쳐 10.2 /s), **`grids` +119~120 /30 s ≈ 4.0 /s**(두 센서 프레임이 다 모인 표본),
  **`grid_discarded=0`**, `dropped=0`, 하트비트 ≈1 /s, `unknown={}`.
- 115200 에서 0x03/0x04·mmWave 0x20·환경 0x10 이 같이 들어오고 `/api/state` `sensor_summary` 에 temp 23.9·humidity 47.9·lux 418·mmwave 가
  그대로 찍힘 — 밀림 없음. 링크 속도를 올릴 이유는 아직 없다.
- MLP(`[tof_mlp] 192→64→10 (8x8, mlp_sim60)`): 표본마다 호출된다(`mlp frames` == `grids`). 빈 자리라 기준선이 안 잡혀 `unknown/0.0 used=0`
  이고 `sensor_summary.tof.posture_source` 는 `rule`(규칙 판정 recline, `head_delta_mm` 605·`coverage_ratio` 0.07~0.13 = 사람 없음) — 설계대로의
  폴백. 다만 MLP 의 `present=1`(150~1300 mm 안 zone 이 6 % 이상 — 책상 면 등으로 보임)은 사람이 앉았을 때 기준선 포착·판정과 함께 다시 봐야 한다.
- 아직 안 본 것: 한쪽 센서만 살 때(`sensors` 1), 격자 방향(손 들기), 사람 착석 시 MLP 기준선·판정 정확도.
- 설치는 `arc install` 대신 빌드 산출물 `hub/atlas/build/arm64/deskmate_hub_service`(zip payload 포함 ELF)를 scp 로 서비스 디렉터리에 덮고
  `chown u0_a5015:g0_a5015` 뒤 `pi4-hub-activate.sh` 를 돌렸다(계정 재할당을 피한다). 보드 시계는 실제보다 하루 가량 늦다(journal 10-16 16:5x KST).

**2026-10-17 실측(2) — 0x05/0x03 송신 주기를 정확히 5 Hz 로, ESP32 소프트 리셋 뒤 부팅 13.3 s → 4.2 s.** 펌웨어 0x0122.

- **송신 주기.** `main.cpp` 의 ToF 타이머를 "기한을 now 로 덮기" 에서 `periodDue()`(기한을 period 씩 밀고, 한 주기 넘게 뒤처지면
  now 로 재동기)로 바꿨다. 덮는 방식은 15 Hz 표본(66 ms) 위상에 걸려 0x05 가 264 ms(≈4 Hz), 0x03 이 루프 지연만큼 늦어 4.2 Hz 였다.
  실측은 Pi 4 `/dev/ttyAMA0` 8 s raw 캡처(허브 미기동 상태, `stty -F /dev/ttyAMA0 115200 raw -echo; cat > /tmp/esp.bin`)를
  `hub/deskmate_hub/ingest/uart_frame.py` `split_stream/decode_frame` 으로 센 것: **0x05 80 프레임 = 10.17 /s(센서 0·1 각 40), SEQ 갭 0**,
  표본 간격 198~199 ms 가 대부분(최대 220); **0x03 40 = 5.09 /s**, 간격 198~200 ms(0x03·0x04 는 SEQ 를 같이 쓰므로 둘을 합쳐 연속);
  0x04·0x20·0xF0 1.02 /s, 0x10 0.25 /s, 디코드·CRC 오류 0. USB `tof_feature` 줄에 누계 `tx_grid`/`tx_feature` 를 붙였다 — 1 s 차분이
  9.8~10.0 / 4.9~5.0 (30 s 관측).
- **부팅 시간.** 원인은 `TofArray::parkStuckSensor()` 였다. ESP32 만 리셋되면 두 센서는 전원이 유지돼 이전 주소(A 0x54·B 0x52)로
  측거를 계속하며 **LPn LOW 를 무시한다**(스캔에 `[all LPn low] 0x52,0x54`). 이때 0x52 는 주소 ACK·`is_alive`·레지스터 읽기는 되지만
  LPn 이 LOW 인 채 `init()` 을 걸면 코드 3(NACK 2 | 폴 타임아웃 1)으로 2 s 폴 → `stop_ranging` 5 s 타임아웃 → 2 s 폴 = **9.3 s 를 쓰고 실패**
  했고(0x56 "치우기"는 한 번도 성공한 적 없음), 그 뒤 `startSensor` 가 LPn 을 올리고 init 하면 측거 중이어도 한 번에 됐다(A·B 각 1.32 s).
  고친 것: (1) A 의 목표 주소와 0x52 가 둘 다 응답하면(이전 부팅이 끝까지 간 소프트 리셋) 주소가 안 겹치니 park 를 건너뛴다.
  (2) 0x52 만 응답하면(이전 부팅이 A 의 주소 변경 전에 끊긴 경우) `vl53l8cx_init()` 맨 앞의 SW 리부트 시퀀스만 보낸다(`softReboot`, ≈100 ms)
  — 리부트된 센서는 미초기화 상태라 LPn LOW 를 다시 따라 버스에서 빠지고(실측 `acks after=0`), 이후는 전원 재투입과 같은 경로다.
  (3) 0x56 후보·`kTofParkedAddress` 제거, 센서별 `tof A: init=0 (1319 ms)` 진단 줄 추가.
  실측(USB 부팅 로그, 호스트 시각): 소프트 리셋 2회 모두 `VL53L8CX x2: ok_mask=3` 가 **4.16~4.17 s**(스캔 0.4 s + A 1.32 s + B 1.32 s;
  이전 13.33 s). "0x52 만 응답" 경로는 임시 USB 명령으로 A 를 SW 리부트한 직후 리셋해 재현 — `[all LPn low] 0x52` → `SW reboot,
  acks after=0` → `ok_mask=3` 4.17 s.
  **미초기화 센서에 `stop_ranging()` 을 먼저 부르는 방법은 쓰지 않았다**: 라이브러리가 레지스터 0x06 bit 7 을 10 ms × 500 회 폴하므로
  부팅 안 된 센서에서는 5 s 타임아웃까지 간다(`vl53l8cx_api.c` `vl53l8cx_stop_ranging`). 측거 중 판별용으로 본 0x06 도 측거 중 0x31 이라
  bit 7 로는 못 가른다.
- **전원 재투입 경로(둘 다 미초기화, LPn 정상)도 실측 확인.** 코드는 그대로다(0x52 무응답이면 park 즉시 return, `startSensor` 의
  "0x52 로 init → A 만 0x54 로 변경" 로직 불변). USB-C 를 뽑았다 꽂으며 포트가 다시 열리는 순간부터 캡처(다른 센서 전부 연결된 상태):
  `tof A: init=0 (1320 ms)` → `tof B: init=0 (1319 ms)` → `VL53L8CX x2: ok_mask=3 grid=16x8`, 직후 `tof_feature` 의 `ms`=3611.
  즉 ESP32 시각으로 **3.6 s 에 두 센서 OK** — 소프트 리셋 때의 `ms`=3630 과 같다(호스트 기준 4.2 s 는 ROM 부트·USB 지연 0.5 s 포함).
  포트 enumerate 지연(≈1 s) 때문에 스캔·park 줄은 USB 로 못 봤다. 같은 부팅에서 SCD41 은 `periodic measurement started` 로 올라왔다
  (소프트 리셋 뒤의 `error=268` 은 SCD41 이 전원 유지 상태에서 재초기화를 거부한 것으로 보임 — ToF 와 같은 종류의 현상, 별건).
- Pi 4 상태 메모: 이 캡처 시점에 허브 프로세스가 없었다(`ps` 0, 저널에 `[bridge]` 줄 없음) — 그래서 포트를 직접 읽을 수 있었다.
  허브를 다시 올리려면 `pi4-hub-activate.sh`.

**2026-10-17 PC 진단: USB 명령 `z` 로 켜는 원본 8×8 줄 `{"t":"tof_grid"}` + `tools/sensor_monitor.py`.** 펌웨어 0x0123.
UART 0x05 와 같은 표본·같은 `frame_id` 를 센서별 `d`(mm, -1)·`st`(255) 64개씩 JSON 으로 USB 에도 낸다(5 Hz, 한 줄 ≈1.1 kB).
**기본은 꺼져 있고** `z` 가 토글(`# tof_grid usb on/off`)이라 운영 출력·"운영 ToF raw 미전송" 원칙은 그대로다 — 수집 모드(`env:tof_logger`)처럼
개발 PC 에서만 본다. `Serial.setTxBufferSize(4096)` 으로 USB TX 링 버퍼를 키워 1.1 kB 를 print 해도 루프가 안 막힌다(115200 에서 95 ms).
`tools/sensor_monitor.py --port COM8` 이 시작할 때 `z` 를 보내고 끝날 때 다시 보내 끈다. 실측: 모니터 수신 `tof_grid` 4.99 Hz, 켠 상태에서
`tx_grid`/`tx_feature` 차분 10/5 유지.

**학습 데이터 수집 모드(`env:tof_logger`, 운영 아님).** VL53L8CX 8×8 을 ST 공식 라이브러리로 읽어 원본 zone 과
규칙 판정을 USB 한 줄 JSON(`{"t":"tof",...}`, 921600)으로 PC 에 보낸다. `src/tof_logger/` 는 운영 빌드에서 빠진다
(`env:esp32dev` 의 `build_src_filter`). PC 쪽 수집기·배선·방향 확인은 [`dataset/tof_logger/README.md`](../dataset/tof_logger/README.md).
줄 형식 시험: `pio test -e native -f test_tof_line`.

## 통신 계약

논리 스키마는 [`docs/data-spec.md`](../docs/data-spec.md), Pi 4 이후 MQTT 매핑은 [`docs/mqtt-topics.md`](../docs/mqtt-topics.md) 참조.
실측 기록: 노션 「ESP32–Pi4 UART 하드웨어 연결·실측 검증」, 「센서별 데이터·처리·전송 스펙」.

### MVP USB(UART0) JSON 라인

115200 8N1에서 한 줄에 JSON 객체 하나를 쓰고 `\n`으로 끝낸다. JSON 센서 줄은 반드시 `{"t":`로 시작하므로
같은 포트의 일반 디버그 로그와 함께 쓸 수 있다.

```json
{"t":"mmwave","ms":1234,"present":true,"motion_state":"still","motion_level":12,"distance_cm":87,"resp_bpm":15,"resp_valid":true,"heart_bpm":null,"heart_valid":false,"drowsy_state":"AWAKE","valid":true}
{"t":"env","ms":5000,"co2_ppm":null,"temp_c":null,"humidity_pct":null,"lux":null,"co2_valid":false,"temp_valid":false,"humidity_valid":false,"lux_valid":false}
```

- mmWave는 1 Hz, 환경은 0.2 Hz(5초 간격)다.
- `motion_state`는 `none|still|active`, `drowsy_state`는 `NOPERSON|NOLOCK|WARMUP|AWAKE|DROWSY`다.
- 유효하지 않거나 없는 수치는 `null`로 출력하고 해당 valid 플래그를 `false`로 둔다.
- warmup·정지 지속·motion level 임계값은 `platformio.ini`의 `build_flags`에서 조정한다.
- 빌드: `cd firmware/esp32_sensor_node; pio run`
- 업로드가 안 되면 BOOT를 누른 채 EN을 한 번 누른 뒤 업로드한다.
