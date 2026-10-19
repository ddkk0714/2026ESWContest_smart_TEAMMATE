# 조합 조건(guard) 사양 — 다신호 AND 규칙

담당: 박소연 · 상태: **설계 제안 (팀 승인 대기, E-1)**
관련: [`fsm-spec.md`](fsm-spec.md) · [`data-spec.md`](data-spec.md) §8.2 · `hub/deskmate_hub/config/fsm.yaml`

## 1. 왜 필요한가

현재 판정은 `scoring.py` 의 가중합뿐이다.

```
C_fatigue = Σ (wᵢ · δᵢ)
```

가중합은 각 신호를 **독립적으로** 더한다. 그래서 다음 두 상황이 같은 `C_fatigue` 로 도착한다.

| 상황 | 실제 의미 |
|---|---|
| 심박 느림 + 자세 굽음 + 고개 떨굼이 **동시에** | 졸고 있다 |
| 자세만 크게 굽음 | 그냥 자세가 나쁘다 |

앞은 깨워야 하고 뒤는 자세 교정이면 된다. 개입이 다른데 점수가 같으니 가를 수가 없다.

`fsm-spec.md` 의 자세 해석 분기표도 같은 형태다.

| 컨텍스트 | 관찰 | 해석 |
|---|---|---|
| PC | 숙임 + 키입력↓ | 피로↑ |
| PC | 숙임 + 키입력 정상 | 중립 |

이 표는 **문서에만 있고 코드에는 없다.** 가중합으로는 "숙임 AND 키입력↓" 을 표현할 방법이 없기 때문이다.

## 2. 설계 원칙

1. **상태를 늘리지 않는다.** 18상태 FSM은 그대로 둔다. `DROWSY` 같은 새 상태 추가 여부는 별도 미결정(E-4)이므로 여기서 전제하지 않는다.
2. **점수를 대체하지 않는다.** guard 는 가중합과 **병렬**로 평가되는 별도 층이다. guard 가 하나도 없어도 엔진은 지금과 동일하게 동작한다.
3. **코드에 규칙을 넣지 않는다.** 임계값·가중치와 같은 취급 — 전부 `config/fsm.yaml` 에 선언한다.
4. **신호 키를 쪼개지 않는다.** `environment` 를 온도/CO₂/조도로 분리하면 가중치·라우팅·스키마가 전부 바뀐다. 대신 `data-spec.md` §8.2 에 이미 정의된 `reasons` 를 근거로 쓴다 — 계약 변경이 가장 적은 경로다.
5. **미가용 신호는 조용히 실패한다.** 신호가 `available=false` 면 그 조건은 거짓이고 guard 도 거짓이다. 호흡·심박이 비활성이어도 나머지 경로는 그대로 돈다.

## 3. 스키마

```yaml
guards:
  drowsy:                       # guard 이름 (TickResult.guards 에 그대로 노출)
    enabled: true
    all:                        # 조건 결합자
      - {signal: respiration, reason: heart_slow}
      - {signal: posture,     reason: slouch}
      - {signal: posture,     reason: head_down}
    hold_sec: 60                # 60초 연속 성립해야 인정 (기본 0 = 즉시)
    route_to: environment       # CAUSE_ANALYSIS 라우팅 강제 (선택)
    actions: [wake_alert]       # 성립 순간 1회 방출 (선택)
```

### 3.1 조건(predicate)

| 형태 | 참이 되는 조건 |
|---|---|
| `{signal: S, reason: R}` | `S` 가 가용하고 `R` 이 `S.reasons` 에 있다 |
| `{signal: S, delta_gte: x}` | `S` 가 가용하고 `S.delta >= x` |
| `{signal: S, delta_lt: x}` | `S` 가 가용하고 `S.delta < x` |
| `{signal: S, phi_gte: x}` / `{phi_lt: x}` | 위와 같되 `phi` 기준 |
| `{signal: S, available: true\|false}` | 가용성 자체를 검사 |

한 predicate 에 여러 키를 쓰면 **모두** 만족해야 한다(암묵 AND).

### 3.2 결합자

| 키 | 의미 |
|---|---|
| `all: [...]` | 전부 참 (빈 목록은 참) |
| `any: [...]` | 하나라도 참 (빈 목록은 거짓) |
| `none: [...]` | 전부 거짓 |

결합자는 중첩할 수 있다. guard 최상위에 여러 결합자를 두면 그것들끼리 AND 다.

### 3.3 동작

- **평가 시점** — 매 tick, 상태 전이 **전**. 결과는 `TickResult.guards` 로 노출된다.
- **`hold_sec`** — 조건이 끊기면 타이머가 0으로 리셋된다. 깜빡임(chattering) 방지.
- **`actions`** — guard 가 **새로 성립한 tick 에만** 1회 방출한다. 유지되는 동안 매 tick 반복하지 않는다(개입 폭주 방지).
- **`route_to`** — `CAUSE_ANALYSIS` 에서 dominant 계산 대신 이 그룹을 쓴다. 단 **아직 시도하지 않은 원인(`remaining_options`)일 때만** 적용한다. 그렇지 않으면 개입 실패 후 `ESCALATE → CAUSE_ANALYSIS` 로 돌아왔을 때 같은 원인을 무한 반복하게 된다.
- **충돌** — `route_to` 를 가진 guard 가 여럿 활성이면 **`guards:` 에 먼저 선언된 것**이 이긴다.

## 4. `reasons` 요청 사양 — 김태환

guard 는 `reasons` 없이는 동작하지 않는다. `data-spec.md` §8.2 가 `signal_contribution` 에 `reasons` 를 이미 정의해 두었으므로 **새 필드가 아니라 값 채우기**의 문제다. 아래는 그 어휘 목록이다.

원칙 — reason 은 **판정 결과의 요약 라벨**이다. 원시값을 보내는 것이 아니라, `features/` 가 baseline 대비 상대 판정을 마친 뒤 그 결론을 문자열로 붙인다.

### posture (ToF)

| reason | 의미 | 근거 L1 필드 |
|---|---|---|
| `slouch` | 자세 enum 이 `slouch` 로 지속 | `posture` |
| `lean_forward` | 자세 enum 이 `lean_forward` | `posture` |
| `lean_back` | 자세 enum 이 `lean_back` | `posture` |
| `head_down` | 머리·상체 거리가 baseline 대비 하강 지속 | `head_delta_mm` |
| `nodding` | 반복 숙임 주기 검출 | `nod_rate_hz` |
| `still` | 모션 변화가 baseline 대비 낮음 | `motion_score` |

### respiration (mmWave C1001)

⚠️ `respiration_enabled: false` 이고 `heart_bpm` 은 data-spec 상 "MVP 가중치 0" 이다. 아래 reason 은 **E-3 결정과 8월 go/no-go 를 통과한 뒤에만** 유효하다.

| reason | 의미 | 근거 L1 필드 |
|---|---|---|
| `heart_fast` | 심박이 baseline 대비 상승 | `heart_bpm` · `heart_valid` |
| `heart_slow` | 심박이 baseline 대비 하강 | `heart_bpm` · `heart_valid` |
| `resp_slow` | 호흡수가 baseline 대비 하강 | `resp_bpm` · `resp_valid` |

### environment (SCD41 · BH1750)

환경은 **개인 baseline 이 아니라 절대 임계**로 판정한다. 계절 변동을 개인차로 오독하지 않기 위해서다(계절 축은 대회 기간 내 학습 불가).

| reason | 의미 | 근거 L1 필드 |
|---|---|---|
| `temp_high` / `temp_low` | 쾌적 구간 이탈 | `temperature_c` |
| `co2_high` | 절대 임계 초과 | `co2_ppm` |
| `humid_high` / `humid_low` | 쾌적 구간 이탈 | `humidity_rh_pct` |
| `lux_low` | 조도 부족 | `illuminance_lux` |

### keystroke (PC 수집기)

| reason | 의미 | 근거 L2 특징 |
|---|---|---|
| `typing_idle` | 입력 공백 비율 상승 | `idle_ratio` |
| `rhythm_slow` | 키 간격이 baseline 대비 증가 | `flight_mean_ms` |
| `rhythm_irregular` | 리듬 변동 증가 | `flight_std_ms` · `flight_cv` |
| `correction_up` | 백스페이스 빈도 상승 | `correction_rate` |

### elapsed

| reason | 의미 |
|---|---|
| `long_session` | 세션 경과가 개인 평균 대비 김 |
| `no_break` | 마지막 휴식 이후 경과가 김 |

### 규칙

- 어휘는 **이 문서가 정본**이다. 새 reason 이 필요하면 이 문서를 같은 PR 에서 함께 고친다.
- 알 수 없는 reason 은 **무시한다** (`data-spec.md` §14 minor 규칙과 동일).
- reason 은 0개 이상이다. 비어 있어도 정상이며, 그 신호를 참조하는 guard 만 거짓이 된다.

## 5. 예시 — 요청된 판정 규칙이 어떻게 표현되는가

| 요구 | guard |
|---|---|
| 평소보다 온도 높음 → 에어컨 | `hot_room`: `environment/temp_high` → `route_to: environment` + `actions: [cool_down]` |
| 심박이 갑자기 빠름 → 경고 | `heart_spike`: `respiration/heart_fast`, `hold_sec: 30` → `actions: [heart_alert]` |
| 심박 느림 + 자세 굽음 + 고개 떨굼 → 졸음 → 깨우고 환기 | `drowsy`: 3조건 `all`, `hold_sec: 60` → `route_to: environment` + `actions: [wake_alert]` |
| 숙임 + 키입력↓ → 피로↑ (fsm-spec 분기표) | `slouch_with_idle`: `posture/slouch` + `keystroke/typing_idle` |

`cool_down`(에어컨)과 `ventilate`(환기)가 갈리는 지점이 §2-4 의 효과다. 신호 키를 쪼개지 않고도 `ACTION_ENV` 안에서 서로 다른 제어를 낸다.

## 6. 미결정

| ID | 내용 |
|---|---|
| E-1 | 이 스키마 자체의 채택 여부 (팀 승인) |
| E-3 | 심박 사용 여부 — `heart_*` reason 의 유효성이 여기 걸린다 |
| E-4 | 졸음을 상태로 올릴지 — 올린다면 `route_to` 대신 전이 조건이 된다 |
| — | `actions` 어휘(`wake_alert`·`cool_down`·`ventilate`)와 `control/` 매핑은 제어 담당과 별도 합의 필요 |
