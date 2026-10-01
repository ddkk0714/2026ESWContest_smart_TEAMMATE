# ml — 2단계 경량 분류기 학습 파이프라인

담당: 조명희

자가기록 ESM 라벨로 경량 분류기(CNN 2D 또는 MLP)를 **PC에서 학습**하고,
TFLite 로 변환해 Raspberry Pi 4 에 배포한다. 클라우드는 추론에 사용하지 않는다.

## 위치 설정

2단계 분류기는 **개념 실증(demonstration)** 이다. 완성도의 축은 1단계 규칙 FSM 이고,
분류기는 이를 보완 · 검증한다. 정확도가 목표치에 못 미쳐도 시스템은 동작해야 한다.

이렇게 잡는 이유: ESM 라벨을 2개월 내 충분히 모으기 어렵고,
집중 · 피로 같은 인지상태는 정답 자체의 노이즈가 크다.

## 제약

| 항목 | 목표 |
|---|---|
| 모델 크기 | 수십 ~ 수백 KB (참조: ST HPD 5세대, 신경망당 가중치 10,000개 미만 · Flash 29KB) |
| 추론 지연 | 판정 사이클 500ms 예산 안에 들어올 것 |
| 배포 형식 | TFLite, Pi 4 온디바이스 |

## 구조

```
ml/
├── datasets/       수집 데이터 (gitignore — 커밋 금지)
├── training/       전처리 · 학습 · 평가 스크립트
├── models/         변환된 .tflite (gitignore, 배포본만 릴리스로)
├── export_tflite.py
└── requirements.txt
```

## 데이터

| 출처 | 라이선스 · 취급 |
|---|---|
| 자가기록 ESM 라벨 | 팀원 수집, **비공개 — 저장소에 커밋 금지** |
| ST VL53L8CX Hand Posture Dataset | 출처 표기 |
| nQSI Dataset (nQ Medical) | 키스트로크 피로 연구 참조 |

`datasets/` 와 `models/` 는 `.gitignore` 로 막혀 있다.
데이터는 팀 구글 드라이브에 두고, 여기에는 다운로드 스크립트와 체크섬만 커밋한다.

## 학습 창·증강 파이프라인

`training/build_dataset.py` 는 합성 dry-run 또는 짝이 맞는 `frames-*.jsonl`·`state-*.jsonl` 을
읽어 6 tick × 17특징 창을 만든다. `--esm` 으로 정정 라벨을 덮을 수 있다. 세션 단위로
학습·평가를 나눈 뒤 **학습 창에만** 잡음·시간 변형·센서 결측·기준선 이동·민감도 배율을 적용한다.
실제 로그와 ESM 파일은 저장소에 넣지 않는다.

```sh
python -m pip install -r ml/requirements.txt
python ml/training/build_dataset.py --name synth-v1 --synthetic default,short,demo --seeds 1-20 --offsets 0,3,7 --augment 4
python ml/training/evidence.py ml/datasets/synth-v1 --out docs/ml-augmentation-evidence.md
python -m pytest ml -q
```

출력은 `ml/datasets/<name>/windows.npz` 와 `manifest.json` 이다. NPZ의 `meta` 는
학습 창 다음 평가 창 순서의 JSON 문자열 배열이며 세션 ID·시작 tick·라벨 출처·증강 종류·분할을 담는다.
증거 문서의 정확도는 합성 약한 라벨에 대한 sanity check이며 실사용 성능으로 해석하지 않는다.
호흡·경과 시간처럼 센서 측정값이 아닌 칸(`augment.EXEMPT`)에는 잡음·오프셋·배율을 넣지 않는다.
증거 문서에는 클래스 가중 소프트맥스(드문 rest 확인)와 **결측·개인차 내성**(평가 창에 결측·기준선 이동·민감도 차이를 입혀
원본 학습 vs 증강 학습 비교)이 함께 실린다. 공개 데이터셋 후보는 [`docs/dataset-survey.md`](../docs/dataset-survey.md).

## ESM 라벨링 체계

디스플레이 단말의 사용자 피드백(`accept` / `reject` / `correct`)이 그대로 라벨이 된다.
별도 라벨링 앱을 만들기보다 피드백 UI 를 라벨 수집 채널로 쓰는 편이
라벨 양 확보에 유리하다. 라벨 스키마는 이 문서에 확정 후 기록한다.
