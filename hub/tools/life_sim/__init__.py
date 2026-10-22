"""생활 각본 폐루프 시뮬레이션 (life_sim, 2026-10-22).

사람의 하루를 각본(yaml)으로 재생해 가상 ToF 두 센서 → 자세·졸음 모델 → hub FSM(18상태) → 개입 → 주변기기 →
사람 반응까지 한 바퀴를 **실시간 없이** 돌린다. 모든 결과는 시뮬레이션이다(실측 증거가 아니다).

    python -m tools.life_sim run hub/tools/life_sim/scenarios/a1_wake_first.yaml --out runs/lifesim --model <ckpt.pt>

구성
    scenario.py       각본·반응·기기 설정 로딩(yaml, 깊은 병합)
    tofsim_bridge.py  dataset/tof_sim_v5 (UBAI 사본) 를 찾아 import
    scene.py          CAD04 두 센서 장면(MuJoCo / numpy 백엔드) 렌더
    person.py         각본대로 움직이는 사람 + "개입이 오면 각본이 바뀌는" 동적 전환
    posture_model.py  GRU(torch) 자세·졸음 모델(센서별 추론 → log-softmax 융합), 0x03 규칙 특징 대역, 시험용 오라클
    hub_bridge.py     LiveHub 를 UART 0x05/0x03 + MQTT env/keystroke 경로 그대로 먹이는 어댑터
    reaction.py       사람 반응 모델(개입 종류×페르소나별 깨어남·수락·무시·거절·되돌리기)
    devices.py        주변기기 mock(선풍기·공기청정기·무드등·스피커 TTS·화면 카드) + 실내 환경·날씨
    harness.py        폐루프 본체, JSONL·요약 출력
    plots.py          PNG 타임라인
"""
