# 동일 프레임 FSM/모델 비교

PC에서 전체 SensorFrame 세션을 FSM과 선택 모델에 함께 replay한다.
tick마다 FSM 상태·게이트·actions·cause와 모델 예측·confidence·신호 미가용 목록을 기록한다.
모델 결과는 FSM에 넣지 않는다. 별도 기준 엔진과 tick 출력/세션 리포트가 같은지도 확인한다.

## 실행 명령

검증 bundle 생성은 [TFLite 변환](tflite-export-validation.md)을 따른다.
합성 replay 입력은 다음과 같이 만들 수 있다.

```powershell
New-Item -ItemType Directory -Force logs/model-comparison-inputs
.\.venv-ml\Scripts\python.exe tools/demo_dryrun.py --scenario default --seed 201 --respond none --frames-out logs/model-comparison-inputs/default.jsonl
.\.venv-ml\Scripts\python.exe tools/demo_dryrun.py --scenario short --seed 201 --respond none --frames-out logs/model-comparison-inputs/short.jsonl
.\.venv-ml\Scripts\python.exe tools/demo_dryrun.py --scenario demo --seed 201 --respond none --frames-out logs/model-comparison-inputs/demo.jsonl
.\.venv-ml\Scripts\python.exe -m ml.training.compare_replay --frames logs/model-comparison-inputs/default.jsonl logs/model-comparison-inputs/short.jsonl logs/model-comparison-inputs/demo.jsonl --bundle ml/models/tflite-verified --output logs/model-comparison-new --fsm-config hub/deskmate_hub/config/fsm.demo.yaml --runtime tensorflow
```

output은 새 디렉터리여야 한다. 입력 파일마다 FSM·추론 창·리포트를 새로 시작한다.
실로그는 수집 당시 FSM/ingest 설정을 명시해야 한다. 이미 FSM 입력으로 정규화된 프레임을 사용하며 원본 센서를 재수집하지 않는다.
기본 runtime은 tflite_runtime이다. PC TensorFlow Interpreter는 --runtime tensorflow로 명시한다.
--no-ablations를 사용하면 정상 입력만 비교한다.

## 비교 기준과 산출물

분석 정책은 `hub/deskmate_hub/config/model_comparison.yaml`에 둔다.
confidence < 0.75를 저신뢰도 진단으로 집계한다. 이 값은 행동 게이트나 승인 기준이 아니다.
불일치/저신뢰도 구간은 연속 tick이고 간격이 12초 이내인 경우만 연결한다.

- comparison.json: 세션/조건별 상태 개수, 비교 가능 tick 수, 일치율, FSM→모델 행렬, 연속 구간, 센서 미가용 패턴 및 입력/config/model fingerprint.
- trace.jsonl: 센서 원본 없이 tick별 비교 진단. 모델 warming_up/unavailable도 기록한다.
- comparison.md: 조건별 요약 표.

warming_up/unavailable은 일치율 분모에서 제외한다. 비교할 예측이 없으면 일치율은 null/N/A다.
모델 4개 클래스와 FSM phase 대응은 기존 학습 라벨 매핑을 사용한다.
start/end→idle, recovery→rest, ACTION/MONITOR 등→fatigue이므로 불일치가 곧 인지 판정 오류는 아니다.

## 센서 누락 비교

정상 입력과 keystroke/posture/respiration/environment/elapsed의 지속 누락 조건 5개를 비교한다.
각 조건은 해당 특징의 phi/delta/available을 모두 0으로 만든다. 실제 기기 분리는 수행하지 않는다.
각 조건 안에서 모델 관찰 유무는 FSM을 바꾸지 않는다.
정상 조건 대비 FSM 변화는 입력 누락에 대한 FSM 반응이고, 모델 관찰이 FSM을 바꾸는 현상과 구분한다.
원본 입력 파일은 수정하지 않는다. FSM actions는 비교하지만 실제 제어 명령을 보내지는 않는다.

## 실제 실행 결과

2026-10-06, common 선택 모델, 학습에 쓰지 않은 합성 seed 201, fsm.demo.yaml.
전체 271 tick 중 정상 조건의 비교 가능한 예측은 250개다.

| 시나리오 | tick | 비교 가능 | 불일치 | 일치율 | 저신뢰도 |
|---|---:|---:|---:|---:|---:|
| default | 201 | 193 | 6 | 96.89% | 11 |
| short | 38 | 31 | 10 | 67.74% | 11 |
| demo | 32 | 26 | 0 | 100.00% | 4 |

기본 시나리오에서 keystroke 지속 누락 시 일치율은 66.84%, environment 누락 시 79.79%였다.
short에서 elapsed 누락 시 32.26%, demo에서 posture 누락 시 57.69%였다.
원래 없던 신호도 누락 조건에 포함되며 일부 조건에서는 FSM의 전이 경로도 바뀐다.
숫자는 합성/FSM 일치율이고 실제 사용자 정확도·센서 중요도의 인과적 증거가 아니다.

18개 replay 조건 모두 모델 관찰이 FSM tick 출력과 세션 리포트를 바꾸지 않았다.
다른 시나리오에서 불일치가 남아 있으므로 자동 fusion을 적용하지 않는다.
실기 제어·수락/거절/되돌리기 검증은 별도 개입 사이클 작업으로 유지한다.

## 다음 작업과 제한

실센서 접근 전 SW 개인화 계약·학습·개인 head·변환·관찰 비교 기반을 마련했다.
다음은 실제 사용자 ESM 정정과 센서 replay로 불일치 원인을 확인하고 fusion 규칙을 팀에서 결정하는 단계다.
C_focus 부호·fusion·동의/저장 정책을 임의로 변경하지 않는다.
Pi 4 native ABI와 latency, 실센서/물리 제어는 아직 미검증이다.
