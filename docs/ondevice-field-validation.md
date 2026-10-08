# 온디바이스 개인화 PR와 실기 검증 인계

## PR를 올리는 기준

이번 관찰 모드 구현으로 첫 실기 검증 PR를 올릴 수 있다. 포함 범위는 초기 공통 CNN export·순수 Python 추론, 개인 head 로컬 학습·평가·저장/복원, 동의·삭제, Pi 5 학습 상태 UI, 회귀 시험과 이 체크리스트다. Pi 4 native 실행·성능은 PR의 확인 요청 항목이다. 실사용 정확도나 FSM 결합 완료를 PR 제목·설명에 쓰지 않는다.

현재 작업 트리에는 기존 개입 사이클 수정도 있으므로 개인화 변경과 별도 커밋/PR로 구분한다. 모델·학습 데이터·개인 로그·IPK는 Git에 넣지 않는다. 최초 공통 bundle은 개발자가 재현해 기기에 설치한다. 사용자 사용 중에는 PC 파일 복사를 요구하지 않는다.

권장 PR 제목: `feat(personalization): add local head learning and app learning status`

설명에 쓸 핵심: 사용자 개인화는 기기 안에서 진행하며 PC는 초기 공통 모델 준비에만 사용한다. 학습/적용은 opt-in이고 기본 비활성이다. 모델은 관찰 전용이며 FSM·제어는 유지한다. 실제 ATLAS runtime·권한·지연시간과 Pi 5 터치 검증을 요청한다.

## 준비할 것

- 동일 소스 revision의 Pi 4 hub IPK와 Pi 5 release 앱. hub는 [Atlas hub 빌드](../hub/atlas/README.md), 앱은 [display 빌드](../display/atlas/README.md) 절차를 따른다. 보드에서 Docker·PC용 학습 가상환경을 실행하지 않는다.
- [공통 portable export 명령](ondevice-personalization.md#초기-공통-모델-내보내기-개발-pc)으로 만든 common.portable.json·common.metadata.json·변환 보고서. source revision과 모델 SHA256을 기록한다. 합성/FSM 라벨 모델은 실행 확인용이며 실제 사용자 성능 모델로 소개하지 않는다.
- 현재 Pi 4/Pi 5 주소, broker와 live/native MQTT 기동. 통신 전체 재개발 없이 기존 점검 절차로 연결 상태만 확인한다.
- 실제 서비스 계정이 읽을 수 있는 공통 bundle 경로와 쓸 수 있는 개인 data_root. 설치 때 UID가 바뀔 수 있으므로 UID를 고정하지 않고 현재 설치 계정을 확인한다. 전체 공개 쓰기 권한으로 우회하지 않는다.

## A. 센서·개인 동의 없이 가능한 실행 점검

학습 정책을 승인하거나 개인 기록을 모으기 전에 합성 입력으로 실행 가능성을 확인한다.

1. personalization.yaml의 backend를 portable_cnn, model_path/metadata_path를 보드의 공통 bundle 절대 경로로 설정한다. 모델·ondevice·privacy enabled는 false로 둬도 된다. ingest의 baseline 정규화·10초 주기가 bundle metadata와 일치해야 한다. 설정은 source YAML → IPK JSON이므로 hub IPK를 다시 빌드한다.
2. 설치된 hub 서비스의 기존 hub.env에 시험용 `DESKMATE_PERSONALIZATION_SELF_CHECK=1`을 설정하고 서비스를 재시작한다. hub.env 위치·계정·재시작은 기존 활성화/복구 안내를 따른다. SSH 셸에서 제한 Python을 직접 실행하는 방식이 아니다.
3. hub stderr가 전달되는 실행 로그에서 `[personalization-self-check]` JSON을 확인한다. passed, synthetic=true, persistent_writes=false, head_update_verified=true, backbone_unchanged=true와 inference_ms를 기록한다.
4. 점검은 초기 공통 모델에 대해 합성 창 한 번의 추론과 임시 head의 SGD를 확인한다. 개인 파일·동의·실센서·실제 학습 후보를 변경하지 않으며 실패해도 hub는 계속 시작한다. failed는 runtime/import/파일/계약/성능 문제를 먼저 조사한다. not_run이면 backend가 portable_cnn인지 확인한다.
5. 종료 후 SELF_CHECK 설정을 제거하거나 0으로 되돌린다. 한 번의 inference_ms는 p95나 실제 센서 지연시간이 아니며 다음 단계 측정을 대신하지 않는다.

## B. 동의 후 실제 사용자 흐름

팀이 시험 정책을 승인하고 참여자가 동의한 경우 진행한다. privacy.policy_approved와 실제 policy_version은 그 승인에 맞춰 설정한다. 정책 승인을 임의로 만들어 채우지 않는다.

설정 관계 예시:

```yaml
# personalization.yaml: 기존 window·budget 등 나머지 설정 유지
enabled: true
mode: observe
backend: portable_cnn
model_path: /data/share/deskmate/common/common.portable.json
metadata_path: /data/share/deskmate/common/common.metadata.json

# ondevice.yaml: 기존 학습 정책 유지
enabled: true
head_path: /data/share/deskmate/personal/head.json

# privacy.yaml: 승인/버전 필드는 별도로 설정
enabled: true
data_root: /data/share/deskmate/personal
consent_file: /data/share/deskmate/personal/consent.json
personal_model_files:
  - /data/share/deskmate/personal/head.json
  - /data/share/deskmate/personal/head.json.tmp
```

ingest.baseline.persist.path는 같은 개인 data_root 안에 둔다. baseline 영구 저장을 시험할 때만 persist.enabled도 켠다. 공통 모델은 삭제 목록에 넣지 않는다. Pi 5 앱은 실제 MQTT broker에 연결한다.

| 점검 | 기대 결과 | 미완료를 구분할 것 |
|---|---|---|
| 동의 전 | 모델 사용·개인 표본·학습 차단, 동의 버튼은 승인 정책과 연결이 있어야 활성 | 기존 baseline 세션 보정은 계속 가능 |
| 동의 버튼 | hub 확인 후 완료 표시, warming_up → predicted | 터치했지만 확인 누락이면 성공 아님 |
| 명시적 상태 정정 | 정정 기록·세션 수 증가, 수락/거절은 학습 표본 아님 | 원시 키 내용·영상은 수집하지 않음 |
| 유휴 학습 | 충분한 기록 후 training, 작업 재개 시 paused, END/IDLE에서 재개 | 기록 부족이면 수집 상태 유지 |
| 후보 평가 | 개선이면 accepted, 아니면 rejected와 기존 모델 유지 | accepted를 강제로 만들기 위해 평가 조건을 완화하지 않음 |
| 재시작 | 동의와 검증된 head 복원 | RAM 표본·미완성 학습은 사라짐 |
| 동의 취소·삭제 | 학습/job/메모리 중지, head·임시 파일·등록 baseline 삭제 | ESM/PC 자료까지 삭제된 것으로 표시하지 않음 |
| 연결 끊김 | 학습 상태 확인 대기, 오래된 개인 모델 상태 숨김 | 연결 복구 후 최신 snapshot 확인 |
| 오류·손상 | 저장 실패 시 기존 head 유지, head 손상/다른 backbone이면 공통 폴백 | 손상 공통 모델은 unavailable, FSM 계속 실행 |

학습에는 최소 train 2개·validation 1개·audit 1개 독립 세션과 각 분할의 4개 클래스별 최소 표본이 필요하다. 같은 hub 실행 동안 세션을 진행한다. 각 세션마다 보드를 재부팅하면 RAM 표본이 사라져 학습 조건을 채울 수 없다. 검증된 head가 아직 없다면 복원 시험은 미검증으로 남기고, 실행·학습 거절 검증과 구분해 보고한다.

## 성능과 보고

인터넷을 끊어도 hub·앱·학습이 유지되는지 확인한다. 모델 운영을 위해 개발 PC를 켤 필요는 없으며 MQTT 감시 PC를 종료해도 학습은 진행되어야 한다. PC 키스트로크 신호는 별도 입력이므로 수집 PC까지 종료하면 그 신호가 미가용이 되는 것은 정상이다.

정상 작업·유휴 학습·평가·저장 시 전체 hub tick의 p50/p95/max, 모델 inference_ms, CPU·메모리·발열을 측정한다. 현재 25ms 학습 budget은 업데이트 후 검사하는 소프트 제한이며 평가·저장 선점 기능은 없다. tick 지연이 크면 worker/native 경로를 검토한다. 개인 모델 효과는 별도 독립 사용자 시험으로 평가한다.

실기 담당자가 남길 결과: 소스 revision, IPK 버전, 공통 모델 hash, 보드/OS/Python 버전, A self-check 결과, B 각 항목 통과·실패·미검증, 성능 수치와 실패 재현 순서. 개인 embedding·학습 가중치·개인 로그는 PR 댓글/저장소에 올리지 않는다.

## 실기 뒤 진행

런타임·지연·권한 문제 수정 → 앱과 학습 흐름 재검증 → 승인된 보존/사용자 분리 정책 구현 → 실사용자 효과 평가 → 팀이 정한 규칙으로 FSM 결합 순서다. 첫 PR에서 FSM 결합까지 기다리지 않는다.

최종 PR 준비 검증: 전체 Python 358 passed, 1 skipped, 1 xfailed; hub 및 실제 로컬 MQTT 부분집합 245 passed; Flutter 전체 171 tests passed. 분석 오류·경고 없음, 기존 스타일 info 5개. 실기 완료는 아니다.
