# posture_sim — 자세판정 시뮬 (브랜치 `feat/posture-sim`)

사람의 하루 각본을 재생해 **가상 ToF 8×8 두 센서 + mmWave C1001 → 책상 재실 판정 → 자세 모델(Pi 4 경로) → 평가**만 돈다.
hub FSM·기기·환경은 없다 — 그건 전체 시뮬(`tools/life_sim`, 브랜치 `feat/life-sim`, 이 브랜치 위)이 붙인다. 결과는 전부 시뮬레이션.

## 실행

```bash
# hub/ 에서. tofsim(dataset/tof_sim_v5 사본)은 TOFSIM_ROOT, 모델은 PostureNet 체크포인트(.pt)
python -m tools.posture_sim run tools/posture_sim/scenarios/posture_tour.yaml --model <ckpt.pt> --tofsim <dir>
python -m tools.posture_sim run tools/posture_sim/scenarios/presence_leave_return.yaml --presence any   # 재실 규칙 비교
python -m tools.posture_sim run <각본> --synthetic --posture oracle      # tofsim·torch 없이(시험·점검)
python -m tools.posture_sim table runs/posture_sim/*/summary.json
pytest tests/test_posture_sim.py
```

## 구성

| 파일 | 역할 |
|---|---|
| `scenario.py`, `scenarios/*.yaml`, `config/sensors.yaml` | 각본(구간 목록) + 센서 설정(재실 규칙·mmWave·0x03 규칙 특징·모델 옵션). 각본의 `sensors:` 가 깊은 병합으로 덮는다 |
| `person.py` | 각본대로 움직이는 사람(tofsim 자세·끄덕임 헬퍼 재사용, `override()` 로 각본 자르기 — 전체 시뮬의 개입 반응용) |
| `scene.py`, `source.py` | tofsim `sample_session(placement="desk")` 으로 책상·사람·CAD04 두 센서 배치, 센서별 렌더. `where_now()` = desk · room · out |
| `mmwave.py` | C1001 대역(재실·체동·호흡·DrowsyDetector·거리, UART 0x20 필드). 자리비움 `where: room` 이면 2~4.5 m 로 계속 재실(11 m·100° 레이더) |
| `posture_model.py` | `GruPosture`(PostureNet 체크포인트, 센서별 은닉 상태, log-softmax 융합, 1 s 다수결), `RuleFeatures`(ESP32 0x03 규칙 대역), `OraclePosture`(시험) |
| `runner.py` | 본체: 센서별 격자 → UART 0x05 인코드 → 디코드 → TofGridAssembler → 모델(`update(sample, now, present)`), 재실 판정, 평가 |
| `synthetic.py` | tofsim 없는 합성 격자(시험용) |
| `ubai/` | UBAI 실행 키트: `push.sh`(PC → UBAI) · `setup.sh`(실행 폴더) · `submit.sh`(시드마다 작업 하나, 병렬) · `run.sbatch` |

## 책상 재실 규칙 (`config/sensors.yaml presence`, hub 계약과 같게)

`priority: mmwave` — mmWave 가 신선·유효하면 먼저: 부재 → 부재 / 재실 + `distance_cm ≤ desk_max_cm`(130) → 책상 재실 / 더 멀면
"방엔 있지만 책상 빔" → 부재 / 거리 없음 → ToF / mmWave 끊김 → ToF. `any` = 둘 중 하나(이전 hub). `gate_posture` 면 모델에 책상 재실을 넘긴다.
hub 쪽 구현은 노트북 `feat/mmwave-presence`(presence.source · gate_posture)가 맡는다 — 거리 게이트는 요청 중.

## 출력 (`runs/posture_sim/<name>[_pres-…][_sN]/`)

`frames.jsonl`(1 s: 정답 라벨·구간·where·모델 판정·재실 셋(ToF·mmWave·결정)·mmWave 거리·drowsy_state·0x03 플래그),
`summary.json/md`: 자세 정확도(초, 전이 제외)·라벨별 재현율/정밀도·혼동 상위·misplaced 오탐·0x03 drowsy, 책상 재실 정확도(ToF·mmWave·OR·결정),
자리비움 구간마다 부재 판정까지·복귀 뒤 재실까지·헛재실 초, 졸기·엎드림 중 놓침.

## UBAI

```bash
bash hub/tools/posture_sim/ubai/push.sh v7                       # PC, 레포 루트에서 — ~/tof-sim-v5-lifesim/repo_v7
ssh ubai 'bash ~/tof-sim-v5-lifesim/repo_v7/hub/tools/posture_sim/ubai/setup.sh'      # 처음 한 번(sim/·models/·pylib/)
ssh ubai 'cd ~/tof-sim-v5-lifesim && bash repo_v7/hub/tools/posture_sim/ubai/submit.sh v7 posture_sim "1 2 3" tools/posture_sim/scenarios/posture_tour.yaml'
```
결과는 `~/tof-sim-v5-lifesim/runs/posture_sim_v7/`. 계산 노드에는 EGL 이 없어 3D 영상은 PC 에서(전체 시뮬의 `video`).
속도: 시뮬 시간의 ~11 배(10 Hz, 렌더 ~74 %·모델 ~25 %) — 2 시간 각본 ≈ 11 분.

## 한계

- mmWave·0x03 규칙은 데이터시트·추정 기반 대역(실측 보정 전). C1001 의 실제 무인 판정 지연·거리 정확도는 설치 후 잰다.
- 시뮬은 10 Hz 로 돈다(실기 ESP32 0x05 는 5 Hz). 10-21 실험에서 5 Hz 유지 평가는 −0.006~−0.019 F1.
