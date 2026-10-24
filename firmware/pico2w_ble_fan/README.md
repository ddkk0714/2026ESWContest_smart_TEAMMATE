# pico2w_ble_fan — 블루투스 선풍기

> **⚠ 모터·버튼은 미검증(2026-10-24).** Pico 2 W 단품(모듈 없음)에 구워 USB 시리얼 명령·BLE 광고·연결·읽기·쓰기·알림·거절까지
> 확인했다(아래 [실기 확인 순서](#실기-확인-순서)의 ✔). L9110 모듈을 단 모터 구동, 버튼, LED, 리모컨 페이지의 실제 BLE 연결, Pi 5 경로는 아직이다.

Raspberry Pi Pico 2 W 와 **L9110 선풍기 DC 모터 모듈**(Keyes 계열, 핀 `GND·VCC·INA·INB`)로 만든 블루투스 선풍기다.
집 선풍기처럼 쓴다:

| 기능 | 본체 버튼(선택) | 블루투스 |
|---|---|---|
| **풍량 1·2·3단** (모터 듀티 30 % · 45 % · 60 %) | 풍량 버튼: 끔 → 1 → 2 → 3 → 끔 | 리모컨 1·2·3 / `level N` |
| 전원 끄기 | 풍량 버튼 길게(0.8 s) | 리모컨 전원 / `off` |
| **자연풍** — 바람 세기가 돌풍처럼 오르내림(그 단 듀티의 40~100 %, 1단 듀티 아래로는 안 내려감) | 자연풍 버튼(꺼져 있으면 1단 자연풍으로 켜짐) | 리모컨 자연풍 / `natural` |
| **꺼짐 타이머** 30분 → 1시간 → 2시간 → 4시간 → 해제 | 타이머 버튼(켜져 있을 때만) | 리모컨 타이머 / `timer 30m` (아무 시간이나) |
| 세밀한 세기 0~100(100 = 3단), 역회전 | — | 리모컨 "세밀하게" / `speed N` · `reverse` |

- **모터 듀티는 60 % 를 넘지 않는다**(10-24 실기에서 정함). 블루투스·허브의 세기 0~100 은 듀티 0~60 % 에 대응하고, 돌고 있는 동안은 1단 듀티(30 %) 아래로 내려가지 않는다.
- 정지에서 출발할 때 250 ms 동안 최대 듀티(60 %)로 밀어 준다(작은 모터가 낮은 세기에서 안 도는 것 방지). 돌던 중 방향을 바꾸면 0.4 s 멈췄다가 반대로.
- 블루투스가 끊겨도 마지막 상태로 계속 돈다. 전원을 다시 넣으면 항상 꺼진 상태로 시작한다.
- 같은 명령을 USB 시리얼로도 받는다 — 블루투스 없이 배선부터 확인할 수 있다.

## 배선

| Pico 2 W (물리 핀) | 연결 | 비고 |
|---|---|---|
| **40 VBUS** (USB 5 V) | 모듈 **VCC** | 모터 전원. 3V3(36번)에 물리지 않는다 — 모터 기동 전류에 보드가 리셋된다 |
| **38 GND** (또는 아무 GND) | 모듈 **GND** | 공통 접지 |
| **21 GP16** | 모듈 **INA** | PWM 20 kHz. INA=PWM·INB=0 이면 정회전 |
| **22 GP17** | 모듈 **INB** | 역회전 때 PWM |
| 20 GP15 ─ 버튼 ─ GND | 풍량 버튼 | 선택. 버튼 3개는 18번 GND 를 같이 쓴다 |
| 19 GP14 ─ 버튼 ─ GND | 자연풍 버튼 | 선택 |
| 17 GP13 ─ 버튼 ─ GND | 타이머 버튼 | 선택 |

```
              ┌───USB───┐
              │1      40├─ VBUS ─────────── VCC ┐
              │       39│                       │
              │       38├─ GND ──────────── GND │  L9110 선풍기 모듈
              │    …    │                       │
 타이머 ┌─ GP13┤17       │                       │
 버튼들 ├─ GND ┤18       │                       │
        ├─ GP14┤19     21├─ GP16 ─────────── INA │
        └─ GP15┤20     22├─ GP17 ─────────── INB ┘
              └─────────┘
   (각 버튼은 GPxx 와 18번 GND 사이에 하나씩: 13=타이머, 14=자연풍, 15=풍량)
```

- 버튼은 전부 선택이다. 내부 풀업이라 저항 없이 GPxx–GND 사이에 택트 스위치만 단다. 버튼이 없으면 블루투스로만 쓴다.
- 모듈은 2.5~12 V 에서 돈다. USB 5 V 로 충분하다. L9110 입력은 3.3 V 로직으로 구동된다.
- 팬이 켜지는 순간 보드가 재부팅되거나 블루투스가 끊기면 모듈 VCC–GND 사이에 **100~470 µF 전해 콘덴서**(+ 0.1 µF)를 단다(극성 주의).
- 바람이 뒤로 나오면 INA·INB 선을 서로 바꾸거나, `-DFAN_DEFAULT_DIR=1` 로 빌드하거나, 리모컨에서 방향을 바꾼다.
- 핀을 바꾸려면 `-DFAN_PIN_INA=…` `-DFAN_PIN_BTN_TIMER=…` 등. GP23·24·25·29 는 Pico 2 W 무선칩이 쓰므로 피한다.

**보드 LED**(무선칩 쪽이라 배선 필요 없음): 1초마다 짧게 깜빡 = 블루투스 연결 대기, 계속 켜짐 = 연결됨.
버튼을 누르면 잠깐 대답한다 — 풍량은 단계 수만큼, 타이머는 30분 1번·1시간 2번·2시간 3번·4시간 4번, 자연풍 켜짐은 3번, 끔·해제는 길게 1번.

## 굽기

`build/deskmate_ble_fan.uf2` 를 쓴다(빌드 산출물이라 git 에는 없다 — 아래 [빌드](#빌드)).

1. Pico 2 W 의 **BOOTSEL** 버튼을 누른 채 USB 를 꽂는다 → `RP2350` 드라이브가 뜬다.
2. `deskmate_ble_fan.uf2` 를 그 드라이브에 끌어 놓는다 → 자동 재부팅, LED 가 1초마다 깜빡이면 정상.

이미 Pico SDK 프로그램(이 펌웨어 포함, USB 장치 `2E8A:0009`)이 돌고 있으면 BOOTSEL 없이 COM 포트를 **1200 baud 로 열었다 닫으면**
굽기 모드로 들어간다: `python -c "import serial,time; s=serial.Serial('COM10',1200); time.sleep(.2); s.close()"`
(포트가 사라지며 오류가 나는 게 정상). 10-24 이 방법으로 구웠다.

## 블루투스 리모컨

[`remote/index.html`](remote/index.html) — 전원·1/2/3단·자연풍·타이머 버튼이 있는 리모컨 페이지(Web Bluetooth).
**Android Chrome, PC Chrome/Edge** 에서 동작한다(iPhone Safari 는 Web Bluetooth 가 없어 nRF Connect 를 쓴다).
Web Bluetooth 는 https 나 localhost 에서만 열리므로:

```bash
cd firmware/pico2w_ble_fan/remote
python -m http.server 8000
```

PC 에서 `http://localhost:8000` → **블루투스 연결** → `DESKMATE-FAN` 선택. 선풍기 없이 화면만 보려면 `http://localhost:8000/?demo`.
휴대폰에서 쓰려면 이 파일을 https 로 올리거나(GitHub Pages 등), Android Chrome 의 `chrome://flags/#unsafely-treat-insecure-origin-as-secure` 에
PC 주소(`http://<PC IP>:8000`)를 넣는다.

그 밖의 방법:
- **nRF Connect**(Android/iOS): `DESKMATE-FAN` CONNECT → 특성 `f1970002-…` 에 UTF-8 텍스트 `level 2`, `natural`, `timer 30m` 를 쓴다.
- **PC 명령줄**: `pip install bleak` 뒤 `python tools/ble_fan.py level 2`, `… send "natural; timer 1h"`, `… watch`.

블루투스는 한 번에 한 기기만 붙는다. 휴대폰이 연결돼 있으면 PC 가 못 찾는다(그 반대도).

## 실기 확인 순서

하드웨어가 생기면 이 순서로 하나씩 확인하고 결과를 PR 에 남긴다.

1. **모듈 없이 굽기** — LED 가 1초마다 깜빡이는가. COM 포트(115200)에 `status` → `[fan] usb: power=off speed=60 …`.
   ✔ 10-24: 1200 baud 리셋으로 굽기, `status`·모르는 명령·범위 초과 거절 확인. 노트북 BLE 검색에 `DESKMATE-FAN`(RSSI −33),
   `tools/ble_fan.py status`·`mode natural`(쓰기 뒤 다시 읽기), 잘못된 명령 ATT 0x13 거절, USB 로 바꾼 상태가 BLE 알림으로 옴. LED 는 아직 눈으로 안 봄.
2. **USB 시리얼로 모터** — 줄 끝 LF 로 `level 1` → `level 2` → `level 3` → `off`. 1단에서도 출발하는가(안 되면 아래 조정).
3. **자연풍·역회전·타이머** — `natural`(세기가 몇 초 주기로 오르내림), `reverse`, `on; timer 20s`(20초 뒤 꺼짐).
4. **리모컨** — 연결, 1/2/3단, 자연풍, 타이머 30분 표시, 끊었다 다시 연결.
5. **버튼**(달았다면) — 풍량 순환과 길게 눌러 끄기, 자연풍, 타이머 순환, LED 대답.
6. **전원 안정** — 3단에서 껐다 켜기를 반복해도 보드가 재부팅되지 않는가(되면 콘덴서).

조정값(`-D` 로 빌드): `FAN_PWM_HZ`(기본 20000 — 낮은 단에서 안 돌거나 L9110 이 뜨거우면 1000), `FAN_MAX_DUTY_PERMILLE`(600 — 세기 100·출발 킥),
`FAN_MIN_DUTY_PERMILLE`(300 — 돌 때의 바닥), `FAN_KICK_MS`(250), `FAN_LEVEL1/2/3_SPEED`(50/75/100 → 듀티 30/45/60 %), `FAN_TIMER_PRESETS_MIN`(`30, 60, 120, 240`).
10-24 실기: 처음 100 % 듀티였던 것을 사용자가 보고 최대 50 % → 20/35/50 % → **30/45/60 %** 로 정했다.
실측해서 바꾼 값은 이 README 에 적는다.

## GATT 규격

광고 이름 `DESKMATE-FAN`(광고 패킷), 서비스 UUID 는 스캔 응답에 있다. 페어링 없음.

| 특성 | UUID | 속성 |
|---|---|---|
| 서비스 | `f1970001-51e1-4b0e-a7f1-8d27d8e34683` | |
| 명령 | `f1970002-51e1-4b0e-a7f1-8d27d8e34683` | Write, Write Without Response |
| 상태 | `f1970003-51e1-4b0e-a7f1-8d27d8e34683` | Read, Notify |

**명령 — 바이너리.** `<op> <인자>` 를 여러 개 이어 붙여도 된다(`01 01 02 3C` = 켜고 60 %). 하나라도 틀리면 아무것도 적용하지 않고
응답 있는 쓰기에는 ATT 오류 `0x13`(Value Not Allowed)을 돌려준다.

| op | 인자 | 뜻 |
|---|---|---|
| `01` | `00` 끔 · `01` 켬 · `02` 토글 | 켜면 마지막 속도로 돈다 |
| `02` | `00`~`64`(0~100) | 세기(100 = 듀티 60 %), `00` = 끔(세기 설정은 기억) |
| `03` | `00`~`03` | 단계(1·2·3 = 듀티 30·45·60 %, 세기 50·75·100), `00` = 끔 |
| `04` | `00` 일반 · `01` 자연풍 | |
| `05` | `00` 정 · `01` 역 | 돌던 중이면 0.4 s 멈췄다가 반대로 |
| `06` | u16 LE 초 | 꺼짐 타이머, `00 00` = 해제. 끄면 타이머도 지운다. 프리셋 길이(1800·3600·7200·14400)면 타이머 버튼이 그다음 단계로 이어 간다 |

**명령 — 텍스트**(nRF Connect·USB 시리얼용, 대소문자 무관, `;` `,` 줄바꿈으로 여러 개):
`on` `off` `toggle` · `speed N` · `level N` · `natural` `normal`(= `mode …`) · `forward` `reverse`(= `dir …`) ·
`timer N`(초) `timer Nm`(분) `timer 0` · `status`.

**상태 — 8바이트:** `[버전=1, 전원 0/1, 속도 %, 단계 0~3, 모드 0/1, 방향 0/1, 남은 타이머 초 LE 2바이트]`.
명령·버튼·타이머 만료 때마다 알림이 나간다. 꺼져 있어도 속도는 다음 `on` 을 위해 남아 있다.

## Pi 4 명령으로 켜고 풍량 바꾸기

```
Pi 4 hub ── MQTT control/cmd (vent_fan) ──▶ Pi 5 앱 ── 블루투스 GATT ──▶ 선풍기
         ◀── MQTT control/result ──────────          ◀── 상태 읽기 ──────
```

hub 의 환경 개입 대상 `vent_fan`(`config/control.yaml`)이 이 선풍기다. Pi 5 앱이 램프와 같은 Atlas 블루투스 경로로 명령을 쓴다.

1. Pi 5 앱 → 블루투스 탭 → **기기 검색** → `DESKMATE-FAN` 의 **선풍기 선택**. 저장돼서 앱을 다시 켜도 검색 없이 붙는다.
2. 같은 화면의 **상태 기반 피드백 스위치를 켠다**(꺼져 있으면 허브 명령을 `feedback_disabled` 로 거절한다).
   **선풍기 1단/3단/끄기 테스트** 버튼으로 블루투스 쓰기를 먼저 확인한다.
3. 허브 없이 경로만 확인: 노트북에서 `python tools/send_control.py --broker <Pi 4 IP> vent_fan set_level 3` → `✓ succeeded actual=3`.
4. 허브가 보내는 명령은 `config/control.yaml` 의 `actions.environment` 에 적힌 대로다. 지금 기본은 `set_power on`(되돌리기 `off`)이고,
   풍량으로 켜려면 `{target_id: vent_fan, operation: set_level, value: 3, undo_value: 0}` 처럼 바꾼다(코드 변경 없음).

받는 operation: `set_power` on|off · `set_level` 0~3 · `set_speed` 0~100 · `set_mode` normal|natural · `set_timer` 초.
앱은 쓴 뒤 상태 특성을 다시 읽어 `actual_value` 로 돌려준다. Atlas 의 읽기(`ReadCharacteristicValues`)는 아직 실기 미확인이라,
읽기가 안 되면 램프처럼 쓰기 완료를 성공으로 본다. 계약·거절 조건은 `docs/mqtt-topics.md`.

Pi 5 없이 노트북으로 대신할 때는 `python tools/ble_fan.py bridge --broker <Pi 4 IP>` — Pi 5 앱과 **같이 띄우지 않는다**
(블루투스 연결이 하나뿐). Pi 5 앱이 선풍기에 붙어 있는 동안에는 휴대폰 리모컨도 못 붙는다. 본체 버튼은 언제나 된다.

## 빌드

Pico SDK 2.x(BTstack·cyw43-driver 서브모듈 포함), Arm GCC, CMake, Ninja. RP2350 UF2 는 picotool 이 만드는데,
없으면 SDK 가 GitHub 에서 받아 빌드한다(호스트 C++ 컴파일러 필요). Windows 에 호스트 gcc 가 없어 WSL Docker 로 빌드했다:

```bash
# 한 번: ubuntu:noble + cmake ninja-build gcc-arm-none-eabi libnewlib-arm-none-eabi libstdc++-arm-none-eabi-newlib build-essential git python3 → pico-build:local
MSYS_NO_PATHCONV=1 wsl docker run --rm -v <pico-sdk>:/sdk:ro -v <이 폴더>:/fw:ro -v <이 폴더>/build:/out pico-build:local bash -c \
  "cp -r /fw /tmp/fw && rm -rf /tmp/fw/build && cmake -S /tmp/fw -B /tmp/b -G Ninja -DPICO_SDK_PATH=/sdk && cmake --build /tmp/b && cp /tmp/b/deskmate_ble_fan.uf2 /out/"
```

리눅스·macOS 나 VS Code "Raspberry Pi Pico" 확장에서는 평범하게 `cmake -B build -DPICO_SDK_PATH=… && cmake --build build`.
`PICO_BOARD` 는 CMakeLists 에서 `pico2_w` 로 고정.

**호스트 테스트**(하드웨어 없이 명령 해석·타이머·버튼·단계별 듀티·출발 킥·역회전 정지·자연풍 범위·상태 바이트, 113 검사):

```bash
cd test && gcc -std=c11 -Wall -Wextra -Werror -I../src ../src/fan_core.c test_fan_core.c -o test_fan_core && ./test_fan_core
```

PC 도구 테스트: `pytest tools/test_ble_fan.py`(21, bleak·BLE 없이).

## 구조

| 파일 | |
|---|---|
| `src/fan_core.{h,c}` | 하드웨어 없는 선풍기 논리 — 명령 해석(바이너리·텍스트, 원자 적용), 단계, 타이머 프리셋, 킥, 자연풍, 버튼, 상태 인코딩 |
| `src/main.c` | PWM(GP16·17 같은 슬라이스), BTstack GATT 서버·광고, 버튼 3개 디바운스, USB 시리얼 줄 입력, LED 대답, 워치독 8 s |
| `src/deskmate_fan.gatt` | GATT 정의 → 빌드 때 `deskmate_fan.h` 생성 |
| `src/btstack_config.h` | 연결 1개 LE 주변기기 설정 |
| `remote/index.html` | Web Bluetooth 리모컨(`?demo` 로 선풍기 없이 화면 시험) |

BTstack 은 `pico_cyw43_arch_poll` 위에서 돈다 — 20 ms BTstack 타이머 하나가 버튼·USB·모터·LED·워치독을 모두 처리해 인터럽트 문맥이 없다.
보안: 페어링이 없어 블루투스 범위 안 누구나 켜고 끌 수 있다(시연용). 되돌릴 수 없는 동작은 없고, 부팅 직후 모터는 항상 꺼져 있다.
