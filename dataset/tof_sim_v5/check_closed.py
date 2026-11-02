"""Are all body pieces closed (no boundary edges left)?"""
import numpy as np

from tofsim import smplx_body as sb

for g in ("neutral", "female"):
    b = sb.make(g, [0.0] * 10, 0.8)
    for name, p in b.parts.items():
        edges = np.sort(p.faces[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1)
        _, cnt = np.unique(edges, axis=0, return_counts=True)
        print(f"{g:8s} {name:8s} verts {len(p.verts):5d} faces {len(p.faces):5d} open edges {int((cnt == 1).sum())}")
