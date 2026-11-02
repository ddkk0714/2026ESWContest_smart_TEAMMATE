DESKMATE 2단계 피로 모델(4클래스 1D CNN) — v9 life_sim 120일, 2026-10-24
- 파일: common.portable.json(보드 형식 deskmate-portable-cnn/1, 40,250 B) + common.metadata.json(계약 1.0, 6x17, normalization baseline)
- hub 설정: personalization.yaml enabled true · mode observe · backend portable_cnn · model_path/metadata_path 를 이 두 파일로.
  ingest.yaml normalization 이 baseline 이어야 계약 검사를 통과한다(linear 이면 unavailable).
- 관찰 전용: FSM 결정은 바꾸지 않는다. 시뮬 정답 기준 시험 정확도 0.78 / macro F1 0.80, 피로 정밀도 0.48 (실사용 정확도 아님).
- PC 확인: Keras 대비 최대 오차 1.3e-6, top-1 일치 1.0, 추론 p50 0.62 ms. Pi 4 확인 전(board_verified false).
- 자세한 결과: docs/fatigue-sim-check.md "2단계 피로 모델" 절(PC feat/fatigue-sim 로컬 커밋).
