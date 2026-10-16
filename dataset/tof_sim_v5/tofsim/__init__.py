"""Synthetic multizone ToF frames of a person at a desk, with ground-truth posture labels."""

from .session import LABELS, SessionConfig, generate_frames, sample_session

__all__ = ["LABELS", "SessionConfig", "generate_frames", "sample_session"]
