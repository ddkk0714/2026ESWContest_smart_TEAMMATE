# 개인 head 재학습과 공통 모델 유지

PC에서 backbone을 동결하고 마지막 `personal_head`만 재학습한다.
전체 1,956개 파라미터 중 68개만 학습하며 모든 backbone 가중치의 SHA256을 전후 비교한다.
[Keras 전이 학습 안내](https://keras.io/guides/transfer_learning/)의 동결·재컴파일 방식을 따른다.
실사용자 데이터·동의 저장소·hub 자동 활성화는 이번 개발에 포함하지 않는다.

## 재현 명령

학습용 가상환경 설치는 [공통 모델 학습](common-model-training.md)을 따른다.
공통 모델은 체크포인트 fingerprint를 포함한 새 training-report가 필요하다.

```powershell
.\.venv-ml\Scripts\python.exe -m ml.training.train_common --dataset ml/datasets/common-cnn-smoke --output ml/models/common-personal-reference --epochs 20 --batch-size 128 --seed 42
.\.venv-ml\Scripts\python.exe -m ml.training.build_dataset --name personal-head-smoke --synthetic default,short,demo --seeds 101-108 --offsets 0,3,7 --augment 1
.\.venv-ml\Scripts\python.exe -m ml.training.train_personal --dataset ml/datasets/personal-head-smoke --common-model ml/models/common-personal-reference/common.keras --common-report ml/models/common-personal-reference/training-report.json --output ml/models/personal-head-run-new --synthetic-demo --epochs 20 --batch-size 128
```

output은 비어 있는 새 디렉터리여야 한다. 이전 후보를 폴백 결과로 잘못 사용하는 것을 방지한다.
common 모델 파일은 수정하지 않으며 모델/report fingerprint 불일치는 거절한다.
공통 모델에 사용된 train/validation/test 그룹과 개인 데이터 그룹이 겹치면 거절한다.

실로그 실행은 이미 권한·동의를 확보한 로컬 데이터에 대해서만 `--authorized-local-data`를 명시한다.
이 옵션은 제품의 동의/저장 정책을 새로 정의하거나 동의를 기록하는 UI가 아니다.
실로그와 합성 데이터를 섞지 않으며 `--synthetic-demo`에서는 실로그를 거절한다.

## 학습 및 채택 조건

PC 전용 정책은 `hub/deskmate_hub/config/personal_head.yaml`에 둔다.
보수적인 개발 기본값이며 실제 사용자 검증으로 보정하기 전에는 제품 정책으로 승격하지 않는다.

- train/validation/test 최소 그룹: 2/1/1개.
- 클래스별 최소 원본 창: train 10, validation 5, test 5개. train 증강 복사본은 데이터 부족 판정을 채우지 못한다.
- 실로그는 fit 그룹의 원본 ESM 정정 창이 최소 10개 필요하다. 합성 데모는 이 조건만 제외한다.
- validation loss가 가장 낮은 epoch를 선택한다. test는 epoch 선택에 사용하지 않는다.
- validation과 test 모두 macro F1이 개선되고 클래스별 recall이 떨어지지 않아야 candidate를 선택한다.
- 데이터 부족·클래스 편중·성능 악화이면 common을 선택한다. 누락/손상/계약 불일치는 명시적인 오류로 종료한다.
- test에 의한 최종 거절은 한 번의 채택 감사다. 같은 test에 맞추어 정책이나 학습 설정을 반복 조정하지 않는다.

report의 selected/selected_model/selected_model_sha256을 확인한다.
candidate.keras가 있어도 selected가 common이면 후보를 배포하지 않는다.
후보와 report만 로컬에 저장하며 모델 자동 복사·활성화·hub 게이트 변경은 하지 않는다.

## 실제 합성 데모 결과

2026-10-06, 공통 seed 1–8과 분리된 개인 데모 seed 101–108, 20 epochs.
개인 train/validation/test 그룹 14/4/6개, validation 창 866개, test 창 1,045개.
선택 epoch 7, head 변경 확인, backbone 전후 hash 동일.

| 모델 | test FSM 라벨 일치율 | macro F1 | focus recall |
|---|---:|---:|---:|
| 공통 | 89.19% | 0.8649 | 95.00% |
| 개인 후보 | 89.86% | 0.8818 | 92.33% |

macro F1은 개선됐지만 focus recall이 validation과 test에서 낮아져 **공통 모델 유지**로 판정했다.
후보를 채택하려고 조건을 완화하지 않았다. 이것은 합성/FSM 라벨 파이프라인 결과이며 실제 개인화 효과나 집중도 정확도의 증거가 아니다.

## 산출물과 검증

모델·dataset·report는 Git에서 제외한다.
report에는 공통 체크포인트/report 및 dataset fingerprint, 그룹, 정책, seed, 이력,
backbone hash, head 변화, 비교 지표, FSM/ESM 라벨 출처별 평가, 선택 모델·이유를 남긴다.
학습·후보 저장·재로딩·backbone 완전 일치·공통 파일 불변·강제 성능 미달 폴백을 테스트했다.
기존 Keras/NumPy 의존성의 deprecation warning은 남아 있으나 저장/재로딩은 정상이다.

다음 개발은 선택된 모델의 TFLite 변환, 원본/변환 출력 오차·top-1 비교와 실제 runtime 연결 검증이다.
