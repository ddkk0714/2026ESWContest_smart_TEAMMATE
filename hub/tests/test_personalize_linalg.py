"""개인화 정책의 순수 Python 선형대수·난수(personalize/linalg.py)가 numpy 판과 같은 값을 내는지.

보드(Pi 4)에는 numpy 가 없어서 정책을 순수 Python 으로 옮겼다(2026-10-23). numpy 는 여기서 정답지로만 쓴다.
"""
from __future__ import annotations

import numpy as np
import pytest

from deskmate_hub.personalize.features import DIM
from deskmate_hub.personalize.linalg import PurePRNG, cholesky, inverse, matvec
from deskmate_hub.personalize.policy import BayesLinReg


def spd(seed: int, n: int = DIM) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(3 * n, n))
    return 2.0 * np.eye(n) + x.T @ x / n


def test_inverse_and_cholesky_match_numpy():
    a = spd(1)
    np.testing.assert_allclose(np.array(inverse(a.tolist())), np.linalg.inv(a), rtol=1e-9, atol=1e-12)
    np.testing.assert_allclose(np.array(cholesky(a.tolist())), np.linalg.cholesky(a), rtol=1e-9, atol=1e-12)
    with pytest.raises(ValueError):
        cholesky([[1.0, 2.0], [2.0, 1.0]])            # 양의 정부호 아님
    with pytest.raises(ValueError):
        inverse([[1.0, 2.0], [2.0, 4.0]])             # 특이


def test_posterior_mean_and_thompson_sample_match_numpy():
    """같은 numpy 난수열이면 numpy multivariate_normal(method="cholesky") 와 같은 샘플."""
    rng = np.random.default_rng(3)
    theta0 = rng.normal(size=DIM)
    m = BayesLinReg(theta0.tolist(), lam=2.0, noise_var=0.5)
    A, b = 2.0 * np.eye(DIM), 2.0 * theta0
    for _ in range(25):
        x, r = rng.normal(size=DIM), float(rng.normal())
        m.update(x.tolist(), r)
        A += np.outer(x, x)
        b += r * x
    mu = np.linalg.inv(A) @ b
    np.testing.assert_allclose(m.mean(), mu, rtol=1e-9, atol=1e-12)
    cov = 0.5 * 0.7 * np.linalg.inv(A)
    want = np.random.default_rng(11).multivariate_normal(mu, 0.5 * (cov + cov.T), method="cholesky")
    got = m.sample(np.random.default_rng(11), 0.7)
    np.testing.assert_allclose(got, want, rtol=1e-8, atol=1e-10)
    # 저장·불러오기 왕복
    q = BayesLinReg([0.0] * DIM, lam=2.0, noise_var=0.5)
    q.load(m.to_dict())
    np.testing.assert_allclose(q.mean(), m.mean())
    with pytest.raises(ValueError):
        q.load({"A": [[1.0]], "b": [1.0]})


def test_pure_prng_is_seeded_and_standard_normal():
    a, b = PurePRNG(7), PurePRNG(7)
    xs = a.standard_normal(20000)
    assert xs[:5] == b.standard_normal(5)
    mean = sum(xs) / len(xs)
    var = sum((v - mean) ** 2 for v in xs) / len(xs)
    assert abs(mean) < 0.03 and abs(var - 1.0) < 0.05
    u = [PurePRNG(1).random() for _ in range(3)]
    assert all(0.0 <= v < 1.0 for v in u)
    assert PurePRNG(1).standard_normal(3) != PurePRNG(2).standard_normal(3)
    np.testing.assert_allclose(matvec([[1.0, 2.0], [3.0, 4.0]], [1.0, 1.0]), [3.0, 7.0])
