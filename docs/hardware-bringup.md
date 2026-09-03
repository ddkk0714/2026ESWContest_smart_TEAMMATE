# Pi 4 FSM → Pi 5 Atlas 화면 하드웨어 연결

> 목표: 센서를 연결하기 전에 합성 입력으로 두 보드·화면·터치를 먼저 검증한다.
> 보드 간 최종 통신은 미정이며, 이 단계의 TCP 8765 HTTP는 개발용 미리보기 어댑터다.

## 0. 배포 경로

Pi 5 앱은 Pi 5에서 소스 빌드하거나 Docker로 실행하지 않는다.

```text
개발 PC Docker → flutter-atlas build atlas --ipk → SSH → Pi 5 AI Native OS 설치·실행
```

개발 PC에서 [Atlas 개발 가이드](../display/atlas/README.md)에 따라 `.ipk`를 만든 후 Pi 5를
custom device로 등록한다. `flutter-atlas run` 콘솔과 Pi 5 화면을 함께 보면서 검증한다.

## 1. 지금 연결할 것

| 순서 | 장치 | 연결 |
|---|---|---|
| 1 | Pi 4 | 정격 전원, 유선 LAN(권장) 또는 같은 Wi-Fi 공유기 |
| 2 | Pi 5 | 27W USB-C PD 전원, 액티브 쿨러, Pi 4와 같은 LAN |
| 3 | 7인치 화면 | 아래의 **실제 화면 인터페이스 한 종류만** 선택해 Pi 5에 연결 |
| 4 | 개발 PC | 같은 LAN. SSH와 Atlas 패키지 전송에 사용 |

화면 연결은 반드시 Pi 5 전원을 끈 상태에서 한다.

- DSI 화면이면 Pi 5의 `DISPLAY` 커넥터와 화면을 해당 제품용 22↔15핀 FFC/어댑터로 연결하고,
  케이블 접점 방향과 별도 5V/GND 전원은 화면 제조사 설명서를 따른다.
- HDMI+USB 터치 화면이면 Pi 5 micro-HDMI→화면 HDMI와 화면의 USB 터치 케이블을 모두 연결한다.
- 두 방식의 전원·리본 연결법을 섞지 않는다. 모델명이 확인되지 않은 화면에 GPIO 5V를 임의로 넣지 않는다.

### 현재 보유 화면의 USB 터치 관찰값

- 터치 컨트롤러는 USB `0416:c168`, 제품 문자열 `TSTP MTouch`로 열거된다.
- Pi 5 부팅 시 `usb 1-1`의 `xhci-hcd.0`에서 장치를 찾지만 입력 event 노드를 만들지 못한다.
- 약 10초 뒤 `xHCI host controller not responding, assume dead`가 발생하고 장치가 분리된다.
- 재부팅해도 같은 순서로 재현됐다. 현재 상태에서는 터치 이벤트가 앱까지 전달되지 않는다.
- **정정(2026-09-01 실측):** 키보드는 별도 컨트롤러가 아니다. 터치와 **같은** `xhci-hcd.1` 에 물려 있고
  컨트롤러가 죽을 때 함께 떨어진다. `dmesg` 상 순서는 다음과 같다.

  ```
  [ 3.654] input: Logitech USB Keyboard ... xhci-hcd.1/usb3/3-1
  [ 4.174] usb 3-2: Product: MTouch            (터치, 같은 컨트롤러)
  [14.996] xhci-hcd.1: xHCI host not responding to stop endpoint command
  [15.023] xhci-hcd.1: xHCI host controller not responding, assume dead
  [15.031] xhci-hcd.1: HC died; cleaning up
  [15.036] usb 3-1: USB disconnect              ← 키보드
  [15.304] usb 3-2: USB disconnect              ← 터치
  ```

  즉 **터치만의 문제가 아니라 `xhci-hcd.1` 컨트롤러 전체가 부팅 15초 뒤 죽는 문제**다.
  살아남는 Logitech 마우스는 다른 컨트롤러(`xhci-hcd.0`)에 있다. 죽은 뒤에는 그 컨트롤러
  포트에 무엇을 꽂아도 `dmesg` 에 아무 줄도 남지 않는다 — 이게 죽었는지 판별하는 기준이다.
  전원 문제로 보고 있으며, 확인 전까지 그 컨트롤러 포트에 장치를 늘리지 않는다.
  당장 키보드가 필요하면 마우스와 같은 컨트롤러 쪽 포트를 쓴다.
- 커널 로그에는 명시적인 저전압·과전류 경고가 없었지만, 이 사실만으로 전원·역급전 문제를 배제하지 않는다.
- 정확한 화면 모델과 전원·USB 배선도를 확인하기 전 `5V+GND`와 USB의 5V를 동시에 연결하거나
  반복 재연결하지 않는다. 우선 알려진 정상 USB 포트/데이터 케이블과 단일 전원 경로를 확인한다.

## 2. Pi 4에서 FSM 데모 실행

### IP 변경에 대비한 SSH 접속

Pi 4 Headless 보드는 DHCP를 사용한다. 마지막 확인값은 다음과 같으며, 고정 주소가 필요하면
보드에 임의의 정적 IP를 넣지 말고 네트워크 관리자에게 유선 MAC 기준 DHCP 예약을 요청한다.

- 보드: Raspberry Pi 4 Model B Rev 1.2
- 유선 MAC: `DC:A6:32:85:F3:72`
- 마지막 확인 IP: `172.16.34.146/24` (DHCP이므로 고정값이 아님)
- 로컬 SSH 별칭: `atlas`, `rpi4`, `deskmate-pi4`

개발 PC에서 주소를 다시 찾고 SSH 설정을 갱신하려면 저장소 루트에서 실행한다. 스크립트는
현재 연결된 사설 `/24` 대역과 기존 `172.16.34.0/24`에서 SSH 장치를 찾고, 원격의
`/proc/device-tree/model`이 실제 Raspberry Pi 4인지 확인한 뒤에만 별칭을 갱신한다.

```powershell
PowerShell -ExecutionPolicy Bypass -File .\tools\connect-deskmate-pi4.ps1
ssh rpi4
```

개발 PC의 PowerShell 프로필에 래퍼가 등록돼 있으면 다음 한 줄로 탐색·갱신·접속한다.

```powershell
rpi4
```

`.local` mDNS 이름은 같은 브로드캐스트 구간에서만 동작하며 학교의 라우팅 LAN이나 VPN/WARP를
통과하지 않을 수 있으므로 유일한 접속 방법으로 사용하지 않는다. 검색 범위를 추가해야 하면
`-KnownSubnets '192.168.10.0/24'`처럼 명시한다. 인터넷 공인 대역은 검색하지 않는다.

Pi 4 런타임에 Python 3가 있는지 먼저 확인한다. AI Native OS Headless Profile에 Python/pip가
없다면 이 명령을 억지로 설치하지 말고, 우선 Raspberry Pi OS 개발 카드에서 검증하거나 제공된
Headless SDK의 애플리케이션 배포 방식으로 포팅한다.

```bash
python3 --version
hostname -I
cd ~/2026ESWContest_smart_TEAMMATE-main/hub
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m deskmate_hub demo --host 0.0.0.0 --port 8765 --interval 1
```

PC나 Pi 5에서 `curl http://<Pi4-IP>:8765/health`가 `status: ok`를 반환해야 한다. 공유기에서
Pi 4 IP를 DHCP 예약하고, 방화벽을 쓰는 경우 같은 LAN에서 TCP 8765만 허용한다. 인터넷 공개나
공유기 포트 포워딩은 하지 않는다.

## 3. Pi 5 Atlas 화면 배포·실행

1. 개발 PC Docker에서 URL 없이 debug 실행해 Pi 5의 내장 데모와 터치를 확인한다.
2. `DESKMATE_HUB_URL=http://<Pi4-IP>:8765`를 넣어 debug 실행한다.
3. 헤더가 `화면 내장 데모`에서 Pi 4 IP로 바뀌고 FSM 상태와 화면 상태가 같아야 한다.
4. 피로 화면에서 `진행`/`아니요`를 눌러 Pi 4 콘솔에 feedback이 표시되는지 확인한다.
5. 통과한 소스로 `flutter-atlas build atlas --ipk --release`를 실행해 시연 후보를 고정한다.
6. `flutter-atlas run -d <device_id> --release`로 release 패키지를 다시 설치·실행한다.

Atlas 개발·빌드 명령은 [`../display/atlas/README.md`](../display/atlas/README.md)를 따른다.

## 4. 아직 연결하지 않을 센서

첫 두 보드 데모가 끝날 때까지 ESP32·VL53L9CX·SEN0623·SCD41·BH1750은 연결하지 않는다.
UI/네트워크 문제와 센서 전기·드라이버 문제를 분리하기 위해서다.

화면 데모 완료 뒤에는 다음 순서로 한 종류씩 추가한다.

1. ESP32에 SCD41/BH1750 I2C: 공통 GND, SDA/SCL, 각 breakout의 허용 전압을 데이터시트로 확인.
2. ESP32에 SEN0623 UART: 센서 TX→ESP32 RX, 센서 RX→ESP32 TX, 공통 GND, 공급 전압은 모듈 사양 준수.
3. VL53L9CX: 보유 보드의 정확한 제품명·호스트 인터페이스·FFC/어댑터를 확인한 뒤 Pi 4 MIPI spike.
   실패하면 확정된 fallback인 ESP32 I2C 특징값 경로를 시험한다.

GPIO 번호와 공급 전압은 ESP32 보드 모델 및 각 breakout 제품명이 확정되기 전에는 고정하지 않는다.
사진과 제품 링크/실크 인쇄를 확인한 뒤 별도 배선표를 작성하고, 전원 인가 전에 멀티미터로 GND·전압을 확인한다.

## 5. 통과 기준

- [ ] Pi 4 `/health`, `/api/state` 응답
- [ ] 개발 PC에서 `flutter test` 통과
- [ ] release `.ipk` 생성 및 Git 미추적 확인
- [ ] `flutter-atlas run`으로 Pi 5 자동 업로드·설치·실행
- [ ] Pi 5에서 내장 데모 5상태 순환
- [ ] Pi 5에서 실제 Pi 4의 18상태 중 대표 경로 표시
- [ ] 수락·거절 터치가 Pi 4로 전달
- [ ] Pi 5 화면을 종료해도 Pi 4 FSM 순환 지속
- [ ] 센서·가전·인터넷 없이 위 항목 모두 동작
- [ ] 실행 오류와 DevTools URL을 run 콘솔에서 확인하고 결과 기록
