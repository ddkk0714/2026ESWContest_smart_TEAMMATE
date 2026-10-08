# DESKMATE 다음 개발 계획 — 실기 없이 진행할 작업 (2026-10-01)

## 2026-10-06 추가 진행: 재부팅·장애 복구

자동 시작 unit·설치기와 UART 권한 복구, FSM 진행 감시, 디스플레이 재기동을 구현했다. PC 모의 검증과 실제 보드 검증을 구분하며 영구 unit 경로가 없는 ATLAS 이미지는 설치를 거부한다. 설치 절차와 남은 실기 기준은 [재부팅·장애 복구](../reboot-recovery.md)를 참고한다.

> 작성 2026-10-01 · 기준 main `7ab7ea0` (앱 연결 안정화 0~3단계 PR #30~#32 머지 완료)
> 마감: **통합 MVP 10-05** · 시험 평가 10-19 · 서류 제출 10-30 · 발표 11-06
> 근거: `docs/roadmap.md` §4 미완료 항목, 2026-09-22 멘토 피드백, 10-01 코드 확인 결과

## 1. 목표

실기(보드·센서) 확인이 막혀 있는 동안, **실기 없이 끝낼 수 있는 작업으로 통합 MVP의 빈칸과 시험 평가·서류 준비를 채운다.**
작업은 Claude(디스플레이·통합)와 Codex(hub·학습 파이썬)가 **파일이 겹치지 않게** 나눠 동시에 진행한다.

하드웨어가 필요한 항목(ToF 연결, 스마트 플러그, 실센서 경로 재현, 보드 자동 시작, 하우징·목업)은 이 계획 밖이다(§7).

## 2. 멘토 피드백(09-22) 반영

| 피드백 | 반영 |
|---|---|
| 온디바이스·클라우드 미사용 → **보안 강조** | 구조는 이미 전부 로컬. W7(서류)에 보안·프라이버시 절을 두고, W1 대기 화면에 "모든 처리는 이 기기 안에서" 표시 |
| 평소 **범용 위젯**(시계·날짜·일정), 필요할 때만 **제안 카드**로 개입 | W1 의 핵심 구조 |
| AI 효과를 **정량 측정** | W3 — ESM 기록과 평가 지표(수용률·회복 시간·정정 비율) 리포트 |
| 2단계 학습은 **증강 데이터 + 증거 자료** | W5 — 합성·리플레이 기반 증강 파이프라인과 증강 방법 문서 |
| 대회장 환경 차이 → **샌드박스 목업**, 열악한 환경, 전원 | 하드웨어 항목. §7 팀 결정으로 남김 |

## 3. 현재 상태 (10-01 코드 확인)

| 영역 | 확인 결과 |
|---|---|
| 디스플레이 개입 UI | 제안 카드 수락·거절만 있음. **자동 실행 알림·되돌리기·실행 이유·정정 입력 없음** |
| 디스플레이 화면 구조 | 상세 대시보드 중심. **대기(위젯)/집중 저자극/상세/리포트 전환 없음** |
| hub 환경 신호 | **CO₂ 절대값 하나만** 사용(`ingest/mapping.py`). CO₂ 센서가 빠지면 온습도·조도가 정상이어도 환경 신호 전체가 미가용 |
| hub 피드백 처리 | `accept`·`reject` 만 처리(`live.py`). 계약에 있는 `correct`·`timeout`·`corrected_state`·`response_ms` 는 무시 |
| ESM 라벨 | 세션 리포트에 휴식 수락·거절 2종만(`inference/report.py`). 스키마는 `data-spec.md` §11 초안 |
| 시연 시나리오 | `config/fsm.demo.yaml` 초안·`tools/rehearsal_local.py`·`tools/mqtt_scenario_sim.py` 있음. **5분 고정 시나리오 없음** |
| 2단계 학습 | `ml/` 비어 있음 |
| mmWave → FSM 연결 | **이미 구현됨**(`posture`·`respiration` 신호). 로드맵 체크만 남음 |

## 4. 작업 목록

### W1. 개입 UI + 범용 위젯 화면 — Claude · 2.5일 · 통합 MVP 핵심

평소에는 위젯 화면, 개입이 필요할 때만 카드가 뜨는 구조로 화면을 정리한다.

- **대기 화면(범용 위젯):** 시계·날짜, 환경 요약(CO₂·온습도·조도), 오늘 집중 시간 요약, "모든 처리는 이 기기 안에서" 표시
  - 일정 위젯은 1차 범위에서 뺀다(보드에 키보드가 없어 입력 수단이 없고, 클라우드 연동은 온디바이스 원칙과 충돌). §7 결정 항목
- **집중 저자극 화면:** 몰입 중에는 정보를 줄이고, 터치하면 상세(집중 시간·환경·수동 조작)
- **제안 카드(확신도 0.45~0.75):** 수락·거절 + **실행 이유 문구**(원인·근거를 사람 말로) + 만료 시 무응답(`timeout`) 전송
- **자동 실행 알림(0.75 이상):** "환기를 켰어요 · 이유" + **되돌리기** 버튼(→ `feedback/user` `reject` → hub 디스패처 undo)
- **정정 입력:** "지금 상태가 아니에요" → 4개 국면 중 선택(`verdict: correct`, `corrected_state`)
- **응답 시간:** 카드 표시부터 응답까지 `response_ms` 기록
- 파일: `display/atlas/ui_env_app/lib/` (`main.dart`, `dashboard_view.dart`, 새 `intervention_card.dart`·`ambient_home.dart` 등), 해당 테스트
- 완료 기준: 위젯 테스트로 카드 3종(제안·자동 알림·정정)과 화면 전환 고정, 1024×600 에서 넘침 없음, 전체 테스트 통과

### W2. 환경 특징 확장 — Codex · 1일

- CO₂ **절대값**(기존) + **세션 시작 대비 상승량** + **온습도 쾌적 범위 이탈** + **조도 부족**
- 피로 증거(`delta`) = max(CO₂ 절대, CO₂ 상승, 조도 부족) · 집중 저하 증거(`phi`) = max(온도 이탈, 습도 이탈)
- **센서 하나라도 유효하면 환경 신호 가용**(지금은 CO₂ 필수)
- 화면 "실행 이유"용으로 `sensor_summary.env_flags`(예: `co2_high`, `co2_rising`, `too_hot`, `too_dark`) 발행
- 임계값은 전부 `config/ingest.yaml` (실측 후 값만 교체)
- 파일: `hub/deskmate_hub/ingest/mapping.py`, `config/ingest.yaml`, 새 `hub/tests/test_environment_features.py`, `docs/mqtt-topics.md`·`docs/data-spec.md` 해당 절
- 완료 기준: 새 테스트 + hub 전체 테스트 통과. 프롬프트는 부록 A

### W3. ESM 기록 + 정량 평가 리포트 — Codex · 1.5일 · W1 계약에 의존(계약은 이미 문서에 있음)

- hub 가 `correct`·`timeout` 을 처리: 정정은 FSM 을 강제로 바꾸지 않고 **라벨로만 기록**(판정의 근거를 사람 응답으로 덮어쓰지 않음), 무응답은 수락·거절과 구분
- ESM 라벨 레코드: 질문 ID·종류·원 판정(상태·원인·확신도)·응답(accept/reject/correct/timeout)·정정 상태·응답 시간 → 로컬 JSONL(`logs/`, 커밋 금지)
- **평가 지표** (세션 리포트 + 오프라인 도구): 제안 수용률, 자동 실행 되돌림 비율, 정정 비율(오판정 근사), 개입 후 회복까지 걸린 시간, 무응답 비율
- `data-spec.md` §11 ESM 스키마 확정
- **되돌리기 창(10-01 W1 작업 중 발견):** hub 는 ACTION_ENV 를 벗어날 때 제어 에피소드를 닫는데(`live.py` → `control.close_episode()`), `auto_complete_actions: true` 면 ACTION 이 한 tick(10 s) 만에 끝난다. 화면의 자동 실행 알림은 30 s 동안 되돌리기를 받지만, 10 s 뒤의 `reject` 는 hub 에서 아무 일도 하지 않는다 → 자동 실행 뒤 `control.undo_window_sec`(예 60 s) 동안은 에피소드를 닫아도 `reject` 로 undo 할 수 있게 한다(설정은 `config/control.yaml`)
- 파일: `hub/deskmate_hub/live.py`(피드백 처리), `inference/report.py`, 새 `hub/deskmate_hub/esm.py`, 새 `tools/evaluate_sessions.py`, 테스트, `docs/data-spec.md` §11

### W4. 시연 시나리오 고정 — Claude · 1일 · W1·W2 이후

- 시작 → 몰입 → 피로 → 개입(제안·자동) → 회복 → 리포트를 **5분 안에** 재현하는 합성 시나리오(센서 입력 시퀀스) + 시연용 임계값 프로파일(`fsm.demo.yaml`)
- 보드 없이 PC 에서 hub + 앱(데모 브로커)으로 한 번에 돌리는 리허설 명령, 리플레이로 같은 전이 재현
- 산출물: 시나리오 파일, 리허설 명령, 시연 동선 메모(영상·발표용)

### W5. 2단계 학습용 증강 데이터 파이프라인 — Codex · 2일 · 조명희 님과 범위 확인 후

- 입력: 리플레이 로그·합성 시나리오(실데이터가 생기면 추가), 공개 데이터셋(조사 결과·멘토 공유분)
- 증강: 시간 늘이기·잡음·센서 결측·개인 기준선 이동 등으로 국면 라벨이 있는 학습 창 생성
- 산출물: `ml/` 파이프라인, **증강 방법과 데이터 규모를 적은 증거 문서**(멘토 요구), 1D CNN 학습·TFLite 변환은 범위 협의 후
- 공개 데이터셋 조사(카메라 없이 키스트로크·생체 신호로 집중·피로를 다룬 것)는 Claude 가 따로 정리

### W6. 시험 자동화 — Claude · 1.5일

- 세션 JSONL 기록 형식을 리플레이 입력과 맞춤(`--replay` 로 바로 재생)
- 결측·유실 시나리오 테스트: 센서 분리, 브로커 재시작, 디스플레이 종료(hub 계속 동작)
- 판정 주기 p95 측정 도구(PC 기준, Pi 4 실측은 실기 때)

### W7. 문서 — Claude · 0.5일 + 서류 기간

- 이번 계획 문서·앱 연결 안정화 계획 커밋, `docs/roadmap.md`·`CLAUDE.md` 갱신(0~3단계 완료, mmWave 연결 체크)
- 보고서용 **보안·프라이버시 절** 초안: 클라우드 미사용, 키 값·카메라 영상 미수집, MQTT 로컬망, 설정 파일 로컬 저장

## 5. 진행 순서

| 묶음 | Claude | Codex | 목표일 |
|---|---|---|---|
| 1 | **W1** 개입 UI + 위젯 화면 | **W2** 환경 특징 | 10-03 |
| 2 | **W4** 시연 시나리오 | **W3** ESM + 평가 지표 | 10-05 (통합 MVP) |
| 3 | **W6** 시험 자동화 + 데이터셋 조사 | **W5** 증강 파이프라인 | 10-09 |
| 수시 | **W7** 문서 | — | 묶음마다 |

- 묶음마다 PR 하나씩(필요하면 Claude·Codex 몫을 같은 PR 에 커밋 분리). 실기 체크리스트는 PR 본문에 누적해 두고 실기가 되면 한 번에 확인.

## 6. Codex 협업 규칙

1. **main 에서 작업하지 않는다.** 묶음별 브랜치를 `origin/main` 에서 만들고, 보고에 `git branch --show-current` 출력을 붙인다.
2. **프롬프트의 API·키 이름을 바꾸지 않는다.** 바꿀 이유가 있으면 "제안"으로 보고만.
3. **허용 파일만 수정.** Claude 와 동시에 손대는 파일은 없게 나눈다(이 계획 §4 의 파일 목록 기준).
4. **커밋·푸시 금지.** Claude 가 검토(명세 대조표)·통합·커밋·PR 을 맡는다.
5. 검증: hub 는 `cd hub && python -m pytest tests -q`, 앱은 Atlas Docker 이미지 `deskmate-atlas-dev:local` 안에서 `flutter analyze`·`flutter test`(이 PC 에는 flutter 가 없다. Docker Desktop 은 `D:\SW임베디드경진대회_LG\Toolchains\DockerDesktop\Docker Desktop.exe`). 보고에 결과 마지막 줄과 "명세와 달라진 점(없으면 없음)".

## 7. 결정·하드웨어 대기 (개발 범위 밖)

| 항목 | 상태 |
|---|---|
| I7 카메라 자세 판정 사용 여부 | 팀 결정 필요(`docs/roadmap.md` §3 I7) |
| ToF 연결 경로 | 09-19 마감 경과, 규칙상 Path B |
| 스마트 플러그 모델 | 미선정 — 통합 MVP 제어 1사이클의 게이트 |
| 대기 화면 일정 위젯 | 입력 수단(보드 키보드 없음)과 온디바이스 원칙 때문에 1차 제외. 대안: PC 에서 로컬 MQTT 로 오늘 일정을 보내는 방식 |
| 샌드박스 목업·전원(배터리·무선·멀티탭) | 멘토 권장. 하드웨어 담당 결정 |
| 실기 체크리스트 | PR #30~#32 본문. 보드 확보 시 한 번에 확인 |

## 8. 리스크

- **통합 MVP(10-05)까지 4일:** W1·W2 가 늦으면 W4(시연 시나리오)가 밀린다 → W1 은 카드 3종을 먼저, 화면 전환은 그다음.
- **실기 미검증 누적:** 앱·블루투스·연결 복구가 모두 실기 확인 전 → 보드 확보 즉시 PR 체크리스트를 우선 처리.
- **2단계 학습 범위:** 라벨이 없어 증강 의존 → "개념 실증" 범위로 고정하고 보고서 서술을 맞춘다(로드맵 I5).

## 부록 A. Codex 프롬프트 — 묶음 1 / W2 환경 특징 확장

Codex 에 그대로 붙여 넣는다. 다음 묶음의 프롬프트는 묶음 시작 때 이 부록에 덧붙인다.

````text
# 작업: DESKMATE W2 — hub 환경 특징 확장 (CO₂ 상승·온습도 쾌적 이탈·조도 부족)

## ⚠ 먼저 지킬 것
1. **main 에서 작업하지 않는다.** 메인 폴더에서 `git fetch origin && git switch -c feat/hub-env-features origin/main`.
   작업 전과 보고 직전에 `git branch --show-current` 가 `feat/hub-env-features` 인지 확인하고 보고에 붙인다.
2. **아래 설정 키·필드 이름을 그대로 쓴다.** 바꿀 이유가 있으면 바꾸지 말고 보고에 "제안"으로만 적는다.
3. **커밋·푸시 금지.** 변경은 작업 트리에 둔다. `docs/plan/*.md`(미커밋 계획 문서)는 건드리지 않는다.

## 배경
저장소: D:\SW임베디드경진대회_LG\2026ESWContest_smart_TEAMMATE-main (main = 7ab7ea0)
계획: docs/plan/next-development-plan.md §4 W2
지금 hub 의 환경 신호(`hub/deskmate_hub/ingest/mapping.py` build_frame 의 environment 부분)는 **CO₂ 절대값 하나만** 본다.
`co2_valid` 가 false 면 온습도·조도가 정상이어도 환경 신호 전체가 `available=False` 다. 로드맵 4-B §4 의
"환경 특징: CO₂ 절대 구간·시작 대비 누적 상승, 온습도 쾌적 범위 이탈, 조도 구간" 을 구현한다.
같은 시간에 Claude 는 디스플레이(`display/**`)만 고친다.

## 수정·생성할 파일 (이것만)
- 수정: hub/deskmate_hub/ingest/mapping.py
- 수정: hub/deskmate_hub/config/ingest.yaml
- 생성: hub/tests/test_environment_features.py
- 수정(문서): docs/mqtt-topics.md 의 `state/phase` `sensor_summary` 설명, docs/data-spec.md §6.3 마지막 문단("hub 는 co2_valid 인 co2_ppm 만 environment.delta 로 쓴다")
다른 파일 수정 금지 — 특히 inference/**, config/fsm.yaml, live.py, display/**.

## 규칙
환경 표본(`env.data`) 필드: `co2_ppm`·`temp_c`·`humidity_pct`·`lux` 와 각각의 `co2_valid`·`temp_valid`·`humidity_valid`·`lux_valid`.
**valid 가 true 이고 값이 null 이 아닌 측정만 쓴다.**

하위 증거(각각 [0,1]):
| 이름 | 계산 | 쓰는 측정 |
|---|---|---|
| co2_abs | 기존 그대로: `tracker.evidence("co2_ppm", co2, _ramp(co2, co2_ppm_low, co2_ppm_high))` | CO₂ |
| co2_rise | `_ramp(co2 - session_co2_start, co2_rise_low, co2_rise_high)` — session_co2_start = 세션 시작(`tracker.session_started` 가 정해진 뒤) **첫 유효 CO₂** 값. 세션이 끝나면(IDLE/END) 지운다. 세션 밖이면 0 | CO₂ |
| temp_dev | 쾌적 범위 [temp_comfort_min, temp_comfort_max] 안이면 0, 벗어나면 벗어난 °C / temp_margin 을 clip | 온도 |
| humidity_dev | [humidity_comfort_min, humidity_comfort_max] 밖으로 벗어난 %RH / humidity_margin 을 clip | 습도 |
| lux_dim | lux ≥ lux_dim_high 면 0, lux ≤ lux_dim_low 면 1, 사이는 선형(어두울수록 큼) | 조도 |

신호:
- `delta`(피로 증거) = max(co2_abs, co2_rise, lux_dim) — 쓸 수 있는 것만으로 max. 하나도 없으면 0.
- `phi`(집중 저하 증거) = max(temp_dev, humidity_dev) — 같은 방식.
- **유효 측정이 하나라도 있으면** `signals["environment"] = Signal(phi=..., delta=..., available=True)`, 하나도 없으면 `Signal(available=False)`.
- SessionTracker 에 `co2_session_start: float | None = None` 필드 추가. `observe_state` 에서 IDLE/END 로 갈 때 None 으로(기존 session_started 지우는 자리와 같이).

화면 "실행 이유" 용 플래그 — `sensor_summary()` 결과에 **`env_flags`: list[str]** 추가(환경 표본이 신선할 때만, 해당하는 것만, 순서 고정):
`co2_high`(co2_abs ≥ env_flag_threshold), `co2_rising`(co2_rise ≥ 임계), `too_hot`/`too_cold`(온도가 범위 위/아래이고 temp_dev ≥ 임계),
`too_humid`/`too_dry`(습도 위/아래, humidity_dev ≥ 임계), `too_dark`(lux_dim ≥ 임계). 해당 없으면 키를 넣지 않는다.
sensor_summary 는 tracker 를 받지 않으므로 co2_rising 계산에 필요한 세션 시작 CO₂ 는 **인자 추가 없이 처리할 방법이 없으면** `sensor_summary(view, now, cfg, tracker=None)` 처럼 **선택 인자**로 받고, None 이면 co2_rising 은 생략한다(기존 호출부 호환 — live.py 는 이번에 고치지 않는다. 고치지 않아도 나머지 플래그는 나온다).

ingest.yaml `environment:` 아래에 추가(기존 co2_ppm_low/high 유지, 값은 잠정 — 실측 후 교체한다는 주석):
```yaml
environment:
  co2_ppm_low: 800
  co2_ppm_high: 1500
  co2_rise_low: 200        # 세션 시작 대비 상승 ppm
  co2_rise_high: 600
  temp_comfort_min: 20.0   # °C
  temp_comfort_max: 26.0
  temp_margin: 4.0         # 범위 밖으로 이만큼 벗어나면 1.0
  humidity_comfort_min: 30.0
  humidity_comfort_max: 60.0
  humidity_margin: 15.0
  lux_dim_high: 300        # 이 이상이면 조도 부족 0 (책상 작업 권장 하한)
  lux_dim_low: 100         # 이 이하면 1.0
  env_flag_threshold: 0.5  # 하위 증거가 이 이상이면 env_flags 에 이유를 싣는다
```
코드에 임계값 숫자를 두지 않는다(모두 cfg 에서 읽기). 주석은 기존 파일처럼 한국어로 "왜"를 쓴다.

## 테스트 (hub/tests/test_environment_features.py)
기존 tests/test_ingest_mapping.py 의 헬퍼 방식(Sample·SensorCache·load_ingest_config)을 참고해 새 파일로:
- CO₂ 무효 + 온습도·조도 유효 → environment available, phi/delta 계산됨(지금은 미가용이던 경우)
- 모든 측정 무효 → available False
- CO₂ 900 은 co2_abs 작지만, 세션 시작 CO₂ 500 → 1000 이면 co2_rise 0.75, → 1100 이면 1.0 → delta 1.0 (10-01 정정: 처음 예시가 200·600 기준과 맞지 않았다)
- 세션 밖(session_started None)에서는 co2_rise 0, IDLE 로 가면 co2_session_start 초기화
- 온도 30°C → temp_dev 1.0 → phi 1.0, 23°C → 0 / 습도 20% → too_dry
- 조도 50 → lux_dim 1.0, 200 → 0.5, 400 → 0
- delta = max 규칙(co2_abs 0.2, lux_dim 0.8 → 0.8), phi = max 규칙
- env_flags: 해당 플래그만, 순서 고정, 표본이 오래되면(freshness 밖) 키 없음, tracker 없이 호출해도 co2_rising 만 빠지고 동작
- normalization: baseline 설정에서도 co2_abs 경로가 기존처럼 tracker.evidence 를 거침(기존 동작 유지)

## 검증
`cd hub && python -m pytest tests -q` — 기존 테스트 전부 + 새 테스트 통과. (실패하는 기존 테스트가 있으면 고치지 말고 보고)

## 끝나면 보고
- `git branch --show-current` 출력(반드시 feat/hub-env-features)
- 수정·생성 파일, 테스트 개수·결과 마지막 줄
- 명세와 달라진 점(없으면 "없음"). 커밋하지 말 것.
````

## 부록 B. Codex 프롬프트 — 묶음 2 / W3 ESM 기록 + 정량 평가

````text
# 작업: DESKMATE W3 — hub ESM 라벨 기록 + 정량 평가 지표 + 되돌리기 창

## ⚠ 먼저 지킬 것
1. 작업 브랜치: `git switch -c feat/hub-esm feat/display-intervention` 로 만들고 시작. 끝나면 `git branch --show-current` 결과를 보고에 적는다. main 에서 작업하지 않는다.
2. **커밋·푸시·브랜치 전환·stash·reset 금지.** 변경은 작업 트리에만 둔다(리뷰 후 Claude 가 커밋).
3. 기존 함수·클래스·MQTT 키 이름을 바꾸지 않는다. 새 키·필드는 추가만.
4. 임계값·시간 값은 코드에 박지 말고 `hub/deskmate_hub/config/*.yaml` 에서 읽는다.
5. FSM 판정(engine·scoring)은 건드리지 않는다. 정정(correct)은 **라벨로만 기록**하고 상태를 바꾸지 않는다.
6. 로그 파일은 `logs/` 아래(.gitignore 대상)만. 실제 라벨 파일을 커밋 대상에 만들지 않는다.

## 배경
앱(W1, 이미 feat/display-intervention 에 있음)이 `deskmate/feedback/user` 로 보내는 것:
`{"request_id", "verdict": "accept"|"reject"|"correct"|"timeout", "corrected_state"?: "FOCUS_PC"|"FATIGUE"|"REST"|"IDLE", "response_ms"?: int}`
- accept/reject: 제안 카드 응답. reject 는 자동 실행 알림의 "되돌리기"로도 온다(request_id 가 없거나 "atlas-display" 일 수 있음).
- correct: 언제든 "지금 상태가 아니에요" — 요청이 없을 때도 온다.
- timeout: 제안 카드 만료(앱이 expires_in_s 로 판단).
현재 hub(`live.py` tick_once)는 accept/reject 만 처리하고, `control/dispatcher.py` 는 진행 중 episode 에만 반응한다.
문제: hub 는 ACTION_ENV 를 벗어나면 episode 를 닫는데 FSM 이 다음 tick 에 MONITOR 로 가므로, 앱에서 "되돌리기"를 누를 즈음엔 episode 가 이미 history 로 가 있어 undo 가 안 된다.

## 수정·생성할 파일 (이것만)
- hub/deskmate_hub/esm.py (신규) — ESM 레코드 생성·JSONL 기록·지표 계산(순수 함수 위주)
- hub/deskmate_hub/live.py — feedback 처리 확장, ESM 기록 연결
- hub/deskmate_hub/control/dispatcher.py — 되돌리기 창
- hub/deskmate_hub/config/control.yaml — `undo_window_sec: 60` 추가
- hub/deskmate_hub/inference/report.py — 세션 리포트에 지표 추가(기존 키 유지)
- tools/evaluate_sessions.py (신규) — 오프라인 지표 집계 CLI
- hub/tests/test_esm.py, hub/tests/test_undo_window.py (신규), 필요하면 기존 테스트에 케이스 추가
- docs/data-spec.md §11 `esm_label` 항목 보강, docs/mqtt-topics.md feedback/user 절(verdict 4종·필드)

## 규칙
### 1) feedback 처리 (live.py)
- verdict 4종을 받는다. 그 밖의 값은 무시하고 로그만.
- accept/reject: 지금 동작 유지(request_id 검사·pending_request 해제·control.on_feedback).
- timeout: request_id 가 현재 pending 과 같을 때만 pending_request 를 해제하고 ESM 에 기록. control 에는 "응답 없음"으로 전달하되 **실행·취소하지 않는다**(suggest 대기 episode 는 dispatcher 의 기존 suggest_timeout_sec 규칙을 그대로 따른다).
- correct: pending 여부와 무관하게 ESM 에 기록만. FSM·control·pending 은 그대로.
  corrected_state 가 4종 밖이면 기록하지 않고 로그.
### 2) ESM 레코드 (esm.py)
`deskmate/esm/label` 같은 새 토픽은 만들지 않는다. JSONL 한 줄 = 한 레코드:
`label_id`(boot_id-seq), `ts`, `source`("display"), `request_id`(없으면 null), `kind`(요청 kind 또는 "correction"),
`verdict`, `predicted_state`(그 시점 FSM 상태), `cause`, `gate`, `c_fatigue`, `c_focus`, `confidence`,
`corrected_state`(correct 만), `response_ms`(없으면 null), `answer_code`(verdict 와 같거나 "correct:<STATE>"),
`target_window_start_ms`/`target_window_end_ms`(요청 발행 시각~응답 시각, correct 는 응답 직전 score_period 구간).
- 저장 경로: 설정 키가 있으면 그 값, 없으면 `logs/esm-<boot_id>.jsonl`. 디렉터리가 없으면 만든다. 쓰기 실패는 로그만 남기고 hub 가 죽지 않게.
- 레코드는 SessionRecorder 에도 넘겨 리포트 지표에 쓴다(기존 break_accept/break_reject 라벨은 유지).
### 3) 지표 (esm.py 순수 함수 + report.py)
레코드 목록(+ 제어 episode 목록)에서:
- `suggest_accept_rate` = accept / (accept+reject+timeout) — 제안 카드 기준
- `timeout_rate` = timeout / 제안 수
- `auto_undo_rate` = 되돌린 자동 실행 / 자동 실행 수(dispatcher episode outcome undone vs executed)
- `correction_rate` = correct 수 / 판정 시간(시간당) 과 `correction_count`
- `median_response_ms` (응답 시간 있는 것만)
- `recovery_time_s` 중앙값 = 개입(실행) 시각 → 같은 원인 피로 신호가 게이트 아래로 내려간 시각(report.py 의 기존 FatigueEpisode/Intervention 정보로 계산 가능한 만큼, 못 구하면 null)
분모 0 이면 null. 세션 리포트(`session/report` 데이터)에 `metrics` 객체로 추가. 기존 키·break_accept_rate 는 그대로 둔다.
### 4) 되돌리기 창 (dispatcher.py + control.yaml)
- Episode 에 `executed_ts: float | None` 추가(_dispatch 로 실행할 때 기록).
- on_feedback(reject): 진행 중 episode 가 없거나 해당 없으면, history 의 **가장 최근** episode 가
  executed·outcome=="executed"·`now - executed_ts <= undo_window_sec` 이면 그걸 _undo 한다. 창을 넘었으면 로그만.
- `undo_window_sec` 는 config/control.yaml 에서 읽고 없으면 60. 한 episode 는 한 번만 undo.
- suggest 대기 중 reject 는 기존처럼 skipped(rejected) 이고 history undo 를 건드리지 않는다.
### 5) tools/evaluate_sessions.py
`python tools/evaluate_sessions.py logs/esm-*.jsonl [--json]` → 파일별·전체 지표 표(사람용) 또는 JSON. hub 패키지의 esm 지표 함수를 재사용(sys.path 에 hub 추가, tools/ 의 기존 스크립트 방식을 따른다). 외부 의존 추가 금지.

## 테스트
- test_esm.py: 레코드 필드(accept/reject/timeout/correct 각각), correct 가 FSM 상태·pending 을 바꾸지 않음, 잘못된 verdict/corrected_state 무시, 기록 실패해도 예외 없음(tmp_path), 지표 계산(분모 0→null, 중앙값), live 경로에서 feedback→JSONL 한 줄 생성(기존 live 테스트의 가짜 소스 방식 재사용).
- test_undo_window.py: auto 실행 → close_episode → 30 s 뒤 reject → undo 명령 발행 / 90 s 뒤 reject → undo 없음 / 두 번 reject → undo 1회 / suggest 대기 reject 는 skipped.
- evaluate_sessions: 작은 JSONL 픽스처로 JSON 출력 키 확인.

## 검증
`cd hub && python -m pytest tests -q` (기존 132 passed 1 xfailed 유지 + 신규), `python -m pytest tools -q` 가 있다면 그것도.

## 끝나면 보고
브랜치명, 바꾼 파일 목록, 테스트 결과 숫자, 명세와 다르게 한 부분과 이유, 결정이 필요한 부분.
````

## 부록 C. Codex 프롬프트 — 묶음 3 / W5 2단계 학습용 증강 데이터 파이프라인

범위 결정(10-01, Claude): numpy 만 쓰는 "학습 창 생성 → 증강 → 증거 리포트" 까지. 1D CNN 학습·TFLite 변환은 조명희 님 몫으로 남긴다.

````text
# 작업: DESKMATE W5 — 2단계 학습용 데이터 파이프라인 (학습 창 생성·증강·증거 리포트)

## ⚠ 먼저 지킬 것
1. 작업 브랜치: `git switch -c feat/ml-augment feat/display-intervention` 로 만들고 시작. 끝나면 `git branch --show-current` 결과를 보고에 적는다. main 에서 작업하지 않는다.
2. **커밋·푸시·브랜치 전환·stash·reset 금지.** 변경은 작업 트리에만(리뷰 후 Claude 가 커밋).
3. hub/·tools/·display/ 는 **읽기만**. 기존 함수·키 이름을 바꾸지 않는다(필요하면 "제안"으로 보고).
4. 의존은 **numpy 만**(이미 설치됨). TensorFlow·scikit-learn·pandas 추가 금지.
5. 생성 데이터는 `ml/datasets/`(gitignore) 아래만. 실제 ESM 라벨·사람 로그를 커밋 대상에 만들지 않는다. 시험은 tmp_path 만 쓴다.

## 배경
2단계 분류기는 "개념 실증"이다(ml/README.md). 실사용 라벨이 아직 없어서 멘토(09-22)가 "증강 데이터로 학습하되 증강 방법과 근거를 보여 달라"고 했다.
재료:
- 합성 세션: `tools/demo_dryrun.py` 의 `run_dryrun(name, seed=, offset=, respond=, frame_log=)` — 시나리오 `default`·`short`·`demo`
  (`tools/mqtt_scenario_sim.py SCENARIOS`). frame_log 에 tick 마다 SensorFrame JSONL 이 쓰인다(리플레이 형식, `hub/deskmate_hub/replay.py` 문서 참조).
  같은 tick 의 상태는 `res["trace"]` 가 아니라 LiveHub 의 state 로그가 정확하다 → 아래 2) 참고.
- 실로그(있으면): `hub/logs/**/frames-*.jsonl` + 같은 stamp 의 `state-*.jsonl`(한 줄 = 한 tick, 순서 일치), ESM `esm-*.jsonl`(W3, data-spec §11).
- SensorFrame 신호 키: `keystroke, posture, respiration, environment, elapsed` 각 `{phi, delta, available}` + `present`, `pc_ratio`.

## 만들 파일 (이것만)
- ml/training/__init__.py
- ml/training/frames.py — 로그 읽기·특징 벡터·라벨
- ml/training/synth.py — run_dryrun 으로 합성 세션 생성(시나리오×시드×offset)
- ml/training/augment.py — 증강(순수 함수, 시드 고정)
- ml/training/build_dataset.py — CLI: 재료 → 창 → 증강 → `ml/datasets/<name>/windows.npz` + `manifest.json`
- ml/training/evidence.py — CLI: manifest·npz → 증거 리포트 Markdown
- ml/tests/test_frames.py, test_augment.py, test_build_dataset.py
- ml/requirements.txt (`numpy`), ml/README.md (파이프라인 절 추가 — 기존 내용 유지)
- docs/ml-augmentation-evidence.md — evidence.py 로 **합성 데이터만** 써서 생성한 결과(커밋용)

## 규칙
### 1) 특징 (frames.py)
tick 하나 = 길이 고정 벡터: 신호 5종 × (phi, delta, available) = 15 + present + pc_ratio = 17. 결측(available=false)은 phi=delta=0, available=0.
`FEATURES` 상수로 이름 목록을 둔다(리포트·시험에서 사용).
### 2) 라벨
4 클래스 `focus, fatigue, rest, idle`(앱 정정 선택지 FOCUS_PC·FATIGUE·REST·IDLE 와 대응). 기본은 state 로그의 `data.phase` 로 약한 라벨:
focus→focus, fatigue→fatigue, recovery·rest→rest, idle·start·end→idle(그 밖의 phase 는 표로 정의, 모르면 제외하고 카운트).
ESM 의 `verdict=correct` 레코드가 있으면 `target_window_start_ms~end_ms` 에 걸친 tick 의 라벨을 `corrected_state` 로 덮고 `label_source="esm"` 로 표시(나머지 "fsm").
합성 세션은 run_dryrun 에 frame_log 와 함께 LiveHub state 를 얻어야 한다 — run_dryrun 을 바꾸지 말고, synth.py 에서 LiveHub 의 `state_log` 로
쓸 StringIO 를 넘길 방법이 없으면 **frame 마다 `res["trace"]` 를 tick 시각 기준으로 앞으로 채워(forward-fill) 상태를 복원**한다(trace 는 상태가 바뀐 tick 만 있다).
phase 는 `hub/deskmate_hub/presentation.py` 의 `_PHASE_BY_STATE`(상태→phase)를 import 해서 쓴다(복사 금지, 이름은 그대로).
### 3) 창
길이 W tick(기본 6 = 60 s), 보폭 S(기본 1). 라벨 = 창 마지막 tick 의 라벨. 출력 X: (N, W, 17) float32, y: (N,) int, 그리고 meta(세션 id, 시작 tick, label_source, 증강 종류).
### 4) 증강 (augment.py) — 각 함수는 (창, rng) → 창, available 플래그 의미를 깨지 않는다
- `jitter`: phi·delta 에 가우시안 잡음(σ 기본 0.03) 후 [0,1] 클립. available=0 칸은 그대로 0.
- `time_stretch`: 비율 0.8~1.25 로 선형 보간 후 W 로 다시 맞춤(available 은 최근접).
- `sensor_dropout`: 신호 하나를 창 전체 결측으로(키스트로크 미입력·mmWave 끊김 모사).
- `baseline_shift`: 신호별 상수 오프셋(±0.1)으로 개인 기준선 차이 모사.
- `mix_scale`: delta 전체에 0.85~1.15 배(개인 민감도 차이).
증강은 **학습 분할에만** 적용한다. 분할은 세션 단위(같은 세션의 창이 학습·평가에 섞이지 않게), 기본 평가 비율 0.25, 시드 고정.
### 5) build_dataset.py CLI
`python ml/training/build_dataset.py --name synth-v1 [--synthetic default,short,demo] [--seeds 1-20] [--offsets 0,3,7] [--logs hub/logs] [--esm 'hub/logs/**/esm-*.jsonl'] [--window 6] [--augment 4]`
→ `ml/datasets/synth-v1/windows.npz`(X_train,y_train,X_test,y_test,meta) + `manifest.json`(재료 목록·SHA-256·세션 수, 클래스별 창 수(원본/증강), 증강 종류별 수와 파라미터, 시드, FEATURES, 생성 시각).
`--augment k` = 학습 창 하나당 증강본 k 개(종류는 무작위 조합).
### 6) evidence.py → Markdown
`python ml/training/evidence.py ml/datasets/synth-v1 --out docs/ml-augmentation-evidence.md`
- 데이터 규모 표(세션·창·클래스별, 원본 vs 증강, label_source 별)
- 증강 방법·파라미터 표
- 분포 비교: 특징별 평균·표준편차(원본 학습 vs 증강), 2표본 KS 통계량(numpy 로 직접 구현)
- 기준 분류기 sanity check(numpy 로 구현): 창 평균·표준편차 요약 특징 → 최근접 중심 + 소프트맥스 로지스틱 회귀(경사하강). (a) 원본만 학습 (b) 원본+증강 학습 → 같은 평가 세트의 정확도·클래스별 재현율·혼동행렬. 수치는 "합성 데이터 기준, 실사용 성능 아님"이라고 명시.
- 한계·다음 단계(실데이터 ESM 라벨 축적, 1D CNN·TFLite 는 조명희 님 범위) 문단.

## 시험 (ml/tests, `python -m pytest ml -q`)
- 특징 벡터 길이·순서·결측 처리, phase→라벨 대응, ESM 정정 덮어쓰기, forward-fill 복원
- 각 증강: 모양 유지·[0,1] 범위·available 보존·시드 재현성, dropout 이 정확히 한 신호를 끈다
- 세션 단위 분할 누수 없음, 증강이 평가 세트에 안 들어감
- build_dataset 을 작은 설정(synthetic demo, seeds 1-2)으로 tmp_path 에 돌려 npz 키·manifest 키·카운트 합 확인
- evidence 가 필수 절(규모·방법·분포·기준 분류기·한계)을 포함

## 검증
`python -m pytest ml -q`, `cd hub && python -m pytest tests -q`(기존 155 passed 1 xfailed 유지), `python -m pytest tools -q`.
docs/ml-augmentation-evidence.md 는 `--synthetic default,short,demo --seeds 1-20 --offsets 0,3,7 --augment 4` 로 생성.

## 끝나면 보고
브랜치명, 파일 목록, 시험 결과 마지막 줄, 데이터 규모 요약(창 수·클래스 분포), 기준 분류기 (a)/(b) 정확도, 명세와 다른 점과 이유.
````

## 2026-10-06 추가 진행: 개입 한 사이클 마무리

별도 로컬 개입 검증 도구를 통합하고 mock MQTT의 응답 5종·화면 무응답·제어 실패·결과 누락을 검증한다. 수락 시 명령 기한 갱신, hub의 timeout 기록, 수신 시각 기준 기한 처리, 종료된 에피소드의 되돌리기 타임아웃, 리포트의 forward·undo 결과 집계를 보완했다. 실센서·Pi 5 터치·실기 제어는 미검증이다. [절차와 제한](../intervention-cycle-check.md)을 참고한다.
