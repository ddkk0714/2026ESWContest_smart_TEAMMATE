"""Multizone direct-ToF sensor model (VL53L8CX by default).

Each zone is sampled with k×k jittered sub-rays. The first-hit distances of
those rays form the zone's return histogram. Hits closer together than
`separation_m` merge into one peak (mixed pixel: the reported distance is the
signal-weighted mean); farther ones are separate targets. Like the real part
with target order CLOSEST, the zone reports the nearest peak whose signal
clears the threshold.

Signal per ray ∝ reflectivity / distance². The threshold scales with ambient
light per session. Range noise, the target-status code and dropouts all get
worse as the signal-to-threshold ratio falls, which is where the real sensor
misbehaves too (dark hair, black chairs, object edges).

The numbers are a plausible model, not a calibration. Fit `SensorModel`
parameters against real captures (`tools/fit_noise.py`, TODO) before trusting
fine-grained results.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .raycast import Surface, cast

# A caster takes (origin (3,), unit directions (N,3)) and returns per ray
# (distance m, inf = miss), (reflectivity 0..1), (cosine of incidence 0..1).
# The numpy primitives and MuJoCo (mj_multiRay) both provide one.


@dataclass
class SensorSpec:
    name: str = "vl53l8cx"
    width: int = 8
    height: int = 8
    fov_h_deg: float = 45.0
    fov_v_deg: float = 45.0


PRESETS = {
    "vl53l8cx": SensorSpec("vl53l8cx", 8, 8, 45.0, 45.0),
    # VL53L9CX: zone counts are from the team docs; the field of view here is an
    # assumption — check the datasheet before relying on 54×42 results.
    "vl53l9cx": SensorSpec("vl53l9cx", 54, 42, 55.0, 42.0),
    "vl53l9cx_8x6": SensorSpec("vl53l9cx_8x6", 8, 6, 55.0, 42.0),
}


@dataclass
class SensorModel:
    sub_rays: int = 4                 # k → k×k rays per zone (cost ∝ k²; 4 is enough for 8×8 mixed pixels)
    separation_m: float = 0.60        # peaks closer than this merge
    threshold: float = 0.015          # signal units (reflectivity / m²) at no ambient
    ambient: float = 1.0              # threshold multiplier (sunlight 2–4)
    max_range_m: float = 4.0
    sigma_floor_mm: float = 2.0
    sigma_rel: float = 0.010          # relative range noise at SNR 1
    merged_spread_m: float = 0.08     # peak wider than this -> status 9
    dropout: float = 0.005            # baseline chance a zone reports an invalid status
    semi_valid: float = 0.01          # chance of status 6/10 on a weak but valid zone


@dataclass
class SensorPose:
    position: np.ndarray              # (3,) world metres
    pitch_down_deg: float = 15.0
    yaw_deg: float = 0.0
    roll_deg: float = 0.0
    upside_down: bool = False


def _rot(pose: SensorPose) -> np.ndarray:
    p, y, r = np.radians([pose.pitch_down_deg, pose.yaw_deg, pose.roll_deg + (180.0 if pose.upside_down else 0.0)])
    cx, sx = np.cos(p), np.sin(p)
    cy, sy = np.cos(y), np.sin(y)
    cz, sz = np.cos(r), np.sin(r)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])      # forward (0,0,1) -> (0,-sin,cos): +pitch looks down
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return Ry @ Rx @ Rz


class Sensor:
    def __init__(self, spec: SensorSpec, model: SensorModel, rng: np.random.Generator):
        self.spec = spec
        self.model = model
        self.rng = rng
        k = model.sub_rays
        self.k2 = k * k
        W, H = spec.width, spec.height
        # Zone centres on the tangent plane; row 0 = top, col 0 = sensor's left (−X)
        tx = np.tan(np.radians(spec.fov_h_deg / 2))
        ty = np.tan(np.radians(spec.fov_v_deg / 2))
        self.cell_w = 2 * tx / W
        self.cell_h = 2 * ty / H
        cols = (np.arange(W) + 0.5) * self.cell_w - tx
        rows = ty - (np.arange(H) + 0.5) * self.cell_h
        cx, cy = np.meshgrid(cols, rows)                      # (H,W)
        self.zone_xy = np.stack([cx.ravel(), cy.ravel()], 1)  # (Z,2)
        Z = self.zone_xy.shape[0]
        self._zone_base = (np.arange(Z) * self.k2)[:, None]    # flat (zone, peak) index base
        self._cell = np.array([self.cell_w, self.cell_h])
        self._ones = np.ones((Z, self.k2, 1))

    def zone_centers(self, R: np.ndarray) -> np.ndarray:
        """Unit direction through each zone centre, (Z,3) in world, row-major."""
        local = np.concatenate([self.zone_xy, np.ones((self.zone_xy.shape[0], 1))], axis=1)
        local /= np.linalg.norm(local, axis=1, keepdims=True)
        return local @ R.T

    def _directions(self, R: np.ndarray) -> np.ndarray:
        Z = self.zone_xy.shape[0]
        k2 = self.k2
        jitter = self.rng.uniform(-0.5, 0.5, size=(Z, k2, 2)) * self._cell
        xy = self.zone_xy[:, None, :] + jitter                # (Z,k2,2)
        local = np.concatenate([xy, self._ones], axis=2).reshape(-1, 3)
        local /= np.sqrt(np.einsum("ij,ij->i", local, local))[:, None]
        return local @ R.T                                    # (Z*k2,3)

    def measure_surfaces(self, surfaces: list[Surface], pose: SensorPose):
        """numpy-primitive backend (Y-up world of body.py)."""
        def caster(origin, dirs):
            t, _part, refl = cast(surfaces, origin, dirs)
            return t, refl, np.ones_like(t)
        return self.measure(caster, pose.position, _rot(pose))

    def measure(self, caster, origin: np.ndarray, R: np.ndarray):
        """One frame. R maps sensor-local (right, up, forward) to world.

        Returns (distance_mm int16 [H,W], status uint8 [H,W]); -1 / 255 = no target.
        """
        m, spec = self.model, self.spec
        dirs = self._directions(R)
        t, refl, cosang = caster(origin, dirs)
        Z, k2 = self.zone_xy.shape[0], self.k2
        t = t.reshape(Z, k2)
        refl = refl.reshape(Z, k2)
        cosang = cosang.reshape(Z, k2)
        t = np.where(t <= m.max_range_m, t, np.inf)
        # Lambertian return: grazing surfaces send less light back
        sig = np.where(np.isfinite(t), refl * np.maximum(cosang, 0.15) / np.maximum(t, 0.05) ** 2, 0.0)

        order = np.argsort(t, axis=1)
        t = np.take_along_axis(t, order, 1)
        sig = np.take_along_axis(sig, order, 1)
        gaps = np.diff(np.where(np.isfinite(t), t, 1e9), axis=1) > m.separation_m
        cid = np.concatenate([np.zeros((Z, 1), dtype=int), np.cumsum(gaps, axis=1)], axis=1)

        # Per-(zone, peak) sums. bincount over a flat index is ~10× faster than np.add.at.
        idx = (self._zone_base + cid).ravel()
        tf = np.where(np.isfinite(t), t, 0.0).ravel()
        sf = sig.ravel()
        n = Z * k2
        s_sum = np.bincount(idx, weights=sf, minlength=n).reshape(Z, k2)
        st_sum = np.bincount(idx, weights=sf * tf, minlength=n).reshape(Z, k2)
        stt_sum = np.bincount(idx, weights=sf * tf * tf, minlength=n).reshape(Z, k2)

        thr = m.threshold * m.ambient
        peak_sig = s_sum / k2
        ok = peak_sig >= thr
        has = ok.any(axis=1)
        first = np.argmax(ok, axis=1)                         # closest valid cluster
        S = peak_sig[np.arange(Z), first]
        with np.errstate(invalid="ignore", divide="ignore"):
            mean_t = st_sum[np.arange(Z), first] / s_sum[np.arange(Z), first]
            var_t = stt_sum[np.arange(Z), first] / s_sum[np.arange(Z), first] - mean_t ** 2
        spread = np.sqrt(np.maximum(var_t, 0.0))

        snr = np.where(has, S / thr, 0.0)
        d_mm = mean_t * 1000.0
        sigma = m.sigma_floor_mm + m.sigma_rel * d_mm / np.sqrt(np.maximum(snr, 1e-3))
        d_mm = d_mm + self.rng.normal(0.0, 1.0, Z) * sigma

        status = np.full(Z, 5, dtype=np.uint8)
        status[spread > m.merged_spread_m] = 9
        weak = snr < 3.0
        u = self.rng.random(Z)
        status[weak & (u < m.semi_valid * 10)] = 6
        status[(~weak) & (u < m.semi_valid)] = 10
        p_bad = np.clip(0.4 / np.maximum(snr, 1e-3) - 0.08, 0.0, 0.6) + m.dropout
        bad = self.rng.random(Z) < p_bad
        status[bad] = self.rng.choice(np.array([4, 7, 12], dtype=np.uint8), size=int(bad.sum()))

        dist = np.where(has, np.round(d_mm), -1).astype(np.int16)
        status[~has] = 255
        dist[~has] = -1
        return dist.reshape(spec.height, spec.width), status.reshape(spec.height, spec.width)
