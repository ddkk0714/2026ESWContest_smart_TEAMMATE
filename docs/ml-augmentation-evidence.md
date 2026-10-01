# DESKMATE 2단계 학습 창 증강 증거

**합성 데이터 기준, 실사용 성능 아님.** FSM phase의 약한 라벨로 만든 개념 실증이다.

## 데이터 규모

| 항목 | 학습 | 평가 | 전체 |
|---|---:|---:|---:|
| 세션 | 135 | 45 | 180 |
| 원본 창 | 11160 | 4180 | 15340 |
| 증강 창 | 44640 | 0 | 44640 |

| 라벨 출처 | 창 수 |
|---|---:|
| FSM | 59980 |
| ESM | 0 |

| 클래스 | 원본 학습 | 증강 학습 | 평가 |
|---|---:|---:|---:|
| focus | 3392 | 13568 | 1288 |
| fatigue | 4441 | 17764 | 1578 |
| rest | 617 | 2468 | 244 |
| idle | 2710 | 10840 | 1070 |

## 증강 방법·파라미터

| 방법 | 적용 수 | 파라미터 |
|---|---:|---|
| jitter | 17730 | {"sigma": 0.03} |
| time_stretch | 17818 | {"ratio_min": 0.8, "ratio_max": 1.25} |
| sensor_dropout | 17893 | {"signals": ["keystroke", "posture", "respiration", "environment", "elapsed"]} |
| baseline_shift | 17877 | {"offset_min": -0.1, "offset_max": 0.1} |
| mix_scale | 17990 | {"factor_min": 0.85, "factor_max": 1.15} |

## 분포 비교

원본 학습 창과 증강 창의 모든 tick을 비교했다. KS는 numpy 경험적 누적분포의 최대 차이다.

| 특징 | 원본 평균 | 원본 표준편차 | 증강 평균 | 증강 표준편차 | 2표본 KS |
|---|---:|---:|---:|---:|---:|
| keystroke_phi | 0.1065 | 0.2027 | 0.0981 | 0.1978 | 0.0653 |
| keystroke_delta | 0.1594 | 0.2766 | 0.1453 | 0.2652 | 0.0625 |
| keystroke_available | 0.4156 | 0.4928 | 0.3833 | 0.4862 | 0.0324 |
| posture_phi | 0.1403 | 0.1480 | 0.1300 | 0.1488 | 0.1504 |
| posture_delta | 0.3588 | 0.4389 | 0.3177 | 0.4188 | 0.2262 |
| posture_available | 0.7328 | 0.4425 | 0.6691 | 0.4705 | 0.0637 |
| respiration_phi | 0.0000 | 0.0000 | 0.0095 | 0.0225 | 0.2427 |
| respiration_delta | 0.0000 | 0.0000 | 0.0095 | 0.0226 | 0.2424 |
| respiration_available | 0.7328 | 0.4425 | 0.6672 | 0.4712 | 0.0657 |
| environment_phi | 0.0538 | 0.1758 | 0.0600 | 0.1666 | 0.2780 |
| environment_delta | 0.4017 | 0.4434 | 0.3635 | 0.4240 | 0.1376 |
| environment_available | 1.0000 | 0.0000 | 0.8446 | 0.3623 | 0.1554 |
| elapsed_phi | 0.0000 | 0.0000 | 0.0101 | 0.0230 | 0.2554 |
| elapsed_delta | 0.0755 | 0.0792 | 0.0707 | 0.0832 | 0.1355 |
| elapsed_available | 0.7698 | 0.4210 | 0.7000 | 0.4582 | 0.0697 |
| present | 0.7328 | 0.4425 | 0.7328 | 0.4425 | 0.0000 |
| pc_ratio | 0.5678 | 0.2666 | 0.5678 | 0.2664 | 0.0014 |

## 기준 분류기 sanity check

창별 평균·표준편차(34차원)를 입력으로 쓴다. 최근접 중심과 소프트맥스 로지스틱 회귀(경사하강)를 원본만/원본+증강으로 학습하고 동일한 세션 분리 평가 창에서 비교했다.

| 방법 | 학습 | 정확도 | focus 재현율 | fatigue 재현율 | rest 재현율 | idle 재현율 |
|---|---|---:|---:|---:|---:|---:|
| nearest_centroid | original | 0.691 | 0.925 | 0.285 | 0.930 | 0.953 |
| nearest_centroid | augmented | 0.685 | 0.925 | 0.269 | 0.930 | 0.953 |
| softmax_logistic | original | 0.907 | 0.951 | 0.948 | 0.000 | 1.000 |
| softmax_logistic | augmented | 0.905 | 0.951 | 0.945 | 0.000 | 0.997 |

혼동행렬(행=실제, 열=예측; 순서: focus, fatigue, rest, idle):

- `nearest_centroid_original`: `[[1192, 79, 0, 17], [433, 449, 664, 32], [0, 17, 227, 0], [50, 0, 0, 1020]]`
- `nearest_centroid_augmented`: `[[1192, 79, 0, 17], [434, 425, 688, 31], [0, 17, 227, 0], [50, 0, 0, 1020]]`
- `softmax_logistic_original`: `[[1225, 46, 0, 17], [65, 1496, 0, 17], [0, 244, 0, 0], [0, 0, 0, 1070]]`
- `softmax_logistic_augmented`: `[[1225, 46, 0, 17], [71, 1491, 0, 16], [0, 244, 0, 0], [3, 0, 0, 1067]]`

## 한계·다음 단계

시나리오와 FSM에서 유래한 약한 라벨이므로 결과가 높아도 실사용 성능을 뜻하지 않는다. 시드·offset 변형은 독립적인 사람이나 환경을 대체하지 못한다. 이번 기준에서 소프트맥스 정확도는 원본 0.907, 증강 0.905이고 증강 후 rest 재현율은 0.000이다. ESM 정정 라벨을 실제 세션에서 축적하고 사람·시간 단위로 재평가해야 한다. 1D CNN 학습과 TFLite 변환은 조명희 님의 후속 범위다.

재현: `python ml/training/build_dataset.py --name synth-v1 --synthetic default,short,demo --seeds 1-20 --offsets 0,3,7 --augment 4`

특징 순서: `keystroke_phi, keystroke_delta, keystroke_available, posture_phi, posture_delta, posture_available, respiration_phi, respiration_delta, respiration_available, environment_phi, environment_delta, environment_available, elapsed_phi, elapsed_delta, elapsed_available, present, pc_ratio`
