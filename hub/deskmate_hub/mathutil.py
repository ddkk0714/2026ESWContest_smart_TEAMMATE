"""보드 제한 Python 에서도 도는 작은 수치 함수.

`statistics` 는 `random` → C 확장 `_random` 을 import 하는데 Pi 4 `/restricted/python3` 에는 `_random` 이 없다.
hub 가 보드에서 import 단계에서 죽지 않도록 필요한 것만 여기 둔다(tests/test_board_runtime.py 가 지킨다).
"""
from __future__ import annotations

from typing import Iterable


def median(values: Iterable[float]) -> float:
    """statistics.median 과 같은 값(짝수 개면 가운데 둘의 평균). 비어 있으면 ValueError."""
    xs = sorted(values)
    n = len(xs)
    if n == 0:
        raise ValueError("median of empty data")
    mid = n // 2
    return xs[mid] if n % 2 else (xs[mid - 1] + xs[mid]) / 2
