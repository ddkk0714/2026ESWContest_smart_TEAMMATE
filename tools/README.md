# tools — 개발 · 실험 보조 스크립트

실기기 없이도 개발을 진행하기 위한 도구를 모은다.

## 만들면 유용한 것 (우선순위 순)

| 도구 | 용도 | 언제 필요한가 |
|---|---|---|
| `mqtt_sim.py` | 가짜 센서 스트림 발행 | ESP32 없이 허브 · 디스플레이 개발. **가장 먼저 필요** |
| `log_recorder.py` | 전 토픽 JSONL 로 기록 | 8월 데이터 수집 · 리플레이 소스 |
| `plot_session.py` | 세션 타임라인 시각화 (자세 · 타이핑 · CO₂ · 국면) | 임계값 튜닝, 발표 자료 |
| `tof_probe.py` | VL53L9CX 54×42 원본·축소 depth map 실시간 확인 | Pi 4 MIPI CSI-2 연결 1일 spike와 자세 특징 검증용 |

`mqtt_sim.py` 를 초반에 만들어 두면 5명이 장비 1세트를 기다리지 않고
병렬로 작업할 수 있다. 장비 수령이 7/30 이므로 그 전까지는 이게 유일한 개발 경로다.

## 사용

```bash
python tools/mqtt_sim.py --broker localhost --scenario fatigue
python tools/log_recorder.py --broker <pi4-ip> --out logs/
python tools/plot_session.py logs/2026-08-01.jsonl
```
