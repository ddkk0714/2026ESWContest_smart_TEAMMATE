# 개인화 모델 계약 v1.0

원본은 `hub/deskmate_hub/personalization/contract.py`다. 학습 코드도 동일한 상수와 변환을 사용한다.

## 입출력

입력 float32 `[1,6,17]`, 시간은 오래된 tick부터 정렬한다. 기본 주기 10초이며 window와 실제 주기를 metadata로 검증한다.

특징 순서:

1. keystroke_phi, keystroke_delta, keystroke_available
2. posture_phi, posture_delta, posture_available
3. respiration_phi, respiration_delta, respiration_available
4. environment_phi, environment_delta, environment_available
5. elapsed_phi, elapsed_delta, elapsed_available
6. present, pc_ratio

미사용 신호는 phi/delta/available 모두 0이다. NaN/Infinity는 거절한다.
출력 float32 `[1,4]`, 순서는 `focus, fatigue, rest, idle`의 softmax 확률이다.
유한한 [0,1]과 합계 1을 검사한다. 오차와 실행 시간 제한은 personalization.yaml에 둔다.
수락/거절은 개입 피드백이며 인지 상태 정답이 아니다. 명시적인 ESM corrected_state만 정정에 사용한다.
자세/졸음 teacher 데이터는 별도 과제이며 이 출력에 직접 붙이지 않는다.

## 메타데이터와 로딩

`model_metadata(window, period, normalization)`으로 JSON을 생성한다.
필수 필드는 contract_version, features, classes, input_shape, output_shape, dtype, frame_period_sec, normalization이다.
features/classes 순서까지 일치해야 한다. 기본 모델 경로는 `ml/models/personalized.tflite`,
metadata 경로는 `ml/models/personalized.metadata.json`이다. 데이터와 모델은 커밋하지 않는다.

선택적 backend는 활성화 후 metadata를 검증한 다음 NumPy와 tflite_runtime을 로드한다.
tensor shape/dtype 검사 후 set_tensor → invoke → get_tensor를 사용한다.
API 근거: [TensorFlow Interpreter](https://www.tensorflow.org/api_docs/python/tf/lite/Interpreter),
[Google AI Edge Python 안내](https://developers.google.com/edge/litert/microcontrollers/python).
ATLAS native 확장 설치·ABI 호환성은 미검증이며 IPK에 이 패키지를 자동으로 넣지 않는다.

## 관찰과 폴백

기본 `enabled: false`, 지원 mode는 observe뿐이다. 활성화하려면 YAML과 모델 배치를 명시적으로 준비한다.
sensor_summary.personalization은 진단용이며 source·FSM 점수·게이트·제어·리포트를 변경하지 않는다.
모델 confidence는 행동 confidence로 사용하지 않는다.

창 부족은 warming_up, 정상 출력은 predicted, 모델/metadata/런타임/출력 오류는 unavailable이다.
시간 역행·중복·주기 차이 초과 또는 세션 START/END 진입에서 창을 비운다.
장애는 프로세스 재시작까지 unavailable로 유지한다. 실행시간 초과는 실행 종료 후 검사한다.
native invoke hang을 선점/강제 종료하지 못하므로 실기 latency 검증 전에는 활성화하지 않는다.

변환 모델 metadata의 model_sha256이 있으면 backend에서 fingerprint를 검증한다. PC 검사에는 Interpreter factory를 주입할 수 있으며 기본은 tflite_runtime이다. 확률 범위 반올림 허용값은 personalization.yaml에 두고 유효 출력만 [0,1]로 제한한다.

## 동의 관리 연결 (2026-10-07)

2026-10-08 온디바이스 학습 경로를 추가했다. `backend: portable_cnn`은 동일 입출력 계약을 유지하는 고정 공통 CNN을 JSON 가중치로 실행하고 내부 16차원 embedding에 대한 개인 head만 Pi 4에서 학습한다. 별도 ondevice.yaml·동의·등록 파일 조건이 필요하며, 기존 `backend: tflite` 경로도 유지한다. 두 경로 모두 observe 전용이고 실제 보드 검증은 남았다. [온디바이스 구현·제한](ondevice-personalization.md)을 참고한다.

privacy 관리 기능을 켜면 승인된 정책의 사용자 동의가 있어야 선택적 모델을 로딩한다. 동의 취소·삭제는 backend 참조와 입력 창을 초기화하고 FSM을 계속 사용한다. 파일 삭제는 등록된 개인 bundle만 대상으로 하며 공통 모델을 등록하지 않는다. 기본은 관리 기능·모델 추론 모두 꺼짐이다. [개인화 동의·삭제 흐름](personalization-privacy-flow.md)에 설정과 제한을 정리했다.
