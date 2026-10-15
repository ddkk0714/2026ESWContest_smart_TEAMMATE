"""가상 VL53L8CX: 장면 → 8×8 거리(mm)·상태값.

MuJoCo 는 광선이 어디에 맞는지만 알려준다. ToF 다운 부분은 여기서 만든다.

- 칸(zone)마다 sub×sub 개 광선을 칸 시야(45°/8 = 5.6°) 안에 고르게 쏜다(mj_multiRay, 표면 법선 포함).
- 광선 하나의 신호 = 반사율 × |cos 입사각| / 거리², 칸 안 광선 수로 나눈다(칸 전체를 덮은 흰 벽 1 m ≈ 1).
- VL53 는 칸 안 반사를 거리 히스토그램으로 받아 봉우리를 찾는다. 두 대상이 약 600 mm 보다 가까우면
  한 봉우리로 섞인다(데이터시트 최소 대상 간격) → 600 mm 안쪽 광선은 신호 가중 평균 거리 한 개로 합친다.
  머리 가장자리 칸처럼 사람과 1 m 뒤 벽이 섞이면 두 대상으로 갈리고, Closest 순서라 가까운 쪽을 낸다.
- 신호가 문턱보다 약하면 그 대상은 안 보인다(검은 머리·비스듬한 면·먼 거리).
- 거리 잡음 표준편차 = √(σ0² + (σ1/√신호)²). 신호가 약할수록 커진다.

숫자(문턱·잡음)는 데이터시트 경향을 따른 추정값이다. 실기 L8 로그가 생기면 맞춘다(README).
"""
from __future__ import annotations

from dataclasses import dataclass

import mujoco
import numpy as np

from .scene import PERSON_GROUP, Scene


@dataclass
class Mount:
    """센서 위치·자세. 위치는 월드 좌표(m), 각은 도. pitch 양수 = 위를 봄, yaw 양수 = 왼쪽(+y)."""
    x: float = 0.0
    y: float = 0.0
    z: float = 0.88
    pitch: float = 10.0
    yaw: float = 0.0
    roll: float = 0.0

    def rotation(self) -> np.ndarray:
        p, yw, rl = np.radians([self.pitch, self.yaw, self.roll])
        ry = np.array([[np.cos(-p), 0, np.sin(-p)], [0, 1, 0], [-np.sin(-p), 0, np.cos(-p)]])
        rz = np.array([[np.cos(yw), -np.sin(yw), 0], [np.sin(yw), np.cos(yw), 0], [0, 0, 1]])
        rx = np.array([[1, 0, 0], [0, np.cos(rl), -np.sin(rl)], [0, np.sin(rl), np.cos(rl)]])
        return rz @ ry @ rx


@dataclass
class L8Params:
    rows: int = 8
    cols: int = 8
    fov_deg: float = 45.0
    sub: int = 6                 # 칸당 sub×sub 광선
    max_range: float = 4.0       # m
    min_sep: float = 0.6         # m, 이보다 가까운 반사는 한 대상으로 섞인다
    signal_min: float = 0.02     # 이보다 약하면 대상 없음
    signal_semi: float = 0.05    # 이보다 약하면 상태 6(대체로 유효), 아니면 5
    sigma0_mm: float = 3.0
    sigma1_mm: float = 4.0
    dropout: float = 0.003       # 칸이 이유 없이 비는 확률


class L8:
    def __init__(self, mount: Mount | None = None, params: L8Params | None = None):
        self.mount = mount or Mount()
        self.p = params or L8Params()
        p = self.p
        half = np.radians(p.fov_deg) / 2
        # 장면 방향: row 0 = 위(고각 +), col 0 = 왼쪽(방위 +)
        el_edges = np.linspace(half, -half, p.rows + 1)
        az_edges = np.linspace(half, -half, p.cols + 1)
        f = (np.arange(p.sub) + 0.5) / p.sub
        dirs = np.zeros((p.rows, p.cols, p.sub, p.sub, 3))
        for r in range(p.rows):
            el = el_edges[r] + (el_edges[r + 1] - el_edges[r]) * f
            for c in range(p.cols):
                az = az_edges[c] + (az_edges[c + 1] - az_edges[c]) * f
                E, A = np.meshgrid(el, az, indexing="ij")
                dirs[r, c, :, :, 0] = np.cos(E) * np.cos(A)
                dirs[r, c, :, :, 1] = np.cos(E) * np.sin(A)
                dirs[r, c, :, :, 2] = np.sin(E)
        self._local = dirs.reshape(-1, 3)
        self.nray = len(self._local)
        self._geomid = np.zeros(self.nray, dtype=np.int32)
        self._dist = np.zeros(self.nray)
        self._normal = np.zeros(self.nray * 3)

    def cast(self, sc: Scene, present: bool) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """광선별 (거리 m, 신호, 맞은 geom). 안 맞으면 거리 -1."""
        mt = self.mount
        world = self._local @ mt.rotation().T
        pnt = np.array([mt.x, mt.y, mt.z], dtype=np.float64)
        groups = np.ones(6, dtype=np.uint8)
        groups[5] = 0                              # 센서 표시용 상자
        groups[PERSON_GROUP] = 1 if present else 0
        mujoco.mj_multiRay(sc.model, sc.data, pnt, np.ascontiguousarray(world).ravel(), groups, 1, -1,
                           self._geomid, self._dist, self._normal, self.nray, self.p.max_range)
        dist = self._dist.copy()
        hit = dist >= 0
        n = self._normal.reshape(-1, 3)
        cos = np.abs(np.einsum("ij,ij->i", n, world))
        refl = np.where(hit, sc.reflectance[np.clip(self._geomid, 0, None)], 0.0)
        sig = np.where(hit, refl * np.maximum(cos, 0.02) / np.maximum(dist, 0.05) ** 2, 0.0)
        sig /= self.p.sub * self.p.sub
        dist[~hit] = -1.0
        return dist, sig, self._geomid.copy()

    def measure(self, sc: Scene, present: bool, rng: np.random.Generator,
                return_truth: bool = False):
        """(d mm int16 [rows, cols], st uint8) 장면 방향. -1/255 = 대상 없음."""
        p = self.p
        dist, sig, gid = self.cast(sc, present)
        k = p.sub * p.sub
        dist = dist.reshape(-1, k)
        sig = sig.reshape(-1, k)
        nz = p.rows * p.cols
        d_out = np.full(nz, -1, dtype=np.int16)
        st_out = np.full(nz, 255, dtype=np.uint8)
        person_frac = np.zeros(nz)
        gperson = sc.person_geoms[np.clip(gid, 0, None)].reshape(-1, k) & (dist >= 0)
        for z in range(nz):
            ok = dist[z] >= 0
            if not ok.any():
                continue
            dz, sz = dist[z][ok], sig[z][ok]
            order = np.argsort(dz)
            dz, sz = dz[order], sz[order]
            cut = np.flatnonzero(np.diff(dz) > p.min_sep) + 1
            for a, b in zip(np.r_[0, cut], np.r_[cut, len(dz)]):  # 가까운 대상부터
                s = sz[a:b].sum()
                if s < p.signal_min:
                    continue
                rng_m = float((dz[a:b] * sz[a:b]).sum() / s)
                sigma = np.sqrt(p.sigma0_mm ** 2 + (p.sigma1_mm / np.sqrt(s)) ** 2)
                d_out[z] = int(round(rng_m * 1000 + rng.normal(0, sigma)))
                st_out[z] = 5 if s >= p.signal_semi else 6
                break
            person_frac[z] = gperson[z].mean()
        drop = rng.random(nz) < p.dropout
        d_out[drop], st_out[drop] = -1, 255
        shape = (p.rows, p.cols)
        if return_truth:
            return d_out.reshape(shape), st_out.reshape(shape), person_frac.reshape(shape)
        return d_out.reshape(shape), st_out.reshape(shape)
