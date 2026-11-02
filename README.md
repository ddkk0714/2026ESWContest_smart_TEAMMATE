# DESKMATE

**제24회 임베디드SW경진대회 · 스마트 가전 부문 · 팀 TEAMMATE**
**비접촉 센싱 기반 인지상태 모니터링 스마트 데스크**

> **책상 위에 두기만 하면, 묻기 전에 먼저 움직이는 가전.**
> ToF 자세 · 60 GHz mmWave · 키 입력 타이밍 · 환경 4종 신호를 융합해 작업 상태(시작 · 몰입 · 피로 · 종료)를 **10초마다** 판정하고,
> 피로의 원인(환경 · 자세 · 경과)에 맞는 개입 하나를 골라 조명 · 선풍기 · 휴식 안내를 **제안하거나 되돌릴 수 있게 자동 실행**한다.

- **카메라 · 마이크 없음, 키 값 미수집.** 판정 · 제어 · 개인화가 ESP32 · Raspberry Pi 4 · Raspberry Pi 5 안에서 인터넷 없이 끝난다.
- 판단은 규칙 FSM 이 맡고, 사람마다 다른 반응은 Pi 4 안에서 학습해 다음 개입에 반영한다.

| 소스코드 | 시연 영상 | 개발완료보고서 · 작품소개서 |
|---|---|---|
| 이 저장소 | 업로드 후 추가 (`2026ESWContest_스마트가전_TEAMMATE_시연동영상`) | 결선 제출본 |

---

## 무엇을 하는가

| 단계 | DESKMATE 가 감지 · 판단하는 것 | 7" 화면에서 보는 것 |
|---|---|---|
| **착석 · 시작** | mmWave 로 착석 감지, 첫 5분 동안 자세 · 타이핑 · 환경 기준선 수집, PC / 혼용 / 비PC 판별 | 프로필 선택 |
| **몰입** | 10 s 마다 ToF 자세 · 키 입력 타이밍 · CO₂ 로 `C_fatigue` · `C_focus` 계산 | 조용한 시계, 몰입 중엔 어둡게 |
| **피로 누적** | `C_fatigue` ≥ 0.40 이 180 s → 의심, ≥ 0.70 이 180 s → 확정 | — (본인은 아직 모름) |
| **개입** | 기여가 가장 큰 원인 하나를 골라 신뢰도 3단 게이트로 실행 | 제안 카드(이유 · 센서 값 · 응답) |
| **확인 · 회복** | 개입 10분 뒤 효과 확인. 나아지면 회복, 아니면 다음 원인. 자리를 뜨면 세션 종료 | 세션 요약(피로 횟수 · 한 줄 피드백) |

**신뢰도 게이트:** **< 0.45 기록만 · 0.45 ~ 0.75 + α 제안 · ≥ 0.75 + α 자동 실행.**
α 는 사람마다 다른 문턱으로, 되돌리기 1회당 +0.05(최대 0.25)씩 오른다. 세션 중 되돌린 개입은 끝까지 묻고 실행한다.
비가역 동작은 자동 실행하지 않고, 자동 실행은 **60초 안에 되돌릴 수 있다.** 같은 명령은 300 s 쿨다운, 세션당 6회까지.

---

## 시스템 구성

```
              [ 센싱 ]                          [ 판단 ]                              [ 출력 ]

  ESP32 · Arduino C++ ─────┐
   VL53L8CX ToF 8×8 ×2     │  USB 시리얼               Raspberry Pi 4                   Raspberry Pi 5
   C1001 60 GHz mmWave     ├─ COBS + CRC-16 ──────►   AI Native OS Headless ─Ethernet─► AI Native OS Video
   SCD41 CO₂ · BH1750 조도 │  115200 · 유선            · native service (C++17)    MQTT  · Atlas Flutter 7" 터치 앱
   DHT22 온습도            │                          · ToF 자세 서비스 (CNN+GRU)  ◄──── · 제안 카드 · 수락 · 되돌리기
                           │                          · 18상태 FSM (Python)                │
  PC 수집기 ── Wi-Fi MQTT ─┘                          · 제어 · 개인화 · 밴딧               │ BLE · A2DP
   pynput · 키 타이밍만                               · Mosquitto 2.0.20                   ▼
                                                                                    iLink 무드등 · Pico 2 W 선풍기 · 스피커
                                                                                    LG ThinQ 공기청정기 (연동 예정)
```

| 계층 | 장치 · 런타임 | 맡은 일 | 디렉터리 |
|---|---|---|---|
| 센싱 | ESP32 · Arduino C++ (PlatformIO) | ToF ×2 · mmWave · 환경 3종 읽기, 1차 전처리, COBS 송신. ToF 센서당 5 Hz | [`firmware/esp32_sensor_node`](firmware/esp32_sensor_node) |
| 센싱 | PC · Python | 키 값 없는 입력 타이밍(dwell · flight · idle · correction) | [`collector/`](collector/) |
| 판단 | Raspberry Pi 4 · AI Native OS Headless | USB 디코더 · MQTT 3.1.1 클라이언트(C++), 자세 추론, 센서 융합, FSM, 개입 선택, MQTT 브로커 | [`hub/`](hub/) |
| 출력 | Raspberry Pi 5 · AI Native OS Video | Flutter 7" 터치 앱, 제안 카드, BLE 무드등 · 선풍기 · 스피커 실행 | [`display/`](display/) |
| 제어 기기 | Pico 2 W | BLE 선풍기(1 · 2 · 3단 · 자연풍 · 꺼짐 타이머) | [`firmware/pico2w_ble_fan`](firmware/pico2w_ble_fan) |
| 학습 | PC · PyTorch · ONNX · MuJoCo | 자세 모델 학습, MuJoCo 시뮬레이션, 공용 모델 | [`ml/`](ml/), [`dataset/`](dataset/) |

> 대회 가산점 항목인 **3종 HW 연동**(ESP32 + Pi 4 + Pi 5, Pi Zero 는 대회 측에서 Pi 5 로 대체 지급)과 **멀티모달**(신호 4묶음을 10 s 창에서 한 번에 판정)을 모두 갖췄다.
> Pi 5 가 꺼져도 Pi 4 의 판정과 제어 판단은 계속 돈다. MQTT retain 으로 재접속하면 상태가 즉시 동기화된다.

---

## 핵심 구현

### 1. 신호 4묶음을 10 s 창에서 한 번에

| 신호 | 10 s 창 표본 | 혼자서는 | 함께 보면 |
|---|---|---|---|
| **ToF** VL53L8CX ×2 | 50회 ×2 | 가림 · 고장 시 판정 불가 | 센서별 8×8 원본을 Pi 4 에만 보내 센서별 추론 후 확률 융합, 한쪽 고장 시 폴백 |
| **mmWave** C1001 | 10회 | 심박 순간값이 86 ~ 126 으로 흔들림 | 재실 · 체동 · 호흡 소실만 증거로 쓰고 상시 동작 |
| **키 입력** | 10회 | PC 밖 작업에서는 신호 없음 | PC 사용 비율로 맥락을 바꿔 자세 · 환경에 무게를 둔다 |
| **환경** SCD41 · BH1750 · DHT22 | 2회 | CO₂ 가 높아도 사람이 없으면 무의미 | 피로 원인을 환경으로 특정해 환기 · 조명 개입을 고른다 |

빠진 신호는 분자 · 분모에서 함께 빼고 가중치를 다시 나눈다. 센서 하나를 뽑아도 점수가 이어진다.

### 2. ToF 자세 판정 — 시뮬 학습, 보드 추론, 실측 보정

- **9클래스 자세:** 바른 자세 · 앞으로 숙임 · 뒤로 기댐 · 엎드림 · 턱 괴기 · 졸음 · 자리 비움 · 센서 위치 이상 · 거북목.
- 실측 라벨이 없어 **MuJoCo 가상 사람 · 가상 센서**로 학습하고(`dataset/tof_sim_v5`), Pi 4 에서 표준 C++ 서비스(`hub/atlas-tof`)로 센서별 CNN+GRU 를 돌린다.
- 모델은 **시뮬 점수가 아니라 실측 채점으로 고른다.** 17개 후보 중 실측 macro-F1 0.456 으로 1위인 모델을 썼다.
- 재학습 대신 보드에서 보정했다: 엎드림 단계 검출 규칙과 위치 이상 게이트로 **엎드림 단계 검출 100 %(오검출 0), 위치 이상 오판 76 → 0 %**(10-26 실측 리플레이).

### 3. 규칙 FSM — 18상태, 원인 라우팅

- **6계층 18상태**(대기 · 시작 · 몰입 · 판단 · 후속조치 · 출력), 화면에는 6 phase 로 축약. 10 s 마다 판정, tick 당 최대 1회 전이. 사양 [`docs/fsm-spec.md`](docs/fsm-spec.md).
- **피로 가중치(PC / 비PC):** 타이핑 0.35 / — · 자세 0.25 / 0.40 · 호흡 (꺼짐) 0.15 / 0.25 · 환경 0.10 / 0.20 · 경과 0.15 / 0.15.
- 피로 여부가 아니라 **"왜 피곤한가"** 를 고른다. 기여가 가장 큰 원인(환경 · 자세 · 인지) 하나에만 개입하고, 효과가 없으면 다음 원인으로 넘어간다.
- 바로 연결 규칙: 숙임 + 타이핑 멈춤 60 s → 자세, 고온 120 s → 환경. 조합 조건(guard) 층이 가중합과 병렬로 다신호 AND 를 본다.
- 가중치 · 임계값 · 타이머는 전부 [`hub/deskmate_hub/config/*.yaml`](hub/deskmate_hub/config). 실측 뒤 코드 수정 없이 값만 바꾼다.

### 4. 공용 모델과 보드 개인화

- **개인 기준선:** 중앙값 · MAD Modified z-score 로 첫 5분 안에 보정한다.
- **맥락 밴딧 개입 선택:** 시각 · 조도 · CO₂ · 졸음을 보고 원인별 후보(무드등 깨우기 · 조명 밝게/어둡게 · 음성 안내 · 알림음 · 휴식 제안 · 환기팬)에서 고른다. 수락 · 되돌리기 · 10분 뒤 효과가 다음 선택에 반영된다.
- **반응학습:** `c = c_FSM + clip(α · (p − c_FSM))`, 울타리 −0.05 ~ +0.25, α 는 라벨이 쌓일수록 0 → 0.8. 라벨은 KSS 응답 · 수락 · 거절 · 되돌리기 · 무응답 · 확인 카드.
- **공용 모델:** 17차원 × 최근 6 tick 을 받는 1D CNN(1,956 파라미터, 시뮬 정확도 0.78)과 공통 반응 모델(가상 24명 × 14일, 라벨 4,756). 공용 모델은 **관찰 전용**으로 FSM 결정을 바꾸지 않는다.
- 첫 설정의 빛 · 소리 · 바람 선호와 수면 시각을 정책 사전값으로 쓰고, `profile_id` 마다 따로 저장한다.

### 5. 명령이 기기에 닿기까지

| 기기 | 연결 | 동작 |
|---|---|---|
| iLink 무드등 | Pi 5 BLE GATT | 켜기 · 끄기 · 지금 대비 % 밝기(깨우기 +60 % · 환경 +30 %), 끊기면 다시 찾아 재연결 |
| DESKMATE 선풍기 (Pico 2 W) | Pi 5 BLE | 1 · 2 · 3단(듀티 30 · 45 · 60 %) · 자연풍 · 꺼짐 타이머 |
| 스피커 | Pi 5 A2DP | 알림음 3종 · 음성 안내 · 집중 음악 |
| LG 공기청정기 | ThinQ API | 풍량 제어와 공기질(PM1 · PM2.5 · PM10 · 냄새) 읽기를 실물로 확인. FSM 연동은 예정 |

- Pi 4 FSM → `control/cmd` → Pi 5 앱이 BLE 로 실행 → `control/result`(QoS 1, 15 s 안에 결과). 명령 · 실행 · 결과를 한 계약으로 묶었다.
- 결과에 `previous` · `actual` 이 있어 되돌리기는 이전 상태로 복원한다. 사용자가 직접 조작한 값이 자동 명령보다 우선한다.
- 연결 실패는 재시도 · 차례 · 조용히. 실패마다 시스템 팝업이 쌓여 화면이 멈추던 문제를 고쳐, 자동 연결은 처음과 끊긴 뒤 한 번만 한다.

### 6. Pi 5 터치 앱과 개발자 화면

- 저자극 집중 화면, 프로필 선택, 제안 카드(이유 · 실제 관측값 · 조절 대상 · 남은 시간), 자동 실행 알림과 되돌리기, 세션 리포트.
- 개발자 모드와 노트북 개발자 화면(`hub/atlas-tof/pc/devview`): 센서 원값 · 키 입력 · 추론 근거 · 제어 진행을 그대로 보여준다.

---

## 프라이버시 · 보안

- **카메라 · 마이크 없음.** 거리 격자 · 레이더 · 키 타이밍 · 환경만 융합한다.
- **키 값은 받지 않는다.** 타임스탬프 기반 집계만 쓴다.
- ToF 원본 8×8 은 Pi 4 밖으로 나가지 않는다(MQTT · 로그 · 화면 미전송).
- 판정과 제어는 기기 안에서 끝난다. 개인화는 동의 기반이고 삭제할 수 있다.
- 자격증명 · 개인 로그 · 자가기록 라벨 · 학습 모델은 저장소에 없다.

자세한 내용은 [`docs/security-privacy.md`](docs/security-privacy.md).

---

## 실행

### PC 에서 바로 보기 (보드 없이)

```bash
cd hub
python -m venv .venv && source .venv/bin/activate     # Linux 기준 (일부 테스트가 fcntl 을 쓴다)
pip install -r requirements.txt
python -m pytest tests -q
python -m deskmate_hub demo --host 0.0.0.0 --port 8765    # 합성 센서로 FSM 순환, http://127.0.0.1:8765/api/state
```

5분 시연 시나리오 리허설과 합성 센서 주입은 [`tools/README.md`](tools/README.md)(`demo_dryrun.py` · `mqtt_scenario_sim.py` · `rehearsal_local.py`).

### 실기 (ESP32 · Pi 4 · Pi 5)

1. **ESP32** — PlatformIO 로 `firmware/esp32_sensor_node` 를 굽는다. 배선은 [`docs/hardware.md`](docs/hardware.md) · [`docs/breadboard-esp32-node.md`](docs/breadboard-esp32-node.md).
2. **Pi 4** — hub IPK(`hub/atlas`)와 자세 서비스(`hub/atlas-tof`)를 설치한다. [`hub/atlas/README.md`](hub/atlas/README.md).
3. **Pi 5** — 개발 PC 의 Docker(Atlas SDK)에서 앱을 빌드 · 설치한다. [`display/atlas/README.md`](display/atlas/README.md).
4. **부팅할 때마다 Pi 4 에서 한 번** 브로커 · hub 를 켠다. 현재 ATLAS 이미지는 부팅 자동 시작을 지원하지 않는다.

```sh
/mnt/rawdata/deskmate_tof/ch340cfg auto 115200                    # ESP32 USB 시리얼 잡기 (꽂을 때마다)
sh /data/share/deskmate/recovery/install.sh pi4 --runtime-only   # 브로커 · hub 기동 + 감독(죽으면 재시작)
```

Pi 5 는 `sh /data/share/deskmate/recovery/install.sh pi5 --runtime-only`. 전체 절차와 문제 해결은 [`docs/reboot-recovery.md`](docs/reboot-recovery.md).

> **주의:** ATLAS 설정 앱에서 무드등 · 선풍기를 페어링하면 시스템이 링크를 잡아 앱 연결이 거절된다. 연결은 DESKMATE 앱에서만 한다.

---

## 실기 · 시험 결과

| 항목 | 결과 |
|---|---|
| ESP32 → Pi 4 USB 시리얼 | 18.6 프레임/s, CRC 오류 0 |
| ToF 수신 | 5 Hz 실기 수신, 프레임 손실 0 |
| 수락 → 무드등 실행 | 0.4 s |
| 브로커 재시작 뒤 앱 수신 | 재구독 · 상태 감시로 3/3 수신 |
| ToF 자세 (실측 리플레이, 10-26) | 엎드림 단계 검출 100 % · 오검출 0, 위치 이상 오판 76 → 0 %, 실측 macro-F1 0.456 |
| 자동 테스트 (10-26 보드 배포판 기준) | hub pytest 604 · Flutter 572 · 펌웨어 unity 61 · ToF 로거 · 시뮬 32 |

시뮬 수치는 실측이 아니다. 개입 효과(환기 · 조명 · 휴식 · 자세 안내)는 선행 연구 근거이며, DESKMATE 실사용 측정은 결선 이후 진행한다.
출처와 조건은 [`docs/sim-results/`](docs/sim-results) · [`docs/ondevice-field-validation.md`](docs/ondevice-field-validation.md) 에 있다.

---

## 다음 버전

- **결선 직후:** 실착석 자세 세션으로 모델 보정, 개입 효과 실측, Pi 4 2시간 연속 구동.
- **메인 센서 고해상도화:** L8 8×8(64칸) → L9 54×42(2,268칸, 약 35배). 학습 도구는 이미 L9 격자를 지원한다.
- **가전을 입력으로:** ThinQ 공기청정기 공기질과 Wi-Fi CSI 를 더해 방 전체를 본다.
- **클라우드는 사용자가 켤 때만:** 원격 리포트 · 공용 모델 갱신. 판정과 제어는 지금처럼 기기 안에서 끝낸다.

---

## 디렉터리 구조

```
├── firmware/
│   ├── esp32_sensor_node/   ESP32 센서 노드 (ToF ×2 · mmWave · 환경, COBS 송신)
│   └── pico2w_ble_fan/      Pico 2 W BLE 선풍기 펌웨어
├── collector/               PC 키 타이밍 수집기
├── hub/                     Raspberry Pi 4 허브
│   ├── atlas/                   ATLAS native service IPK (C++ 디코더 · MQTT 클라이언트)
│   ├── atlas-tof/               ToF 자세 서비스(C++) · UART 중계기 · 노트북 개발자 화면(pc/devview)
│   ├── mqtt/                    Mosquitto 설정
│   └── deskmate_hub/
│       ├── ingest/              USB · MQTT → 10 s SensorFrame
│       ├── features/            개인 기준선 정규화
│       ├── inference/           18상태 FSM · 신뢰도 게이트 · guard
│       ├── personalize/ learning/ personalization/   밴딧 · 반응학습 · 온디바이스 학습
│       ├── fatigue/             공용 피로 모델 (KSS)
│       ├── control/             제어 디스패처 · 기기 어댑터
│       └── config/              가중치 · 임계값 · 타이머 (YAML)
├── display/atlas/           Pi 5 Flutter 앱 · 피로 기록 앱 · 배포 스크립트
├── ml/                      공용 모델 학습 · 변환
├── dataset/                 MuJoCo ToF 시뮬 · 실측 기록 도구
├── tools/                   시나리오 시뮬 · 리허설 · 센서 모니터 · 복구 설치기 · ThinQ
└── docs/                    명세 · 하드웨어 · 시험 기록
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
| [`docs/personalization-spec.md`](docs/personalization-spec.md) · [`docs/ondevice-personalization.md`](docs/ondevice-personalization.md) | 개인화 · 온디바이스 학습 |
| [`docs/demo-scenario.md`](docs/demo-scenario.md) | 시연 동선 |
| [`docs/security-privacy.md`](docs/security-privacy.md) | 보안 · 프라이버시 |

---

## 팀 TEAMMATE — 서울시립대학교 전자전기컴퓨터공학부

| 구분 | 성명 | 개발 담당 | 주요 디렉터리 | 기타 업무 |
|---|---|---|---|---|
| 팀장 | 김태환 | 총괄 · 일정, 센서 인터페이싱, ToF 전처리 · 재실/자세 판정, 캘리브레이션, HW 구조 · 핀맵, mmWave 테스트 | `firmware` · `hub/features` · `dataset/tof_sim_v5` · `hub/atlas-tof` | 발표 |
| 팀원 | 박소연 | 규칙 기반 FSM 추론 엔진, 신뢰도 공식 · 게이트, 작업 리포트, 키스트로크 파이프라인, 개인화 | `hub/inference` · `ml` · `dataset/tof_sim` | 개발 사진 · 영상 |
| 팀원 | 조명희 | 경량 분류기 · 온디바이스 최적화, ThinQ 연동, ESM 라벨 체계, FSM 구조 설계, 요구사항 · 데이터 명세 | `ml` · `hub/control` · `docs` | 저장소 관리 |
| 팀원 | 이민혁 | ESP32 펌웨어 · 1차 전처리, MQTT 브로커 · 토픽, 로깅 · 시간 동기화, ESP–Pi 통신, CAD · 하우징, 브랜딩 | `firmware` · `hub/ingest` | 노션 정리 |
| 팀원 | 최민경 | 디스플레이 UI, ThinQ 연동, 피드백 처리 · 리포트 시각화, IPK · D-Bus 조사, 보조 센서 파이프라인 | `display` · `collector` | 재료 관리 |

## 개발 일정

| 날짜 | 마일스톤 |
|---|---|
| 07-20 | 예선 합격 |
| 07-24 | 설계 문서 · FSM 1차, 첫 PR 머지 |
| 07-30 | 1차 기술교육, Pi Zero 대신 Pi 5 수령 |
| 08-27 | 요구사항 명세 확정, mmWave 심박 첫 테스트 |
| 09-14 | 판정 주기 10 s 확정, 7" 디스플레이 교체 |
| 09-16 | ESP32 → Pi 4 첫 실수신, CRC 오류 0 |
| 09-18 | Pi 4 에 브로커 탑재, C++ MQTT 로 PC 없이 동작 |
| 10-01 | 통합 리허설 통과 (합성 센서로 전 과정 시연) |
| 10-12 | L9 보드 폴트 → L8 8×8 두 개로 전환 결정 |
| 10-16 | L8 두 개 실물 연결 (LPn 핀으로 주소 분리) |
| 10-19 | 첫 실측 녹화, 실측으로 모델 선택 |
| 10-26 | 보드 배포판 완성, 위치 이상 오판 76 → 0 % |
| 10-28 | 결선 서류 제출 (보고서 · 소개서 · 영상) |
| 11-06 | 발표 심사 |

## 라이선스

[MIT](LICENSE). 앱 음원의 출처는 각 앱의 `assets/audio/CREDITS.md` 에 있다.
