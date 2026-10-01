# 5분 시연 시나리오 (W4)

> 기준일 2026-10-01. 계획: `docs/plan/next-development-plan.md` §4 W4.
> 합성 센서로 **시작 → 몰입 → 피로 → 개입(자동) → 회복 → 리포트**를 약 5분(315 s)에 재현한다.
> 실기 검증을 대신하지 않는다. 보드·센서가 없을 때의 리허설, 발표 영상의 백업 경로, 회귀 시험용이다.

## 1. 구성

| 무엇 | 파일 |
|---|---|
| 시나리오(센서 입력 시퀀스) | `tools/mqtt_scenario_sim.py` 의 `demo_phases()` — `--scenario demo` |
| 시연 임계값 프로파일 | `hub/deskmate_hub/config/fsm.demo.yaml` — 임계값·가중치는 운영과 같고 **타이머만 짧다** |
| 실시간 없이 확인(수 초) | `tools/demo_dryrun.py` |
| 실시간 리허설(브로커·hub·모의 플러그·시뮬레이터 한 번에) | `tools/rehearsal_local.py --scenario demo` |
| 동선 회귀 시험 | `tools/test_demo_scenario.py` (`python -m pytest tools -q`) |

시나리오·`fsm.demo.yaml`·`ingest.yaml`·`control.yaml` 중 하나라도 바꾸면 dry-run 으로 전이를 보고 시험을 돌린다.
시험이 깨지면 시연 동선이 바뀐 것이므로 아래 §3 타임라인도 같이 고친다.

## 2. 실행

```bash
# (1) 몇 초 만에 전이 확인 — 브로커 불필요
python tools/demo_dryrun.py                    # 표로 출력
python tools/demo_dryrun.py --offset 7         # hub tick 과 시나리오 시작이 7 s 어긋난 경우
python tools/demo_dryrun.py --frames-out demo-frames.jsonl   # 리플레이용 프레임 저장

# (2) 실시간 리허설 — PC 단독 (amqtt·paho 필요: tools/requirements.txt)
python tools/rehearsal_local.py --scenario demo --respond accept

# (3) 실시간 리허설 + Pi 5 화면 — 같은 망에서 앱 연결 탭에 <PC IP>:18832
python tools/rehearsal_local.py --scenario demo --host 0.0.0.0

# (4) 리플레이로 같은 전이 재현
cd hub && python -m deskmate_hub --replay logs/rehearsal/frames-<stamp>.jsonl --config deskmate_hub/config/fsm.demo.yaml --report
```

`rehearsal_local.py` 는 끝나면 전이 요약, 모의 플러그가 받은 명령, 리플레이 명령을 출력한다. 로그는 `hub/logs/rehearsal/`(gitignore).

## 3. 타임라인과 화면 (dry-run, tick 어긋남 0 기준)

hub 는 10 s 마다 판정하므로 실시간에서는 각 시각이 0~9 s 늦을 수 있다. 순서는 바뀌지 않는다(시험이 어긋남 0·3·7 s 를 확인).

| 시각 | 단계(시나리오) | FSM | 화면에서 보여 줄 것 | 말할 것 |
|---|---|---|---|---|
| 0:00 | absent | IDLE | 대기 위젯(시계·환경·"모든 처리는 이 기기 안에서") | 카메라·마이크 없이 책상 위 센서만 쓴다 |
| 0:10 | start_typing | START → (30 s 기준선) | 착석 감지, 기준선 측정 중 | 사람마다 다른 평소 상태를 먼저 잰다 |
| 0:50 | env_worse | FOCUS_PC | 몰입 화면. 환경 카드에 "방이 더워요" | 키 값은 안 보고 타이핑 리듬만 본다 |
| 1:40 | drowsy | FOCUS_MIXED → FATIGUE_SUSPECT(1:50) | 노란 경고 | mmWave 체동·졸음 신호로 피로 의심 |
| 2:50 | drowsy_stuffy | FATIGUE → CAUSE_ANALYSIS | CO₂ 상승·높음 플래그가 함께 뜸 | 피로 확정 후 원인을 따진다 |
| 3:10 | 〃 | **ACTION_POSTURE (auto)** | 자동 알림 "자세를 바꿀 때라고 알렸어요" · 이유 · 확신도 | 확신도 0.75 이상이라 묻지 않고 실행 |
| 3:20~3:40 | 〃 | MONITOR → ESCALATE → CAUSE_ANALYSIS | (알림 유지 30 s) | 효과가 없으면 다른 원인을 시도한다 |
| 3:50 | 〃 | **ACTION_ENV (auto)** | 자동 알림 "환경을 조정했어요" · "CO₂가 높아요 (… ppm) · CO₂가 빠르게 오르고 있어요 · 방이 더워요" · 되돌리기 | 환기팬 ON·조명 70% 명령이 나간다(모의 플러그 로그) |
| 4:00 | 〃 | MONITOR | | 실행 뒤 피로가 내려가는지 본다 |
| 4:10 | wake_fresh | RECOVERY → FOCUS(4:20) | 회복 화면 → 몰입 복귀 | 개입 효과를 확인하고 원래 작업으로 |
| 4:55 | leave | (이탈) | 리포트 탭: 집중 시간·피로 1회·개입 2회·회복 | 세션 기록은 로컬에만 남는다 |

자동 실행 알림의 **되돌리기**는 hub 에 `reject` 를 보낸다. hub 는 ACTION_ENV 를 벗어나며 제어 건을 닫으므로,
지금은 알림이 남아 있는 30 s 동안 눌러도 undo 명령이 나가지 않는다 — W3 의 되돌리기 창(`control.undo_window_sec`)이 들어오면 나간다.

## 4. 이 동선에 제안 카드가 없는 이유 (팀 확인 필요)

제안 카드(gate suggest)는 원인 분석 시점의 C_fatigue 가 **0.45~0.75** 일 때 뜬다. 그런데 원인 분석은 FATIGUE 확정 뒤에만 오고,
FATIGUE 확정 조건이 C_fatigue ≥ `fatigue_confirm`(0.70) 지속이다. 그래서 실제로 제안 카드가 뜰 수 있는 구간은 **0.70~0.75** 뿐이다.

- 키 입력이 없는 졸음 구간은 PC 컨텍스트에서 최대 약 0.70+, MIXED 컨텍스트에서 0.77~0.81 이 나와 자동(≥0.75)으로 간다.
- 시연 5분 안에는 컨텍스트가 PC→MIXED 로 내려가므로(키 입력 비율 창 15분) 제안 구간을 안정적으로 지나가지 않는다.
- 환경만으로는 원인 1순위가 되지 않는다(PC 가중치 environment 0.10). 그래서 이 동선은 "자세 알림이 효과 없음 → 환경 재시도" 로 환경 제어에 닿는다.

시연에서 제안 카드를 꼭 보여 주려면 셋 중 하나를 팀이 정한다.
1. `gate.conf_suggest`·`conf_auto` 를 `fatigue_confirm` 과 맞게 다시 잡는다(예: 자동 0.85). 운영 동작도 바뀌므로 실측과 함께 결정.
2. FATIGUE_SUSPECT 단계에서 가벼운 제안을 띄우는 경로를 FSM 명세에 추가한다(fsm-spec 변경).
3. 시연에서는 앱의 데모 소스(`DemoStateSource`)로 제안 카드만 따로 보여 준다(실제 경로가 아님을 밝힘).

## 5. 무대 전 체크

- [ ] `python -m pytest tools -q` 통과(동선 시험 포함)
- [ ] 리허설 1회: 전이 요약에 ACTION_POSTURE·ACTION_ENV·RECOVERY, 모의 플러그 명령 2건
- [ ] Pi 5 앱이 PC 브로커(18832)에 붙어 연결 배지 정상, 자동 알림 두 번·리포트 탭 확인
- [ ] 실센서 시연이면 이 시나리오는 백업 — 같은 화면 흐름을 영상으로 녹화해 둔다
