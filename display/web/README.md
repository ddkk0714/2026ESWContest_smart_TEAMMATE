> 이 문서는 ATLAS **web-app** 판 키스트로크 뷰(2026-07)의 설명이다. 지금 팀 표준 단말 UI 는
> Atlas Flutter 통합 앱(`display/README.md`, `display/atlas/`)이고, 이 web-app 은 그와 별개의 경량 대안이다.
> 아래 경로는 `display/` 기준이다.

# display — 출력 단말 UI

책상 위 디스플레이형 가전의 출력 단말. 현재 국면 · 제안 카드 · 휴식 알림 · 작업 리포트를
표시하고, 사용자 피드백을 `deskmate/feedback/user` 로 발행한다.

## 화면 (최종 목표)

| 화면 | 내용 | 상태 |
|---|---|---|
| **키스트로크 라이브** | collector 지표 실시간 표시 | ✅ 구현 |
| **보드 키보드 캡처** | 보드에 직접 꽂은 키보드로 같은 지표를 실기 확인 (`?src=local`) | ✅ 구현 |
| AOD (기본) | 현재 국면, 작업 지속 시간, 환경 요약(CO₂ · 조도 · 온습도) | ⬜ |
| 제안 카드 | 신뢰도가 `conf_suggest` ~ `conf_auto` 구간일 때. 수락 / 거절 버튼 | ⬜ |
| 자동 실행 알림 | 신뢰도가 `conf_auto` 이상이라 이미 실행된 제어를 사후 통지. 되돌리기 제공 | ⬜ |
| 작업 리포트 | 세션 종료 시 국면 타임라인, 휴식 수용률, 환경 추이 | ⬜ |

`deskmate/state/phase` 구독은 이미 붙어 있다. hub 에서 국면이 나오기 시작하면
같은 틀에 화면을 추가하면 된다.

## 피드백 설계

사용자의 수락 · 거절 · 정정이 그대로 2단계 분류기의 ESM 라벨이 되므로,
피드백 UI 는 **라벨 수집 채널**이기도 하다. 입력 부담을 낮추는 게 라벨 양을 좌우한다.

- 제안 카드는 2탭 이내로 응답 가능해야 한다
- 정정(`correct`)은 4개 국면 중 선택하는 단순 형태
- 무응답도 하나의 신호로 기록한다 (수락도 거절도 아님)

---

## 타깃: ATLAS `web-app`

LG 지급 **ATLAS 보드**에 `web-app` 으로 올린다. ARC CLI 템플릿 중 하나이고,
실체는 그냥 HTML/CSS/JS 다. 그래서 `web/` 폴더가 곧 앱이다.

> **ATLAS 는 라즈베리파이 OS 가 아니다.** Yocto/BusyBox 최소 이미지라 `apt` · 데스크톱 ·
> Chromium · **Python 런타임이 없다.** ARC CLI 프로젝트 템플릿에도 Python 이 없다
> (headless-app / native-service / web-app / widget-provider-service / Flutter).

### 그래서 데이터 경로가 이렇게 된다

```
PC (collector, Python)                    ATLAS 보드
  └─ MQTT 1883 (TCP) ─┐                     └─ web-app (브라우저)
                      ├─► mosquitto (허브)        │
  ESP32 ──────────────┘    listener 1883         │
                           listener 9001 ◄───────┘  MQTT over WebSocket
```

**브라우저는 원시 TCP 소켓을 못 연다.** 그래서 1883 이 아니라 **WebSocket 리스너 9001** 로
붙는다. 노션 「통신 아키텍처」 §3-1 에서 *"Pi 5 UI 가 웹 기반일 때만 필요"* 라고 보류해 둔
WebSocket 리스너가 이걸로 확정 필요해졌다 → `broker/mosquitto-deskmate.conf`

중간에 Python 서버가 없으므로, 링버퍼·신선도 판정 같은 상태 관리는
**브라우저(`web/js/store.js`)가 직접 진다.**

## 구조

```
display/
  web/                     ← ATLAS web-app 소스. 이 폴더가 곧 앱이다
    index.html
    css/style.css
    js/mqtt.min.js         mqtt.js 5.15.2 (MIT) — 유일한 외부 의존, 벤더링
    js/config.js           브로커 주소 등. 보드에서 이 파일만 고치면 된다
    js/store.js            최신값 + 추이 링버퍼 + 신선도 (UMD — node 로 테스트 가능)
    js/keycap.js           보드 키보드 직접 캡처 → 특징 추출 (collector/features.py 의 JS 포팅)
    js/demo.js             ?demo=1 용 가짜 데이터
    js/app.js              MQTT 구독 + 렌더 + canvas 차트
  broker/
    mosquitto-deskmate.conf  허브 브로커 설정 (1883 + 9001)
  tools/
    preview.py             로컬 미리보기 정적 서버 (개발용, 배포 제외)
    fake_publisher.py      브로커에 가짜 keystroke 발행 (배선 점검용)
  tests/store.test.js      node --test
  tests/keycap.test.js     node --test — collector 쪽 test_features.py 와 같은 시나리오
```

**외부 CDN 을 쓰지 않는다.** 보드가 오프라인일 수 있고, 패키징된 앱이기 때문이다.
폰트도 시스템 폰트만 쓴다.

## 화면 구성 (터치 3탭)

| 탭 | 내용 |
|---|---|
| 라이브 | 활동 상태(타이핑 / 마우스 / 무입력), dwell · flight · flight_cv · idle_ratio · correction_rate · 마우스 이벤트율 |
| 추이 | 최근 5분 flight_mean_ms, idle_ratio · correction_rate 겹쳐 그리기 |
| 원본 | 마지막 수신 JSON 그대로 (규약 대조·디버깅용) |

### 데이터 소스 세 가지

같은 화면에 어디서 온 값을 채울지만 다르다. store 에 들어가는 페이로드 모양은 셋 다 같다.

| 소스 | 켜는 법 | 값의 출처 | 브로커 | 쓰는 곳 |
|---|---|---|---|---|
| `mqtt` (기본) | `config.js` 의 `source` | 허브 브로커 → PC collector | 필요 | 실제 운영 |
| `local` | `?src=local` 또는 `source: "local"` | **보드에 직접 꽂은 키보드** | 불필요 | 실기 입력 확인 |
| `demo` | `?demo=1` | 가짜 데이터 | 불필요 | 레이아웃 확인 |

`staleAfterMs`(기본 5초) 넘게 수신이 없으면 화면을 흐리게 하고 "수신 끊김" 을 띄운다.
**옛 값을 현재 상태로 읽는 사고를 막기 위한 것이므로 없애지 말 것.**

### 화면 크기

실기 타깃이 공식 7인치 DSI (**800×480**, 약 133ppi) 라 vmin 이 4.8px 밖에 안 된다.
순수 vmin 으로 짜면 글자가 8~9px 로 줄어 못 읽는다. 그래서 CSS 크기를 전부
`clamp(px 하한, vmin, px 상한)` 토큰(`--fs-*`)으로 잡았다. **순수 vmin/vw 로 되돌리지 말 것.**

---

## 지금 바로 보기 (보드 없이)

```bash
cd display
python tools/preview.py
```

- `http://localhost:8080/?demo=1` — 브로커 없이 가짜 데이터 1Hz
- `http://localhost:8080/?src=local` — **이 PC 키보드를 그대로 캡처.** 보드에서 쓸 것과 같은 코드
- `http://localhost:8080/?broker=ws://<허브IP>:9001` — 실제 브로커

`?demo=1` 은 브로커도 파이썬 서버도 필요 없다. 레이아웃·터치 확인용.
`?src=local` 은 화면을 클릭해 포커스를 준 뒤 타이핑하면 값이 오른다.

## 보드 키보드로 테스트하기 (`?src=local`)

보드에 USB 키보드를 꽂고 브로커·collector 없이 키스트로크 파이프라인을 확인하는 모드다.
web-app 이 `keydown`/`keyup` 을 직접 받아 `js/keycap.js` 에서 특징을 뽑고,
평소 브로커에서 받던 자리에 그대로 밀어 넣는다. 그래서 라이브·추이·원본 탭이 다 그대로 동작한다.

**언제 쓰나**

- 보드 USB 입력이 앱까지 도달하는지 확인할 때. 화면 상단 점이 타건마다 깜빡이고 타수가 오른다.
- 브로커·네트워크·collector 를 빼고 화면과 특징 계산만 분리해서 볼 때.
- 터치가 아직 안 될 때의 대체 입력 확인. (`docs/atlas-build-handoff.md` 의 MTouch 이슈)

**켜는 법**

| 방법 | 내용 |
|---|---|
| 일회성 | 주소에 `?src=local` 을 붙인다 |
| 고정 | `web/js/config.js` 의 `source` 를 `"local"` 로 바꾼다 |

노드 이름·윈도 길이·발행 주기는 `config.js` 의 `capture` 에 있고,
`flightGapMaxS`·`idleGapS` 는 `collector/config.py` 와 같은 값으로 맞춰 두었다.

**계산이 collector 와 같다**

`js/keycap.js` 는 `collector/capture.py` + `collector/features.py` 의 포팅이다.
상수(flight 2.0s · idle 3.0s · dwell 상한 2000ms), 반올림 자릿수, `to_payload()` 필드 이름까지
같게 맞춰서 PC 노드와 보드 노드의 값을 그대로 비교할 수 있다. 노드 이름만 `pi-keyboard` 로 다르다.

**프라이버시 — 여기도 예외 없다**

- `js/keycap.js` 는 `KeyboardEvent` 를 받지 않는다. 호출자가 '종류' 로 바꿔서 넘긴다.
- 종류는 `char`/`space`/`backspace`/`enter`/`other` 다섯 가지뿐이라 입력 내용을 복원할 수 없다.
- 실제 문자를 저장하거나 화면에 띄우는 경로가 없다. 화면에 뜨는 것도 종류뿐이다.
- 실물 키보드는 `KeyboardEvent.code` 를 채우지만 비어 오는 입력 경로가 있어 `key` 폴백을 둔다.
  폴백도 '길이 1 = 문자' 로만 보고 문자 자체는 쓰지 않는다.

**한계**

- 화면(web-app)에 포커스가 있어야 키가 들어온다. 창이 포커스를 잃으면 눌린 키를 정리하고 멈춘다.
- `Space`·`Backspace`·`Tab`·`F5`·방향키는 기본 동작을 막는다. 키오스크에서 화면이 날아가면 곤란해서다.
- 이 모드는 발행하지 않는다. 보드를 실제 수집 노드로 쓰려면 mqtt.js 로 publish 를 붙여야 한다.

## 브로커 설정 (허브)

```bash
sudo apt install -y mosquitto mosquitto-clients
sudo cp broker/mosquitto-deskmate.conf /etc/mosquitto/conf.d/
sudo systemctl enable --now mosquitto
sudo systemctl restart mosquitto
```

확인:

```bash
mosquitto_sub -h localhost -t 'deskmate/#' -v
```

collector 를 PC 에서 붙인다:

```powershell
.\.venv\Scripts\python.exe -m collector --broker <허브IP>
```

collector 없이 배선만 점검하려면:

```bash
python tools/fake_publisher.py --broker <허브IP>
```

## ATLAS 보드 배포

`web-app` 템플릿은 **Chromium 런처** 기반이다 (`arc-0.5.0` 템플릿의 `src/webapp_launcher.cc`).
그래서 이 폴더의 HTML/CSS/JS 가 그대로 돈다.

### 프로젝트 구조 매핑

`arc create` 가 만드는 web-app 프로젝트는 이렇게 생겼다:

```
<프로젝트>/
  CMakeLists.txt                      템플릿 생성물 — 건드리지 않는다
  src/                                Chromium 런처 — 건드리지 않는다
  files/appinfo.json               ◄── 이 저장소의 display/files/appinfo.json
  com.atlas.app.deskmate-display/  ◄── 이 저장소의 display/web/ 내용
      index.html  css/  js/
```

**웹 자산 폴더의 이름은 앱 id 와 정확히 같아야 한다** (대소문자 구분). 앱 매니저가
설치 디렉터리 이름을 키로 메타데이터를 찾기 때문이다. 우리 id 는
`com.atlas.app.deskmate-display` 다.

> 템플릿이 생성하는 `CMakeLists.txt` · `src/` 는 LG 배포물이라 이 저장소에 넣지 않는다
> (노션 「개발 프로세스 — ATLAS 진행 방식」 §5). 우리가 관리하는 건 `web/` 과
> `files/appinfo.json` 두 개뿐이다.

### 절차

```bash
# 개발 컨테이너 안에서
arc create deskmate-display -t web-app
cd deskmate-display

# 우리 것으로 교체 — 템플릿 샘플 자산은 지운다
rm -rf com.atlas.app.sample-webapp
cp -r /app/<repo>/display/web        ./com.atlas.app.deskmate-display
cp    /app/<repo>/display/files/appinfo.json  ./files/appinfo.json

arc build                  # → build/arm64/ipk/com.atlas.app.deskmate-display.ipk
arc devices add            # 최초 1회. Device ID: rpi4 / IP: <보드IP> / User: root / Password: (빈값)
arc install --deviceId=rpi4
arc run --deviceId=rpi4
```

`arc run` 은 앱의 로그를 PC 터미널로 되돌려준다. 웹 앱은 원격 인스펙션도 된다
(`dev-docs/guide-documents/07-arc-cli-guide` 참조).

**같은 id·같은 version 이 이미 설치돼 있으면 재설치가 거부된다.** 새로 밀 때마다
`files/appinfo.json` 의 `version` 을 올린다.

### 권한

지금은 `required_permissions` 가 없다. 이 앱이 하는 건 **MQTT WebSocket 통신뿐**이고
그건 일반 네트워크라 D-Bus 권한이 필요 없다. 나중에 붙일 때 필요해진다:

| 기능 | 필요한 것 |
|---|---|
| ThinQ 가전 제어 | `com.atlas.ThinqClient1` — privileged `perm_thinq` 선언 |
| 휴식 알림 | `com.atlas.NotificationManager1` |
| AOD · 상태 위젯 | 별도 `widget-provider-service` 프로젝트 (native) |

### 배포 블로커 (이 PC 기준, 2026-08-31 재확인)

| 항목 | 상태 |
|---|---|
| LG 배포물 (`atlas_docker_env` · `dev-docs` · 샘플) | ✅ 확보 (저장소 밖 — 공개 레포에 올리지 말 것) |
| WSL2 Ubuntu | ✅ 설치됨 (`Stopped`) |
| Docker Engine | ❌ 없음 — 호스트에도 WSL 안에도 없다. `atlas_docker_env/Guide.md` 절차를 WSL 안에서 실행 |
| ARC CLI | ❌ — 컨테이너 빌드하면 들어온다 (`arc -V` 로 확인) |
| 디스크 여유 | ⚠️ C: 28GB. WSL ext4(`/`)는 950GB 여유이므로 이미지·저장소를 ext4 안에 두는 편이 낫다 |

> `.ipk` 를 만들려면 개발 PC 에 Docker 가 있어야 한다. 조 쪽 빌드 PC 에는 Docker Desktop 과
> vendor SDK 가 갖춰져 있다(`docs/atlas-build-handoff.md`). 이 PC 에서 직접 빌드하려면
> 아래 절차로 Docker Engine 부터 깔아야 하고, `sudo` 비밀번호 입력이 필요하다.

```bash
# WSL 안에서 — atlas_docker_env/Guide.md 요약
cd <atlas_docker_env>
docker compose build
docker compose up -d
docker compose exec atlas-dev bash
arc -V
```

> **WSL2 서브넷 충돌을 먼저 확인할 것.** WSL2 는 기본적으로 `172.16.0.0/12` 에서 NAT
> 대역을 잡는데 **ATLAS 보드가 `172.16.34.x`** 다. 겹치면 보드로 가는 경로가 WSL 내부로
> 빨려 들어가 통신이 죽는다. 컨테이너 진입 후 `ip route` · `ip addr` 로 확인하고,
> 겹치면 `.wslconfig` 로 WSL 대역을 옮긴다.

## 테스트

```bash
cd display && node --test tests/*.test.js
```

브라우저 없이 검증한다.

- `tests/store.test.js` (7종) — 링버퍼·신선도·잘못된 페이로드 방어
- `tests/keycap.test.js` (12종) — dwell·flight·cv·idle·correction, auto-repeat 억제, 종류 분류.
  `collector/tests/test_features.py` 와 같은 시나리오를 써서 두 노드가 같은 값을 내는지 본다.

## 미결

- **보드 배치 A/B안 팀 결정** (8월 4주 예정) — A안이면 지금 구조 그대로,
  B안이면 디스플레이가 다시 Pi 5 로 간다. 노션 「개발 프로세스 — ATLAS 진행 방식」 §1
- 9001 리스너를 MQTT 규약 문서(`docs/mqtt-topics.md`)에 반영 — 이민혁 확인 필요
- collector 의 additive 필드(`typing_active` 등) 규약 확정 — 이민혁 확인 필요
