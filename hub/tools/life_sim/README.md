# life_sim — 생활 각본 폐루프 시뮬레이션 (2026-10-22)

사람의 하루(착석·몰입·CO₂ 상승·오후 졸음·늦은 밤·자리비움)를 각본으로 재생해
**가상 ToF 8×8 두 센서 → 자세·졸음 모델 → hub FSM(18상태) → (간이 개인화 정책) → 주변기기 → 사람 반응**이 닫힌 고리로 돈다.
실시간이 아니라 가상 시계로 돌고(10 Hz 프레임, 10 s hub tick), 결과는 전부 **시뮬레이션**이다.

> 실측 증거가 아니다. 실측 10-19 녹화에서는 모든 자세 모델의 drowsy 재현율이 0 이었다. 여기서 보이는 "졸음 → 깨우기" 는
> 시뮬 장면(MuJoCo + SMPL-X + l8 보정)에서 GRU 가 drowsy 를 판정했을 때 hub 가 어떻게 움직이는지를 보여 줄 뿐이다.

## 실행

```bash
# hub/ 에서. tofsim(dataset/tof_sim_v5 사본)은 TOFSIM_ROOT 로, 모델은 PostureNet 체크포인트(.pt)
python -m tools.life_sim run tools/life_sim/scenarios/a1_wake_first.yaml --out runs/lifesim --model <ckpt.pt> --tofsim <dir>
python -m tools.life_sim ab  tools/life_sim/scenarios/day_student.yaml --seeds 3 --out runs/lifesim --model <ckpt.pt>
python -m tools.life_sim run <각본> --synthetic --posture oracle        # tofsim·torch 없이 하네스만(시험·점검)
python -m tools.life_sim table runs/lifesim/*/summary.json
pytest tests/test_life_sim.py                                           # 합성 2 + numpy 백엔드 1(tofsim 있을 때)
```

UBAI: `~/tof-sim-v5-lifesim/` (sim = `~/tof-sim-v5-opt` 코드 사본 = cad04 + l8 패치, models/ = opt_l8v5_c256 best·cad04 best,
pylib/ = pyyaml·matplotlib·pytest, repo/ = 이 레포의 hub/·tools/). `sbatch --partition=gpu6 run_lifesim.sh run <각본>`,
`CMD=ab SEEDS=3 sbatch run_lifesim.sh <각본>`. 결과는 `runs/lifesim/<persona>_<name>[_A|_B][_sN]/`.

## 구성과 데이터 경로

| 파일 | 역할 |
|---|---|
| `scenario.py`, `scenarios/*.yaml` | 각본(구간 목록·환경·날씨·반응/기기/정책 덮어쓰기). 기본값은 `config/*.yaml`, 깊은 병합 |
| `scene.py` | tofsim `sample_session(placement="desk")` 으로 책상·사람·CAD04 기기 배치를 뽑고(분류기가 읽을 수 있는 배치만), 두 센서를 따로 렌더 |
| `person.py` | 각본대로 움직이는 사람. tofsim 헬퍼(`_target_pose`·끄덕임·`_lerp_pose`·`_away_frame`) 재사용. `override()` 로 개입 반응(깨어남·자세 복귀·휴식) 시 각본을 자른다 |
| `posture_model.py` | `GruPosture`: PostureNet 체크포인트, 센서별 은닉 상태, log-softmax 융합, 1 s 다수결. `RuleFeatures`: ESP32 0x03 규칙 특징 대역(재실·motion·끄덕임→drowsy 플래그). `OraclePosture`: 시험용 |
| `hub_bridge.py` | LiveHub 를 실기 경로로 먹인다: 0x05 격자 → `encode_tof_sensor_frames` → `ingest_frame`(TofGridAssembler, posture 훅=모델) → `cache.tof_grid/tof_mlp`; 0x03 → `encode_payload` → `cache.tof`; env/keystroke → `route_mqtt_message`; 피드백 → `cache.put_feedback`; 제어 → ControlDispatcher+MockPlug(명령을 밖으로도 넘김) |
| `reaction.py`, `config/reaction.yaml` | 사람 반응: 카드 수락/무응답/거절, 채널별 깨어남 확률(1−Π(1−p)), 자세 복귀, 되돌리기, 휴식 이탈. 페르소나별 덮어쓰기 |
| `devices.py`, `config/devices.yaml` | 기기 mock(선풍기 0~3·공기청정기·무드등 cool/warm·스피커 TTS·화면 카드·창문) + 실내 환경 1차 물리(CO₂·PM·온도·조도) + 날씨 규칙(바람≥4 m/s 환기 우선, 비·눈·바깥이 2 ℃ 더우면 환기 제외, 겨울 5분/그 외 15분) + hub 액션→채널 매핑 |
| `policy.py`, `config/policy_simple.yaml` | §B-2 간이 개인화(feat/personalize-policy 대역): 보상 2축, 채널 점수(간이 밴딧), 되돌리기마다 자동 문턱 +0.05·2회 뒤 자동→제안, 거절 재제안 금지, 쿨다운·세션 상한, 취침 권유 규칙, "왜 이 개입인지" 한 줄 |
| `harness.py` | 폐루프 본체 + JSONL·summary.md/json·PNG |
| `ab.py` | §B-4: 같은 하루를 계정 A(합성 30일 프로필) vs B(전역 기본값) 로, 시드 여러 개 평균 |

출력 파일: `frames.jsonl`(1 s), `ticks.jsonl`(10 s), `events.jsonl`(개입·정책·반응·피드백·명령·구간), `summary.md/json`, `timeline.png`,
`hub.log`, `esm.jsonl`. A/B 는 `<…>_AB/compare.md|json|png`.

## hub 코드에서 확인한 사실 (engine 미수정)

1. **졸음의 라우팅은 "자세성"이다.** ToF 자세 이름 `drowsy`(모델) 또는 0x03 `drowsy` 플래그/nod_rate → `posture` 신호 δ=1.0
   (ingest.yaml `posture_delta`) → CAUSE_ANALYSIS 의 dominant 가 posture 그룹 → **ACTION_POSTURE** → `posture_alert`.
   fsm.yaml 의 `drowsy` guard(→ environment, `wake_alert`)는 심박 `heart_slow` 가 필요해 `enabled: false` 다.
2. **깨우기 개입의 실체 = 화면 알림/카드뿐.** `ControlDispatcher._plan()` 은 `ACTION_ENV` 에서만 명령을 만든다(control.yaml:
   vent_fan set_power on, desk_lamp set_brightness 70). ACTION_POSTURE 의 자동(≥0.75)은 `posture_alert` 액션 문자열(화면 알림),
   제안(0.45~0.75)은 `posture_suggest` 카드다. 스피커 TTS·무드등 각성 조명은 hub 에 없다 → life_sim 의 `devices.yaml channels:`
   가 제안 매핑으로 붙인다(보고서에 그렇게 표기).
3. **졸음만으로는 FATIGUE 확정이 어렵다.** 키 입력이 멎으면 keystroke 가 미가용이라 posture 가중치가 커지지만, 비PC 가중치로도
   posture 0.40/(0.40+0.20+0.15) ≈ 0.53 이라 CO₂(세션 시작 대비 상승이면 baseline z 로 금방 1.0)·경과(90 분에 1.0)가 같이
   올라야 0.70 을 넘는다. 각본은 그래서 졸음 앞에 경과·CO₂ 상승을 둔다.
4. **환경 원인은 첫 라우팅이 되기 어렵고** 재시도(ESCALATE→CAUSE_ANALYSIS, `gate.retry_max: suggest` 라 제안까지만)로 나온다.
5. **MONITOR 는 1 tick 판정**(진입 대비 C_fatigue 부호)이라 깨우기 효과(수십 초 뒤 자세 복귀)를 보기 전에 ESCALATE 로 갈 수 있다.
   끝내 안 깨면 세 원인을 다 쓰고 REST(force_rest) ↔ RECOVERY ↔ ESCALATE 를 반복한다.
6. `ingest/mapping.py` 는 ToF posture 신호에 `reasons` 를 채우지 않는다 → `slouch_with_idle` guard(slouch+typing_idle)도 ToF 경로에선
   절대 성립하지 않는다.
7. 정책(자동 문턱 올리기·채널 선택)은 지금 hub 밖에서만 가능하다. hub 는 자기 게이트대로 이미 실행/카드를 냈고 life_sim 이 그
   전달만 바꾼다(env 자동 명령은 카드 수락까지 보류). 제품에서 같은 효과를 내려면 `engine._action_gate()` 앞에 정책 훅이 필요하다.

## 한계

- 반응 확률·기기 효과 계수·환경 물리는 전부 가정값(yaml). 페르소나 프로필 A 도 합성이다.
- 0x03 규칙 특징은 펌웨어 코드의 이식이 아니라 같은 뜻의 단순 규칙(머리 띠 거리·행 무게중심 이탈 → 끄덕임).
- mmWave 는 넣지 않았다(재실·자세는 ToF 만). 카메라·마이크·얼굴 신호 없음.
- GRU 는 ≤900 s 세션으로 학습됐다 → 장기 상태 표류를 막으려고 `posture_model.reset_every_s: 900`(가정)으로 은닉 상태를 비운다.
