# 노트북 개발자 화면 (`devview`)

결선 시연영상에서 **내부 상태를 보여 주는 화면**이자, 실기 중에 보드의 원값을 실시간으로 보는 개발자 화면이다.
Pi 5 앱의 개발자 모드와 같은 정보에 센서 원값·엎드림(face_down) 신뢰도 추적을 더해 노트북 브라우저 한 페이지(1920×1080)에 띄운다.

- **보기 전용(예외 둘).** 판단(FSM·개인화)과 제어는 전부 Pi 4·Pi 5 보드에서 한다. 이 프로그램은 Pi 4 브로커를
  구독하고 자세 서비스에 GET 만 한다. 예외로 **가상 기기 화면에서 사람이 누를 때만** 두 가지를 보낸다(10-26 사용자 요청):
  첫 설정 프로필 `deskmate/profile/user`(retain)와 카드 응답 `deskmate/feedback/user`. 제어 명령(`control/cmd`)은 보내지 않는다.
  `--read-only` 로 켜면 이 둘도 끈다(녹화만 할 때).
- **원시 센서 값은 화면에만.** 받은 값은 메모리에만 두고 `127.0.0.1` 로만 연다. 파일로 쓰지 않는다
  (녹화는 사람이 화면 캡처로 한다).
- 표준 라이브러리만 쓴다(Python 3.10+). `pip install` 이 필요 없고 외부 CDN 도 없다(오프라인 동작).
- **시스템 프록시를 쓰지 않는다.** 노트북 프록시 설정이 172.20.x(핫스팟) HTTP 요청을 프록시로 보내 시간 초과가 날 수 있어,
  자세 서비스 GET 은 빈 `ProxyHandler` 로 직접 붙는다(MQTT 는 소켓이라 원래 프록시와 무관).

## 실행

```bash
python hub/atlas-tof/pc/devview/devview.py --pi4 172.20.10.3
```

브라우저로 <http://127.0.0.1:8790/> 를 연다. 보드에 못 붙으면 위에 빨간 띠로
`연결 안 됨: MQTT 브로커 :1883 172.20.10.3:1883 — 시간 초과(…)` 처럼 이유가 뜨고, 뒤에서 계속 다시 시도한다.

보드 없이(녹화 연습·화면 확인):

```bash
python hub/atlas-tof/pc/devview/devview.py --fake
```

`--fake` 는 40 초 각본을 반복한다: 0 s START → 6 s FOCUS_PC → 16 s FATIGUE_SUSPECT → 22 s FATIGUE →
27 s ACTION_POSTURE(자세 졸음, 무드등 `adjust_brightness +60%` 명령) → 28 s 결과 `110→176` → 32 s 엎드림
(face_down 0.90 → 37 s 0.995) → 33 s 휴식 제안 → 35 s 수락. 두 ToF 격자도 움직이고, `/raw other`(0x20·0x10·0x03·0x04·0xF0)·
키 입력 토픽·프로필도 가짜로 낸다. `--fake-start 26` 처럼 원하는 장면 직전부터 시작할 수 있다.
가짜 데이터일 땐 띠에 노란 `가짜 데이터(--fake)` 표시가 붙는다 — 시연 녹화에서 이 표시가 보이면 안 된다.

| 옵션 | 기본 | 뜻 |
|---|---|---|
| `--pi4` | `172.20.10.3` | Pi 4 주소(핫스팟 So) |
| `--mqtt-port` | 1883 | Pi 4 Mosquitto |
| `--port` | 8790 | 이 화면의 로컬 포트 |
| `--hub-profile NAME` | | hub 시연 프로파일 이름(예: `demo-b`). 보드가 발행하지 않으므로 프로필 줄에 `(노트북 지정)` 으로 표시 |
| `--read-only` | | 프로필·카드 응답 보내기도 끈다 |
| `--fake` / `--fake-start S` | | 가짜 각본 / S 초부터(보내기는 브로커 대신 화면 안으로만) |

## 가져오는 것

| 출처 | 주기 | 화면 |
|---|---|---|
| MQTT `deskmate/state/phase` | hub 10 s | 판정(FSM 상태·C_fatigue·C_focus·원인·게이트·hub 자세·재실·장치 위치·개인화 설명·제어 에피소드) |
| MQTT `deskmate/control/cmd`·`control/result` | 이벤트 | 기기 한 줄(무드등·선풍기·공기청정기의 마지막 명령·BLE 결과와 결과를 낸 노드, `previous→actual` 밝기) + 명령 로그 |
| MQTT `deskmate/interaction/request`·`feedback/user` | 이벤트 | 명령 로그(REQUEST·FEEDBACK) |
| MQTT `deskmate/health/hub`·`deskmate/profile/user` | retain | 프로필 줄(hub 시연 프로파일 demo-a/b 가 실려 있으면 · 개인화 `active_profile_id` · 첫 설정 `profile_id`) |
| MQTT `deskmate/#` 전부 | — | MQTT 토픽 목록(토픽별 개수·마지막 수신) — 실시간으로 들어오는지 확인 |
| `GET :8771/api/state` | 0.3 s | 자세 서비스 라벨·신뢰도·9 클래스 확률·장치 위치(`placement`, 없으면 '-'), 엎드림 추적 |
| `GET :8772/raw` (없으면 `:8771/raw`) | 0.2 s | ToF 8×8 열지도 — **화면 왼쪽 B·오른쪽, 오른쪽 A·왼쪽**(격자가 센서가 보는 사진 방향이라 사용자 왼쪽 센서 A 가 화면 오른쪽, Pi 5 앱 PR #83 과 같음. 이름표는 사용자 기준)(칸 숫자 cm, 회색 = 무효 상태값), 유효 칸 수·최소 거리, `other` 블록 |

### 자세 서비스 칸 — 보정 상태와 hub 하한

- 라벨 옆 배지: 서비스 라벨은 하한 없는 평활 argmax 라, hub 하한(demo-b bd47052: 졸음 0.80 · 엎드림 0.99, `--hub-floor`) 아래면
  **hub 무시 (0.54 < 0.80)**, 넘으면 **hub 셈 (≥ 0.99)**. 하한이 없는 라벨은 배지 없음.
- 막대는 서비스가 라벨로 쓰는 평활 확률(`probs`, 게이트·규칙 뒤). 그 아래 **모델 원 출력**(`model_output`, 게이트·규칙 전)과
  e415d23 서비스의 보정 상태: 엎드림 규칙(d) 켜짐/꺼짐(share·up·lo·dlo·dup) · 앉음→바른자세 적용 · 위치 재조정 게이트(모드·베이스라인·
  장치 움직임·모델 misplaced → 막음) · 입력 radial·fill. 옛 서비스면 이 줄은 안 보인다.

### 센서 원값 패널

섹션마다 원값 출처를 하나 고른다: **`/raw` 의 `other`(ESP32 UART 프레임 그대로) → MQTT `deskmate/sensor/*` → hub 요약(`state/phase` 의 `sensor_summary`)**.
섹션 머리에 출처·seq·개수·나이가 나온다(15 s 넘게 안 바뀌면 빨강).

| 섹션 | `/raw other` 키 | MQTT 토픽 | hub 요약 |
|---|---|---|---|
| mmWave C1001 (0x20) | `mmwave` | `deskmate/sensor/mmwave/*` | `sensor_summary.mmwave` |
| 환경 CO₂·온도·습도·조도 (0x10) | `env` | `deskmate/sensor/env/*` | `co2_ppm`·`temp_c`·`humidity_pct`·`lux`·`env_flags` |
| ToF 노드 규칙 특징 (0x03, 플래그·움직임·끄덕임) | `tof_feature` | `deskmate/sensor/tof/*` | `sensor_summary.tof` |
| ToF 노드 자세 (0x04) | `tof_posture` | — | — |
| ESP32 하트비트 (0xF0) | `heartbeat` | `deskmate/health/esp32` | — |
| 키 입력 (PC 수집기) | — | `deskmate/sensor/keystroke` | `sensor_summary.keystroke` |

보드 hub 는 UART 로 받은 센서를 MQTT 로 다시 내보내지 않는다(`deskmate/sensor/#` 는 구독만). 그래서 `other` 블록을 주는
UART 중계기(`relay/uart_relay_board`)의 `/raw` 가 가장 원본에 가깝고, 자세 서비스 `/raw`(PR #72) 처럼 `other` 가 없으면
hub 요약으로 내려간다 — 0x04·하트비트는 그때 '출처 없음'.

10-26 실기(hub c3441f4, demo-b)에서 실제로 받은 것: MQTT 는 `state/phase`·`session/report`·`health/hub`·`health/pc-collector`·
`sensor/keystroke` 뿐(`deskmate/sensor/#` 에는 keystroke 만). `:8772/raw` 는 자세 서비스(`other` 없음). 그래서 mmWave·환경·ToF 규칙
특징은 `sensor_summary`(+ `tof_service`·`co2_window`) 에서, 키 입력은 MQTT 원값에서 나오고, 0x04·하트비트는 볼 수 없다.
hub 시연 프로파일은 `state/phase`·`health/hub`·`:8765/health` 어디에도 실리지 않는다 → `--hub-profile`.

### 엎드림 face_down 칸

실기에서 face_down 확률이 0.99 에 닿는지 보려는 칸. `/api/state` 를 0.3 s 마다 읽어
**smooth**(`probs`, EMA — 서비스가 라벨로 쓰는 값)·**instant**(`instant.probs`)·**센서 A·B**(`sensors[].probs`)의 지금 값과 최고값,
0.99 이상 표본 수·마지막 도달 시각, 최근 60 s 막대(0.80~1.00 눈금, 0.99 점선)를 보여 준다. 0.99 이상이면 칸 테두리가 초록.
최고값은 프로그램을 켠 뒤부터(다시 켜면 비워진다). 0.3 s 표본이라 그 사이의 순간 최고는 놓칠 수 있다.

- MQTT 페이로드는 **UTF-8** 로 읽는다(한글 설명). Latin-1 로 읽으면 깨진다(Pi 5 앱에서 실제로 있었던 버그).
- 명령 로그는 받은 순서대로 최근 200 줄. 줄 모양은 Pi 5 `command_log.dart` 와 같다
  (`CMD desk_lamp adjust_brightness +60% (auto, id 9f3c1a) posture` / `RESULT 9f3c1a succeeded 110→176 (display-control)`).
  CMD 파랑 · RESULT 성공 초록/실패 빨강 · REQUEST·FEEDBACK 노랑. 시각은 노트북 수신 시각.
- 값이 없는 칸은 `-`. 심박·호흡은 `*_valid=false` 면 `(무효)` 를 붙인다(락온 풀림 ≠ 사람 없음).
- 열지도 색 눈금은 `pc/posture_view.html`·Pi 5 `tof_heatmap.dart` 와 같다(20 cm 붉음 → 2 m 어둠). 배열은 펌웨어가 방향을 적용한 그대로 그린다(뒤집지 않음).

## 가상 기기 (`/?mode=virtual`) — "기기가 연결돼 있었다면"

오른쪽 위 **가상 기기** 단추, `G` 키, 또는 <http://127.0.0.1:8790/?mode=virtual>. 주변기기(무드등·선풍기·공기청정기·창문 환기·스피커)가
전부 연결되고 Pi 5 앱의 '상태 기반 조명·음악 피드백' 스위치가 켜져 있다고 보고, hub 가 보낸 것을 **Pi 5 앱과 같은 규칙**으로 가상 기기에 적용한다.
실제 Pi 5 가 돌려준 결과(예: `failed feedback_disabled`)는 타임라인 오른쪽 끝에 따로 적는다 — 화면 전체에 '가상' 표시.

| 기기 | 규칙(출처) |
|---|---|
| iLink 무드등 | 단계 조명 start 48 · focus 110 · fatigue 180 · recovery 90 · idle/end 끔, 피로 국면은 ACTION_* 자동이거나 제안 수락 뒤에만(`feedback_policy.dart` ilinkFramesForPhase·apply·accept). `adjust_brightness {percent, min_raw}` = 지금 밝기(꺼짐·모름이면 하한 30) × (1 + p/100), 하한 30·상한 255, 올리는데 같으면 +10, min_raw 까지(`adjustBrightness`, 앱 테스트 15개를 그대로 골든으로). previous = 꺼짐이면 0, 모르면 없음 |
| Pico 2 W 선풍기 | `set_power on/off · set_level 0~3 · set_speed 0~100 · set_mode normal/natural · set_timer` 만 실행(`fan_control.dart` fanFrame) |
| LG 공기청정기 | `set_mode off/auto/low/mid/high`(hub control.yaml) |
| 창문 환기 | `vent_suggest`(또는 arm `ventilate_suggest`) 카드를 수락하면 '창문 열기 N분' |
| 스피커·음성 | 피로 국면 조명과 함께 음악 재생, recovery/idle/end 에서 정지, 피로 국면 무드등 명령도 음악(앱 규칙). arm `posture_voice` 카드 = 음성 안내, `sound_cue` = 알림음 |

명령마다 만료(`expires_ts_ms`)·게이트(auto/suggest/undo)·`requires_confirmation` 을 앱처럼 검사하고, 같은 `command_id` 는 한 번만 실행한다.
타임라인 한 줄: `자세 개입(자세 원인) → iLink 무드등 밝기 +60 % (최소 153) (110→176)` + FSM 상태·원인·자동/제안·개인화 arm·
decision_source·이유(개인화 설명) + 실제 Pi 5 결과. 이 화면을 켠 뒤부터 쌓인다(그 전 밝기는 '모름').

### 노트북을 대역 기기로 (`--act-laptop`, 끈 게 기본)

블루투스 기기가 없을 때 시연용으로 이 Windows 노트북이 대신 움직인다. **켤 때만** 동작하고 끝내면(Ctrl+C) 원래 값으로 되돌린다.

```bash
python hub/atlas-tof/pc/devview/devview.py --pi4 172.20.10.3 --hub-profile demo-b --hub-config <보드 hub 의 config> --act-laptop
```

- **무드등 → 화면 밝기:** 가상 무드등(Pi 5 앱 규칙 — 단계 조명·adjust_brightness 하한 30·min_raw)의 원시 밝기 0~255 를 % 로(176 → 69 %).
  끔은 까만 화면 대신 10 %. 깜빡임 방지로 3 초에 한 번까지(사이 값은 마지막 것만). WMI `WmiSetBrightness`(인라인 PowerShell, 실행 정책 그대로).
- **스피커·음성 안내·알림음 → 볼륨·짧은 소리:** hub 설정에서 소리를 뜻하는 것 — control.yaml 에는 스피커 대상이 없고, Pi 5 앱이 피로 국면에
  음악을 틀며(feedback_policy), 개인화 arm `posture_voice`(voice)·`sound_cue`(sound). 음악 재생·음성·알림음이면 볼륨을 첫 설정 `sound_pref`
  (V18 구간 가운데: low 20 % · mid 50 % · high 80 %, 없으면 V18 기본 30 %, off 면 올리지 않음)로 올리고 짧은 소리(winsound),
  음악 정지면 원래 볼륨. 볼륨은 Windows Core Audio 를 ctypes 로 직접(pycaw 불필요).
- **control/result 는 보내지 않는다.** Pi 5 앱이 같은 command_id 로 먼저 답하고(지금 `failed feedback_disabled`), hub 디스패처는 첫 결과로
  명령을 끝낸 뒤 늦은 결과의 previous·actual 만 보충한다 — 노트북이 succeeded 를 보내면 실패한 명령에 노트북 숫자가 섞여 되돌리기 기준이 틀어진다.
- **사용자가 직접 바꾼 밝기**는 5 초마다 읽어 타임라인에 "사용자(또는 Windows)가 노트북 화면 밝기를 바꿈 69 → 40 % — hub 에 보고하지 않음"
  으로만 적는다(자동 밝기·전원 설정이 바꾼 것과 구별할 수 없어 manual_adjust 로 보내지 않음).
- 화면 '가상 기기' 위에 상태(원래·지금 밝기·볼륨), 타임라인에 "노트북 화면 밝기 43 % → 69 % (무드등 …)".
- **깨우기 = 노트북에서 틀어 둔 음악을 키운다** (블루투스 스피커 대신, `--wake-volume 80` 기본 · `--wake-max-min 5`):
  - 깨우기로 보는 것: 자동 자세 개입(state/phase ACTION_POSTURE 의 음악, control/cmd 무드등 `adjust_brightness`·`set_brightness`
    origin ACTION_POSTURE 또는 cause posture), 자세·휴식(origin REST)에서 온 `speak`·`chime`.
  - 노트북 음량을 **max(지금, 80 %)** 로 올린다. 첫 설정 소리 '작게'여도 올린다('끔'이면 안 올림). 타임라인 "깨우기: 노트북 음량 27 % → 80 % (자세 개입)".
  - 깨우는 동안 다시 와도 쌓지 않는다(올리기 전 음량은 처음 것, 시한만 5분 연장). 첫 설정 음량으로 내리지도 않는다.
  - 음악 위에 winsound 소리를 얹지 않는다("깨우는 중 — 알림음 생략"). 말하기(TTS)는 그대로 하고, TTS 가 실패해도 삑 소리는 생략.
  - FSM 이 회복·집중·대기·종료로 가거나(깨우는 동안 다른 상태를 한 번 본 뒤) 5분이 지나면 올리기 전 음량으로 —
    "깨우기 끝: 노트북 음량 80 % → 27 % (회복)". 깨우기가 아닌 알림음은 지금 음량으로 울린다. 밝기는 그대로, 끝낼 때 둘 다 원래 값으로.
  - `--wake-volume 0` 이면 예전처럼 첫 설정 소리 구간대로.

### 휴식(REST) 알림

hub 가 REST 에 들어가면 가상 기기 화면 위와 영상 모드에 큰 배너: 문구 · 남은 휴식 시간(카운트다운) · 이유.
- hub(f91afd8): `interaction/request` kind `rest_notice` — `text`(문구) · `minutes`(없으면 `rest_min_sec`) · `reason`
  (accepted 수락 · asleep 졸아서 바로 · escalated 효과 없어 격상 · reminder 다시 알림) · `variant`(default|study) · `speak` → 라벨 "hub 휴식 알림".
  예전 이름(message·rest_sec·duration_s·why)도 받는다. 답하는 카드가 아니라 제어 결론·카드 칸에는 안 넣는다.
  배너의 **닫기**(한 번 누름) → `feedback/user` `{request_id, verdict: "dismiss", response_ms}` — hub 는 counts.dismissed 만 세고
  FSM·제어·ESM 에는 안 넣어서 두 번 확인하지 않는다. 자동 안내 띠의 닫기도 같다.
  state/phase `sensor_summary.rest_notice.counts`(알림·말함·다시 알림·생략·닫음)를 배너에 같이.
- 말하기: `control/cmd` target `speaker` operation `speak`(value `{text, lang}`, gate `notice`) → 가상 스피커 음성 안내. `--act-laptop` 이면 노트북이 볼륨을 소리 선호로 올린 뒤
  Windows 내장 TTS(System.Speech, 한국어 목소리 — 이 노트북은 Microsoft Heami)로 말한다. 한국어 목소리가 없으면 알림음 + 배너. 인라인 명령만(실행 정책 그대로).
- 구버전 hub(rest_notice 없음): state/phase 가 REST 로 들어가면 배너만, 라벨 "hub 알림 없음 (구버전)", 시간은 hub 설정 `rest_min_sec`. 말하지 않는다.
- 시험: `--fake --fake-scenario rest`(새 동작) · `--fake-scenario rest_old`(구버전).

### 자동 안내·알림음 (hub ad76ff0)

- 기기 없는 arm 을 AUTO 로 고르면 hub 가 보내는 안내 카드: `kind` posture|break|env_suggest + `auto: true`, options `[dismiss]`,
  `expires_in_s` 180, arm·arm_label·channel(display|sound|voice)·explanation·text. 답하지 않는 띠(가상 기기 화면 위, 시한 뒤 사라짐) + 팝업,
  닫기 = dismiss. 제안 카드·제어 결론으로는 안 본다.
- `operation: chime`(value `{pattern: single|double|triple, volume: low|mid|high}`, gate `notice`) → 가상 스피커 알림음.
  `--act-laptop` 이면 노트북이 볼륨을 잠깐 그 값(low 20 % · mid 50 % · high 80 %)으로 바꿔 그 패턴으로 울리고 원래 볼륨으로 되돌린다.
  posture_voice arm 은 `speak` 로 와서 노트북이 말한다.

### 강제 종료에도 원래 밝기·볼륨

`--act-laptop` 은 켤 때 원래 값을 `%LOCALAPPDATA%\deskmate-devview\laptop_original.json` 에 적고, 되돌린 뒤 지운다. 창을 그냥 닫거나 프로세스가
죽어 되돌리지 못했으면 다음 실행이 그 파일의 값을 원래 값으로 쓰고, 끝날 때 거기로 되돌린다.

### 선풍기 그림 (`?mode=fan`, F 키)

hub 선풍기 명령(vent_fan, 가상 선풍기와 같은 규칙)을 큰 선풍기 그림으로: 풍량(0~3)·속도(0~100)에 비례해 돌고(부드럽게 빨라지고 멈춤),
natural 모드는 세기가 출렁인다. 켠 이유(원인·자동/제안·arm·그 결정의 설명)와 시각. 가상 기기 화면의 큰 칸, `?mode=fan` 은 전체 화면(두 번째 모니터용).
`--act-laptop` 과 상관없이 화면에만 그린다 — 실제 Pico 2 W 선풍기는 Pi 5 앱이 따로 움직인다.

### 두 '개인화'

- **개입 선택 개인화**(`sensor_summary.personalize`, 밴딧): 어떤 기기·카드를 고를지. 결정 tick 에만 arm·source·delivered_gate,
  평소엔 첫 설정 반영 문구(`signal.text_ko`) — '나는 누구?' 칸과 무드등 시점 칸에 보인다.
- **2단계 피로 관찰**(`sensor_summary.personalization`, CNN): observe 전용, 판정에 안 들어감. demo-b 는 linear 정규화라 unavailable 이 정상.

### 무드등 올릴 시점

가상 기기 화면 맨 위와 개발자 화면 판정 줄 아래. **hub 규칙을 hub 설정 파일에서 읽어**(코드에 숫자 없음) 지금 무드등(자세 개입)까지
무엇이 남았는지 한 줄로: "지금 상태가 약 32초 더 이어지면 무드등 +60 % (최소 153) (자동) — 추정" / "올리지 않음: 피로 0.49 < 0.6 (의심 유지)".
- `--hub-config <hub/deskmate_hub/config>` 에 **보드와 같은 hub 커밋**의 config 를 준다(fsm·ingest·control.yaml + `profiles/<--hub-profile>.json|.yaml`,
  hub `profile.py` 처럼 얹음). PyYAML 이 필요하다(없거나 못 읽으면 화면에 이유). 예: 10-26 보드 = `pi4-hub` 워크트리 bd47052.
- 체크리스트: FSM 상태 · 피로도 vs 의심(fatigue_suspect_low)·확정(fatigue_confirm) · 의심/확정 유지(hold 초·hold_min_frac, 진행 막대와 남은 초) ·
  hub 가 센 자세(min_confidence·라벨 하한 → posture_delta) · 주원인 = 자세(가중치 × δ, 추정) · 게이트(conf_auto 자동 / conf_suggest 카드) · 무드등 쿨다운(cooldown_sec).
- hub 는 유지 타이머를 state/phase 에 싣지 않는다. 그래서 '남은 초'는 state/phase tick(10 s)으로 engine.py 규칙(단계 진입 tick 은 판정 안 함,
  지난 hold 초 기록 중 frac 이상)을 앞으로 굴려 **추정**한다. 가짜 hub 로 재 보면 엎드림을 처음 본 tick 부터 예측 시각이 실제 명령 시각과 같다.
- hub ad76ff0 부터 자세 개입에서 실제로 무엇을 할지는 **개입 선택 개인화(밴딧)가 고른 arm** 이다 — 무드등은 `wake_light` 일 때만, 정책 결정이 없으면 기본 무드등.
  그래서 한 줄 결론은 "자세 개입 — 개인화가 고름(무드등·음성 안내·알림음·자세 카드 중, 결정 없으면 무드등 +60 %)"이고, 결정이 보이면(personalize.arm,
  state/phase `arm_notice.last`) "개인화가 고른 것: 알림음(무드등 아님)". `arm_notice.last` 는 "개인화가 고른 것: 알림음 (sound) → 카드, 알림음"으로 그래프·목록에.
- 그래프: 지난 15 분 C_fatigue(흰 선), 의심·확정·회복 선, FSM 상태 배경, 세로선(💡 무드등 명령 · 자세 카드 · 피로 확정 · 개입 자동/제안).
- 신호별 몫: C_fatigue = Σ w·δ / Σw 를 자세(라벨·끄덕임·졸음 플래그 중 큰 것, 추정)·키 입력·환경·경과·호흡으로, 학습기 c_fsm → c_adj(= 화면 값).
  hub 는 자세 말고는 δ 를 싣지 않아서 나머지는 'c_fsm − 자세 몫'과 각 신호의 상한(w/Σw), 단서(env_flags·타이핑)로만 보인다.
- 시험: `--fake --fake-scenario facedown`(15 s 부터 90 s 엎드림 → 카운트다운 → 약 65 s 뒤 무드등) · `--fake-scenario recline`(의심까지만, 무드등 없음).

### 앱 화면(가상)과 알림

블루투스가 끊겨도 Pi 5 앱이 **띄웠을 화면을 같은 문구로** 보여 준다(`ui_env_app/lib/intervention.dart`·`dashboard_view.dart`):
- 제안 카드 — 제목·본문·단추 글자가 앱과 같다(예: "자세를 가볍게 바로잡아볼까요?" 적용할게요/괜찮아요, "지금 피곤하세요?" 예/아니오,
  "창문을 15분 열어 주세요" 열게요/괜찮아요, hub 가 explanation 을 주면 그것이 본문).
- 자동 실행 알림 — "✓ 자동으로 실행했어요 · 자세를 바꿀 때라고 알렸어요 · 무드등 밝기 +60 % (110→176) · 이유: 움직임이 줄고 졸음 신호가 보여요"
  + 되돌리기(60 s)·확인(이 화면에서만 닫음).
- 둘 다 없으면 평소 화면 문구(집중 유지 중 · 잠시 조정이 필요해요 · 회복 중 …).
- 기기가 바뀔 때마다 오른쪽 위에 6 초 팝업과 '최근 알림' 줄: "🔊 스피커 켜짐 · 음악 재생", "💡 무드등 밝기 +60 % (110→176)", "🌀 선풍기 켬",
  "🔔 제안 카드 — 잠깐 환기해 볼까요?" — 모두 '가상'.
- 좁은 화면(노트북)에서는 앱 화면·기기 카드가 맨 위, 프로필 고르기·타임라인은 그 아래.

### 나는 누구? (수험생 / 대학생 / 직접 설정)

고르면 V18 제품 UI(`ui-live display/atlas/ui_demo_app/lib/live/profile_payload.dart` profileData)와 같은 모양의 첫 설정 프로필을
**확인을 한 번 더 누른 뒤** `deskmate/profile/user` 로 보낸다(retain·QoS 1, node `laptop-devview`, 이름·직업은 싣지 않음).
- 수험생 = V18 기본 프로필(19세 → `10s` · 수면 7 h · 23:30 → 06:30 · 조명 high · 소리 low · 바람 off · 도움 순서 조명/선풍기/알림음 · 가운데).
- 대학생 = V18 에 직업이 없어 같은 기본값에 20대(`20s`)와 '수험생 아님' 밝기 규칙(0.45 → 조명 mid).
- 직접 설정 = 연령대·수면·잠드는/일어나는 시각·조명/소리/바람·장치 위치·도움 순서를 고쳐서.
- `profile_id` 는 V18 처럼 `p` + 16 hex 난수, 이 브라우저에 페르소나마다 기억(다시 고르면 같은 사람으로 이어 배운다).
- hub 확인: state/phase 개인화의 `active_profile_id` 가 보낸 id 와 같아지면 '✓ hub 적용 확인'(10 s 주기).
- hub 시연 프로파일(`DESKMATE_PROFILE` demo-b ↔ demo-b-study)은 hub 재시작이 필요해 여기서 바꾸지 않고 표시만 한다(`--hub-profile`).

### 카드 응답 (수락 / 거절 / 되돌리기)

hub 가 카드(posture·env·break·checkin·vent_suggest)를 띄우면 시한 안에 **수락·거절**, 자동 실행 뒤 60 s(`undo_window_sec`) 안이면
**되돌리기**. 한 번 누르면 '한 번 더 → 보내기', 4 s 안에 다시 누르면 Pi 5 앱과 같은 `deskmate/feedback/user` data
(`request_id`·`verdict`·`response_ms`·`kind`, QoS 1)를 보낸다. 되돌리기 = 앱처럼 `reject`(걸린 카드가 없으면 `request_id: atlas-display`) →
hub 가 auto_undo 로 받아 undo 명령을 낸다. 이 응답이 반응 학습(ReactionLearner)의 라벨이 된다.

## 글씨 크기

위 띠의 **A− / A+**(또는 `-` / `=` 키)로 배율을 바꾼다. 화면 파일이 바뀌면 열린 화면이 스스로 다시 불러온다. 처음엔 창 폭이 1700 보다 좁으면(예: 이 노트북 2880×1800 @200 % = 1440×900) 160 %,
1920 이상이면 120 %(80~250 %). 이 브라우저에 기억한다. 배율을 감안한 폭이 1700 보다 좁으면 판정·원값·로그 등을 아래로 쌓고 페이지가 세로로 스크롤된다.
영상 모드는 이 배율 대신 화면 크기(1920×1080 기준)에 맞춰 커지고 작아진다.

## 영상 모드 (`/?mode=video`)

시연영상에서 실제 장면 옆·아래에 붙이는 하단 띠(시뮬 영상의 띠와 같은 칸). 개발자 화면 오른쪽 위 **영상 모드** 단추, `V` 키,
또는 <http://127.0.0.1:8790/?mode=video> 로 바꾼다(`&bg=green` 이면 크로마키 초록 배경). 칸: ToF 8×8 두 개 · 센서가 본 자세
(자세 서비스 라벨, 예: 졸음(꾸벅)) · 칩 `허브 · FSM 상태 · 작업 맥락`(예: 허브 · 집중 · 책·필기) · 피로 막대와 C_fatigue · 환경 한 줄
(CO₂·온도·습도·조도) · **허브 결정**(시뮬의 "실제(시뮬 정답)" 자리 — 아래 제어 결론의 한 줄). 위쪽은 비워 두고 띠만 반투명이다.

## 제어 결론 패널

"어느 블루투스 기기를 왜 제어하나" 를 한 줄로. `interaction/request`·`control/cmd`·`control/result`·`feedback/user` 를 묶어:
- 최근 180 s 안의 **명령**: `자동 · 환경 → Pico 2 W 선풍기 (BLE) 켬 + iLink 무드등 (BLE) 밝기 +30 %` + 게이트(자동/제안)·원인·이유
  (그때 state/phase reasons·개인화 설명)·Pi 5 실행기 결과(`✓ 성공 110→176` / `✗ failed feedback_disabled`, 결과를 낸 노드·시각).
- 최근 **제안 카드**: `제안 카드 · 자세 → iLink 무드등 (BLE) 밝기 +60 % (최소 153)` + 카드 설명·응답(수락/거절/응답 대기)·시한.
- 둘 다 없으면 state/phase 로 **지금 할 일**: `지금 개입 없음 — 집중 (FOCUS_PC)`, `피로 의심: 확정되면 원인별로 — 자세 → … · 환경 → …`.
  원인별 기기는 `hub/deskmate_hub/config/control.yaml` actions(10-25)를 옮긴 표(`ACTION_PLAN`)로 예고만 한다 — 실제로 보낸 것은 control/cmd 가 정본.

target → 기기: `desk_lamp` = iLink 무드등 (BLE), `vent_fan` = Pico 2 W 선풍기 (BLE), `air_purifier` = LG 공기청정기 (ThinQ).

## 키 입력 패널

PC 수집기(`deskmate/sensor/keystroke`, 키 값·문자는 보내지 않음 — 타이밍 집계뿐)의 키 누름 유지·키 입력 간격(ms, ±표준편차),
입력 수(`event_count`)와 분당 수, 리듬 CV·공백·교정 비율, `valid`·`typing_active`, 최근 60 표본 추이(유지·간격), 연결
(`deskmate/health/pc-collector` status·재연결)과 표본 나이. 상태 문구·5 s 끊김 기준·추이 규칙은 Pi 5 `keystroke_page.dart` 와 같다.

## 핫스팟 연결

1. 휴대폰 핫스팟 **So** 를 켜고 노트북 Wi-Fi 를 So 에 붙인다(주소 172.20.10.x/28). 집 Wi-Fi 에서는 Pi 4 가 안 보인다.
2. Pi 4 는 같은 핫스팟에서 `172.20.10.3`. 브로커(1883)·자세 서비스(8771·8772)가 떠 있어야 한다 —
   재부팅 뒤 켜는 순서는 [`docs/reboot-recovery.md`](../../../../docs/reboot-recovery.md) 를 따른다.
3. `python devview.py --pi4 172.20.10.3` → 띠의 점 세 개(MQTT·자세 서비스·ToF raw)가 초록이면 된다.
   하나만 빨강이면 그 서비스만 안 떠 있는 것(배너에 주소와 이유가 나온다).

## 녹화 팁

- 화면 해상도 **1920×1080**, 브라우저 배율 100 %, **F11 전체 화면**. 이 크기에 맞춰 배치했다(스크롤 없음).
- 녹화 전에 `--fake` 로 한 바퀴 돌려 보고, 실제 녹화는 `--fake` 없이(띠에 노란 표시가 없는지 확인).
- `state/phase` 는 10 s 주기라 FSM 상태·피로도는 10 s 마다 바뀐다. ToF 열지도와 자세 막대는 실시간.
- 화면을 바꾸지 않고 다시 시작하려면 터미널에서 Ctrl+C 후 다시 실행(명령 로그가 비워진다).

## 테스트

```bash
python -m pytest hub/atlas-tof/pc/devview -q
```

UTF-8 디코딩(소켓으로 가짜 브로커까지), `state/phase` → 화면 데이터(키 없음 '-'), 센서 원값 출처 우선순위(/raw other → MQTT → 요약),
엎드림 최고값·0.99 횟수·60 s 창, 프록시 우회, 토픽 목록, `/raw` 파싱, 명령 로그 200 줄 상한, 기기 상태(`previous→actual`),
`--fake` 각본이 FSM 다섯 단계와 명령·결과·제안·피드백·0.99 도달을 만드는지, 로컬 HTTP.
