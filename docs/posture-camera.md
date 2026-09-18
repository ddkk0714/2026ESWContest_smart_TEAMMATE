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
| 👁 **센서 보기** | 센서가 지금 보고 있는 것. 초록 = 마스크, 회색 = coverage |
| ⚙ 주소 | Pi 4 노드를 대신 볼 때 쓰는 IP 키패드 |
| `기준 다시 잡기` | 캘리브레이션을 처음부터 |

**센서 보기**가 있는 이유: 라벨만 보면 "지금 잡히고 있는 건지" 를 사람이 알 수 없다. 화각을
벗어났는지, 몸이 잘렸는지, 배경이 잘못 잡혔는지는 눈으로 봐야 안다.

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
