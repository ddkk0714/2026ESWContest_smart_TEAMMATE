# collector — PC 키스트로크 타이밍 수집기

담당: 최민경

키보드 작업 중에만 가용한 **정밀 신호**를 수집한다.

## 프라이버시 원칙 (타협 불가)

- **키 값을 절대 수집하지 않는다.** 입력 타임스탬프만 추출한다.
- 저장 · 전송하는 것은 통계 특징뿐이다. 원시 타임스탬프 시퀀스도 로컬에만 둔다.
- 이 원칙이 본 작품의 "비침습 프라이빗 센싱" 차별점의 근거다.
  코드 리뷰에서 이 부분은 특히 엄격하게 본다.

## 추출 특징

| 특징 | 설명 |
|---|---|
| dwell time | 키 누름 유지 시간 (평균 · 표준편차) |
| flight time | 키 간격 (평균 · 표준편차) |
| idle ratio | 입력 공백 비율 |
| correction rate | 백스페이스 빈도 |

피로 시 리듬이 느려지고 불규칙해지며 정정이 느는 경향을 포착한다.

## 발행

`deskmate/sensor/keystroke` 로 1Hz, 60초 윈도우 통계.
스키마는 [`docs/mqtt-topics.md`](../docs/mqtt-topics.md) 참조.

## 개발 환경 준비

저장소 루트에서 Python 3.10 이상으로 가상환경을 만들고 의존성을 설치한다.

```bash
python -m venv .venv
python -m pip install -r collector/requirements.txt
python -m pytest collector/tests -q
```

가상환경 활성화 명령은 OS에 따라 다르다.

- Windows PowerShell: `.venv\Scripts\Activate.ps1`
- Linux/macOS: `source .venv/bin/activate`

## 실행

저장소 루트에서 다음과 같이 실행한다.

```bash
python -m collector --broker <pi4-ip>
```

브로커를 생략하면 `localhost:1883`을 사용하며, 연결되지 않아도 `logs/keystroke.jsonl`에
동일한 MQTT envelope를 기록한다. `logs/`는 수집 데이터이므로 Git에 포함하지 않는다.

OS별 키 훅 권한이 필요하다. Linux는 데스크톱 세션 및 input 권한, macOS는 손쉬운 사용
권한, Windows는 대상 앱의 권한 수준에 따라 관리자 실행이 필요할 수 있다.
