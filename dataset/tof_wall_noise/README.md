# ToF 벽 잡음 실측 원본

`hub/atlas-tof/pc/tof_wall_noise.py session` 으로 녹화한 VL53L8CX ×2 원본이다.
센서 거리값·상태값과 자세 서비스 출력만 있고 개인정보는 없다.
결과 해석은 [`hub/atlas-tof/docs/WALL_NOISE_RESULT_20261024.md`](../../hub/atlas-tof/docs/WALL_NOISE_RESULT_20261024.md).

| 폴더 | 날짜 | 조건 | 크기 |
|---|---|---|---|
| `session_20261024_230205/` | 2026-10-24 23:02 | 벽 45/50/60 cm 각 5분, 흰 콘크리트, 천장 형광등, 지그 위로 약 25°, 모델 `opt_l8_c256`, 0x05 flags=3 | 6.0 MB |

## 폴더 구성

- `SESSION.md`: 단계별 요약.
- `session.json`: 메모(조명·벽·지그)와 단계 → 폴더.
- `fit.md/.json`: 거리별 맞춤.
  60 cm 하나로만 맞춘 값이다. 세 녹화 모두로 맞춘 값은 결과 문서에 있다.
- `wall45_*/`, `wall50_*/`, `wall60_*/`(녹화 하나씩):
  - `meta.json`: 설정·폴링 통계·자세 서비스 모델
  - `frames.jsonl`: 센서 원본 전부. 한 줄 형식은 아래.
    ```
    {"host_t", "port": "s0"|"s1", "raw": "{\"t\":\"tof\",\"ms\",\"seq\",\"w\":8,\"h\":8,\"d\":[64 mm, -1=타깃 없음],\"st\":[64 상태값],\"flags\"}"}
    ```
    row 0 = 위, col 0 = 센서가 보는 왼쪽. 펌웨어 방향이 이미 적용된 값이다.
  - `state.jsonl`: 1 s 마다 `/api/state` 전체.
  - `report.json`: 분석 전체(칸별 64개 값).
  - `summary.md`: 사람이 읽는 요약.

## 다시 쓰기

```bash
python -I hub/atlas-tof/pc/tof_wall_noise.py analyze dataset/tof_wall_noise/session_20261024_230205/wall*
python hub/atlas-tof/pc/tof_live.py --ports frames.jsonl@s0,frames.jsonl@s1 --model posture_net.onnx   # 모델 리플레이
```

새 측정은 `session_<날짜>_<시각>/` 폴더째 여기에 더하고 위 표에 한 줄 적는다.
