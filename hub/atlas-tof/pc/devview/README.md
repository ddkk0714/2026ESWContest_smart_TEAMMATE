# 노트북 개발자 화면 (`devview`)

결선 시연영상에서 **내부 상태를 보여 주는 화면**이다. Pi 5 앱의 개발자 모드와 같은 정보를
노트북 브라우저 한 페이지(1920×1080)에 크게 띄우고, 그 화면을 녹화한다.

- **보기 전용.** 판단(FSM·개인화)과 제어는 전부 Pi 4·Pi 5 보드에서 한다. 이 프로그램은 Pi 4 브로커를
  구독하고 자세 서비스에 GET 만 한다 — 발행·제어 명령을 보내지 않는다. 화면 맨 위 띠에도 그렇게 적혀 있다.
- **원시 센서 값은 화면에만.** 받은 값은 메모리에만 두고 `127.0.0.1` 로만 연다. 파일로 쓰지 않는다
  (녹화는 사람이 화면 캡처로 한다).
- 표준 라이브러리만 쓴다(Python 3.10+). `pip install` 이 필요 없고 외부 CDN 도 없다(오프라인 동작).

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
27 s ACTION_POSTURE(자세 졸음, 무드등 `adjust_brightness +60%` 명령) → 28 s 결과 `110→176` → 33 s 휴식 제안 →
35 s 수락. 두 ToF 격자도 움직인다. `--fake-start 26` 처럼 원하는 장면 직전부터 시작할 수 있다.
가짜 데이터일 땐 띠에 노란 `가짜 데이터(--fake)` 표시가 붙는다 — 시연 녹화에서 이 표시가 보이면 안 된다.

| 옵션 | 기본 | 뜻 |
|---|---|---|
| `--pi4` | `172.20.10.3` | Pi 4 주소(핫스팟 So) |
| `--mqtt-port` | 1883 | Pi 4 Mosquitto |
| `--port` | 8790 | 이 화면의 로컬 포트 |
| `--fake` / `--fake-start S` | | 가짜 각본 / S 초부터 |

## 가져오는 것

| 출처 | 주기 | 화면 |
|---|---|---|
| MQTT `deskmate/state/phase` | hub 10 s | 판정(FSM 상태·C_fatigue·C_focus·원인·게이트·hub 자세·재실·개인화 설명), 센서 값(`sensor_summary` 의 mmWave·환경·키 입력) |
| MQTT `deskmate/control/cmd`·`control/result` | 이벤트 | 기기 상태 한 줄(무드등·선풍기·공기청정기의 마지막 명령·결과, `previous→actual` 밝기) + 명령 로그 |
| MQTT `deskmate/interaction/request`·`feedback/user` | 이벤트 | 명령 로그(REQUEST·FEEDBACK) |
| `GET :8771/api/state` | 1 s | 자세 서비스 라벨·신뢰도·9 클래스 막대·장치 위치(`placement`, 없으면 '-') |
| `GET :8772/raw` (없으면 `:8771/raw`) | 0.2 s | ToF A·왼쪽 / B·오른쪽 8×8 열지도(칸 숫자 cm, 회색 = 무효 상태값), 유효 칸 수·최소 거리 |

- MQTT 페이로드는 **UTF-8** 로 읽는다(한글 설명). Latin-1 로 읽으면 깨진다(Pi 5 앱에서 실제로 있었던 버그).
- 명령 로그는 받은 순서대로 최근 200 줄. 줄 모양은 Pi 5 `command_log.dart` 와 같다
  (`CMD desk_lamp adjust_brightness +60% (auto, id 9f3c1a) posture` / `RESULT 9f3c1a succeeded 110→176 (display-control)`).
  CMD 파랑 · RESULT 성공 초록/실패 빨강 · REQUEST·FEEDBACK 노랑. 시각은 노트북 수신 시각.
- 값이 없는 칸은 `-`. 심박·호흡은 `*_valid=false` 면 `(무효)` 를 붙인다(락온 풀림 ≠ 사람 없음).
- 열지도 색 눈금은 `pc/posture_view.html`·Pi 5 `tof_heatmap.dart` 와 같다(20 cm 붉음 → 2 m 어둠). 배열은 펌웨어가 방향을 적용한 그대로 그린다(뒤집지 않음).

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

UTF-8 디코딩(소켓으로 가짜 브로커까지), `state/phase` → 화면 데이터(키 없음 '-'), `/raw` 파싱, 명령 로그 200 줄 상한,
기기 상태(`previous→actual`), `--fake` 각본이 FSM 다섯 단계와 명령·결과·제안·피드백을 만드는지, 로컬 HTTP. 보드 실기는 미확인.
