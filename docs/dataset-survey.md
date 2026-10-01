# 공개 데이터셋 조사 — 카메라 없이 집중·피로를 다룬 데이터 (W6)

> 기준일 2026-10-01. 계획: `docs/plan/next-development-plan.md` §4 W5·W6. 멘토 피드백(09-22): "증강 데이터로 학습하되 근거를 보여 달라, 데이터셋을 공유하라".
> 목적: 2단계 분류기(개념 실증)와 W5 증강 파이프라인에 쓸 **외부 근거**를 고른다. 내려받은 원본은 `ml/datasets/`(gitignore)에만 두고, 저장소에는 출처·라이선스·체크섬·변환 스크립트만 둔다.

## 1. 우리 신호와의 대응

| DESKMATE 신호 | 공개 데이터에서 찾는 것 | 쓰임 |
|---|---|---|
| 키스트로크 타이밍(dwell·flight·idle_ratio·correction_rate, 키 값 미수집) | 키 누름·뗌 시각 + 피로/스트레스 라벨 | 특징 분포·개인 차이 범위 → 증강 파라미터(`baseline_shift`·`mix_scale`)의 근거 |
| 환경(CO₂·온습도·조도) | 사무실 시계열 + 재실 | 합성 시나리오의 CO₂ 상승 속도·조도 범위 현실성, 환경 특징 임계값 근거 |
| mmWave 재실·체동·졸음(C1001 내장 판정) | 레이더 졸음/생체 신호 | 호흡 유효성(보조 신호) 검증. 졸음 라벨 데이터는 공개분이 거의 없음 |
| 사람 라벨(수락·거절·정정) | 주관 피로·노력·스트레스 평정 | 라벨 잡음 크기 추정, 클래스 비율 |

## 2. 후보

| 데이터셋 | 내용 | 규모 | 라이선스 | 판단 |
|---|---|---|---|---|
| [neuroQWERTY MIT-CSXPD](https://physionet.org/content/nqmitcsxpd/1.0.0/) (PhysioNet) | 키·hold·press/release 시각. 파킨슨 연구지만 **건강 대조군 43명** 포함 | 85명, 7.3 MB | ODC-By 1.0 | **채택(1순위)** — 내려받기 쉽고 라이선스 명확. 대조군 타이밍으로 dwell·flight 분포·개인 차이 범위 산출. 키 열은 읽자마자 버린다 |
| [Aalto 136M Keystrokes](https://userinterfaces.aalto.fi/136Mkeystrokes/) | 온라인 타자 시험(문장 15개 전사) 키 타이밍 | 16.8만 명, 1.36억 키 | 연구·비상업 + 출처 표기 | **채택(분포 근거)** — 모집단 수준 타이핑 속도·오류율 분포. 피로 라벨은 없음 |
| neuroQWERTY nQSI / nQCS (nQ Medical) | 수면 관성(피로) 통제 실험 14명 / 실사용 크라우드 251명, 피로 점수 ([JMIR 2024](https://pmc.ncbi.nlm.nih.gov/articles/PMC11041424/)) | 14명 / 251명 | **공개 여부 불명** | **문의** — 키스트로크 피로 라벨이 있는 거의 유일한 자료. ml/README 가 이미 참조. 저자에게 연구용 제공 요청 |
| [UCI Occupancy Detection](https://archive.ics.uci.edu/dataset/357/occupancy+detection) (Candanedo 2016) | 사무실 1실 온도·습도·조도·CO₂·재실, 1분 간격 | 20,560행 | CC BY 4.0 | **채택(환경)** — 재실 시 CO₂ 상승 기울기·조도 범위로 합성 시나리오(`demo_phases`)와 `ingest.yaml` 환경 임계값의 근거 |
| [SWELL-KW](http://cs.ru.nl/~skoldijk/SWELL-KW/Dataset.html) | 지식노동 25명: 컴퓨터 로깅(키·마우스·앱), 자세(Kinect), 심박·피부전도, 작업부하·정신적 노력·스트레스 평정 | 25명 × 약 3시간 | 연구 공개(조건 확인 필요) | **부분 채택** — 우리 상황(책상 지식노동)과 가장 비슷. 컴퓨터 로깅 특징 + 주관 평정만 쓰고 **얼굴 영상 파생 특징은 쓰지 않는다**(카메라 미사용 원칙) |
| [Kaggle: Stress Detection by Keystroke, App & Mouse](https://www.kaggle.com/datasets/chaminduweerasinghe/stress-detection-by-keystrokeapp-mouse-changes) | 키·마우스·앱 사용 + 5~30분 간격 피로 값(검색 요약 기준) | 271 MB | CC BY-SA 4.0(표기 기준) | **검토** — 피로 자기평정이 있다면 약한 라벨로 유용. 내용·키 값 포함 여부를 내려받아 확인 후 결정(로그인 필요) |
| [mmWave 생체신호 데이터셋](https://www.nature.com/articles/s41597-026-07172-9) (Scientific Data 2026) | 연령 균형 피험자 레이더 + 기준 생체신호 | — | Zenodo 공개(세부 확인 필요) | **참고** — 호흡 유효성 판단(보조 신호)의 검증용. 레이더 기종이 C1001 과 달라 원신호 직접 사용은 어려움 |
| mmWave 졸음 | FMCW 졸음 연구들([IEEE 2022](https://ieeexplore.ieee.org/document/9813466/))은 대부분 자체 수집 | — | — | **없음** — C1001 내장 졸음 판정을 쓰고, 팀 실측(ESM)으로 보완 |

키스트로크 피로 연구의 배경: [Evaluating keystroke dynamics as a biomarker for mental fatigue detection](https://www.researchgate.net/publication/360407429_Evaluating_keystroke_dynamics_as_a_biomarker_for_mental_fatigue_detection) — nQSI 로 학습하고 nQCS 로 실사용 검증.

## 3. 쓰는 방법 (W5 파이프라인과의 접점)

1. **분포 근거(바로 가능)**: MIT-CSXPD 대조군·Aalto 에서 flight·dwell 의 개인 간 평균·분산 → `baseline_shift`·`mix_scale` 범위를 "공개 데이터 기준 개인 차이 ±x%" 로 적는다. UCI Occupancy 에서 재실 중 CO₂ 상승 속도(ppm/min) 분포 → 합성 시나리오 램프가 현실 범위 안인지 확인.
2. **약한 라벨(라이선스 확인 후)**: SWELL-KW 정신적 노력·스트레스 평정, Kaggle 피로 값 → 우리 특징 형식(60 s 창 1 Hz 키스트로크 요약)으로 변환해 "외부 데이터로 학습 → 합성 평가" 교차 확인.
3. **피로 라벨(제공 시)**: nQSI·nQCS — 키스트로크 피로 분류의 직접 근거.
4. 모든 경우 **키 값 열은 변환 첫 단계에서 버리고**, 우리 collector 와 같은 특징(dwell·flight 통계, idle_ratio, correction_rate)만 남긴다. 원본은 커밋하지 않고 `ml/datasets/README` 에 내려받기 명령·SHA-256 만 둔다.

## 4. 다음 할 일

- [ ] MIT-CSXPD·UCI Occupancy 내려받기 스크립트 + 체크섬(`ml/datasets/README`) — W5 이후 조명희 님과
- [ ] SWELL-KW·Kaggle 라이선스·내용 확인
- [ ] nQ Medical(nQSI/nQCS) 연구용 제공 문의 메일
- [ ] 보고서 "데이터" 절: 자체 합성 + 공개 분포 근거 + 실사용 ESM(opt-in) 세 갈래로 서술

> 표의 규모·라이선스 중 "확인 필요" 표시가 없는 항목은 공식 페이지 기준이다. Kaggle·mmWave 생체신호는 검색 요약과 서지 정보만 확인했다(본문 접근 제한).
