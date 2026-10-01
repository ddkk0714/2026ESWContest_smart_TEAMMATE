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
| jitter | 17852 | {"sigma": 0.03} |
| time_stretch | 17718 | {"ratio_min": 0.8, "ratio_max": 1.25} |
| sensor_dropout | 18015 | {"signals": ["keystroke", "posture", "respiration", "environment", "elapsed"]} |
| baseline_shift | 17738 | {"offset_min": -0.1, "offset_max": 0.1} |
| mix_scale | 17806 | {"factor_min": 0.85, "factor_max": 1.15} |

## 분포 비교

원본 학습 창과 증강 창의 모든 tick을 비교했다. KS는 numpy 경험적 누적분포의 최대 차이다.

| 특징 | 원본 평균 | 원본 표준편차 | 증강 평균 | 증강 표준편차 | 2표본 KS |
|---|---:|---:|---:|---:|---:|
| keystroke_phi | 0.1065 | 0.2027 | 0.0979 | 0.1974 | 0.0661 |
| keystroke_delta | 0.1594 | 0.2766 | 0.1452 | 0.2652 | 0.0624 |
| keystroke_available | 0.4156 | 0.4928 | 0.3825 | 0.4860 | 0.0332 |
| posture_phi | 0.1403 | 0.1480 | 0.1303 | 0.1489 | 0.1514 |
| posture_delta | 0.3588 | 0.4389 | 0.3162 | 0.4180 | 0.2251 |
| posture_available | 0.7328 | 0.4425 | 0.6678 | 0.4710 | 0.0650 |
| respiration_phi | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| respiration_delta | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| respiration_available | 0.7328 | 0.4425 | 0.6684 | 0.4708 | 0.0644 |
| environment_phi | 0.0538 | 0.1758 | 0.0603 | 0.1673 | 0.2768 |
| environment_delta | 0.4017 | 0.4434 | 0.3622 | 0.4239 | 0.1383 |
| environment_available | 1.0000 | 0.0000 | 0.8425 | 0.3642 | 0.1575 |
| elapsed_phi | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| elapsed_delta | 0.0755 | 0.0792 | 0.0683 | 0.0782 | 0.0686 |
| elapsed_available | 0.7698 | 0.4210 | 0.7011 | 0.4578 | 0.0686 |
| present | 0.7328 | 0.4425 | 0.7328 | 0.4425 | 0.0000 |
| pc_ratio | 0.5678 | 0.2666 | 0.5678 | 0.2664 | 0.0014 |

## 기준 분류기 sanity check

창별 평균·표준편차(34차원)를 입력으로 쓴다. 최근접 중심, 소프트맥스 로지스틱 회귀(경사하강), 클래스 가중 소프트맥스(balanced)를 원본만/원본+증강으로 학습하고 동일한 세션 분리 평가 창에서 비교했다.

| 방법 | 학습 | 정확도 | focus 재현율 | fatigue 재현율 | rest 재현율 | idle 재현율 |
|---|---|---:|---:|---:|---:|---:|
| nearest_centroid | original | 0.691 | 0.925 | 0.285 | 0.930 | 0.953 |
| nearest_centroid | augmented | 0.690 | 0.925 | 0.281 | 0.930 | 0.953 |
| softmax_logistic | original | 0.907 | 0.951 | 0.948 | 0.000 | 1.000 |
| softmax_logistic | augmented | 0.904 | 0.951 | 0.945 | 0.000 | 0.993 |
| softmax_logistic_balanced | original | 0.860 | 0.949 | 0.675 | 0.975 | 1.000 |
| softmax_logistic_balanced | augmented | 0.850 | 0.951 | 0.646 | 0.975 | 1.000 |

혼동행렬(행=실제, 열=예측; 순서: focus, fatigue, rest, idle):

- `nearest_centroid_original`: `[[1192, 79, 0, 17], [433, 449, 664, 32], [0, 17, 227, 0], [50, 0, 0, 1020]]`
- `nearest_centroid_augmented`: `[[1192, 79, 0, 17], [447, 444, 668, 19], [0, 17, 227, 0], [50, 0, 0, 1020]]`
- `softmax_logistic_original`: `[[1225, 46, 0, 17], [65, 1496, 0, 17], [0, 244, 0, 0], [0, 0, 0, 1070]]`
- `softmax_logistic_augmented`: `[[1225, 46, 0, 17], [71, 1491, 0, 16], [0, 244, 0, 0], [7, 0, 0, 1063]]`
- `softmax_logistic_balanced_original`: `[[1222, 32, 17, 17], [73, 1065, 408, 32], [0, 6, 238, 0], [0, 0, 0, 1070]]`
- `softmax_logistic_balanced_augmented`: `[[1225, 29, 17, 17], [76, 1020, 450, 32], [0, 6, 238, 0], [0, 0, 0, 1070]]`

## 결측·개인차 내성

깨끗한 평가 창은 학습과 같은 시뮬레이터 분포라 증강의 쓸모가 드러나지 않는다. 평가 창에 센서 결측·기준선 이동·민감도 차이를 입혀(시드 고정) 소프트맥스 정확도를 비교했다.

| 평가 조건 | 원본만 학습 | 원본+증강 학습 | 차이 |
|---|---:|---:|---:|
| clean | 0.907 | 0.904 | -0.003 |
| sensor_dropout | 0.869 | 0.896 | +0.027 |
| baseline_shift | 0.907 | 0.903 | -0.004 |
| mix_scale | 0.909 | 0.904 | -0.005 |

해석: 증강 학습은 sensor_dropout 조건에서 1%p 이상 더 버틴다. baseline_shift, mix_scale 는 차이가 1%p 미만 — 이 크기의 변화는 표준화된 요약 특징만으로도 견딘다. 실데이터에서 사람·날짜가 바뀔 때 같은 비교를 다시 해야 한다.

## 한계·다음 단계

시나리오와 FSM에서 유래한 약한 라벨이므로 결과가 높아도 실사용 성능을 뜻하지 않는다. 시드·offset 변형은 독립적인 사람이나 환경을 대체하지 못한다. 이번 기준에서 소프트맥스 정확도는 원본 0.907, 증강 0.904이고 증강 후 rest 재현율은 0.000이다. rest 는 평가 창의 약 6% 이고 RECOVERY 가 1~2 tick 만 이어지는 짧은 상태라 묻히기 쉽다 — 클래스 가중 학습(balanced)에서는 rest 재현율 0.975, 정확도 0.850 이다. 라벨이 FSM 판정에서 왔으므로 이 수치는 '분류기가 FSM 을 얼마나 따라 하는가'(일관성)이지 사람 상태 정확도가 아니다. 증강은 호흡·경과 시간처럼 센서 측정값이 아닌 칸(`augment.EXEMPT`)을 흔들지 않는다. ESM 정정 라벨을 실제 세션에서 축적하고 사람·시간 단위로 재평가해야 한다. 1D CNN 학습과 TFLite 변환은 조명희 님의 후속 범위다.

재현: `python ml/training/build_dataset.py --name synth-v1 --synthetic default,short,demo --seeds 1-20 --offsets 0,3,7 --augment 4`

특징 순서: `keystroke_phi, keystroke_delta, keystroke_available, posture_phi, posture_delta, posture_available, respiration_phi, respiration_delta, respiration_available, environment_phi, environment_delta, environment_available, elapsed_phi, elapsed_delta, elapsed_available, present, pc_ratio`
