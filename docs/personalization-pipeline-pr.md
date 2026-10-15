# L8 개인화 입력 연결 PR 준비 기준

## 이번 PR 범위

기존 PC 공통 Backbone 학습·Pi 4 고정 Backbone/개인 head 파이프라인에 같은 ToF 특징 프로필을 연결한다.
L8 8×8은 기존 ESP32의 I2C(GPIO21/22)에서 읽고 자세·움직임 등 스칼라 특징만 UART2
(TX25/RX26, 115200 8N1, COBS+CRC16)의 TYPE 0x03, 32 B로 보낸다.
Pi 4의 기존 UART 브리지·ToF 캐시·모델 입력 경로를 재사용한다.

리뷰 기준은 로컬 main이 아닌 origin/main 기준으로 확인한다. 이번 작업 시작 시 origin/main은
0a58a8f, 브랜치 HEAD는 0d26c3f였다. 이후 원격 main 변경 여부는 실제 PR 직전에 다시 확인한다.
push·merge·브랜치 전환·보드 업로드는 이 준비 작업에 포함하지 않는다.

## PR 전에 확인할 것

- hub 전체 회귀와 실제 C++ 특징/COBS/CRC 송신 → Pi 4 파서 교차 시험.
  정상·기준선 준비 중·자리 비움·선택 특징·센서 누락·초기화/읽기 오류·유효 zone 소실·
  999/1000 ms stale 경계·millis wraparound·재연결·CRC/길이/버전/수치 오류를 포함한다.
- ML 전체 시험. 실제 공통 CNN 학습·portable/TFLite 변환·공통 모델 출력 대조·개인 head 업데이트를
  TensorFlow가 있는 환경에서 실행한다. Backbone 불변을 확인하고 TensorFlow 부재에 따른 skip을
  학습 검증 완료로 세지 않는다. 합성 시험은 실사용자 정확도의 증거가 아니다.
- PlatformIO esp32dev·tof_logger 빌드와 L8 경로 포함 컴파일. 학습용 logger 파일은 보존한다.
- wire 계약과 PC 재현 명령 갱신, git diff --check, 원시 ToF·자격증명·개인 로그·모델·빌드 산출물의
  Git 포함 여부 점검. ToF 송신·hub 매핑·개인화·정책 승인은 기본 비활성으로 유지한다.

재현 명령:

~~~powershell
py -m venv .venv-ml
.\.venv-ml\Scripts\python.exe -m pip install -r ml/requirements-training.txt pytest pyyaml
py -m pytest hub/tests -q -rs
.\.venv-ml\Scripts\python.exe -m pytest ml/tests -q -rs
py tools/uart_frame_tool.py encode tof --seq 7 --ts 1000
py tools/uart_frame_tool.py vectors
# firmware/esp32_sensor_node에서 실행
py -m platformio run -e esp32dev -e tof_logger
~~~

학습 환경은 저장소 제외 경로 .venv-ml에 두고 ml/requirements-training.txt의 TensorFlow 2.20.0을 사용한다.
고정 ToF payload는 Python/C++ 간 바이트 단위로 일치해야 한다. CLI 입력의 raw 배열은
특징 allowlist에서 제거되며 운영 ToF raw를 재현 데이터로 쓰지 않는다.

## 검증 기록 (2026-10-15)

- hub: 320 passed, 1 skipped, 1 xfailed. skip은 Windows symlink 생성 제한이고 xfail은 기존 결측 신호 재정규화 항목이다.
- ML: TensorFlow 2.20.0 격리 환경에서 39 passed, skip 없음. 실제 공통 CNN 학습·portable/TFLite 변환·출력 대조·개인 head 업데이트 및 Backbone 불변 시험을 포함한다.
- ML 의존성 경고 65건: Keras/NumPy array 변환의 비권장 API와 tf.lite.Interpreter 이전 안내. 실패는 아니며 이번 센서 연결 변경으로 의존성 API를 교체하지 않는다.
- PlatformIO: esp32dev·tof_logger 모두 SUCCESS. 같은 L8 드라이버 소스의 DESKMATE_TOF_ENABLED=1 빌드도 앞선 검증에서 통과했다. 기본 산출물은 비활성 설정으로 다시 빌드했다.
- ToF 전용 시험: 실제 C++ 드라이버·feature/frame 인코더와 PC 참조 인코더·CLI 벡터를 대조했다. raw/미등록 필드를 넣어도 같은 32 B 특징 payload만 출력된다.
- git diff --check 통과. logger·hub 활성화/동의/정책 설정 변경 없음. 가상환경·모델·로그·빌드 결과는 Git 포함 대상에서 제외된다.
- 실기: Pi5 SSH 접속과 유선 상태 조회는 수행했다. Pi4 재부팅 뒤에도 Pi4 응답은 없었고 마지막 Pi5 접속도 timeout이었다. Pi5 유선 수신은 확인 시 0 packet이었으므로 L8/ESP32/Pi4 동작 증거로 세지 않는다. 임시 유선 100Mbps 시험은 자동 협상으로 복원했다.

자동 검증 근거로 기본 비활성인 구현 PR을 준비할 수 있다. 실기 사용 준비 완료를 의미하지 않는다.
이 절은 PR 게시 전 로컬 검증 기록이다. 실제 게시 상태와 포함 커밋은 GitHub PR을 기준으로 확인한다.

## 실기 검증은 별도로 기록

이 PR은 기본 비활성인 연결 구현으로 리뷰할 수 있다. 아래 항목을 못 했으면 PR 본문에
미검증으로 적고 실기 완료·성능 개선·제품 개인화 완료로 표현하지 않는다.

1. 동일 소스의 승인된 펌웨어/IPK로 L8 → ESP32 → Pi 4 실제 0x03 수신을 확인한다.
   업로드·ToF 활성화는 기존 금지 조건이 해제된 뒤 별도로 진행한다.
2. 센서 누락·읽기 오류·갱신 중단 시 유효성 해제, 복구 후 기준선 재확보를 실기로 확인한다.
   Pi 4 캐시는 마지막 수신 후 YAML의 5초 freshness를 적용한다.
3. 환경 센서와 공유하는 I2C에서 실제 프레임률, C1001·환경/UART 유실 및 CRC 오류,
   초기화/재초기화 중 loop 지연을 측정한다. 설정 15 Hz는 실측 처리율 보장이 아니다.
4. Pi 4 제한 Python import·합성 self-check·고정 Backbone/개인 head 학습을 확인하고,
   승인된 정책·참여자 동의 후 실제 수집·독립 세션 평가·채택/거절·재시작·철회를 확인한다.
   상세 절차는 [기존 실기 인계](ondevice-field-validation.md)를 따른다.

현재 전송 시각은 ESP32 uptime이며 hub freshness는 수신 시각 기준이다.
전송 큐에서 오래 지연된 프레임의 측정 age를 복원하는 시계 동기화는 미구현이다.
현재 L8 초기화는 수 초간 loop를 지연시킬 수 있다. 실기 결과에 따라 별도 최적화를 판단한다.

## PR 본문 초안

### 무엇을

ToF 스칼라 특징을 기존 공통 Backbone/개인 head의 입력 프로필로 연결한다.
기존 ESP32에 L8 드라이버와 특징 전용 UART 0x03 송신을 추가하고 Pi 4 ToF 캐시에 연결한다.
읽기 실패·갱신 중단·재연결을 처리하며 기본 활성화·동의·정책 설정은 유지한다.

### 왜

학습용 USB raw logger와 별도로, 운영 센서의 특징만 기존 UART 경로로 공급해야 한다.
PC 학습과 Pi 4 개인 head 입력에 같은 특징 의미를 사용하고 센서 오류가 이전 정상 자세를 재사용하지 않게 한다.

### 확인 방법

위 자동 검증 명령과 검증 기록을 따른다. 실제 L8·Pi 4 런타임·공유 I2C 처리율·사용자 개인화는
별도 실기 확인 요청 항목이다. 모델은 기존 observe 경로이며 FSM·제어 결합은 추가하지 않는다.

### 제출 직전 점검

UART 데이터 계약을 docs/data-spec.md에 기록했다. 이 작업의 UART 추가는 MQTT topic이나
FSM 상태·임계값을 바꾸지 않는다. 브랜치의 기존 ToF MQTT 계약은 docs/mqtt-topics.md에 기록돼 있다.
학습 모델·수집 데이터·개인 로그·IPK·빌드 산출물·가상환경을 커밋하지 않는다.
