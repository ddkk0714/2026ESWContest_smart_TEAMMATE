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

## 2. Pi 4에서 FSM 데모 실행

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
