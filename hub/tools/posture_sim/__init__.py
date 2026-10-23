"""자세판정 시뮬(posture_sim) — 각본 → 사람 → 두 ToF 센서 + mmWave C1001 → 책상 재실 → 자세 모델 → 평가.

전체 시뮬(tools/life_sim, 브랜치 feat/life-sim)은 이 패키지의 사람·장면·센서 대역을 그대로 쓰고 hub FSM·기기·환경을 붙인다.
실행: `python -m tools.posture_sim run <각본.yaml> --model <ckpt.pt>` (hub/ 에서). README.md 참고.
"""
