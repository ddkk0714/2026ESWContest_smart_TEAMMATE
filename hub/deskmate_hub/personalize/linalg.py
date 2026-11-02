"""개인화 정책의 선형대수·난수 — 순수 Python. Pi 4 `/restricted/python3` 에는 numpy 도 `_random`(→ `random`) 도 없다.

정책은 arm 마다 34차원 베이지안 선형 회귀 두 개(수용도·효과)라 행렬이 34×34 다. 역행렬(가우스-조르당)·숄레스키 둘 다
O(n³) ≈ 4만 번 곱셈이고 사후 분포가 바뀔 때(피드백)만 다시 구한다 — 보드에서 결정 한 번에 수 ms.
`numpy` 판과 같은 값을 내는지는 tests/test_personalize_linalg.py 가 확인한다.
"""
from __future__ import annotations

import math
import time
from typing import Any, Sequence

Vec = list[float]
Mat = list[list[float]]


def zeros(n: int) -> Vec:
    return [0.0] * n


def eye(n: int, scale: float = 1.0) -> Mat:
    return [[scale if i == j else 0.0 for j in range(n)] for i in range(n)]


def dot(a: Sequence[float], b: Sequence[float]) -> float:
    return math.fsum(x * y for x, y in zip(a, b))


def matvec(m: Sequence[Sequence[float]], v: Sequence[float]) -> Vec:
    return [dot(row, v) for row in m]


def add_outer(m: Mat, x: Sequence[float], weight: float = 1.0) -> None:
    """m += weight · x xᵀ (제자리)."""
    for i, xi in enumerate(x):
        if xi:
            row, wxi = m[i], weight * xi
            for j, xj in enumerate(x):
                row[j] += wxi * xj


def axpy(y: Vec, a: float, x: Sequence[float]) -> None:
    """y += a · x (제자리)."""
    for i, xi in enumerate(x):
        y[i] += a * xi


def inverse(m: Sequence[Sequence[float]]) -> Mat:
    """가우스-조르당(부분 피벗). 특이 행렬이면 ValueError — 정책의 A = λI + Σxxᵀ 는 λ > 0 이라 언제나 양의 정부호."""
    n = len(m)
    a = [list(map(float, row)) + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(m)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            raise ValueError("특이 행렬")
        a[col], a[pivot] = a[pivot], a[col]
        p = a[col][col]
        prow = [v / p for v in a[col]]
        a[col] = prow
        for r in range(n):
            if r != col:
                f = a[r][col]
                if f:
                    row = a[r]
                    a[r] = [rv - f * pv for rv, pv in zip(row, prow)]
    return [row[n:] for row in a]


def cholesky(m: Sequence[Sequence[float]]) -> Mat:
    """대칭 양의 정부호 m = L Lᵀ 의 아래 삼각 L. 양의 정부호가 아니면 ValueError."""
    n = len(m)
    low = [[0.0] * n for _ in range(n)]
    for i in range(n):
        li = low[i]
        for j in range(i + 1):
            lj = low[j]
            s = float(m[i][j]) - math.fsum(li[k] * lj[k] for k in range(j))
            if i == j:
                if s <= 0.0:
                    raise ValueError("양의 정부호가 아님")
                li[j] = math.sqrt(s)
            else:
                li[j] = s / lj[j]
    return low


def symmetrize(m: Sequence[Sequence[float]]) -> Mat:
    n = len(m)
    return [[0.5 * (float(m[i][j]) + float(m[j][i])) for j in range(n)] for i in range(n)]


class PurePRNG:
    """`_random` 없는 보드용 난수(SplitMix64 + Box–Muller). 정책은 `standard_normal(n)` 만 쓴다 —
    numpy Generator 도 같은 이름의 메서드가 있어 테스트·PC 에서는 그대로 넘겨도 된다."""

    _MASK = (1 << 64) - 1

    def __init__(self, seed: int | None = None) -> None:
        self._state = (time.time_ns() if seed is None else int(seed)) & self._MASK
        self._spare: float | None = None

    def _next(self) -> int:
        self._state = (self._state + 0x9E3779B97F4A7C15) & self._MASK
        z = self._state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & self._MASK
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & self._MASK
        return z ^ (z >> 31)

    def random(self) -> float:
        """[0, 1) 균등."""
        return (self._next() >> 11) * (1.0 / (1 << 53))

    def _gauss(self) -> float:
        if self._spare is not None:
            v, self._spare = self._spare, None
            return v
        u1 = 1.0 - self.random()                 # (0, 1] — log(0) 방지
        u2 = self.random()
        r = math.sqrt(-2.0 * math.log(u1))
        self._spare = r * math.sin(2.0 * math.pi * u2)
        return r * math.cos(2.0 * math.pi * u2)

    def standard_normal(self, n: int) -> Vec:
        return [self._gauss() for _ in range(int(n))]


def standard_normals(rng: Any, n: int) -> Vec:
    """PurePRNG·numpy Generator 둘 다 받는다."""
    return [float(v) for v in rng.standard_normal(n)]
