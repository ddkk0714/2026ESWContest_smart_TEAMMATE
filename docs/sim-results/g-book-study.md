# (g) 책 공부 자세 보강 학습 — 1차, v7b 출발 (10-24)

> 질문: "수험생이 책을 펴고 책상 위에 손이 있고 페이지를 넘겨도 바른자세로 인식이 잘 돼?"
> 전부 **[시뮬]**(tofsim MuJoCo + SMPL-X, posture_sim). 실측 줄만 **[실측]**(10-19 녹화).
> 출처: PC `tof-sim_ubai_results_1024/book_study/README.md`(장면 추가·1회분 점검), UBAI `~/tof-sim-v5-book/runs/BOOK_FT_REPORT.md`(job 1044228, 10-24 20:13, 전/후 표).

## 결론

- 시뮬에 "책상 위에 펼친 책 + 고개 숙여 읽기 + 손은 책 위 + 페이지 넘김" 장면이 **없었다**. 시뮬 사본(`~/tof-sim-v5-book`)에 기본 꺼짐으로 넣었다(`book_read_desk` → upright, `book_write_desk` → lean_forward, 끈 상태는 원본과 비트 단위로 같음).
- 배포 모델(v7b, opt_l8v5_c256_best)은 책 읽기를 대부분 **거북목(forward_head)** 으로 본다. 보강 학습 뒤 책 읽기(upright) 0.17 → **0.90**(ft_last) [시뮬, 30회].
- 자세 한 바퀴(posture_tour 20 시드)는 0.802 → 0.834 로 올랐지만 **거북목(turtle) 재현이 0.61 → 0.14 로 무너졌다**(대부분 upright 로).
- **[실측]** 학습 중 10-19 녹화 macro-F1 은 0.333~0.368(센서별) — 보드 모델 opt_l8_c256 의 0.445(같은 방식)보다 낮다. v4 출발 재학습은 "추후 추가".
- 페이지 넘김 자체는 판정을 흔들지 않는다(넘기는 초와 그 밖의 책 읽기 정확도가 비슷).

## 전/후 — 책 공부 각본 (위치 −40/−20/0/+20/+40° × 체형 시드 1~6 = 30회) [시뮬]

출처 `BOOK_FT_REPORT.md`. deployed = v7b(md5 331ef28e), ft_best md5 98f8bb38, ft_last md5 24383af7.

| 구간 (정답 라벨) | deployed | ft_best | ft_last |
|---|---|---|---|
| 바른자세 타이핑(기준) | 0.83 | 0.90 | 0.91 |
| 책 읽기·두 손 책 위 (upright) | **0.13** | 0.72 | **0.86** |
| 책 읽기·바른자세 필기 (upright) | **0.16** | 0.94 | 0.97 |
| 책 읽기 + 힐끗·컵 (upright) | 0.27 | 0.88 | 0.93 |
| **책 읽기 전체 (upright)** | **0.17** | 0.81 | **0.90** |
| 책 위로 숙여 필기 (lean_forward) | 0.95 | 0.95 | 0.99 |
| 책 위 졸기·첫 끄덕임 전 (upright) | 0.38 | 1.00 | 0.99 |
| 책 위 졸기 (drowsy) | 0.80 | 0.84 | 0.84 |
| 숙여 필기 (기존 writing) | 0.82 | 0.81 | 0.85 |

- 책 읽기 초의 판정 분포: deployed forward_head 77.1 % · upright 17.4 % → ft_last upright 90.3 % · forward_head 6.5 %.
- 페이지 넘김: deployed 넘기는 초 0.27 / 그 밖 0.16 → ft_last 0.88 / 0.91.

### 1회분 예비 측정과 숫자가 다르다

`book_study/README.md` §4 의 1회분(정면 0°, 체형 시드 1, 1360 s, 하네스 = reactai 워크트리 posture_sim 사본 + 페이지 넘김 래퍼)에서는 같은 배포 모델의 **책 읽기 전체가 0.70**(forward_head 27 %), 책 위로 숙여 필기가 **0.28**(chin_rest 99 %)이었다.
30회 측정(`BOOK_FT_REPORT.md`)의 정면 0° 열은 책 읽기 전체 0.19, 숙여 필기 0.88 이다. 하네스·각본 실행이 달라 생긴 차이로 보이며, 원인은 확인하지 않았다. 결론(배포 모델이 책 읽기를 거북목으로 본다)은 같다.

## 자세 한 바퀴 (posture_tour × 시드 20) [시뮬]

| 모델 | 정확도 평균 | 최소 | misplaced 오탐 | writing | turtle | lean_forward | drowsy_prenod | upright | chin_rest | drowsy |
|---|---|---|---|---|---|---|---|---|---|---|
| deployed (v7b) | 0.802 | 0.601 | 3.5 % | 0.18 | **0.61** | 0.72 | 0.49 | 0.85 | 0.89 | 0.90 |
| ft_best | 0.821 | 0.559 | 1.7 % | 0.23 | 0.36 | 0.81 | 0.76 | 0.95 | 0.77 | 0.95 |
| ft_last | **0.834** | 0.577 | 1.0 % | **0.65** | **0.14** | 0.83 | 0.77 | 0.94 | 0.77 | 0.94 |

ft_last 의 turtle 은 upright 2081 초 · lean_forward 451 초로 틀렸다 — 책 읽기의 고개 숙임을 upright 로 배우면서 거북목과 겹친 것으로 보인다.

## 학습 중 실측 점수 [실측]

`BOOK_FT_REPORT.md` 학습 로그 끝(10-24 17:51~19:21, `real_20261019_p1_front80cm_before17.npz`, 센서별):
acc 0.243~0.275, macro-F1 **0.333~0.368**. 재현 upright 0.04~0.16, chin_rest 0.00, drowsy 0.15~0.16, forward_head 0.00.

## 수험생 프로필 관련 (1회분 근거 — 전체 측정 뒤 확정)

`book_examinee` 는 `lean_forward` 의 자세 증거만 0 으로 끈다. 그런데 책 읽기의 오판은 forward_head(phi 0.3·delta 0.2), 책 위 필기 오판은 chin_rest(delta 0.6 — 피로 증거)로 간다 → 보강 모델을 쓰기 전까지는 수험생 프로필에서 forward_head 증거를 낮추는 쪽을 검토.

## 파일

- 장면 코드(시뮬 사본): PC `tof-sim_ubai_results_1024/book_study/code/`(session.py · mj_scene.py · l8params.py 패치, `l8/v5_rebal_book.json`), 각본 `scenarios/book_study_p0..p4.yaml`.
- 이 저장소에는 결과 문서만 넣었다. 모델 가중치는 커밋하지 않는다.
