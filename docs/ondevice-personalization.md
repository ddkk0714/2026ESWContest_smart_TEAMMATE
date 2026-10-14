# Pi 4 온디바이스 개인화 기반 (2026-10-08)

운영 중 PC로 데이터를 옮기는 과정 없이 개인 head를 학습·평가·적용하는 방향으로 변경했다. 현재 구현은 순수 Python으로 동작하는 실행 후보이며 실제 Pi 4 배포·성능 검증은 남았다. FSM 판단에 모델 확률을 결합하지 않는다.

## 실행 구조

개발 PC에서만 초기 공통 1D CNN을 학습한다. `ml.training.export_portable`은 고정 가중치·초기 공통 head를 JSON으로 내보내고 Keras 출력과 전 test 창의 수치 일치를 확인한다. 입력/출력은 기존 `[1,6,17]`/`[1,4]` 계약을 유지한다. embedding은 내부 16차원이고 MQTT로 전송하지 않는다.

Pi 4의 `PortableBackend`는 Conv1D(same, ReLU) 두 층 → mean pool → Dense(16, ReLU) → softmax head를 표준 라이브러리만으로 실행한다. NumPy·TensorFlow·tflite_runtime이 필요 없다. backbone은 변하지 않으며 68개 head 파라미터만 SGD로 업데이트한다. 기존 TFLite 모델을 직접 역전파하는 방식은 아니다. portable은 추가 실행 후보이고 최종 ATLAS 경로는 보드 실측 후 정한다.

## 데이터와 학습

- `privacy.enabled`, 승인 정책 동의, `ondevice.enabled`, portable 관찰 모델 준비가 모두 필요하다.
- 새 개인 head 파일과 원자적 저장 임시 파일(`head.json.tmp`)을 모두 `privacy.personal_model_files`에 명시적으로 등록한다. 전원 중단으로 남은 임시 파일도 동의 삭제 범위에 포함한다. 공통 portable bundle은 등록하지 않는다.
- 6 tick 창이 준비된 관찰 모델의 최신 embedding과 ESM corrected_state만 결합한다. feedback verdict accept/reject/timeout은 학습 표본이 아니다. 시간 범위와 최대 나이를 확인하며 같은 tick의 정정은 마지막 정정으로 대체한다.
- 임시 표본은 RAM에만 두고 최대 512개로 제한한다. raw 센서·키 내용·카메라·ToF 배열을 저장하지 않는다. 학습 job은 시작 때 제한된 표본 사본을 보유한다. 재시작하면 표본·job·평가 이력은 사라지지만 검증된 개인 head는 복원한다.
- 세션 START마다 그룹을 구분한다. 최근 2개 그룹은 validation/audit이고 나머지 최소 2개 그룹은 train이다. 모든 분할에서 각 클래스 최소 2개 표본이 필요하다. 이 값은 개발 기본값이며 실제 사용자 효과의 증거가 아니다.
- IDLE·END에서만 최대 8 SGD 업데이트/tick, 한 업데이트 이후 25ms 소프트 budget을 확인한다. 별도 thread는 쓰지 않고 작게 나누어 진행한다. 학습 완료 평가·파일 저장은 이 budget으로 선점하지 못하므로 보드에서 전체 tick 지연을 측정해야 한다.
- candidate가 공통 head와 기존 개인 head 양쪽보다 validation/audit macro F1이 엄격하게 개선되고 recall이 떨어지지 않아야 적용한다. 같은 감사 세션으로 후보를 재시도하지 않는다. 더 나은 개인화가 항상 생기는 것은 아니며 부족·편중·성능 악화 시 공통/기존 head를 유지한다.
- 완성 head는 fingerprint와 68개 가중치만 private JSON으로 임시 파일 → replace 저장한다. 저장 성공 이후 활성 head를 교체한다. 학습 표본·개인 평가 데이터는 파일에 넣지 않는다. 이전 head와 backbone이 다르거나 파일이 손상되면 공통 head로 시작한다.
- consent 철회·삭제는 후보·RAM 표본을 비우고 개인 head 사용을 끄며 등록 파일을 삭제한다. 기존 ESM·PC 자료 삭제 범위는 확대하지 않는다. 장기 보존·다중 사용자·백업 정책은 여전히 팀 결정 대상이다.

## 초기 공통 모델 내보내기 (개발 PC)

저장소 루트의 학습 가상환경에서 실행한다. output은 비어 있는 새 디렉터리여야 한다.

```powershell
$env:PYTHONPATH='hub;ml'
.\.venv-ml\Scripts\python.exe -m ml.training.export_portable --common-model ml/models/common-personal-reference/common.keras --dataset ml/datasets/common-cnn-smoke --output ml/models/portable-ondevice-new --normalization baseline
```

산출물은 `common.portable.json`, `common.metadata.json`, `portable-export-report.json`이며 Git에서 제외된다. 체크포인트와 dataset은 팀이 검증한 공통 모델 자료를 사용한다. 개발 PC latency는 보드 latency가 아니다.

## 시험 설정

실험 참여자의 동의·시험 정책 승인 후에만 활성화한다. 기본 파일들은 모두 비활성이다. 아래는 경로 관계를 보여주는 예시이며 서비스 계정·권한은 실기에서 정한다.

```yaml
# personalization.yaml
enabled: true
mode: observe
backend: portable_cnn
model_path: /data/share/deskmate/common/common.portable.json
metadata_path: /data/share/deskmate/common/common.metadata.json

# ondevice.yaml (나머지 기본 학습 정책도 유지)
enabled: true
head_path: /data/share/deskmate/personal/head.json

# privacy.yaml: 승인된 시험 정책과 consent_file을 별도 설정
data_root: /data/share/deskmate/personal
consent_file: /data/share/deskmate/personal/consent.json
personal_model_files:
  - /data/share/deskmate/personal/head.json
  - /data/share/deskmate/personal/head.json.tmp
```

baseline.persist.path도 같은 개인 data_root 안에 둔다. source YAML 변경은 IPK의 JSON 설정으로 패키징되므로 IPK를 다시 빌드한다. 파일 복사는 최초 공통 모델을 설치하는 개발/제조 과정이며 사용자 재학습 때는 필요 없다.

## 실기 수용 기준

1. 최신 hub IPK·Pi 5 앱 설치 후 live/native MQTT 모드로 기동한다. 보드 서비스 안에서 portable 공통 모델이 `warming_up` → `predicted`가 되는지 확인한다.
2. 동의 전 표본·개인 학습이 없고, 동의 후 최신 정정만 표본으로 모이는지 확인한다. 최소 4개의 독립 세션과 분할별 클래스 표본이 있어야 학습이 가능하다. 부족한 상태에서 약한 라벨을 만들어 채우지 않는다.
3. PC 관찰 도구를 종료해도 END/IDLE에서 training_step이 늘어나는지 확인한다. 학습 중 사용자 작업 재개 시 진행이 멈추는지 확인한다.
4. 개선 후보는 accepted, 기존보다 나쁘거나 동률인 후보는 rejected로 표시되는지 확인한다. common bundle은 불변이고 head 파일만 바뀌어야 한다. decision_source는 항상 fsm이다.
5. hub 재시작 후 개인 head 복원, head 손상·공통 bundle 교체 시 공통 폴백, 학습 중 동의 철회·삭제·전원 중단을 확인한다.
6. 전체 hub tick·공통 추론·학습·평가·저장의 지연시간, 메모리·발열을 측정한다. PC에서 빠르다는 이유로 보드 완료로 표시하지 않는다.

MQTT `data.sensor_summary.ondevice_learning`은 status, execution(local), decision_source(fsm), head_source, samples, sessions, training_step을 제공한다. Pi 5 개인화 화면에서도 상태·기록/세션 수·현재 비교 모델을 표시한다. 상태: disabled, unavailable, awaiting_consent, awaiting_portable_model, collecting, training, paused, accepted, rejected, storage_error, head_load_failed. 실제 판단·제어는 기존 FSM을 유지한다고 화면에 설명한다.

## 개발 검증 결과

최종 PR 준비 Python 회귀: 358 passed, 1 skipped(Windows symlink 권한), 1 xfailed, TensorFlow/Keras 경고 57개. 기본 비활성 경로와 기존 FSM·개입 시험을 포함한다. fingerprint 모듈도 실제 보드에서 확인해야 하며 모듈이 없어도 비활성 hub import/FSM은 유지하도록 지연 로딩한다.

2026-10-08 학습된 공통 checkpoint를 portable로 내보내 1,045개 합성 test 창에서 Keras와 비교했다. bundle 크기 40,477 bytes, 최대 확률 오차 약 7.70×10⁻⁷, top-1 일치 100%, 개발 PC p50/p95 약 1.03/1.62ms다. embedding도 Keras 중간 출력과 비교했다. 이 결과는 변환 수치 일치이며 실제 사용자 정확도나 Pi 4 성능이 아니다.

SGD 한 번의 gradient를 독립 NumPy 계산과 비교했고, 합성 embedding의 실제 학습·개선 후보 채택·저장·복원, 악화 후보 거절, 동일 audit 재사용 방지, 유휴 분할 진행·작업 중 일시정지, 동의 취소 중단, 손상·저장 실패·임시 파일 삭제, 모델 관찰 전후 FSM·리포트 불변을 확인했다. Linux Python 3.12.3에서 NumPy·TensorFlow·TFLite 및 ATLAS에서 없는 `_socket/_ssl/_random` import를 막고 실제 portable 추론·head 학습을 실행했다. 이것은 제한을 흉내 낸 호스트 검증이며 실제 ATLAS 실행을 대신하지 않는다.

재현 시험:

```powershell
$env:PYTHONPATH='hub;ml;collector'
.\.venv-ml\Scripts\python.exe -m pytest hub/tests/test_ondevice.py hub/tests/test_personalization.py hub/tests/test_privacy.py hub/tests/test_board_runtime.py ml/tests/test_export_portable.py -q
```

## 남은 작업

실제 ATLAS IPK 설치·권한·속도 및 앱 터치 검증, 승인된 표본 보존/사용자 구분 정책, 독립 실사용자 평가, 팀 결정 이후 FSM 결합이 남아 있다. [실기 검증 인계](ondevice-field-validation.md)에 PR 범위와 합성 시작 점검을 정리했다. 실제 보드 없이 온디바이스 개발의 실기 완료를 주장하지 않는다.
