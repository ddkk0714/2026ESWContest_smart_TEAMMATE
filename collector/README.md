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

## 실행

```bash
pip install -r requirements.txt
python -m collector --broker <pi4-ip>
```

OS 별 키 훅 권한이 필요하다 (Linux: input group / X11, Windows: 관리자).
