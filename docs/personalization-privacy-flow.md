# 개인화 동의·취소·삭제 흐름 (2026-10-07)

3번 개발의 UI와 hub 연결을 구현했다. 저장 기간·사용자 분리 정책은 팀 결정이 남았으므로, 기본 설정은 기능 꺼짐·정책 미승인이다. 이번 코드는 단일 기기의 등록된 로컬 파일을 관리하는 기반이며, 다중 사용자 저장 방식이나 운영 보존 기간을 확정하지 않는다.

## 사용자 흐름

화면 상단의 개인화 관리 아이콘을 누른다.

- 동의: 승인된 정책과 버전을 확인하고 확인창에서 동의한다. hub가 동의 상태를 저장한 뒤에만 완료를 표시한다. 동의만으로 모델 설치나 학습을 시작하지 않는다.
- 동의 취소 및 삭제 / 개인화 데이터 초기화: 확인창을 거쳐 개인화 사용을 중지한다. 메모리 기준선과 관찰 모델·입력 창을 초기화하고, 설정에 등록한 baseline·개인 모델 파일을 삭제한다. 두 동작 모두 동의를 철회한다.
- 삭제 일부 실패: 성공 메시지를 표시하지 않는다. 동의는 먼저 철회하고 메모리 사용은 중지하며, 저장소 권한을 확인해 재시도하도록 표시한다.
- 연결 끊김·hub 응답 누락: 완료로 표시하지 않는다. 처리 결과를 확인한 뒤 재시도해야 한다.

삭제 대상은 로컬 baseline과 personal_model_files 목록이다. 공통 모델을 이 목록에 등록하지 않는다. ESM·세션·실험 로그와 PC 학습 데이터·모델은 이번 삭제 범위에 포함하지 않으며 화면에도 범위를 명시한다. 파일 unlink는 보안 덮어쓰기나 백업 삭제를 의미하지 않는다.

## 설정과 활성화 조건

hub/deskmate_hub/config/privacy.yaml에 다음 설정을 추가했다.

```yaml
enabled: false
policy_approved: false
policy_version: pending
data_root: logs
consent_file: logs/personalization/consent.json
personal_model_files: []
recent_request_limit: 64
```

팀 정책 승인 이후에만 policy_approved와 실제 정책 버전을 등록하고 enabled를 켠다. 실제 보드에서는 실행 계정이 접근할 수 있는 로컬 data_root·consent_file·baseline 경로를 명시해야 한다. 절대 경로 사용을 권장하며 배포에 맞춰 설정한다. 삭제할 개인 모델 bundle 파일만 같은 data_root 아래에 등록한다. 공통 bundle을 등록하면 안 된다.

baseline 저장은 기존 ingest.yaml의 baseline.persist.enabled와 경로도 설정해야 한다. 모델 관찰 추론은 기존 personalization.yaml의 enabled·model_path·metadata_path도 유효해야 한다. 새 관리 기능을 켜면 동의가 없는 동안 저장 경로를 연결하지 않고 모델을 불러오지 않는다. 정책 버전이 바뀌거나 동의 파일이 없거나 손상되면 다시 동의해야 한다.

기능 꺼짐 상태는 기존 개발 경로와 출력을 유지한다. 운영 활성화 전에는 기존 baseline 영구 저장과 모델 관찰의 기본 꺼짐 설정도 유지한다. 동의 관리 기능을 끄는 것으로 기존 수동 개발 설정을 운영 정책으로 승인하지 않는다.

## 안전한 삭제와 결과 확인

- MQTT 요청에서 파일 경로를 받지 않는다. 설정에 등록한 파일만 삭제한다.
- data_root 밖으로 나가는 경로와 symlink, 디렉터리 삭제를 거부한다. 재귀 삭제를 하지 않는다.
- baseline·개인 모델 경로가 동의 기록 파일과 겹치면 설정을 거부한다.
- 동의 기록은 로컬 JSON에 bool·정책 버전만 저장하며 임시 파일 교체로 쓴다. 새 파일은 0600, 새 개인화 디렉터리는 0700으로 생성한다.
- 최근 처리한 request_id의 재전송을 구분하고, 다른 hub 실행의 요청은 거부한다.
- 명령은 기존 feedback/user 경로를 사용한다. FSM 응답·ESM 라벨로 처리하지 않는다.
- state/phase.sensor_summary.privacy에 현재 동의·정책 준비 상태와 요청별 성공·실패를 실어 보낸다. 파일 경로·개인 값·사용자 식별자는 보내지 않는다.
- 화면은 일치하는 요청 ID의 hub 확인을 기다린다. 보드 native MQTT와 PC MQTT가 동일한 라우터를 사용한다.

hub_boot_id는 오래된 요청 방지용 상관 값이며 사용자 인증 수단이 아니다. 실제 운영 활성화에는 기존 MQTT 보안 계획의 display 권한 제한·ACL을 적용해야 한다.

## 검증

Python 시험은 동의 기본 거부·정책 버전 불일치·재시작 유지·철회·범위 제한·삭제 실패·메모리 모델 초기화·공통 파일 보존·MQTT 왕복 확인을 포함한다. 제한 Python import와 privacy.json 배포 패키징도 확인한다.

```powershell
$env:PYTHONPATH='hub;ml;collector'
python -m pytest hub/tests/test_privacy.py hub/tests/test_personalization.py hub/tests/test_board_runtime.py tools/test_intervention_mqtt.py -q
```

Flutter 화면 시험은 정책 미승인, 확인 취소, 처리 확인 대기, 연결 끊김을 포함한다. Atlas 개발 Docker에서 실행한다. 컨테이너를 매번 교체할 때는 pub cache를 유지하고 pub get을 먼저 실행한다.

```sh
flutter pub get
flutter test test/privacy_page_test.dart
flutter analyze --no-fatal-infos
```

Windows의 symlink 생성 권한으로 건너뛴 경로 보호 시험은 Linux 컨테이너에서 실제 symlink로 별도 통과했다. Pi 4 설치·파일 권한·보드 재부팅 후 동의 상태와 Pi 5 실제 터치는 미검증이다.

## 팀 결정과 남은 범위

2026-10-07 전체 회귀 검증: Python 337 passed, 1 skipped, 1 xfailed(기존 TensorFlow/Keras 경고 41개), Flutter 159 tests passed. Flutter 분석은 오류·경고 없이 기존 스타일 info 5개만 남았으며 `--no-fatal-infos`로 통과했다. Windows에서 건너뛴 symlink 보호는 Linux 컨테이너에서 별도로 확인했다.

저장 기간, 사용자별 분리·조회, ESM·실험 로그 보존과 삭제, PC 자료 삭제 및 백업 처리 정책은 미정이다. 그 정책을 확정한 뒤 운영 설정을 승인해야 한다. 이번 작업에서 운영 데이터나 실제 모델을 삭제하지 않았다.
