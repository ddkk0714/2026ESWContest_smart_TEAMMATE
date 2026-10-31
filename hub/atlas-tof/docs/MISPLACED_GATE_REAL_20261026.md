# misplaced 게이트 — 10-26 실측 녹화 리플레이 (전/후)

보드 모델 `opt_l8_c256`(sha256 1ced97c0…), 왼쪽 자리, 장치 기울기 고정. 녹화 3종은 fork `data/real-20261026` 의
`dataset/real_20261026/real_20261026_{left,left2,left2_facedown}.zip` (`rec_toflines.jsonl` = 보드가 받은 `/raw` 격자 그대로,
`labels_*.csv` = 실제 수행 시각 라벨). 도구: `tofposture_real_replay`(`src/real_replay.cpp`), 베이스라인은 각 녹화의 첫 10 s
(left1·left2 = 빈 자리 단계, left2_facedown = 착석 바르게)에서 설치 때처럼 캡처. 라벨 구간 앞뒤 2 s 제외, 10 Hz tick.

```sh
build/host/real_replay models/opt_l8_c256/posture_net.bin left/rec_toflines.jsonl left/labels_20261026_121415.csv left/protocol_start_20261026_121415.txt \
    --capture 0 10 --board left/rec_raw.jsonl
```

열: **board** = 녹화 중 보드가 실제로 낸 `/api/state.posture`(10-26 바이너리, 모델 그대로) · **off** = 같은 격자를 이 코드로 다시 돌린 것
(모델 그대로, `TOFPOSTURE_MISPLACED_RULE=off`) · **and** = 모델 misplaced ∧ 베이스라인 변화 규칙 · **never** = misplaced 항상 억제.
off 가 board 와 비슷하면 리플레이가 보드를 재현한 것이다(리셋 시점·시작 상태가 달라 똑같지는 않다).

## 결론

| 녹화 | 라벨 tick | board misplaced | off misplaced → and | 정확도 off → and | 규칙 발화(device_moved) |
|---|---|---|---|---|---|
| left1 (27 분, 19단계) | 8545 | 대부분 100 % | 76.3 % → **0 %** | 0.193 → **0.440** | 0 / 16614 tick |
| left2 (+엎드림 1차, 10단계) | 4704 | 대부분 100 % | 56.9 % → **0 %** | 0.246 → **0.315** | 0 / 8791 tick |
| left2_facedown (3단계) | 1080 | 엎드림·그 뒤 바르게 85~100 % | 54.6 % → **0 %** | 0.379 → **0.481** | 0 / 4882 tick |

- 장치는 세 녹화 내내 고정이었고 규칙은 한 번도 참이 되지 않았다(max moved_frac 순간값 0.50 / 0.85 / 0.25, 10 s 유지 없음).
  → `and` 와 `never` 가 같은 결과.
- misplaced 가 빠진 자리는 대부분 **upright** 가 차지한다(모델이 두 번째로 보던 라벨). 엎드림(face_down)은 세 녹화 모두 **recline** 으로
  나온다 — 게이트는 misplaced 만 치우므로 엎드림 자체는 여전히 못 잡는다(10-26 메모: 이 기울기에서 엎드림이 모델이 배운 시야에 안 들어옴).
- 빈 자리(away) 단계에서 모델은 misplaced·recline 을 내고 away 를 거의 내지 않는다. 재실은 `present_rule`(coverage) 로 봐야 한다.

## 규칙을 고친 이유 (첫 판은 lost 를 셌다)

첫 구현은 10-17 문서대로 "베이스라인에서 보이던 배경 칸이 지금 무효(lost)" 도 변화로 셌다. 그러자 left2 에서 장치가 가만히 있는데
20 % 구간(바르게·졸음·턱괴기)에서 device_moved 가 참이 되어 misplaced 가 다시 나왔다(28~71 %). 칸별로 보니

| 단계(left2, S0) | same | 사람이 가림(near) | 사람 범위 밖으로 가까워짐 | 멀어짐 | **lost** |
|---|---|---|---|---|---|
| away(빈 자리, 베이스라인) | 0.73 | 0 | 0 | 0 | 0.27 |
| upright | 0.59 | 0.09 | 0 | 0 | 0.32 |
| face_down | 0.16 | 0.32 | 0 | 0 | **0.51** |
| drowsy / chin_rest / recline | 0.19~0.25 | 0.05~0.09 | 0~0.01 | 0 | **0.69~0.73** |

벽이 3~4 m 라 빈 자리에서도 배경 칸의 20~30 % 가 비고, 사람이 앉으면 70~90 % 가 무효로 떨어진다(먼 벽 신호가 약해짐). 반면
"멀어짐"과 "사람 범위(≤ 1300 mm) 밖으로 가까워짐"은 세 녹화 전체에서 0 이었다. 그래서 lost 는 세지 않고, **지금도 유효한 배경 칸만** 평가해
`moved_frac = changed / (bg − lost)` 로 바꿨다(평가 칸 4개 미만이면 보류). 한계: 기울어져 배경 칸이 책상·바닥(사람 범위 안)만 보게 되는
이동은 못 잡는다 — 실제 장치 이동 녹화(left1 17·18단계 예정이었으나 전원 문제로 건너뜀)가 없어 **양성 쪽은 합성 테스트로만** 확인했다.

## 단계별 표

### left1 (`labels_20261026_121415.csv`)

| step | label | ticks | board (recorded) | off (replay, model as-is) | and (model AND baseline rule) | match board / off / and |
|---|---|---|---|---|---|---|
| 1 | away | 260 | recline 96%, misplaced 4% | misplaced 85%, recline 15% | recline 100% | 0 / 0 / 0 |
| 2 | upright | 1160 | upright 58%, misplaced 39%, drowsy 3% | upright 72%, misplaced 26%, away 2% | upright 96%, away 4% | 58 / 72 / 96 |
| 3 | lean_forward | 561 | misplaced 100% | misplaced 100% | upright 94%, drowsy 6% | 0 / 0 / 0 |
| 4 | upright | 260 | upright 56%, misplaced 44% | misplaced 54%, upright 46% | upright 100% | 56 / 46 / 100 |
| 5 | forward_head | 560 | misplaced 100% | misplaced 84%, away 14%, invalid 2% | upright 62%, away 34%, drowsy 2% | 0 / 0 / 0 |
| 6 | upright | 261 | misplaced 100% | upright 48%, misplaced 39%, away 11% | upright 75%, away 23%, invalid 2% | 0 / 48 / 75 |
| 7 | recline | 560 | recline 90%, misplaced 10% | misplaced 45%, recline 35%, away 14% | recline 57%, upright 25%, away 18% | 90 / 35 / 57 |
| 8 | upright | 260 | misplaced 100% | misplaced 100% | upright 100% | 0 / 0 / 100 |
| 9 | chin_rest | 410 | misplaced 100% | misplaced 100% | upright 73%, recline 27% | 0 / 0 / 0 |
| 10 | chin_rest | 410 | misplaced 100% | misplaced 100% | upright 100% | 0 / 0 / 0 |
| 11 | upright | 261 | misplaced 100% | misplaced 100% | recline 100% | 0 / 0 / 0 |
| 12 | face_down | 560 | misplaced 100% | misplaced 100% | recline 100% | 0 / 0 / 0 |
| 13 | upright | 260 | misplaced 64%, upright 36% | misplaced 51%, upright 49% | upright 100% | 36 / 49 / 100 |
| 14 | drowsy | 860 | misplaced 100% | misplaced 100% | upright 53%, drowsy 36%, lean_forward 7% | 0 / 0 / 36 |
| 15 | away | 260 | misplaced 100% | misplaced 65%, away 34%, invalid 2% | away 69%, recline 24%, upright 6% | 0 / 34 / 69 |
| 16 | upright | 261 | misplaced 84%, recline 11%, upright 5% | misplaced 100% | chin_rest 50%, upright 21%, recline 17% | 5 / 0 / 21 |
| 17 | upright | 560 | misplaced 100% | misplaced 72%, upright 28% | upright 100% | 0 / 28 / 100 |
| 18 | lean_forward | 560 | misplaced 100% | misplaced 88%, drowsy 10%, upright 2% | upright 77%, drowsy 23% | 0 / 0 / 0 |
| 19 | upright | 261 | misplaced 100% | misplaced 96%, invalid 4% | upright 96%, invalid 4% | 0 / 0 / 96 |

overall: off acc 0.193 (misplaced 76 %, upright 17 %, away 3 %, recline 3 %) → and acc 0.440 (upright 65 %, recline 19 %, away 7 %, drowsy 6 %, chin_rest 2 %).

### left2 (`labels_20261026_130558_with_facedown.csv`)

| step | label | ticks | board (recorded) | off | and | match board / off / and |
|---|---|---|---|---|---|---|
| 1 | away | 260 | misplaced 100% | misplaced 100% | recline 67%, away 33% | 0 / 0 / 33 |
| 2 | upright | 560 | upright 96%, drowsy 4% | upright 76%, misplaced 22%, away 2% | upright 94%, away 6% | 96 / 76 / 94 |
| 3 | face_down (1차) | 560 | recline 71%, misplaced 29% | recline 91%, misplaced 9% | recline 100% | 0 / 0 / 0 |
| 4 | upright | 261 | misplaced 100% | upright 66%, misplaced 34% | upright 100% | 0 / 66 / 100 |
| 5 | drowsy | 560 | misplaced 100% | misplaced 80%, away 17%, upright 2% | upright 79%, away 20%, drowsy 1% | 0 / 0 / 1 |
| 6 | chin_rest | 560 | misplaced 100% | misplaced 98%, away 2% | upright 94%, away 6% | 0 / 0 / 0 |
| 7 | recline | 560 | recline 100% | recline 100% | recline 100% | 100 / 100 / 100 |
| 8 | lean_forward | 561 | misplaced 100% | misplaced 60%, drowsy 29%, away 8% | drowsy 71%, upright 19%, away 10% | 0 / 0 / 0 |
| 9 | lean_forward(필기) | 561 | misplaced 100% | misplaced 100% | upright 99%, drowsy 1% | 0 / 0 / 0 |
| 10 | upright | 261 | misplaced 94%, drowsy 6% | misplaced 98%, drowsy 2% | drowsy 83%, upright 17% | 0 / 0 / 17 |

overall: off acc 0.246 (misplaced 57 %) → and acc 0.315 (upright 52 %, recline 28 %, drowsy 13 %, away 7 %).

### left2_facedown (`labels_20261026_132307.csv`, 착석 상태 캡처)

| step | label | ticks | board (recorded) | off | and | match board / off / and |
|---|---|---|---|---|---|---|
| 1 | upright | 260 | upright 85%, misplaced 14%, away 1% | upright 100% | upright 100% | 85 / 100 / 100 |
| 2 | face_down | 560 | misplaced 100% | misplaced 86%, recline 14% | recline 100% | 0 / 0 / 0 |
| 3 | upright | 260 | misplaced 85%, upright 15% | upright 57%, misplaced 43% | upright 100% | 15 / 57 / 100 |

overall: off acc 0.379 (misplaced 55 %) → and acc 0.481 (recline 52 %, upright 48 %).
