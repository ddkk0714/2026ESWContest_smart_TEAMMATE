# 엎드림 규칙(d)·upright 치환·모델 입력 변환(radial·fill) — 10-26 실측 리플레이

보드 모델 `opt_l8_c256`(sha256 1ced97c0…), 왼쪽 자리, 장치 기울기 고정. 녹화 4종은 fork `data/real-20261026`
(`dataset/real_20261026/real_20261026_{left,left2,left2_facedown}.zip`, `check_close30/`). 도구는 `tofposture_real_replay`(`src/real_replay.cpp`)로,
보드 서비스와 같은 `DevicePosture` 경로(10 Hz tick, 센서별 GRU, log-prob 평균, EMA 1 s, 게이트·규칙)로 `rec_raw.jsonl` 을 다시 돌린다.

```sh
build/host/real_replay models/opt_l8_c256/posture_net.bin left2/rec_raw.jsonl left2/labels_20261026_130558_with_facedown.csv \
    left2/protocol_start_20261026_130558.txt --golden left2_golden.csv --warmup-skip 4.5
# 변형 기본 묶음: off · radial · fill(=3000) · radial+fill · combo(radial, fill=3000, 엎드림 규칙, upright 치환)
```

## 1. 규칙(d) — PC 기준 구현과 프레임 단위 일치

기준: fork `tools/real-scoring-1026` @ 04527fb `dataset/tof_sim_v5/real_facedown_rule.py`. 골든은 그 모듈로 각 녹화에서 만든 프레임 CSV
(기준 구현의 `--csv` 는 실수를 소수 4자리로 반올림하므로, 같은 짝짓기·순서로 `repr` 로 쓴 전체 정밀도 판). 데이터에서 나온 것이라 커밋하지 않는다.

| 녹화 | 프레임 쌍 | 정수(up·lo·topA·topB)·flag·on·seated 불일치 | 실수(dlo·dup·share) 최대 차 | 서비스 경로(feed 짝짓기) |
|---|---|---|---|---|
| left1 | 7803 | 0 | 0 | 7803 쌍, on·seated 불일치 0 |
| left2 | 4213 | 0 | 0 | 4213 쌍, 0 |
| left2_facedown | 2308 | 0 | 0 | 2308 쌍, 0 |
| check_close30 | 716 | 0 | 0 | 716 쌍, 0 |

- 문턱은 double 로 둔다. float 0.4f·0.45f 는 기준 구현의 0.4·0.45 와 경계에서 달라(share 가 정확히 0.4 인 프레임) 첫 판에서 2 프레임이 어긋났다.
- 서비스는 A(센서 0)·B(센서 1)의 같은 ts_ms 원본 격자가 들어올 때마다 한 번 평가한다(보드에서는 hub 가 두 센서를 연달아 넘긴다).

## 2. 시계 — PC 기준 구현의 라벨 정렬이 약 11 s 늦다

기준 구현은 보드 ms → 사람 시각 변환을 `/api/state` 줄의 `host_t − ts_ms/1000` 중앙값으로 잡는데, `/api/state.ts_ms` 는 Pi 4 서비스의 단조 시계이고
`/raw` 의 `ts_ms` 는 ESP32 millis(UART TS)다. 두 시계는 세 녹화 모두 **10.9~11.1 s** 어긋난다(`/raw` 프레임 자신의 폴링 시각과 ESP32 ts 의 차는
p5~p95 ±0.07 s). 그래서 기준 구현이 보고한 "첫 켜짐 +12.5 s"는 실제로 약 +1.5 s 이고, face_down 단계 80~82 % 는 창이 11 s 밀린 값이다.
리플레이는 기본으로 `/raw` 프레임 시계(`--clock raw`)를 쓰고, PC 수치 대조용으로 `--clock state` 를 둔다.

| 녹화 | off(`/raw` 프레임) | off(`/api/state`) | 차 |
|---|---|---|---|
| left1 | 1792983574.845 | 1792983585.894 | −11.05 s |
| left2 | 1792983574.924 | 1792983585.887 | −10.96 s |
| left2_facedown | 1792983574.948 | 1792983585.878 | −10.93 s |

## 3. 결과 (combo = radial + fill 3000 + 규칙(d) + upright 치환, display, 단계 앞뒤 2 s·GRU 재시작 뒤 4.5 s 제외)

| 녹화 | face_down 단계 | 다른 라벨의 face_down | acc / macro-F1 (`/raw` 시계) | acc / macro-F1 (`/api/state` 시계 = PC 정렬) | PC 목표 |
|---|---|---|---|---|---|
| left2 | **100 %** | 없음 | 0.549 / 0.541 | 0.549 / 0.555 | 0.548 / 0.561 |
| left1 | **100 %** | 11단계 upright 100 % (아래) | 0.514 / 0.301 | 0.449 / 0.242 | 0.514 / 0.323 |
| left2_facedown | **100 %** | 없음 | 1.000 / 1.000 | 0.840 / 0.846 | 0.748 / 0.790 |
| check_close30 | 엎드림 두 구간 [1.9–51.2 s] [114.5–149.8 s] 켜짐, 그 사이 앉은 구간 꺼짐 (프레임 쌍의 56.6 %) | | | | 100 / 97 % · 앉음 0 % |

- `/api/state` 시계로 돌리면 face_down 단계는 PC 와 같은 80 / 81 / 81 % 가 나온다(창이 11 s 밀림). left1 11단계(엎드림 앞 "바르게")의 face_down 은
  PC 정렬에서 66 %, 올바른 시계에서 100 % 인데 **규칙 없이 모델(radial+fill)만으로도 같은 구간을 face_down 80 % 로 읽는다** — 기준 구현 문서가 말한
  "라벨 시각 어긋남(이미 엎드려 있었음)"으로 본다. 11단계 다음의 12단계 face_down 은 첫 시도를 중단하고 다시 했다(녹화 NOTES).
- 모델 입력 변환만(규칙 없음, instant = EMA 없는 융합 argmax, PC 채점기 기준):

| 녹화 | v4 | radial | fill | radial + fill | PC (v4 / radial / fill / radial+fill) |
|---|---|---|---|---|---|
| left1 | 0.164 / 0.126 | 0.111 / 0.094 | 0.471 / 0.298 | 0.469 / 0.286 | 0.166/0.120 · 0.121/0.102 · 0.483/0.299 · 0.484/0.290 |
| left2 | 0.253 / 0.200 | 0.254 / 0.200 | 0.392 / 0.316 | 0.390 / 0.380 | 0.252/0.196 · 0.257/0.198 · 0.427/0.388 · 0.431/0.425 |

  left1 은 1~2 %p 안. left2 의 fill·radial+fill 은 4 %p 낮다(PC 채점기는 센서별 run 을 각자 시작 프레임의 100 ms 격자로 돌리고 두 센서를 시간 겹침으로
  짝짓는다 — 서비스는 두 센서를 같은 tick 에 돈다).
- drowsy 확신(fill 켬, radial+fill): 졸음이 아닌 단계에서 표시가 drowsy 인 tick left1 2.2 % · left2 5.3 % · left2_facedown 0.7 %, 그중 conf ≥ 0.80 은
  0 % · 0 % · 0 %. 평활 P(drowsy) 최대 0.712 · 0.669 · 0.377 — hub 시연 하한 0.80 아래. left1 거북목은 upright 92 %(drowsy 0 %)로 나온다
  (`/api/state` 시계에서는 drowsy 13 %, 최대 0.910, conf ≥ 0.80 0.7 %).

### PC 목표와 2 %p 넘게 다른 단계

| 녹화·단계 | PC | 여기 (`/raw` 시계) | 이유(추정) |
|---|---|---|---|
| left2 face_down | 81 % | 100 % | 시계 11 s (§2). `/api/state` 시계로 81 % |
| left2 away | 93 % | 56 % (misplaced 44) | 녹화 시작 직후 GRU — `/api/state` 시계로 90 % |
| left2 drowsy | 80 % | 67 % | 미확인 (EMA·tick 위상·GRU 상태) |
| left2 lean_forward(타이핑) | upright 86~100 % | upright 73 %, forward_head 10 % | 미확인 |
| left2 마지막 upright | 51 % (drowsy 49) | 39 % (drowsy 61) | 미확인 |
| left1 recline | 89 % | misplaced 67 % | 미확인 — 같은 모델 off 리플레이도 보드 기록(recline 90 %)보다 낮다(48 %). GRU 상태 민감 |
| left1 forward_head | drowsy 69 % | upright 92 % | 미확인 |
| left1 face_down | 82 % | 100 % | 시계 (§2) |
| left2_facedown 전체 | 0.748 | 1.000 | 시계: PC 정렬에선 규칙이 11 s 늦게 켜져 엎드림 앞부분을 놓치고 뒤 upright 로 19 % 샌다 |

규칙 쪽은 결정적이라 골든과 비트 단위로 같고, 남은 차이는 GRU 모델 경로(재시작 시점·tick 위상·두 센서 정렬)에서 나온다. GRU 는 상태 이력에 민감해서
같은 모델 v4 를 보드 기록과 비교해도 단계별로 수십 %p 다른 곳이 있다(예: left1 recline 보드 90 % / 리플레이 48 %). PC 의 combo 스크립트가 오면 같은 입력으로 대조한다.

## 4. 단계별 표 (`/raw` 시계, combo)

### left2 (`labels_20261026_130558_with_facedown.csv`)

| step | label | board (recorded) | off | radial+fill | combo |
|---|---|---|---|---|---|
| 1 | away | misplaced 100% | misplaced 100% | away 56%, misplaced 44% | away 56%, misplaced 44% |
| 2 | upright | upright 96%, drowsy 4% | upright 82%, misplaced 18% | upright 82%, misplaced 18% | upright 100% |
| 3 | face_down | recline 71%, misplaced 29% | recline 86%, misplaced 14% | chin_rest 98%, drowsy 2% | **face_down 100%** |
| 4 | upright | misplaced 100% | upright 77%, misplaced 23% | upright 100% | upright 100% |
| 5 | drowsy | misplaced 100% | misplaced 98%, upright 2% | drowsy 67%, upright 33% | drowsy 67%, upright 33% |
| 6 | chin_rest | misplaced 100% | misplaced 100% | upright 97%, misplaced 2% | upright 100% |
| 7 | recline | recline 100% | recline 100% | recline 100% | recline 100% |
| 8 | lean_forward | misplaced 100% | misplaced 100% | upright 73%, forward_head 10%, away 8% | upright 73%, forward_head 10%, away 8% |
| 9 | lean_forward(필기) | misplaced 100% | misplaced 100% | upright 100% | upright 100% |
| 10 | upright | misplaced 94%, drowsy 6% | misplaced 99%, drowsy 1% | drowsy 61%, upright 39% | drowsy 61%, upright 39% |

### left1 (`labels_20261026_121415.csv`)

| step | label | board (recorded) | off | radial+fill | combo |
|---|---|---|---|---|---|
| 1 | away | recline 96%, misplaced 4% | misplaced 96%, recline 4% | recline 88%, misplaced 12% | recline 88%, misplaced 12% |
| 2 | upright | upright 58%, misplaced 39%, drowsy 3% | upright 60%, misplaced 40% | upright 98%, misplaced 2% | upright 100% |
| 3 | lean_forward | misplaced 100% | misplaced 100% | upright 100% | upright 100% |
| 4 | upright | upright 56%, misplaced 44% | misplaced 100% | upright 100% | upright 100% |
| 5 | forward_head | misplaced 100% | misplaced 97%, invalid 3% | upright 67%, misplaced 25%, away 5% | upright 92%, away 5%, invalid 3% |
| 6 | upright | misplaced 100% | upright 75%, misplaced 22% | upright 88%, away 9% | upright 88%, away 9% |
| 7 | recline | recline 90%, misplaced 10% | recline 48%, misplaced 39%, upright 8% | misplaced 67%, upright 18%, away 16% | misplaced 67%, upright 18%, away 16% |
| 8 | upright | misplaced 100% | misplaced 100% | upright 78%, drowsy 22% | upright 78%, drowsy 22% |
| 9 | chin_rest(왼손) | misplaced 100% | misplaced 100% | misplaced 100% | misplaced 100% |
| 10 | chin_rest(오른손) | misplaced 100% | misplaced 100% | upright 96%, drowsy 4% | upright 96%, drowsy 4% |
| 11 | upright | misplaced 100% | misplaced 100% | face_down 80%, recline 20% | face_down 100% (라벨 어긋남 추정) |
| 12 | face_down | misplaced 100% | misplaced 100% | face_down 83%, recline 16% | **face_down 100%** |
| 13 | upright | misplaced 64%, upright 36% | upright 56%, misplaced 44% | upright 83%, drowsy 17% | upright 83%, drowsy 17% |
| 14 | drowsy | misplaced 100% | misplaced 100% | drowsy 77%, upright 23% | drowsy 77%, upright 23% |
| 15 | away | misplaced 100% | misplaced 75%, recline 24% | misplaced 40%, recline 34%, away 17% | misplaced 40%, recline 34%, away 17% |
| 16 | upright | misplaced 84%, recline 11%, upright 5% | misplaced 100% | misplaced 100% | upright 80%, misplaced 20% (치환) |
| 17 | upright(독서) | misplaced 100% | misplaced 67%, drowsy 17%, upright 16% | upright 100% | upright 100% |
| 18 | lean_forward(필기) | misplaced 100% | misplaced 91%, drowsy 6%, upright 3% | upright 100% | upright 100% |
| 19 | upright | misplaced 100% | misplaced 96%, invalid 4% | upright 87%, drowsy 8% | upright 87%, drowsy 8% |

### left2_facedown (`labels_20261026_132307.csv`)

| step | label | board (recorded) | off | radial+fill | combo |
|---|---|---|---|---|---|
| 1 | upright | upright 85%, misplaced 14% | upright 100% | upright 100% | upright 100% |
| 2 | face_down | misplaced 100% | misplaced 88%, recline 12% | upright 59%, chin_rest 37% | **face_down 100%** |
| 3 | upright | misplaced 85%, upright 15% | misplaced 58%, upright 42% | upright 100% | upright 100% |
