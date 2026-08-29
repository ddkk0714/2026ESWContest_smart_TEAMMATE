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

## 화면

| 화면 | 내용 |
|---|---|
| AOD (기본) | 현재 국면, 작업 지속 시간, 환경 요약(CO₂ · 조도 · 온습도) |
| 제안 카드 | 신뢰도가 `conf_suggest` ~ `conf_auto` 구간일 때. 수락 / 거절 버튼 |
| 자동 실행 알림 | 신뢰도가 `conf_auto` 이상이라 이미 실행된 제어를 사후 통지. 되돌리기 제공 |
| 작업 리포트 | 세션 종료 시 국면 타임라인, 휴식 수용률, 환경 추이 |

## 피드백 설계

사용자의 수락 · 거절 · 정정이 그대로 2단계 분류기의 ESM 라벨이 되므로,
피드백 UI 는 **라벨 수집 채널**이기도 하다. 입력 부담을 낮추는 게 라벨 양을 좌우한다.

- 제안 카드는 2탭 이내로 응답 가능해야 한다
- 정정(`correct`)은 4개 국면 중 선택하는 단순 형태
- 무응답도 하나의 신호로 기록한다 (수락도 거절도 아님)

## 기술 선택

Pi 5 AI Native OS Video Profile의 **Atlas Flutter**로 확정했다. 앱 소스는
[`atlas/app/`](atlas/app/)에 있으며, LG 제공 샘플에서 검토한 Atlas 플랫폼 러너를 복사한 뒤
DESKMATE 전용 앱 ID와 최소 권한으로 분리했다.

- 허브 URL 미지정: 화면 내장 데모가 5개 대표 상태를 순환한다.
- `DESKMATE_HUB_URL` 지정: Pi 4 미리보기 API를 1초 간격으로 조회하고 수락·거절을 되돌린다.
- 현재 HTTP는 개발용 어댑터이며 MQTT를 확정한 것이 아니다.

## 현재 배포 상태

- Atlas Flutter 앱 소스와 플랫폼 러너: 구현됨
- Pi 4 합성 FSM 상태·피드백 연결: 구현됨
- 개발 PC Atlas SDK import 절차: 구현됨, 자산은 Git 제외
- 첫 release `.ipk` 생성과 Pi 5 설치·터치 검증: 대기

> Pi 5 는 27W PD 어댑터와 액티브 쿨러가 사실상 필수다. 상시 화면 출력 +
> 발열 조건에서 스로틀링이 나면 장시간 구동 안정성에 직결된다.
