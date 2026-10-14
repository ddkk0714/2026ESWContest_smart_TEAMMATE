# TFLite 변환과 실제 추론 검증

개인 head 비교 결과가 common이면 공통 체크포인트를 변환한다.
`personal-report.json`의 selected_model과 SHA256을 검사하므로 거절된 개인 후보를 잘못 변환하지 않는다.

## 실행

```powershell
.\.venv-ml\Scripts\python.exe -m ml.training.export_tflite --selection-report ml/models/personal-head-smoke-verified/personal-report.json --dataset ml/datasets/personal-head-smoke --output ml/models/tflite-verified-new --normalization baseline
```

출력은 비어 있는 새 디렉터리여야 한다. 기존 파일을 덮어쓰지 않는다.
normalization은 필수이며 period 기본값은 10초다. 과거 dataset에는 수집 설정이 기록되어 있지 않아 명시적으로 주석을 제공한다.
이번 합성 데이터는 dry-run의 ingest.yaml(baseline, 10초)을 사용했음을 코드로 확인했다.
실로그 데이터는 수집 당시 설정을 확인해야 하며 현재 설정만 보고 채우면 안 된다.

## 변환과 합격 기준

float32 입력 `[1,6,17]`, 출력 `[1,4]` 및 builtin 연산만 사용한다.
TensorFlow 2.20/Keras 3의 변수 읽기 연산이 남는 경로를 피하기 위해 변수 가중치를 상수로 고정하고 변환한다.
이 과정은 버전을 고정한 TensorFlow 내부 convert_variables_to_constants_v2 API를 사용한다.
공식 API: [TFLiteConverter](https://www.tensorflow.org/api_docs/python/tf/lite/TFLiteConverter),
[Interpreter](https://www.tensorflow.org/api_docs/python/tf/lite/Interpreter).

검증 정책은 `hub/deskmate_hub/config/tflite_export.yaml`에 둔다.

- 최대 절대 출력 오차 0.00001.
- top-1 일치율 100%.
- 모델 크기 최대 262,144 bytes.
- 확률 합 오차 0.001, 범위 반올림 허용 오차 0.000001.
- NaN/Infinity, shape/dtype 오류, 손상 또는 fingerprint 불일치는 거절한다.

모든 검사를 통과한 뒤에만 selected.tflite, selected.metadata.json, export-report.json을 출력한다.
metadata는 계약과 모델 SHA256을 포함한다. 모델과 리포트는 Git에서 제외한다.

## 실행 결과

2026-10-06, 선택된 공통 모델, 합성 테스트 창 1,045개, Windows CPU 단일 thread.

| 항목 | 결과 |
|---|---:|
| TFLite 크기 | 11,184 bytes |
| 최대 절대 오차 | 5.96 × 10⁻⁷ |
| 평균 절대 오차 | 2.34 × 10⁻⁸ |
| top-1 일치율 | 100% |
| PC invoke p50 / p95 | 약 0.0055 / 0.0083 ms |

최초 호출은 latency에서 제외했다. PC 측정이며 Pi 4 지연시간으로 해석하지 않는다.
변환 출력 일치는 실제 사용자 집중도 정확도를 증명하지 않는다.

## hub 연결 및 남은 제한

PC에서는 TensorFlow Lite Interpreter factory를 선택적으로 주입해 동일 hub backend를 검증한다.
기본 backend는 tflite_runtime이며 자동으로 TensorFlow를 불러오지 않는다.
실제 변환 모델을 LiveHub 관찰 모드에서 사용해 predicted 출력과 FSM payload·리포트 유지,
손상 모델·fingerprint 불일치·shape 오류 폴백을 확인했다.
부동소수점 반올림의 미세한 범위 오차는 YAML 허용값 안에서만 승인하고 [0,1]로 제한한다.

Pi 4 ATLAS의 native ABI·확장 설치·실기 latency는 미검증이다. 기본 비활성을 유지한다.
TensorFlow Interpreter 및 일부 변환 API deprecation warning이 남아 있다.
이 단계는 PC 검증 완료이며 보드 배포 완료가 아니다.

다음 SW 단계는 동일 프레임 replay에서 FSM/모델 disagreement·저신뢰도·센서 누락을 비교하는 것이다.
fusion과 C_focus 부호는 팀 결정 뒤 구현한다.
