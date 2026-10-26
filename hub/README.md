# hub — Raspberry Pi 4 중앙 추론 허브

ESP32 노드(UART2)와 PC 수집기(MQTT)가 보낸 센서 스트림을 받아 슬라이딩 윈도우(30s ~ 5min)로
특징 벡터를 만들고, 상태와 신뢰도를 산출해 MQTT 로 발행한다. 제어 판단도 여기서 한다.

**런타임(확정):** Pi 4 는 AI Native OS Headless(ATLAS). 이미지에 Python·컴파일러가 없으므로
[`atlas/`](atlas/README.md) 의 native service IPK 가 `/restricted/python3` 로 이 패키지를 실행한다.
UART 디코딩(COBS/CRC-16)과 MQTT 클라이언트는 그 C++ 서비스에 두고, 라인 프로토콜(`bridge` 서브커맨드)로 Python 과 주고받는다.
보드에 개발 도구를 설치하지 않는다.

## 모듈 (2026-09-14)

| 디렉터리 | 담당 | 내용 | 상태 |
|---|---|---|---|
| `atlas/` | 공통 | ARC IPK native service. 제한 Python 으로 FSM 실행, HTTP 8765 개발 API | ✅ 실행. UART 수신·MQTT 발행은 미구현 |
| `mqtt/` | 이민혁 | Pi 4 Mosquitto 설정 | ✅ |
| `ingest/` | 이민혁 | MQTT 센서 토픽(mmwave·env·keystroke) → `SensorCache` → 10 s `SensorFrame`(`config/ingest.yaml` 임시 스케일), freshness·seq 갭, `feedback/user` 수신 | 🟡 MVP 경로 구현(09-14). UART 라인 입력은 통합 MVP 에서 같은 캐시에 추가 |
| `features/` | 김태환 | `baseline.py`: 세션 START 보정 창 → 지표별 median·MAD → Modified z → `phi/delta`, 시간대 버킷 seed, opt-in 저장 | 🟡 09-16 구현(ingest 연동). ToF 기하 특징·환경 추세는 센서 후 |
| `inference/` | 박소연 | 규칙 기반 FSM(1단계), 신뢰도 공식, 신뢰도 게이트 | ✅ 18상태, 테스트 46개(45 통과·1 xfail) |
| `inference/` | 조명희 | TFLite 경량 분류기 로딩 · 추론(2단계) | ⬜ 선택적 의존 |
| `control/` | 조명희 | `dispatcher.py`: ACTION_ENV → `control/cmd`(가역 동작만, 쿨다운, 비가역 자동 금지) → `control/result`/타임아웃 → action_done, 제안 대기·만료, 거절 시 undo. `config/control.yaml`. Mock 어댑터 내장 | 🟡 09-16. 실기 플러그 어댑터는 모델 선정 후 |
| `config/` | 공통 | 임계값 · 토픽 · 장치 설정 (YAML) | ✅ `fsm.yaml`, `ingest.yaml`(센서→신호 임시 스케일) |
| `live.py` | 공통 | `run` 명령: ingest → FSM → `deskmate/state/phase`(retain, QoS 1) 발행, 프레임을 리플레이 호환 JSONL 로 기록 | 🟡 MVP(09-14). 로컬 amqtt 브로커 E2E 확인, Pi 4 Mosquitto 실연동 대기 |
| `ingest/uart_frame.py` `uart_source.py` | 공통 | UART2 프레임 코덱(COBS·CRC-16/CCITT-FALSE·TYPE 구조체)과 `UART\t` 라인/raw 시리얼 소스. 10-17: ToF 원본 8×8 센서 프레임 `0x05` 디코더·`TofGridAssembler`(같은 frame_id 의 센서 프레임을 표본 하나로, `SensorCache.tof_grid`, 메모리 전용) | ✅ 09-15, 테스트 14개 + 0x05 4개 |
| `ingest/tof_service.py` | 공통 | **별도 자세 서비스(deskmate_tofposture, GRU, HTTP 8771) 판정 → FSM**(10-23). `TOFSVC	<json>` 줄(보드: `hub/atlas` C++ 가 1 s 폴링) 또는 Python 폴러(PC)로 `/api/state` 를 받아 `tof_service` 표본으로 두고, `ingest.yaml tof.posture_source: service` 면 그 자세·신뢰도가 posture Signal 이 된다(라벨 9개, drowsy → 피로 증거). 없으면 MLP → 0x03 규칙 폴백 | ✅ Pi 4 배포판 기본. 테스트 `tests/test_tof_service.py` |
| `posture/tof_mlp.py` | 공통 | **ToF 자세 MLP, 순수 Python**(10-17: 모델은 ESP32 가 아니라 Pi 4 에서). 센서별 8×8 → 센서마다 기준선(앉은 뒤 조용한 10 s 중앙값)·특징 192·192→64→10 → 두 센서 log-softmax 합 → 1 s 다수결 → `SensorCache.tof_mlp` → `mapping.py` posture 신호(`tof.posture_source: mlp`, 못 내면 0x03 규칙 판정으로 폴백). 가중치 `config/tof_mlp.json`(`dataset/tof_sim/export_mlp_json.py`, Atlas zip 에 동봉) | ✅ 테스트 8개. **후보 A(미확정)** — 후보 B 는 별도 서비스 `deskmate_tofposture`(HTTP 8771, `tof_sim_v5` PostureNet, `hub/atlas` 가 0x05 를 tcp:9100 중계). 실측 정확도로 고른다. 시뮬 8×8 가중치 — 실측 16×8 모델 대기 |
| `replay.py` `demo.py` `preview_api.py` `service_bridge.py` | 박소연 | 리플레이 하네스 · 합성 데모 · HTTP 미리보기 · Atlas 라인 브리지 | ✅ `report.py`·`--report` 포함 |

## 설계 규칙

- **임계값을 코드에 하드코딩하지 않는다.** 전부 `config/` YAML 로 뺀다.
  8월 실측 후 값만 바꿔 재실험할 수 있어야 한다.
- 1단계 FSM 은 2단계 분류기 없이 **단독으로 완전 동작**해야 한다.
  분류기는 import 실패해도 허브가 죽지 않도록 선택적 의존으로 둔다.
- 판정 사이클 500ms 이하 목표.
- ThinQ 자격증명은 커밋하지 않는다. 환경변수 또는 `config/secrets.yaml`(gitignore).

## 실행

```bash
cd hub
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m deskmate_hub demo --host 0.0.0.0 --port 8765
```

위 명령은 합성 센서 입력을 FSM에 넣고 상태를 1초마다 순환한다. Pi 5 display는
`http://<Pi4-IP>:8765/api/state`에서 상태를 읽고 `/api/feedback`으로 수락·거절을 돌려준다.
이 HTTP API는 **최종 통신을 정하기 전 보드 간 화면 검증용 어댑터**다. MQTT 채택 여부와
무관하게 FSM·화면 계약은 유지하고 어댑터만 교체한다.

```bash
curl http://127.0.0.1:8765/health
curl http://127.0.0.1:8765/api/state
```

Atlas 앱의 **센서 테스트** 화면이 `/api/test-frame`에 입력을 보내면 자동 순환을 멈추고
같은 `FSMEngine`·`config/fsm.yaml`로 가상 시간을 진행한다. 입력은 원시 센서값이 아닌
baseline 대비 `phi`·`delta` 정규화 기여도이며 운영 입력으로 저장하지 않는 개발 전용 API다.
`reset`은 설정된 baseline 시간을 가상으로 진행해 몰입 상태로 빠르게 진입하고,
`tick`은 앱에서 지정한 30초 또는 3분만큼 진행한다.

```bash
python -m deskmate_hub --demo                            # 합성 세션 스모크
python -m deskmate_hub --replay logs/2026-08-01.jsonl    # 로그 리플레이
python -m deskmate_hub --demo --report                   # + 세션 작업 리포트(몰입 시간·피로 에피소드·개입 결과·ESM 라벨)
```

### 실센서 라이브 (MVP 2026-09-18)

```bash
python -m deskmate_hub run --broker <broker-ip>            # 기본 포트 1883, 로그 logs/
python -m deskmate_hub run --broker <ip> --ingest-config my-ingest.yaml --log-dir logs
```

- 구독: `deskmate/sensor/#`(mmwave·env·keystroke — envelope(collector 포함) 또는 구버전 평면 payload), `deskmate/feedback/user`
- 발행: `deskmate/state/phase`(retain, QoS 1, 10 s 주기), `deskmate/interaction/request`(제안 게이트로 ACTION_* 진입 시, retain 없음), `deskmate/control/cmd`(ACTION_ENV 와 ACTION_POSTURE, `control.yaml actions.<cause>` — 10-24 부터 자세성(졸음)도 `desk_lamp` 명령, 개인화 기기 arm 이 있으면 그쪽 우선. 밝기는 10-25 부터 `adjust_brightness {percent}` = 지금 밝기 대비 %(환경 +30·졸음 +60), 되돌리기는 결과의 previous 로 set_brightness), `deskmate/health/hub`(LWT). 구독에 `deskmate/control/result` 포함
- 제어 어댑터가 없으면 명령은 `result_timeout_sec` 뒤 timeout 으로 닫히고 FSM 은 계속 진행한다. 리허설은 `python tools/mock_plug.py --broker <ip>`. `control.yaml adapter: mock` 이면 프로세스 안에서 즉시 성공 처리
- 매핑은 `config/ingest.yaml`. `normalization: baseline`(기본)이면 START 보정 창에서 만든 개인 기준선(중앙값·MAD Modified z)을 우선 쓰고, 기준선이 없는 지표·보정 전에는 선형 램프로 폴백한다. `baseline.persist.enabled: true` 면 median·MAD·표본 수만 JSON 에 저장(opt-in).
- `--config deskmate_hub/config/fsm.demo.yaml` 은 임계값은 같고 **타이머만 짧은 시연·리허설 프로파일**(baseline 30 s, 이탈 60 s 등). 운영 기본값이 아니다.
- 밴딧이 고른 arm 의 실행(`control.yaml arm_notice:`, 10-26): 기기 arm(wake_light 등)은 제어 명령, 기기 없는 arm(자세 카드·알림음·음성)은 자동 게이트에서도 안내 카드(`auto: true`) + `speaker speak`/`chime` 으로 전달하고 그때 기본 램프는 안 나간다(기본 램프는 정책 결정이 없을 때만). 시연 프로파일은 `personalize.bandit.algorithm: greedy`(평균만) 로 온보딩 prior 가 기기를 결정하게 한다 — 졸음에서 수험생(light high·sound low·wind off) wake_light 0.55 > sound_cue 0.35, 대학생(light mid·sound low·wind off) wake_light 0.45 > 0.35 — 둘 다 무드등(여유폭 0.20 vs 0.10), 온보딩이 없거나 중립이면 sound_cue 0.55 > wake_light 0.45 라 알림음; **시연은 온보딩을 먼저 보내야 한다.** 보드 기본은 Thompson.
- REST 진입 알림(`control.yaml rest_notice:`, 10-26): 휴식 제안 수락·개입 실패 뒤 강제·졸음 재시도 어느 길이든 REST 에 들어가면 `interaction/request kind rest_notice`(닫기만 있는 안내 카드, 문구·휴식 분·이유) + `control/cmd speaker speak {text}`(사용자 sound_pref off 면 카드만). REST 재진입 쿨다운 120 s, 길어지면 한 번 더(reminder_after_sec). 시연 프로파일은 `control:` 절로 30 s/120 s.
- 개인화 정책(`personalize.yaml`, 10-25): `policy.causes: [posture, cognitive]` — 밴딧은 자세·인지 원인의 arm(깨우기·자세·휴식)만 고르고, 환경 원인은 `control.yaml actions.environment`(FSM 고정)만 나간다. 환경 에피소드도 로그에는 `source: fsm_fixed` 로 남는다.
- 2단계 피로 모델(`personalization.yaml`, 10-25): Release `stage2_portable_v9_20261024`(config/stage2/, 시뮬 학습)를 portable_cnn 으로 보드에서 관찰 전용 실행 — `state/phase sensor_summary.personalization`(label·confidence). FSM 입력이 아니다. normalization 이 linear(demo-b)면 unavailable.
- 환경변수 `DESKMATE_PROFILE=demo-a|demo-b` 는 `config/profiles/<이름>.yaml` 의 `fsm:`·`ingest:`·`personalize:` 절을 기본 설정 위에 깊은 병합으로 얹는다(`run`·bridge 둘 다, 10-24). demo-a = 피로 유지 20 s·MONITOR 창 30 s·휴식 60 s·자동 0.60·PC 창 120 s·휴식 뒤 경과 초기화, demo-b = demo-a + 보정 10 s·자동 0.55·확정 0.60·선형 정규화. 개인화 정책 gate.conf_auto 도 같은 값으로 맞춘다(다르면 정책이 자동을 제안으로 낮춘다). **시연 전용(시뮬 근거) — 실측 성공 기록에 쓰지 않는다.** 보드에서는 `hub.env` 한 줄(`hub/atlas/README.md`). demo-a 는 보정 300 s 동안 바르게 앉은 뒤 엎드려야 한다(보정 중 엎드리면 점수가 안 오른다); 시연 주력은 demo-b. 시연 프로파일은 0x03 규칙 졸음 플래그·끄덕임의 δ 를 0.3 으로 눌러(ingest `tof.rule_drowsy_delta`·`nod_delta_max`) 깨어 있는 사람에게 헛켜지지 않게 한다.
- `logs/frames-*.jsonl` 은 `--replay` 로 그대로 재생되고, `logs/state-*.jsonl` 은 발행한 envelope 다.
- 콘솔 한 줄 = 한 tick: 상태·ctx·C_fatigue/C_focus·present·pc_ratio·가용 신호(`ekprs` 첫 글자, `.`=미가용)

### Pi 4 native service 의 실센서 모드

`hub/atlas` C++ 서비스가 `python -m deskmate_hub bridge` 를 띄운다. 환경변수 `DESKMATE_HUB_MODE=live` 면 데모 순환 대신
stdin 의 `UART\t{"type","seq","ts_ms","payload_hex"}` 라인(C++ 가 COBS 해제·CRC 검증한 프레임)과, `DESKMATE_MQTT_HOST` 가 있으면
MQTT 센서 토픽을 함께 `SensorCache` 에 넣고 같은 `LiveHub` 루프를 돈다. 결과는 `STATE\t` 라인(HTTP 8765)과 MQTT `state/phase` 로 나간다.
프레임 규약·payload 구조는 `ingest/uart_frame.py`(= `docs/data-spec.md` §13.1), PC 검증 도구는 `tools/uart_frame_tool.py`.

남은 것: C++ 쪽 `/dev/serial0` 수신 → COBS/CRC → `UART\t` 라인 출력(통합 MVP 10-05). HTTP 8765 는 센서 테스트·화면 분리 검증용 fallback 으로 유지.

## 테스트

```bash
pytest tests/
```

FSM 상태 전이는 합성 입력으로 단위 테스트한다 — 실기기 없이 검증 가능한 유일한 부분이다.
