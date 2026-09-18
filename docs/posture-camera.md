# ESP32-CAM 자세 판정 — 통합 앱의 `자세` 화면

앉아 있는지, 자세가 무너졌는지를 Pi 5 화면에 크게 띄운다. 코드는
[`display/atlas/app/lib/posture/`](../display/atlas/app/lib/posture/) 에 있고, 화면은 통합 앱의
`자세` 탭이다. (2026-09-18 이전에는 `display/atlas/camtest` 라는 별도 앱이었다.)

판정을 내는 곳이 둘이고, 화면은 어느 쪽이든 같게 그린다.

```text
1) camsvc (권장)  ESP32-CAM ─UART─> 판정 서비스(MediaPipe 두 모델 + 스켈레톤 판정)
                              ─HTTP 127.0.0.1:8770─> 앱
2) 보드 직결      ESP32-CAM ─UART─> 앱(프레임 디코딩 + coverage 판정)
```

**1번이 앱 안에서 될 수 없는 이유**는 Dart 로 TFLite 를 돌릴 길이 없기 때문이다. 그래서 모델을
쓰는 판정은 네이티브 서비스([`display/atlas/camsvc`](../display/atlas/camsvc/))로 나가 있고, 앱
안의 2번은 그게 없을 때의 대비책으로 남는다. 둘 다 원본 파이썬을 옮긴 것이고 옮긴 결과가
원본과 같은지는 각자 골든 벡터로 채점한다
(`display/atlas/app/test/posture/golden/posture_golden.json` · `camsvc/test/pose_golden.txt`).

서비스도 센서도 못 잡으면 **저장된 주소 → 빌드에 박힌 `DESKMATE_POSTURE_URL` → 화면 내장 데모**
순으로 떨어진다. 화면 코드는 어느 경로든 같다.

## 왜 RP2040 이 경로에 남아 있나

ESP 는 **coverage(`DIFF54`, zone 별 배경 대비 차이 0~255)만** 보낸다. 판정이 먹는 **이진 마스크는
RP2040 의 `vision.c` 가 만든다**(임계값 40 + 열림·채움·닫힘). ESP 를 Pi 5 GPIO 에 직결하면 그
단계가 빠져서, 프레임은 CRC 오류 없이 도착하는데 마스크가 영영 안 온다. 그래서 브리지를 USB 로
꽂는다.

RP2040 을 빼려면 `vision.c` 의 마스크 생성을 Dart 로 옮겨야 한다(~200줄).

## 보드 준비 — 부팅할 때마다 한 번

```bash
scp display/atlas/scripts/board-uart-setup.sh root@<보드IP>:/tmp/
ssh root@<보드IP> sh /tmp/board-uart-setup.sh
```

두 가지를 맞춘다. **둘 다 안 하면 앱이 데모로 떨어진다.**

| | 왜 |
|---|---|
| 장치 권한 | 기본이 `root:tty 0620` 이라 앱이 못 읽는다. 앱이 가진 `perm_peripheral` 그룹으로 바꾼다 |
| `stty` 921600 raw | Dart 에 termios 가 없다. ATLAS 의 UART `Open(u)` 은 속성만 바꾸고 실제 회선 속도를 안 건드린다(실기 확인) |

스크립트는 `Vision Stream` 이라고 적힌 포트를 이름으로 골라 준다. 보드는 CDC 두 개짜리 복합
장치라 포트가 둘 뜨는데, 인터페이스 0(브리지)을 열면 **포트는 멀쩡히 열리고 프레임만 영영 안
온다.**

`/dev` 권한과 `stty` 는 재부팅하면 사라진다.

## D-Bus 정책 (한 번만)

`display/atlas/app/atlas/meta/com.atlas.app.deskmate_display_ui_test.conf` 를
`/data/share/usr/share/dbus-1/system.d/` 에 두고 `systemctl reload dbus`.

기본 정책은 `perm_peripheral` 그룹에 `EnablePeripheral`·`DisablePeripheral` 만 열어 주고 UART
인터페이스는 root 만 통과시킨다. 앱이 지금은 장치 파일을 직접 읽으므로 필수는 아니지만, ATLAS
UART API 경로(`AtlasUart`)를 쓸 때 필요하다. 앱 쪽에서는 `appinfo.json` 의
`required_permissions.privileged` 에 `com.atlas.permission.privileged.peripheral` 이 있어야
그 그룹에 들어간다.

## 화면

| 자리 | 보여주는 것 |
|---|---|
| 착석 배지 | `착석 중` · `자리 비움` |
| 가운데 원 | 바른 자세 · 엎드림 · 뒤로 젖힘 · 졸음 · 자리 비움 · 기준 측정 중 |
| 지표 넷 | 머리 거리(기준 대비, − 가까움) · 꾸벅임(회/분) · 움직임 · 화면 점유 |
| 막대 둘 | 집중 저하 · 피로 — 허브 `Signal(phi, delta)` 로 그대로 들어가는 값 |
| 근거 칩 | 판정이 실제로 본 축 |
| 👁 **센서 보기** | 보드 직결일 때. 센서가 지금 보고 있는 것. 초록 = 마스크, 회색 = coverage |
| 🎥 **영상 보기** | camsvc 일 때. 카메라 그림 + 뼈대 |
| 🔥 **열화상 보기** | 영상 보기의 기본 화면. 54x42 zone + 열화상 팔레트 + tee 뼈대 + HUD |
| 🌀 **차이값 보기** | 같은 자리에서 한 번 더 누르면. 배경차분 coverage/마스크 |
| ⚙ 주소 | Pi 4 노드를 대신 볼 때 쓰는 IP 키패드 |
| `기준 다시 잡기` | 캘리브레이션을 처음부터 |

**센서 보기**가 있는 이유: 라벨만 보면 "지금 잡히고 있는 건지" 를 사람이 알 수 없다. 화각을
벗어났는지, 몸이 잘렸는지, 배경이 잘못 잡혔는지는 눈으로 봐야 안다.

## 카메라를 54x42 로 보기

보드 직결 경로에는 54x42 격자 화면이 원래 있었지만, 판정 서비스(camsvc)에 붙으면 원본 카메라
그림만 볼 수 있었다. 같은 그림을 두 경로가 다르게 보여 주면 서로 맞춰 볼 수가 없어서, camsvc
쪽에도 격자를 붙였다. 영상 보기 오른쪽 위 버튼이 **열화상 → 카메라 → 차이값** 을 돌린다
(원본 도구의 `m` 키와 같다).

둘 다 [`pico_esp32-cam_ftdi`](https://github.com/76EHwan/pico_esp32-cam_ftdi) 에서 옮겼다.

### 열화상 (기본) — `tools/thermal_pose.py`

노트북에서 그 도구로 보던 그림이다. `feat/thermal-pose-tee-zones` 판을 옮겼다.

```text
흑백 preview ──> camsvc 랜드마크 ──┐
      │                            │
      ▼                            ▼
54x42 축약 → CLAHE → 팔레트  ←── tee 뼈대를 그 위에
```

- **CLAHE** `clipLimit 2.5` · `8x8` 타일. 전역 평활화를 쓰면 배경이 넓은 프레임에서 사람이
  뭉개지고, 타일 LUT 를 이중선형으로 안 섞으면 타일 경계가 격자로 드러난다.
- **팔레트**는 앱의 `heat_palette.dart` 를 그대로 쓴다. 그쪽 정지점이 원본 `palette.py` 의
  `RAINBOW_HC`(기본 팔레트)와 같은 값이라, 같은 장면을 노트북 도구와 보드가 같은 색으로 그린다.
- **tee 뼈대**는 머리·어깨 가로대·가운데서 올라온 기둥·양쪽 팔이다. `CHEST` 는 MediaPipe
  랜드마크가 아니라 양 어깨의 중점으로 그때 만든다 — 머리를 어깨 둘에 각각 이으면 삼각 천막이
  되는데, 정면에서 본 상반신은 가로대에 기둥이 선 모양이다.
- **HUD** 의 `spread`(max−min)가 그림이 쓸 만한지 말해 주는 숫자다. 가려진 렌즈와 날아간
  프레임은 둘 다 평평해서, 화면에서는 모델이 죽은 것과 구분이 안 된다. 24 아래로 내려가면
  경고색으로 바뀐다.
- 격자선은 `g` 에 해당하는 버튼으로 켠다. 칸이 정수 픽셀이 아닐 때(800px / 54칸 = 14.81)
  고정 간격으로 그으면 오른쪽 끝에서 세 칸이 밀리므로, 칸 경계를 올림으로 계산해 얹는다.

**이건 열화상 카메라가 아니다.** ESP32-CAM 은 빛을 재지 온도를 재지 않는다. 밝기를 열화상
팔레트에 통과시킨 가짜 색이고, 어떤 픽셀값도 도(℃)가 아니다. 원본 `use_seg`(체열 분할)는
사람 마스크가 있어야 하는데 camsvc 가 주지 않아 옮기지 않았다.

### 차이값 — `tools/camera_source.py`

보드 직결 경로의 `센서 보기` 와 같은 그림이다. 순서와 상수(`BG_PERIOD 4` · `BG_GUARD 24` ·
`GAIN_Q4 16` · 임계 40)가 그쪽과 같다.

```text
흑백 → 시그마-델타 배경 → |현재-배경| → 54x42 로 박스 축약
                                            |
        최대 블롭 ← 열림 ← 구멍 채움 ← 임계
```

**이건 센서가 아니라 대역이다.** 카메라의 자동 노출·색 처리·해상도가 ESP 와 달라서 여기서 맞춘
임계값을 보드에 그대로 옮기면 안 된다. 재현되는 것은 신호의 *구조* — 밝기 차이는 피사체가
배경과 밝기로 다를 때만 잡힌다는 것이고, 그게 실제로 문제가 되는 실패다. 판정에는 쓰지 않는다.

배경 모델은 연속된 프레임으로만 서므로, 화면을 전환하지 않아도 계속 먹인다. 처음 20프레임은
배경을 심는 구간이라 빈 격자가 나온다 — 화면이 "배경을 잡는 중" 이라고 말한다. 사람이 이미
화각 안에 있는 채로 시작했으면 오른쪽 위 `기준 다시 잡기` 로 다시 심는다.

## 캘리브레이션

보드에는 키보드가 없으므로 PC 뷰어의 SPACE 두 번을 시간으로 돌린다. **순서가 핵심**이다.

```text
clear(8초 비켜) → settle(센서에 b) → background(빈 책상) → sit(10초 앉기) → baseline → live
```

**앉는 10초 동안의 자세가 '바른 자세'의 정의가 된다.** 그때 숙이고 있으면 그게 정상으로 굳어서
나중에 엎드려도 UPRIGHT 이 나온다.

## 판정이 원본과 같은지

`lib/posture/posture_judge.dart` 는 `espcam_with_decision` 의 `tools/posture.py` 를 옮긴 것이다.
옮긴 결과가 같은 숫자를 내는지는 골든 벡터로 채점한다.

```bash
# 원본 레포에서 정답지를 다시 만들 때
python tools/gen_posture_golden.py <이 레포>/display/atlas/app/test/posture/golden/posture_golden.json
```

10개 시나리오 1,130 프레임을 프레임마다 비교한다 — 라벨뿐 아니라 `phi·delta·nod_rate`·features
7종·`parts` 전 키까지. **이 테스트가 깨지면 포팅이 틀린 것이다. 골든 파일을 고쳐서 맞추지 말
것.**

## 진단

이 보드는 앱 표준출력이 journal 에 안 남는다. 그래서 앱이 직접 로그를 쓴다.

```bash
ssh root@<보드IP> 'tail -f /data/share/usr/atlas/apps/com.atlas.app.deskmate_display_ui_test/deskmate.log'
```

`link: 바이트 … · 프레임 … · fps · 버린 … · CRC오류 … · 단계 … · 판정 …` 한 줄로 어디가
막혔는지 가른다 — 바이트가 0이면 배선·권한, 바이트는 오는데 프레임이 0이면 속도(`stty`)나 프레임
타입 문제다.

## 아직 안 된 것

- 실기에서 장시간 검증. 프레임 수신·판정·화면까지는 확인했다(12.5fps, CRC 오류 0)
- 부팅 시 자동 시작. 지금은 `abusctl call com.atlas.AppManager1 Start "com.atlas.app.deskmate_display_ui_test"`
- `board-uart-setup.sh` 의 영구화(udev/부팅 스크립트). 루트 파일시스템이 읽기 전용이다
