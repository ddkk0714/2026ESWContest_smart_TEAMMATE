# collector — PC 키스트로크 타이밍 수집기

담당: 박소연 *(역할 조정: 기존 최민경 → 박소연. 상위 문서 `CLAUDE.md`/`README.md` 표는 팀 확인 후 갱신)*

키보드 작업 중에만 가용한 **정밀 신호**를 수집한다. 노트북(Windows)에서 돌며
특징 벡터를 계산해 MQTT 로 발행하고, Raspberry Pi 4(hub)가 구독한다.

## 프라이버시 원칙 (타협 불가)

- **키 값을 절대 수집하지 않는다.** 입력 타임스탬프만 추출한다.
- 저장·전송하는 것은 통계 특징뿐이다. 원시 타임스탬프 시퀀스도 로컬에만 둔다.
- 마우스도 좌표·클릭 버튼·스크롤 내용을 보지 않고 **활동량만** 센다.
- 이 원칙이 본 작품의 "비침습 프라이빗 센싱" 차별점의 근거다. 코드 리뷰에서 특히 엄격히 본다.

## 추출 특징

| 특징 | 설명 |
|---|---|
| dwell time | 키 누름 유지 시간 (평균·표준편차) |
| flight time | 키 간격 (평균·표준편차) |
| idle ratio | 입력 공백 비율 |
| correction rate | 백스페이스 빈도 |

피로 시 리듬이 느려지고 불규칙해지며 정정이 느는 경향을 포착한다.
`auto-repeat`(키 꾹 누름)는 한 번의 타건으로 취급해 통계 왜곡을 막는다.

### additive 신호 (2026-09-14 **구현 기준으로 계약 확정**, 세부 튜닝은 이후)
`typing_active` / `mouse_active` / `input_active` / `flight_cv` / `mouse_event_rate`.
`input_active` 는 **"마우스 작업(입력 O) vs 글 읽기(입력 전무)"** 를 PC 단에서 구분해
hub 가 비타이핑 작업과 유휴를 가르는 데 쓴다.

## 발행 (계약 — 2026-09-14 구현 기준 확정)

`deskmate/sensor/keystroke` 로 **1 Hz, 60 초 슬라이딩 윈도우** 통계 (QoS 0).
생존 신호는 `deskmate/health/<node>` (LWT online/offline, retain). 스키마는 [`docs/data-spec.md`](../docs/data-spec.md) §6.4 와
[`docs/mqtt-topics.md`](../docs/mqtt-topics.md) 가 이 구현을 따른다.

- 키 간격 > 2 s 는 리듬 통계(flight)에서 제외, ≥ 3 s 는 idle 로 집계 (`flight_gap_max_s`, `idle_gap_s`)
- **미입력 구간**: 윈도우에 keydown 이 없으면 `typing_active=false`, `idle_ratio=1.0`, 통계는 0. hub 는 `typing_active=false` 를 키스트로크 신호 **미가용**으로 보고 가중치를 재정규화한다. `input_active` 는 마우스만 있어도 true.
- 특징값은 공통 envelope(`schema_version`·`ts`·`node`·`boot_id`·`seq`)의 `data` 에 담아 보낸다. hub ingest 는 평면·envelope 둘 다 받는다.

```json
{
  "schema_version": "1.0", "ts": 1769000001.0,
  "node": "pc-collector", "boot_id": "7f2a91c4", "seq": 1042,
  "data": {
    "node": "pc-collector", "window_s": 60,
    "dwell_mean_ms": 92.4, "dwell_std_ms": 21.8,
    "flight_mean_ms": 148.2, "flight_std_ms": 63.5,
    "idle_ratio": 0.18, "correction_rate": 0.07,
    "typing_active": true, "mouse_active": true, "input_active": true,
    "flight_cv": 0.42, "mouse_event_rate": 96.0
  }
}
```

## 개발 환경 준비

저장소 루트에서 Python 3.10 이상으로 가상환경을 만들고 의존성을 설치한다.

```bash
python -m venv .venv
python -m pip install -r collector/requirements.txt
python -m pytest collector/tests -q
```

가상환경 활성화 명령은 OS에 따라 다르다.

- Windows PowerShell: `.venv\Scripts\Activate.ps1`
- Linux/macOS: `source .venv/bin/activate`

## 실행

저장소 루트에서 다음과 같이 실행한다.

```bash
# Pi4(hub) 연동
python -m collector --broker <pi4-ip>

# 노트북 단독(브로커 없이 로컬 로깅으로 검증). 저장소 루트에서 실행
python -m collector

# 테스트 (실제 키 캡처 없이 로직 검증)
pytest collector/tests -v
```

### 실기(핫스팟) 연동 — 수험생·대학생 개인화 확인 때

노트북을 Pi 4 와 같은 휴대폰 핫스팟에 붙인 뒤 저장소 루트에서:

```bash
python -m collector --target hotspot    # = --broker <PI4_HOST> (Pi 4 wlan0)
python -m collector --target lan        # = --broker <PI4_HOST> (연구실 LAN · Pi 5 직결)
```

- 켜면 `[mqtt] connected → <PI4_HOST>:1883` 이 뜨고 1초마다 `[PUB]` 줄이 나온다. 핫스팟이 끊기면 `disconnected (자동 재연결)` 뒤
  1→30 s 백오프로 다시 붙는다. 그동안 값은 `logs/keystroke.jsonl` 에만 남는다.
- Pi 4 에서 수신 확인: `ssh pi4-via-pi5 '/data/share/deskmate/bin/mosquitto_sub -h 127.0.0.1 -t deskmate/sensor/keystroke -v'`
- hub 반영 확인: `deskmate/state/phase` 의 `sensor_summary.keystroke` 가 이 값이고, 타이핑하면 `context` 가 `npc` → `mixed`/`pc` 로
  간다(demo 프로파일은 `pc_ratio_window_sec` 120 s, 기본 900 s 창의 입력 비율).
- 대학생 세션은 타이핑 중 켜 둔다. 수험생(책·필기) 세션도 켜 두면 입력이 없어 `context: npc` 로 잡히는지 함께 확인된다.
- Pi 5 개발자 앱 키보드 탭(PR #37·#43)에서도 보인다. 그 탭은 표본 `ts`(노트북 시계)와 Pi 5 시계 차이로 5 s 넘으면 '수신 끊김'이라
  두 시계가 맞아야 한다(10-26 실측: Pi 5 가 노트북보다 88 s 늦음 — 늦은 쪽이면 통과, Pi 5 가 앞서면 끊김으로 보인다).
- 보내는 것은 타이밍 통계와 keydown 개수뿐이다. 어떤 키인지는 어디에도 남지 않는다.

### Pi 4 자동 찾기·로그인 때 자동 시작 (11-02)

```bash
python -m collector --broker auto       # 같은 Wi-Fi 에서 DESKMATE hub 브로커를 스스로 찾는다
```

- 찾는 순서: 지난번 찾은 주소(`%LOCALAPPDATA%\DESKMATE\collector_broker.json`) → `--target` 별칭 주소 → 노트북 IPv4 마다 같은 /24 전체.
  1883 포트가 열린 기기 중 hub 의 retain 토픽(`deskmate/health/hub`·`deskmate/state/phase`)이 실제로 오는 브로커만 고른다
  (핫스팟 공유기 등은 걸러진다). 11-02 핫스팟 실측: 힌트 없이 Pi 4 `<PI4_HOST>` 을 4.5 s 에 찾음.
- 처음엔 찾을 때까지 15 s 마다 다시 찾고, 붙은 뒤엔 30 s 넘게 끊기면(핫스팟·장소가 바뀐 경우) 다시 찾는다. 그 사이 값은
  `logs/keystroke.jsonl` 에만 남는다.
- 로그인 때 창 없이 자동으로 켜기(Windows 작업 스케줄러, 현재 사용자):

```powershell
powershell -ExecutionPolicy Bypass -File collector\autostart.ps1 -Python <venv>\Scripts\pythonw.exe   # 등록 + 지금 켜기
powershell -ExecutionPolicy Bypass -File collector\autostart.ps1 -Uninstall                           # 해제 + 끄기
```

  작업 이름은 "DESKMATE keystroke collector". `-Python` 을 생략하면 `<저장소>\.venv\Scripts\pythonw.exe` 를 쓴다.

옵션: `--target {hotspot,lan}`, `--broker <ip|auto>`, `--port`, `--node`, `--window`, `--period`. 환경변수(`DESKMATE_BROKER` 등)로도 지정 가능.
브로커를 생략하면 `localhost:1883`을 사용하며, 연결되지 않아도 `logs/keystroke.jsonl`에
동일한 MQTT envelope를 기록한다. `logs/`는 수집 데이터이므로 Git에 포함하지 않는다.

## 주의

- OS별 키 훅 권한이 필요하다. Linux는 데스크톱 세션 및 input 권한, macOS는 손쉬운 사용
  권한, Windows는 대상 앱의 권한 수준에 따라 관리자 실행이 필요할 수 있다.
- 전역 키/마우스 후킹은 백신·SmartScreen 이 키로거로 의심할 수 있어 데모 PC 에서 예외 처리.

## 구조

```
collector/
├── __main__.py     python -m collector 엔트리 (argparse)
├── config.py       기본값 ← 환경변수 ← CLI 인자
├── capture.py      pynput 키보드+마우스 → 이벤트(시간·종류만)
├── features.py     슬라이딩 윈도 → 특징 → 규약 페이로드(to_payload)
├── publisher.py    공통 envelope 래핑 + paho-mqtt 발행 + 미연결 시 로컬 폴백 + health(LWT)
└── tests/          합성 이벤트 단위 테스트
```

### 노트북 HTML에서 키보드 캡처 선택

상단 버튼은 실제 키보드 리스너를 중지/새로 시작한다. 마우스 리스너는 유지한다.
전환마다 키 버퍼와 눌림 상태를 비우고 리스너 세대를 바꿔 이전 콜백의 입력 혼입을 막는다.
수집기가 없거나 heartbeat가 5초 넘게 오래되면 미연결로 표시하며 자동 실행하지 않는다.

현재 사용자 전용 `%LOCALAPPDATA%\DESKMATE\capture-control\`의 요청/상태 파일을 사용한다.
파일에는 ON/OFF·프로세스 생존·ACK만 있고 키 이벤트·PAT·ThinQ 식별자는 없다.
수집기는 사용자별 파일 잠금으로 중복 실행을 거부한다. 자동 시작 작업은 임의 변경하지 않는다.
기존 버전 수집기에는 이 제어 인터페이스가 없으므로 미연결로 표시한다.

Windows PowerShell에서 해당 worktree로 이동한 후 기존 의존성이 있는 Python으로 실행:

```powershell
# 실제 MQTT를 쓰지 않는 로컬 캡처/HTML 시험
python -X utf8 -m collector --local-only
# Pi와 연결한 후 사용할 기존 MQTT 실행 경로 (위 수집기를 먼저 종료)
python -X utf8 -m collector --broker auto
```

`--local-only`는 키/마우스 캡처만 시험하며 MQTT 탐색·발행과 특징 로그 저장을 하지 않는다.
OFF 선택은 재실행 후에도 유지한다. 실제 MQTT 모드에서는 동일 Envelope/주기에
`valid:false`, `keyboard_capture_enabled:false`, `capture_status:user_disabled`를 보내며
키보드 수치를 0으로 만들지 않는다. 마우스 활동은 별도로 유지한다.
단, 기존 Hub의 과거 윈도우 이력은 소급 삭제하지 않는다. 운영 FSM·개인화 코드는 변경하지 않는다.
