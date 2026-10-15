# 5분 시연 시나리오 (W4)

> 기준일 2026-10-01. 계획: `docs/plan/next-development-plan.md` §4 W4.
> 합성 센서로 **시작 → 몰입 → 피로 → 개입(자동 알림 → 제안 카드) → 회복 → 리포트**를 약 5분(315 s)에 재현한다.
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
python tools/demo_dryrun.py --respond reject   # 제안 카드 거절 / --respond timeout: 카드 만료(무응답)
python tools/demo_dryrun.py --undo-at 255      # 수락해 실행된 환경 제어를 15 s 뒤 '되돌리기'

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
| 3:50 | 〃 | **ACTION_ENV (suggest)** | 제안 카드 "환경을 잠시 조정해볼까요?" · 이유 "CO₂가 높아요 (… ppm) · CO₂가 빠르게 오르고 있어요 · 방이 더워요" · 남은 시간 → **적용할게요** | 두 번째 원인은 근거가 약하니 묻고 실행한다 |
| 4:00 | 〃 | MONITOR | 수락 즉시 환기팬 ON·조명 70%(모의 플러그 로그) | 실행 뒤 피로가 내려가는지 본다 |
| 4:10 | wake_fresh | RECOVERY → FOCUS(4:20) | 회복 화면 → 몰입 복귀 | 개입 효과를 확인하고 원래 작업으로 |
| 4:55 | leave | (이탈) | 리포트 탭: 집중 시간·피로 1회·개입 2회·회복 | 세션 기록은 로컬에만 남는다 |

제안 카드에서 거절하면 명령 없이, 카드가 만료되면(데모 30 s) 무응답 라벨을 남기고 넘어간다 — 두 경우 모두 이후 회복까지 간다.
실행된 제어는 hub 가 ACTION_ENV 를 벗어나며 닫지만, 실행 후 `control.undo_window_sec`(60 s) 안이면 `reject`(되돌리기)로
팬 OFF·조명 40% undo 명령이 나간다. 확인: `python tools/demo_dryrun.py --undo-at 255`.

## 4. 제안 카드가 나오는 자리 — 재시도 원인은 제안까지만 (10-01 결정)

게이트 입력은 원인 분석 시점의 C_fatigue 다. 원인 분석은 FATIGUE 확정(C_fatigue ≥ `fatigue_confirm` 0.70 지속) 뒤에만 오므로,
첫 원인은 거의 늘 0.70 이상이고 제안 구간(0.45~0.75)을 지나갈 일이 드물다(실측상 0.70~0.75 의 좁은 틈뿐).

그래서 포스터의 게이트 값(0.45/0.75)은 그대로 두고, **첫 원인이 효과가 없어 다른 원인을 재시도할 때는 자동 대신 제안까지만**
가도록 했다(`fsm.yaml` `gate.retry_max: suggest`, fsm-spec "신뢰도 게이트"). 재시도 원인은 처음 고른 원인보다 근거가 약하므로
사람이 확인하고 실행한다 — 명세의 "엇갈리면 보수적으로 suggest 로 낮춘다"와 같은 원칙이다. `retry_max: auto` 로 끄면 예전 동작.

- 이 동선: 자세(1순위, 0.78) 자동 알림 → 효과 없음 → 환경(재시도) 제안 카드 → 수락 → 팬·조명.
- 환경만으로는 원인 1순위가 되지 않는다(PC 가중치 environment 0.10). 환경 제어는 대개 이 재시도 경로로 닿는다.
- 제안 카드가 만료되면 앱이 `timeout` 을 보내고 hub 는 그 제안을 바로 만료한다(`suggest_timeout_sec` 까지 붙들지 않음).
- 남은 과제: 2단계 분류기 확신도가 들어오면 게이트 입력을 C_fatigue 단독에서 융합값으로 바꾼다(fsm-spec).

## 5. 무대 전 체크

- [ ] `python -m pytest tools -q` 통과(동선 시험 포함)
- [ ] 리허설 1회(`--respond accept`): 전이 요약에 ACTION_POSTURE·ACTION_ENV·RECOVERY, 모의 플러그 명령 2건
- [ ] Pi 5 앱이 PC 브로커(18832)에 붙어 연결 배지 정상, 자세 자동 알림 → 환경 제안 카드(30 s 안에 수락) → 리포트 탭 확인
- [ ] (선택) 수락 직후 상태 정정 시트·되돌리기 동작 확인
- [ ] 실센서 시연이면 이 시나리오는 백업 — 같은 화면 흐름을 영상으로 녹화해 둔다

### 화면 종료 시 만료 보완 (2026-10-06)

앱이 종료되어 timeout 메시지를 보내지 않아도 hub가 카드의 expires_in_s를 적용한다. 시연 환경 제안은 30초 후 만료되어 명령 없이 MONITOR로 넘어가며 무응답률에 반영된다. 운영 설정의 180초는 유지한다. 기한 전에 hub에 도착한 응답은 다음 tick에서 처리해도 유효하다. [개입 사이클 검증](intervention-cycle-check.md)에 상세 경로와 제한을 정리했다.
