"""특징 추출·개인 기준선 정규화 (features).

지금 있는 것: `baseline.py` — 중앙값·MAD Modified z-score 기준선(세션 보정 창 + 시간대 버킷, opt-in 저장).
ingest/mapping 이 `normalization: baseline` 일 때 이 모듈로 원지표를 [0,1] 증거값으로 바꾼다.
ToF 기하 특징 7종·환경 추세 특징은 센서 경로 확정 후 여기에 추가한다.
"""
from .baseline import BaselineStore, MetricBaseline, median_mad, modified_z

__all__ = ["BaselineStore", "MetricBaseline", "median_mad", "modified_z"]
