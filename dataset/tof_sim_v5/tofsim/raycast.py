"""Vectorized ray–primitive intersection (numpy).

All rays share one origin (the sensor). Directions are unit vectors, so the
returned t is the radial distance in metres — the quantity a direct ToF zone
measures. Missing hits are +inf.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

EPS = 1e-6


@dataclass
class Ellipsoid:
    center: np.ndarray            # (3,)
    radii: np.ndarray             # (3,) along the local axes
    rot: np.ndarray = field(default_factory=lambda: np.eye(3))  # columns = local axes in world

    def intersect(self, o: np.ndarray, d: np.ndarray) -> np.ndarray:
        oc = (o - self.center) @ self.rot / self.radii          # (3,)
        dl = (d @ self.rot) / self.radii                         # (N,3)
        a = np.einsum("ij,ij->i", dl, dl)
        b = 2.0 * dl @ oc
        c = oc @ oc - 1.0
        disc = b * b - 4.0 * a * c
        t = np.full(d.shape[0], np.inf)
        ok = disc >= 0.0
        sq = np.sqrt(np.where(ok, disc, 0.0))
        t0 = (-b - sq) / (2.0 * a)
        t[ok & (t0 > EPS)] = t0[ok & (t0 > EPS)]
        return t


@dataclass
class Capsule:
    a: np.ndarray                 # (3,) segment start
    b: np.ndarray                 # (3,) segment end
    radius: float

    def intersect(self, o: np.ndarray, d: np.ndarray) -> np.ndarray:
        # Inigo Quilez, "capsule intersection", vectorized over directions.
        r = self.radius
        ba = self.b - self.a
        oa = o - self.a
        baba = ba @ ba
        bard = d @ ba
        baoa = ba @ oa
        rdoa = d @ oa
        oaoa = oa @ oa
        A = baba - bard * bard
        B = baba * rdoa - baoa * bard
        C = baba * oaoa - baoa * baoa - r * r * baba
        h = B * B - A * C
        t = np.full(d.shape[0], np.inf)

        body = (h >= 0.0) & (np.abs(A) > EPS)
        sq = np.sqrt(np.where(body, h, 0.0))
        tb = np.where(body, (-B - sq) / np.where(np.abs(A) > EPS, A, 1.0), np.inf)
        y = np.where(body, baoa + np.where(body, tb, 0.0) * bard, 0.0)
        side = body & (y > 0.0) & (y < baba) & (tb > EPS)
        t[side] = tb[side]

        # End caps: whichever end the infinite-cylinder hit fell beyond.
        caps = (h >= 0.0) & ~side
        if np.any(caps):
            oc = np.where((y <= 0.0)[:, None], oa[None, :], (o - self.b)[None, :])
            Bc = np.einsum("ij,ij->i", d, oc)
            Cc = np.einsum("ij,ij->i", oc, oc) - r * r
            hc = Bc * Bc - Cc
            okc = caps & (hc > 0.0)
            tc = -Bc - np.sqrt(np.where(okc, hc, 0.0))
            sel = okc & (tc > EPS)
            t[sel] = tc[sel]
        return t


@dataclass
class Box:
    center: np.ndarray            # (3,)
    half: np.ndarray              # (3,) half extents along the local axes
    rot: np.ndarray = field(default_factory=lambda: np.eye(3))

    def intersect(self, o: np.ndarray, d: np.ndarray) -> np.ndarray:
        ol = (o - self.center) @ self.rot                        # (3,)
        dl = d @ self.rot                                        # (N,3)
        with np.errstate(divide="ignore", invalid="ignore"):
            inv = 1.0 / np.where(np.abs(dl) < EPS, EPS, dl)
        t1 = (-self.half - ol) * inv
        t2 = (self.half - ol) * inv
        tmin = np.max(np.minimum(t1, t2), axis=1)
        tmax = np.min(np.maximum(t1, t2), axis=1)
        hit = (tmax >= np.maximum(tmin, 0.0)) & (tmin > EPS)
        return np.where(hit, tmin, np.inf)


@dataclass
class Surface:
    """One opaque object in the scene."""

    shape: object                 # Ellipsoid | Capsule | Box
    part: int                     # Part id (see body.Part)
    reflectivity: float           # 0..1 at 940 nm


def cast(surfaces: list[Surface], origin: np.ndarray, dirs: np.ndarray):
    """First hit per ray. Returns (distance_m, part_id, reflectivity); misses are inf / -1 / 0."""
    n = dirs.shape[0]
    best = np.full(n, np.inf)
    part = np.full(n, -1, dtype=np.int16)
    refl = np.zeros(n)
    for s in surfaces:
        t = s.shape.intersect(origin, dirs)
        closer = t < best
        best[closer] = t[closer]
        part[closer] = s.part
        refl[closer] = s.reflectivity
    return best, part, refl
