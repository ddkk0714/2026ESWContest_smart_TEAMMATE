"""Streaming posture classifier small enough for a microcontroller.

Per frame: a tiny CNN over the zone grid -> a GRU that carries state from frame
to frame -> class logits. The GRU is what lets it count nods over a minute;
at run time it steps once per frame with no window buffer.

~70 k parameters for 8×8 input.
"""

from __future__ import annotations

import torch
from torch import nn


class PostureNet(nn.Module):
    def __init__(self, height: int = 8, width: int = 8, classes: int = 7, hidden: int = 64):
        super().__init__()
        self.enc = nn.Sequential(
            nn.Conv2d(2, 16, 3, padding=1), nn.ReLU(),
            nn.Conv2d(16, 32, 3, stride=2, padding=1), nn.ReLU(),
            nn.Conv2d(32, 32, 3, padding=1), nn.ReLU(),
            nn.AdaptiveAvgPool2d((4, 4)),
            nn.Flatten(),
            nn.Linear(32 * 16, hidden), nn.ReLU(),
        )
        self.gru = nn.GRU(hidden, hidden, batch_first=True)
        self.head = nn.Linear(hidden, classes)

    def forward(self, x: torch.Tensor, h: torch.Tensor | None = None):
        """x: (B,T,2,H,W) -> logits (B,T,C), h (1,B,hidden)"""
        B, T = x.shape[:2]
        f = self.enc(x.reshape(B * T, *x.shape[2:])).reshape(B, T, -1)
        out, h = self.gru(f, h)
        return self.head(out), h


def count_params(m: nn.Module) -> int:
    return sum(p.numel() for p in m.parameters())
