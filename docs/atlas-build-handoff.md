# Pi 5 Atlas IPK 빌드·배포 인계

> 기준일: 2026-09-14. Atlas 앱 소스와 세부 빌드 명령은
> [`../display/atlas/README.md`](../display/atlas/README.md)를 기준으로 한다.

## 현재 검증 상태

- 개발 PC Docker 환경에서 arm64 release `.ipk` 빌드와 Pi 5 설치·실행을 확인했다.
- Pi 5는 ATLAS Video Profile, Pi 4는 ATLAS Headless Profile이다. 보드에서 Docker를 실행하지 않는다.
- UI는 대시보드·18상태 그래프·키스트로크 패널·오디오·MQTT 구독을 포함한다.
- 교체된 디스플레이의 터치는 정상 동작한다. 이전 USB 터치 장애 기록은 더 이상 운영 절차가 아니다.
- Pi 5 앱은 아직 재부팅 자동 시작이 아니다. 실기 release 재검증과 자동 시작 등록이 남았다.
- 제품 통신은 Pi 4 Mosquitto를 경유한 MQTT다. `DESKMATE_MQTT_HOST`를 사용해 연결하며,
  HTTP Hub URL은 개발 fallback으로만 유지한다.

## 빌드와 배포 절차

1. 개발 PC에서 `display/atlas/README.md`의 Docker/Atlas SDK 준비 절차를 따른다.
2. 앱 디렉터리에서 분석·테스트를 통과시킨 뒤 `flutter clean`을 실행한다. debug 뒤 release를 빌드할
   때 번들 파일이 섞이지 않도록 하기 위함이다.
3. release IPK를 빌드하고 arm64 산출물임을 확인한다.
4. DHCP로 확인한 Pi 5 주소와 안전한 SSH 인증으로 `flutter-atlas run -d <device_id> --release`를
   실행해 설치·기동한다. IP를 소스·문서의 영구 고정값으로 기록하지 않는다.
5. Pi 5에서 터치 화면 전환, MQTT 상태 갱신, 피드백 발행, 앱 재기동 후 동작을 확인한다.

## 실기 점검표

- [ ] Pi 5의 현재 DHCP 주소와 SSH 인증 방식을 현장에서 확인했다.
- [ ] Pi 4 broker 주소로 MQTT 연결되고 `state/phase`, `interaction/request`, `display/message`를 수신한다.
- [ ] `feedback/user` 발행이 Pi 4에서 수신된다.
- [ ] 터치로 대시보드·상세·제안·리포트 화면을 전환할 수 있다.
- [ ] 전원 재인가 후 필요한 서비스가 자동 시작된다.

## 남은 연동 작업

1. Pi 4 Hub가 UART·키스트로크 입력을 `SensorFrame`으로 통합하고 MQTT 상태를 발행한다.
2. Pi 5에서 실제 Hub MQTT 연결로 상태·제안·피드백 왕복을 검증한다.
3. 앱과 Hub의 자동 시작을 등록하고, 부스 전원 재인가 시나리오를 검증한다.

자격증명·개인키·고정 IP 요청은 채팅이나 저장소에 넣지 않는다. 네트워크가 바뀌면 DHCP 예약 또는
현장 주소 확인으로 대응한다.
