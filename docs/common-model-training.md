# PC 공통 1D CNN 학습

공통 모델 학습 단계 구현을 완료했다. 개인 head 재학습과 TFLite 변환은 다음 단계다.
hub에는 TensorFlow를 추가하지 않으며 관찰 모드 기본 비활성을 유지한다.

## 재현 명령

저장소 루트에서 실행한다. Windows PowerShell 예시:

```powershell
python -m venv --system-site-packages .venv-ml
.\.venv-ml\Scripts\python.exe -m pip install -r ml/requirements-training.txt
.\.venv-ml\Scripts\python.exe -m ml.training.build_dataset --name common-cnn-smoke --synthetic default,short,demo --seeds 1-8 --offsets 0,3,7 --augment 1
.\.venv-ml\Scripts\python.exe -m ml.training.train_common --dataset ml/datasets/common-cnn-smoke --output ml/models/common-cnn-smoke --epochs 20 --batch-size 128 --seed 42
```

학습 전용 요구사항은 TensorFlow 2.20.0이다. 검증 환경은 Windows CPU/Python 3.11이다.
설치 근거: [TensorFlow 공식 설치 안내](https://www.tensorflow.org/install/pip).
Conv1D API: [TensorFlow Conv1D](https://www.tensorflow.org/api_docs/python/tf/keras/layers/Conv1D).

## 모델과 데이터 분할

- 입력: float32 시간 창 6 × 특징 17. 추가 표준화 없이 기존 특징 계약을 사용한다.
- 구조: Conv1D(16, kernel 3, same, ReLU) 두 층 → GlobalAveragePooling1D → Dense(16, ReLU) → personal_head Dense(4, softmax).
- 파라미터 1,956개. backbone/head 이름을 분리해 이후 개인 head 재학습에 사용할 수 있다.
- 같은 합성 scenario/seed의 offset 변형을 하나의 원본 세션 그룹으로 묶는다. 기존 생성기의 offset 분할 누수도 수정했다.
- train 그룹에서 validation을 분리한다. validation/test는 원본 창만 사용하고 train은 증강 창을 포함한다.
- validation loss 최소 epoch로 모델을 선택한다. test는 선택 이후 마지막 비교에만 사용한다.
- train 클래스 빈도 역수 가중치를 사용한다. seed를 고정하고 TensorFlow 결정적 연산을 활성화한다.
- 실로그는 파일 세션 단위로 분리한다. 사용자 식별을 검증하지 않으므로 사용자 단위 일반화 성능으로 해석하지 않는다.

## 실제 실행 결과

2026-10-06, seed 42, 20 epochs, batch 128.

- 원본 세션 72개 = 독립 원본 그룹 24개 × offset 3개.
- train/validation/test 그룹: 14/4/6개.
- train 창 8,450개, test 창 1,045개. validation 그룹의 증강본은 학습에도 평가에도 쓰지 않는다.
- 선택 epoch: 20. Keras 모델 파일 59,340 bytes이며 TFLite 크기가 아니다.
- test 라벨은 전부 FSM 약한 라벨이다. ESM 정정 test 표본은 없다.

| 모델 | FSM 라벨 일치율 | macro F1 |
|---|---:|---:|
| 1D CNN | 88.80% | 0.8590 |
| 클래스 균형 logistic 기준선 | 81.24% | 0.7654 |

CNN 클래스별 recall: focus 94.67%, fatigue 80.75%, rest 91.30%, idle 100%.
rest 표본은 46개에 불과하다. 독립 실사용자 검증이나 모델 승격 근거로 사용하지 않는다.

혼동행렬 행은 정답, 열은 예측이며 순서는 focus/fatigue/rest/idle이다.

| 정답 | focus | fatigue | rest | idle |
|---|---:|---:|---:|---:|
| focus | 284 | 13 | 0 | 3 |
| fatigue | 63 | 407 | 34 | 0 |
| rest | 0 | 4 | 42 | 0 |
| idle | 0 | 0 | 0 | 195 |

## 산출물과 검증

`ml/models/common-cnn-smoke/common.keras`와 `training-report.json`은 Git에서 제외한다.
report는 seed·옵션·패키지 버전·dataset SHA256·manifest·분할 그룹·학습 이력·선택 epoch·클래스별 지표·기준선·라벨 출처별 평가를 포함한다.
입력 계약/비유한 값/학습-평가 그룹 중복/평가 증강을 거절한다.
실제 학습·모델 재로딩·확률 출력 계약을 테스트했다.

## 다음 개발

backbone 동결 개인 head 재학습을 PC에서 구현하고 독립 개인 세션으로 공통 모델과 비교한다.
동의/저장 정책을 확대하지 않는다. 실데이터가 없는 현재는 합성 데이터로 경로만 검증한다.
이후 TFLite 변환과 원본/변환 수치 비교를 진행한다.
