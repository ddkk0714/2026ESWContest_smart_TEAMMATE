"""사람별 자세 보정(11-03) — 공용 자세 모델 출력 위에 그 사람 녹화로 맞춘 작은 로지스틱 회귀를 얹는다.

왜: 공용 모델(opt_l8_c256)은 시뮬로 학습했고 규칙·기준선은 한 사람(10-26 녹화) 몸에 맞춰져 있어, 키·앉은키가 다른 사람은
바르게 앉아도 졸음·턱 괴기로 흔들린다(11-03 민경 녹화: 공용 2회차 acc 0.60, 바르게 재현율 0.58·숙임 0.00).
사용자 지시: 공용 모델은 그대로, 사람(프로필)마다 따로, 다른 프로필에는 영향 없게.

  파일  profiles/<profile_id>/posture_calib.json (person_data/tools/person_calib.py 가 만든다). 없으면 아무것도 바꾸지 않는다.
  입력  자세 서비스 /api/state 의 융합 probs 와 센서별 probs(#s0·#s1) — 각 log(p+1e-4), 순서는 labels.
  출력  argmax 라벨·softmax 확률로 tof_service 표본의 posture·posture_confidence·drowsy 만 바꾼다(재실·격자·규칙은 그대로).
제한 Python(보드)에서 돈다 — 표준 라이브러리만.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import replace
from typing import Any

FILE = "posture_calib.json"


class PersonCalib:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.labels = [str(x) for x in cfg["labels"]]
        self.mean = [float(x) for x in cfg["mean"]]
        self.std = [float(x) for x in cfg["std"]]
        self.W = [[float(x) for x in row] for row in cfg["W"]]
        self.b = [float(x) for x in cfg["b"]]
        if not (len(self.mean) == len(self.std) == len(self.W) == 3 * len(self.labels)) or len(self.b) != len(self.labels):
            raise ValueError("posture_calib 크기가 맞지 않음")

    @classmethod
    def load(cls, path: str) -> "PersonCalib | None":
        try:
            with open(path, encoding="utf-8") as fp:
                return cls(json.load(fp))
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _features(self, data: dict[str, Any]) -> list[float] | None:
        fused = data.get("probs")
        if not isinstance(fused, dict):
            return None
        by_id: dict[int, dict[str, Any]] = {}
        for s in data.get("sensors") or []:
            name = str(s.get("name") or "")
            if "#s" in name and isinstance(s.get("probs"), dict):
                try:
                    by_id[int(name.rsplit("#s", 1)[-1])] = s["probs"]
                except ValueError:
                    pass
        vec = [math.log(float(fused.get(c, 0.0)) + 1e-4) for c in self.labels]
        for sid in (0, 1):
            p = by_id.get(sid) or {}
            vec += [math.log(float(p.get(c, 0.0)) + 1e-4) for c in self.labels]
        return vec

    def classify(self, data: dict[str, Any]) -> tuple[str, float] | None:
        x = self._features(data)
        if x is None:
            return None
        z = [(v - m) / s for v, m, s in zip(x, self.mean, self.std)]
        logits = [sum(z[i] * self.W[i][k] for i in range(len(z))) + self.b[k] for k in range(len(self.labels))]
        top = max(logits)
        exps = [math.exp(v - top) for v in logits]
        k = max(range(len(exps)), key=exps.__getitem__)
        return self.labels[k], exps[k] / sum(exps)


class PostureCalibrator:
    """지금 사람(profile_id)의 보정 파일을 따라간다. 사람이 바뀌면 다시 읽고, 파일이 없으면 None(원래 판정)."""

    def __init__(self, profiles_root: str | None) -> None:
        self.root = profiles_root
        self.profile_id: str | None = None
        self.calib: PersonCalib | None = None
        self.last: dict[str, Any] | None = None

    def follow(self, profile_id: str | None) -> None:
        if profile_id == self.profile_id:
            return
        self.profile_id = profile_id
        self.calib = (PersonCalib.load(os.path.join(self.root, profile_id, FILE))
                      if self.root and profile_id else None)

    def apply(self, view: Any) -> Any:
        """view.tof_service 를 보정한 사본으로 바꾼 view. 보정이 없거나 표본이 판정 불가면 view 그대로."""
        sample = getattr(view, "tof_service", None)
        if self.calib is None or sample is None or not sample.data.get("valid", True):
            return view
        out = self.calib.classify(sample.data)
        if out is None:
            return view
        label, conf = out
        before = sample.data.get("posture")
        self.last = {"profile_id": self.profile_id, "model": before, "calibrated": label, "conf": round(conf, 4)}
        data = dict(sample.data, posture=label, posture_confidence=round(conf, 4), drowsy=label == "drowsy",
                    model_posture=before)
        return replace(view, tof_service=replace(sample, data=data))

    def snapshot(self) -> dict[str, Any]:
        return {"profile_id": self.profile_id, "active": self.calib is not None, **({"last": self.last} if self.last else {})}
