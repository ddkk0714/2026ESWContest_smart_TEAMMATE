# DESKMATE

**제24회 임베디드SW경진대회 · 스마트 가전 부문 · 팀 TEAMMATE**

> **카메라 없이 읽는 책상 위 인지 상태.**
> ToF 자세 · 60 GHz mmWave · 키스트로크 타이밍 · 환경 센서를 융합해 작업 상태(시작 · 몰입 · 피로 · 종료)를 추론하고,
> 판단 확신도에 따라 조명 · 환기 · 자세 교정 · 휴식을 **제안하거나 되돌릴 수 있게 자동 실행**하는 비침습 워크스페이스 가전.

- 카메라 · 마이크 · 키 내용을 수집하지 않는다. 센싱 · 추론 · 제어 · 개인화가 모두 책상 위 장치 안에서 끝난다.
- 판단은 규칙 FSM(1단계)이 맡고, 사람마다 다른 반응은 보드 안에서 학습해 다음 개입에 반영한다.

| 시연 영상 | 개발 완료 보고서 | 작품소개서 |
|---|---|---|
| (YouTube 링크) | (PDF 링크) | (문서 링크) |

---

## 무엇을 하는가

| 상황 | DESKMATE 의 판단 | 행동 |
|---|---|---|
| 자리에 앉아 타이핑을 시작 | 재실 + 키 입력 → 작업 시작 · 맥락(PC / 비PC / 혼용) 판정 | 저자극 집중 화면, 방해 없음 |
| 오래 몰입 중 입력 리듬이 흐트러짐 | `C_focus` 상승 → 집중 저하 의심 | 짧은 휴식 · 스트레칭 제안 카드 |
| 엎드리거나 고개를 끄덕임 | ToF 자세(엎드림 · 졸음) + mmWave 정적 → `C_fatigue` ≥ 0.70 피로 확정 | 무드등 밝기를 올려 깨우기 + 알림음, 선풍기 바람 |
| 환기 없이 CO₂ 가 오름 | 재실 중 CO₂ 1000 ppm 이상 5분 지속 | 창문 환기 제안, 공기청정기 연동 |
| 사용자가 "싫어요"를 누름 | 거절 · 되돌리기를 음의 보상으로 기록 | 직전 조치를 원래대로 돌리고, 그 사람에게는 그 개입을 덜 고른다 |

**확신도 게이트(포스터와 동일):** 확신도 **< 0.45 무동작 · 0.45~0.75 제안 · ≥ 0.75 가역 자동 실행 + 이유 표시.**
비가역 동작은 자동으로 실행하지 않고, 모든 자동 실행은 화면에서 되돌릴 수 있다.

---

## 시스템 구성

```
              [ 센싱 ]                         [ 추론 · 판단 ]                      [ 출력 · 제어 ]

  ESP32 센서 노드 ──────────┐
   VL53L8CX ToF 8×8 ×2      │  UART (COBS + CRC-16)       Raspberry Pi 4                 Raspberry Pi 5
   C1001 60 GHz mmWave      ├─ USB 직결 / GPIO UART2 ──►  중앙 허브 (ATLAS Headless) ─MQTT─► Atlas Flutter 터치 UI
   SCD41 CO₂ · DHT22 온습도 │                            · UART 디코더 (C++)        (LAN)   · 집중 · 제안 · 리포트
   BH1750 조도              │                            · ToF 자세 GRU 서비스       ◄────  · 수락 · 거절 · 정정
                            │                            · 18상태 규칙 FSM                    │
  PC 수집기 ── Wi-Fi/MQTT ──┘                            · 개인화 정책 · 반응학습             │ Bluetooth LE
   키스트로크 타이밍(키 값 없음)                          · 제어 디스패처                      ▼
                                                                                      iLink 무드등 · Pico 2 W 선풍기 · 스피커
                                                          LG ThinQ 공기청정기 (AS183HWWA)
```

| 계층 | 장치 | 맡은 일 | 디렉터리 |
|---|---|---|---|
| 센싱 | ESP32 | ToF 원본 8×8 · mmWave · 환경 센서 수집, 프레임 인코딩 | [`firmware/esp32_sensor_node`](firmware/esp32_sensor_node) |
| 센싱 | PC | 키 내용 없는 입력 타이밍 특징(dwell · flight · idle · correction) | [`collector/`](collector/) |
| 추론 · 제어 | Raspberry Pi 4 | UART 디코드, 특징 통합, 자세 모델, FSM, 개인화, MQTT 브로커, 제어 판단 | [`hub/`](hub/) |
| 출력 | Raspberry Pi 5 | 터치 UI, 사용자 피드백, BLE 기기 실행, 음성 · 알림음 | [`display/`](display/) |
| 제어 기기 | Pico 2 W | BLE 선풍기(1 · 2 · 3단 · 자연풍 · 꺼짐 타이머) | [`firmware/pico2w_ble_fan`](firmware/pico2w_ble_fan) |
| 학습 | PC | 공통 모델 학습 · 시뮬레이션 데이터 · 변환 검증 | [`ml/`](ml/), [`dataset/`](dataset/) |

> LG 기술교육 제공 구성에 따라 ESP32 · Raspberry Pi 4 · Raspberry Pi 5 **3종 분산 구성**을 쓴다.
> Pi 4 는 AI Native OS Headless(ATLAS) 위 native service IPK, Pi 5 는 AI Native OS Video Profile 위 Atlas Flutter IPK 로 배포한다.
> Pi 5 가 꺼져도 Pi 4 의 판정과 제어 판단은 계속 돈다.

---

## 핵심 구현

### 1. 센서별 처리

| 신호 | 처리 | 판정에 쓰는 것 |
|---|---|---|
| **ToF** VL53L8CX ×2 | ESP32 가 센서별 원본 8×8 을 UART `0x05` 로 Pi 4 에만 전송 → 센서별 GRU 추론 → log-softmax 합 융합 | 9라벨 자세(바른 자세 · 앞으로 숙임 · 뒤로 기댐 · 엎드림 · 턱 괴기 · 졸음 · 자리 비움 · 센서 위치 이탈 · 거북목). 없으면 MLP → ESP32 규칙 판정(`0x03`)으로 폴백 |
| **mmWave** C1001 | 수신 전용 파서 + 졸음 판정기(DrowsyDetector), 117분 7,085프레임 체크섬 오류 0건 실측 | 재실 · 움직임 · 거리. 호흡은 중앙값 · 기준선 대비 "소실 여부"만 보조 증거 |
| **키스트로크** | 60 s 창 · 1 Hz 집계, 키 값 미수집 | 입력 리듬 붕괴 · 정정 비율 · 무입력 |
| **환경** | 단일 센서 측정값, 보정값을 만들어 넣지 않음 | CO₂ 세션 상승 · 온습도 쾌적 이탈 · 조도 부족 |

책상 재실은 **mmWave 우선 + 거리 게이트**로 판정하고, 책상이 비면 ToF 자세를 쓰지 않는다.
모든 신호는 개인 기준선(중앙값 · MAD Modified z-score, 시간대 버킷)으로 정규화한 뒤 FSM 에 들어간다.

### 2. 1단계 규칙 FSM — 완성도의 축

- **5계층 18상태**(VER5), 화면에는 6 phase 로 축약. 사양 [`docs/fsm-spec.md`](docs/fsm-spec.md).
- **이중 모니터링:** `C_fatigue`(피로) · `C_focus`(집중 저하)를 10 s 마다 계산, PC / 비PC / 혼용 맥락별 가중치.
- **조합 조건(guard) 층:** 가중합과 병렬로 도는 다신호 AND 규칙으로 오판을 줄인다.
- 원인 분석(자세 · 인지 · 환경) → 원인별 개입 → MONITOR 에서 효과 확인 → 효과가 없으면 다른 원인을 **제안**으로 재시도.
- 임계값 · 가중치 · 타이머는 전부 [`hub/deskmate_hub/config/*.yaml`](hub/deskmate_hub/config). 코드에 하드코딩하지 않는다.
- 2단계 모델이 없어도 단독으로 완전히 동작한다. 판정 주기 PC 측정 p95 ≈ 0.6 ms(목표 ≤ 500 ms).

### 3. 보드 안에서 끝나는 개인화

- **첫 설정 → 정책 기준:** 빛 · 소리 · 바람 선호, 몰입 도움, 수면 시각을 개입 정책의 사전값으로 쓴다. 소리를 끄면 소리 개입은 고르지 않는다.
- **암묵 신호 학습:** 밝힌 뒤 사용자가 직접 어둡게 하면 그 개입을 덜 고르고, 엎드림 → 바른 자세 회복을 "깨우기 성공" 보상으로 쓴다.
- **반응학습:** 확인 카드("지금 피곤하세요?") · 무응답 · 되돌리기로 그 사람의 피로 판정을 FSM 위에서 잔차 조정한다(울타리 안에서만).
- **사람별 저장:** `profile_id` 마다 정책을 따로 저장하고 재시작 후에도 이어간다. 동의 · 철회 · 삭제를 지원한다.
- **2단계 모델:** 공통 1D CNN 은 PC 에서 준비하고, 백본을 고정한 채 개인 head 만 Pi 4 에서 순수 Python 으로 학습 · 검증 · 적용한다. 개선이 없으면 기존 모델을 유지한다.

### 4. 가전 제어 — 양방향 · 가역

| 기기 | 연결 | 동작 |
|---|---|---|
| iLink 무드등 | Pi 5 BLE | 켜기 · 끄기 · 지금 대비 % 밝기(깨우기 +60 % · 최소 60 %), 끊기면 다시 찾아 재연결 |
| DESKMATE 선풍기 (Pico 2 W) | Pi 5 BLE | 1 · 2 · 3단(듀티 30 · 45 · 60 %) · 자연풍 · 꺼짐 타이머 |
| 스피커 | Pi 5 오디오 | 알림음 3종 · 음성 안내 · 집중 음악, 음량 3단 |
| LG 공기청정기 AS183HWWA | ThinQ Connect | 공기질 · 필터 · 전원 조회, 승인 기반 제어 |

- 명령은 `control/cmd` → 실행 결과 `control/result` 로 돌아온다. 결과에 `previous` · `actual` 이 있어 **되돌리기는 이전 상태로 정확히 복원**한다.
- 쿨다운(같은 조치 5분에 1회) · 타임아웃 · 중복 방지 · 사용자가 직접 조작한 값 우선을 지킨다.
- 명령 하나당 "왜 → 무엇을 → 결과 → 걸린 시간 → 되돌림" 을 **제어 진행 로그**로 남기고 화면에서 보여준다.

### 5. Pi 5 터치 UI (DESKMATE 앱 V35)

- 저자극 집중 홈 · 위젯, 터치 시 상세, 제안 카드(이유 · 실제 관측값 · 조절 대상 · 남은 시간), 자동 실행 알림과 되돌리기.
- 세션 리포트(수용률 · 무응답률 · 되돌림률 · 정정률 · 회복 시간), 프로필 32종 · 직업 18종 개인화 설정.
- 연결 탭 · 상태 배지 · MQTT 자동 복구 · BLE 기기 기억과 자동 재연결.
- 개발자 모드: 센서 원값 · 키스트로크 · 추론 근거 · 제어 진행 표. 노트북 개발자 화면(`hub/atlas-tof/pc/devview`)과 원격 조작창(`tools/laptop-control`)으로 심사 중 내부 판단을 그대로 보여준다.

### 6. 시뮬레이션과 데이터

- **MuJoCo 자세 시뮬(`tof_sim_v5`):** SMPL-X 몸 · 센서 무작위 배치 · 거북목 등으로 ToF 자세 모델 학습 데이터를 만든다.
- **생활 각본 폐루프 시뮬(`life_sim`):** 센서 → hub FSM → 개인화 정책 → 기기 mock → 방 물리(CO₂ · 온습도 · 조도) → 사람 반응까지 하루를 돌려 정책을 검증한다.
- **교사 라벨:** 깊이 카메라(D435if)는 **오프라인 데이터셋 라벨링에만** 쓰고 제품 경로에는 넣지 않는다.
- 피로 KSS 자가보고 수집 앱 · 기록기, 학습 창 · 증강 · 증거 리포트 파이프라인([`docs/ml-augmentation-evidence.md`](docs/ml-augmentation-evidence.md)).

---

## 프라이버시 · 보안

- **키 값 미수집**(타임스탬프 기반 집계만). **카메라 · 마이크 미사용**.
- ToF 원본 8×8 은 ESP32 → Pi 4 유선 UART 와 Pi 4 메모리 안에서만 다룬다. MQTT · 로그 · 화면으로 내보내지 않는다.
- 모든 판단 · 학습이 로컬에서 끝나고, 개인화는 동의 기반 · 언제든 삭제 가능하다.
- ThinQ 토큰은 별도 터미널 숨김 입력 · 프로세스 메모리에만 보관한다. 자격증명 · 개인 로그 · ESM 라벨 · 학습 모델은 저장소에 없다.

자세한 내용은 [`docs/security-privacy.md`](docs/security-privacy.md).

---

## 실행

### PC 에서 바로 보기 (보드 없이)

```bash
cd hub
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest tests -q                                 # FSM · ingest · 개인화 · 제어 단위 테스트
python -m deskmate_hub demo --host 0.0.0.0 --port 8765    # 합성 센서로 FSM 순환, http://127.0.0.1:8765/api/state
```

5분 시연 시나리오 리허설과 합성 센서 주입은 [`tools/README.md`](tools/README.md)(`demo_dryrun.py` · `mqtt_scenario_sim.py` · `rehearsal_local.py`).

### 실기 (ESP32 · Pi 4 · Pi 5)

1. **ESP32** — PlatformIO 로 `firmware/esp32_sensor_node` 를 굽는다. 배선은 [`docs/hardware.md`](docs/hardware.md) · [`docs/breadboard-esp32-node.md`](docs/breadboard-esp32-node.md).
2. **Pi 4** — hub IPK(`hub/atlas`) · 자세 서비스(`hub/atlas-tof`)를 설치한다. [`hub/atlas/README.md`](hub/atlas/README.md).
3. **Pi 5** — 개발 PC 의 Docker(Atlas SDK)에서 앱을 빌드 · 설치한다. [`display/atlas/README.md`](display/atlas/README.md).
4. **부팅할 때마다 Pi 4 에서 한 번** 브로커 · hub 를 켠다(ATLAS 이미지가 부팅 자동 시작을 지원하지 않음).

```sh
sh /data/share/deskmate/recovery/install.sh pi4 --runtime-only   # 브로커 · hub 기동 + 감독(죽으면 재시작)
# ESP32 를 USB 로 꽂았다면 그 전에: /mnt/rawdata/deskmate_tof/ch340cfg auto 115200
```

Pi 5 는 `sh /data/share/deskmate/recovery/install.sh pi5 --runtime-only`. 전체 절차와 문제 해결은 [`docs/reboot-recovery.md`](docs/reboot-recovery.md).

> **주의:** ATLAS 설정 앱에서 무드등 · 선풍기를 페어링하면 시스템이 링크를 잡아 앱 연결이 거절된다. 연결은 DESKMATE 앱에서만 한다.

---

## 시험 결과 요약

| 항목 | 결과 |
|---|---|
| UART 링크 | mmWave 117분 7,085프레임 체크섬 오류 0 · Pi 4 USB 직결 30 s 동안 527프레임 CRC 오류 0 |
| 재부팅 · 장애 복구 | hub 강제 종료 후 약 11 s 안에 새 프로세스로 복구, 브로커 재시작 시 hub 자동 재연결 |
| 판정 주기 | PC p95 ≈ 0.6 ms (목표 ≤ 500 ms) |
| 무드등 제어 | Pi 5 실기 `set_power on` 성공(재탐색 후 12.5 s), 되돌리기 결과 확인 |
| 2단계 모델 변환 | 공통 CNN portable / Keras top-1 일치 100 %(합성 입력 1,045개) |
| 시뮬(life_sim) | CO₂ 환기 규칙으로 재실 중 CO₂ ≥ 1000 ppm 시간 118 → 51분(대학생 각본) |
| 자동 테스트 | hub pytest 640 통과 · Pi 5 앱 flutter test 290 통과 · devview 102 통과 · 선풍기 펌웨어 호스트 테스트 113 checks |

수치의 출처와 조건은 [`docs/sim-results/`](docs/sim-results) · [`docs/ondevice-field-validation.md`](docs/ondevice-field-validation.md) · 각 PR 에 있다. 시뮬 결과는 실측이 아니다.

---

## 디렉터리 구조

```
├── firmware/
│   ├── esp32_sensor_node/   ESP32 센서 노드 (ToF ×2 · mmWave · 환경, UART 프레임)
│   └── pico2w_ble_fan/      Pico 2 W BLE 선풍기 펌웨어
├── collector/               PC 키스트로크 타이밍 수집기
├── hub/                     Raspberry Pi 4 중앙 허브
│   ├── atlas/                   ATLAS native service IPK (C++ UART 디코더 · MQTT 브리지 · 제어 로그)
│   ├── atlas-tof/               ToF 자세 GRU 서비스 · UART 중계기 · 노트북 개발자 화면(pc/devview)
│   ├── mqtt/                    Mosquitto 설정
│   └── deskmate_hub/
│       ├── ingest/              UART · MQTT → SensorFrame (10 s)
│       ├── features/            개인 기준선 정규화
│       ├── posture/             ToF 자세 MLP (폴백)
│       ├── inference/           18상태 규칙 FSM · 신뢰도 게이트 · guard
│       ├── personalize/ learning/ personalization/   개인화 정책 · 반응학습 · 온디바이스 개인 head
│       ├── fatigue/             공용 피로 모델 (KSS)
│       ├── control/             제어 디스패처 · 기기 어댑터 · 가전 제어 플랫폼
│       └── config/              임계값 · 토픽 · 장치 설정 (YAML)
├── display/atlas/
│   ├── ui_demo_app/             DESKMATE 앱 (V35, 제품 UI)
│   ├── ui_env_app/              이전 통합 앱
│   └── fatigue_app/             피로 KSS 기록 앱 (데이터 수집용)
├── ml/                      공통 모델 학습 · 증강 · TFLite / portable 변환
├── dataset/                 ToF 시뮬(MuJoCo) · 깊이 교사 라벨 · 실측 기록 도구
├── tools/                   시나리오 시뮬 · 리허설 · Node-RED · 복구 설치기 · ThinQ · 노트북 원격 조작
└── docs/                    명세 · 하드웨어 · 시험 기록 · 제출 서류
```

---

## 문서

| 문서 | 내용 |
|---|---|
| [`docs/README.md`](docs/README.md) | 문서 지도 |
| [`docs/requirements-spec.md`](docs/requirements-spec.md) · [`docs/data-spec.md`](docs/data-spec.md) | 요구사항 · 데이터 계층 계약 |
| [`docs/fsm-spec.md`](docs/fsm-spec.md) · [`docs/guard-spec.md`](docs/guard-spec.md) | 18상태 전이 · 신뢰도 공식 · 조합 조건 |
| [`docs/mqtt-topics.md`](docs/mqtt-topics.md) | Pi 4 ↔ Pi 5 MQTT 토픽 · payload |
| [`docs/hardware.md`](docs/hardware.md) | 배선 · 센서 · 통신 |
| [`docs/personalization-spec.md`](docs/personalization-spec.md) · [`docs/ondevice-personalization.md`](docs/ondevice-personalization.md) | 개인화 정책 · 온디바이스 학습 |
| [`docs/device-control-platform.md`](docs/device-control-platform.md) | 가전 제어 플랫폼 |
| [`docs/demo-scenario.md`](docs/demo-scenario.md) | 시연 동선 |
| [`docs/security-privacy.md`](docs/security-privacy.md) | 보안 · 프라이버시 |

---

## 팀 TEAMMATE

| 이름 | 역할 | 담당 | 주 디렉터리 |
|---|---|---|---|
| 김태환 | 팀장 | 총괄 · 일정, 센서 인터페이싱, ToF 전처리 · 재실/자세 판정, 캘리브레이션 | `hub/features/`, `firmware/` |
| 박소연 | 팀원 | 규칙 기반 FSM 추론 엔진, 신뢰도 공식 · 게이트, 작업 리포트 | `hub/inference/` |
| 조명희 | 팀원 | 경량 분류기 · 온디바이스 최적화, ThinQ 연동, ESM 라벨 체계 | `ml/`, `hub/control/` |
| 이민혁 | 팀원 | ESP32 펌웨어 · 1차 전처리, MQTT 브로커 · 토픽 설계, 로깅 · 시간 동기화 | `firmware/`, `hub/ingest/` |
| 최민경 | 팀원 | 디스플레이 UI, 사용자 피드백 · 리포트 시각화, 키스트로크 수집 | `display/`, `collector/` |

## 개발 일정

| 시기 | 내용 |
|---|---|
| 2026-07 | 예선 합격 · 장비 수령 · 개발 환경 |
| 2026-08 | FSM 엔진 · Pi 5 Atlas 배포 파이프라인 · 명세 문서 · 통신 구조 확정 |
| 2026-09 | ESP32 → Pi 4 UART 실측 통과, 실센서 → MQTT → FSM MVP, 앱 연결 안정화 |
| 2026-10 | ToF VL53L8CX ×2 연결 · GRU 자세 서비스, 개입 UI · 되돌리기, 온디바이스 개인화, BLE 무드등 · 선풍기 · ThinQ, 시뮬 검증 |
| 2026-10-30 | 결선 서류 제출 (개발완료보고서 · 작품소개서 · 시연 영상) |
| 2026-11-06 | 오프라인 발표 심사 |

## 라이선스

[MIT](LICENSE). 앱에 넣은 음원의 출처는 `display/atlas/ui_demo_app/assets/audio/CREDITS.md` 에 있다.
