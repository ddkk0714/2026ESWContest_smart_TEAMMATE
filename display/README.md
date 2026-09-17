# display — Raspberry Pi 5 디스플레이 단말

## 개발 환경

Atlas Flutter 기반 구현과 크로스 빌드는 [atlas/README.md](atlas/README.md)의 Docker Compose 환경에서 수행한다. Docker가 사용하는 공급사 자산은 레포 내부의 gitignore된 `atlas/vendor/`에 준비하며, 외부 경로를 Docker 설정에 사용하지 않는다.

실제 반복 단위는 `개발 PC에서 코드 수정·Docker 크로스 빌드 → SSH로 Pi 5 설치·실행 →
run 콘솔/DevTools 로그 확인 → 코드 수정`이다. Pi 5는 AI Native OS에서 `.ipk` 앱을
네이티브로 실행하며 Docker를 실행하지 않는다.

담당: 최민경

책상 위 디스플레이형 가전의 출력 단말. `deskmate/state/phase` 를 구독해
현재 국면 · 제안 카드 · 휴식 알림 · 작업 리포트를 표시하고,
사용자 피드백을 `deskmate/feedback/user` 로 발행한다.

## 화면 (상태 적응형 — 포스터 기준 4화면)

| 화면 | 내용 | 상태 |
|---|---|---|
| 대기 (AOD) | 시각 · 환경 요약(CO₂ · 조도 · 온습도) · 재실만 표시 | 🟡 대시보드에 환경 요약 있음, 대기 전용 저정보 화면은 미분리 |
| 집중 저자극 | 몰입 중 불필요 정보를 줄인 화면. 터치 시 상세(집중 시간·환경·수동 제어) 노출 | 🟡 대시보드가 항상 상세를 보여줌. 저자극/상세 토글 미구현 |
| 제안 카드 / 자동 실행 알림 | `conf_suggest`(0.45)~`conf_auto`(0.75) 구간은 수락 / 거절, `conf_auto` 이상은 사후 통지 + 되돌리기. 실행 이유 문구 표시 | 🟡 제안 카드·피드백 전송 있음. 자동 실행 알림·되돌리기·이유 문구 미구현 |
| 세션 리포트 | 종료 시 집중 시간 · 상태 변화 · 개입 결과 · 주요 패턴 요약 | ⬜ 미구현 (`hub` `report.py` 는 `feat/merge-pending` PR 로 main 진입 예정) |

그 밖에 개발 보조 화면으로 18상태 전이 그래프, 센서 테스트(Pi 4 `/api/test-frame`), 키스트로크 패널, 오디오 재생 토글이 있다.

## 피드백 설계

사용자의 수락 · 거절 · 정정이 그대로 2단계 분류기의 ESM 라벨이 되므로,
피드백 UI 는 **라벨 수집 채널**이기도 하다. 입력 부담을 낮추는 게 라벨 양을 좌우한다.

- 제안 카드는 2탭 이내로 응답 가능해야 한다
- 정정(`correct`)은 4개 국면 중 선택하는 단순 형태
- 무응답도 하나의 신호로 기록한다 (수락도 거절도 아님)

## 기술 선택

Pi 5 AI Native OS Video Profile의 **Atlas Flutter**로 확정했다. 앱 소스는
[`atlas/ui_env_app/`](atlas/ui_env_app/)에 있으며, LG 제공 샘플에서 검토한 Atlas 플랫폼 러너를 복사한 뒤
DESKMATE 전용 앱 ID와 최소 권한으로 분리했다.

- 허브 URL 미지정: 화면 내장 데모가 5개 대표 상태를 순환한다.
- `DESKMATE_HUB_URL` 지정: Pi 4 미리보기 API를 1초 간격으로 조회하고 수락·거절을 되돌린다(개발용 fallback).
- `DESKMATE_MQTT_HOST` 지정: **최종 경로.** `deskmate/state/phase`(retain)·`interaction/request`·`display/message` 를 QoS 1 구독하고
  `feedback/user` 를 발행한다. HTTP 와 동시에 주면 MQTT 우선.

## 현재 배포 상태 (2026-09-14)

- Atlas Flutter 앱 소스와 플랫폼 러너: 구현됨
- Pi 4 합성 FSM 상태·피드백 연결(HTTP): 구현됨
- MQTT 구독·발행(`MqttStateSource`): 구현됨. 단 **hub 측 MQTT 발행이 아직 없어** 지금까지는 Node-RED 주입으로 확인함
- 대시보드 재설계 · 18상태 그래프 · 키스트로크 패널 · 오디오: 구현됨
- 개발 PC Atlas SDK import 절차: 구현됨, 자산은 Git 제외
- release `.ipk` 생성과 Pi 5 설치·실행: 완료
- **실기 터치: 정상** — 2026-09-14 디스플레이 교체 후 터치 동작 확인(이전 `xhci-hcd.1` 장애는 이력)
- 재부팅 자동 시작: 미구현
- 세션 리포트 화면, 저자극/상세 토글, 자동 실행 알림·되돌리기: 미구현

> Pi 5 는 27W PD 어댑터와 액티브 쿨러가 사실상 필수다. 상시 화면 출력 +
> 발열 조건에서 스로틀링이 나면 장시간 구동 안정성에 직결된다.
