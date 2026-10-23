"""ESP32 환경 센서 대역 — SCD41(CO₂) · DHT22(온습도) · BH1750(조도) → UART 0x10 environment_sample (0.2 Hz).

data-spec §6.3 대로 물리량마다 센서 하나(CO₂=SCD41, T/RH=DHT22, lux=BH1750), 교차 보정 없음. 각 센서는
  - 실행마다 한 번 뽑는 고정 오차(교정 편차: 데이터시트 정확도의 절반 정도를 표준편차로)
  - 읽을 때마다 잡음, 응답 지연(1차 지연 τ), 해상도, 읽기 실패(→ valid=false)
를 가진다. 숫자는 config/devices.yaml `env_sensors`(데이터시트 기반 가정, 실측 아님).
"""
from __future__ import annotations

import random
from typing import Any


class _Lag:
    def __init__(self, tau_s: float):
        self.tau = float(tau_s)
        self.v: float | None = None

    def step(self, x: float, dt: float) -> float:
        if self.v is None or self.tau <= 0:
            self.v = x
        else:
            self.v += min(1.0, dt / self.tau) * (x - self.v)
        return self.v


class EnvSensors:
    def __init__(self, cfg: dict[str, Any], *, seed: int):
        c = dict(cfg or {})
        self.c = c
        self.rnd = random.Random(seed + 53)
        r = self.rnd
        s, d, b = c.get("scd41") or {}, c.get("dht22") or {}, c.get("bh1750") or {}
        self.co2_bias = r.gauss(0.0, float(s.get("bias_ppm", 15.0)))
        self.co2_gain = 1.0 + r.gauss(0.0, float(s.get("bias_pct", 2.0)) / 100.0)
        self.t_bias = r.gauss(0.0, float(d.get("bias_c", 0.3)))
        self.rh_bias = r.gauss(0.0, float(d.get("bias_rh", 2.0)))
        self.lux_gain = 1.0 + r.gauss(0.0, float(b.get("gain_sd", 0.07)))
        self.lag = {"co2": _Lag(float(s.get("tau_s", 60))), "t": _Lag(float(d.get("tau_t_s", 20))),
                    "rh": _Lag(float(d.get("tau_rh_s", 8))), "lux": _Lag(float(b.get("tau_s", 0.5)))}
        self.next = {"co2": 0.0, "dht": 0.0, "lux": 0.0}
        self.val: dict[str, Any] = {"co2_ppm": None, "temp_c": None, "humidity_pct": None, "lux": None,
                                    "co2_valid": False, "temp_valid": False, "humidity_valid": False, "lux_valid": False}
        self.t_prev: float | None = None
        self.t0: float | None = None
        self.errors: dict[str, list[float]] = {"co2": [], "temp": [], "rh": [], "lux": []}

    def step(self, now: float, env: dict[str, float]) -> dict[str, Any]:
        """1 초마다 참값을 넣는다. 지금 ESP32 가 들고 있는 마지막 읽기(0x10 계약 필드)를 돌려준다."""
        c, r = self.c, self.rnd
        dt = 1.0 if self.t_prev is None else max(0.0, now - self.t_prev)
        self.t_prev = now
        self.t0 = now if self.t0 is None else self.t0
        co2 = self.lag["co2"].step(env["co2_ppm"], dt)
        tc = self.lag["t"].step(env["temp_c"], dt)
        rh = self.lag["rh"].step(env["humidity_pct"], dt)
        lx = self.lag["lux"].step(env["lux"], dt)
        s, d, b = c.get("scd41") or {}, c.get("dht22") or {}, c.get("bh1750") or {}
        if now >= self.next["co2"]:
            self.next["co2"] = now + float(s.get("period_s", 5))
            if now - self.t0 >= float(s.get("first_after_s", 5)):
                v = co2 * self.co2_gain + self.co2_bias + r.gauss(0.0, float(s.get("noise_ppm", 10)))
                self.val["co2_ppm"] = int(round(min(40000, max(400, v))))
                self.val["co2_valid"] = True
                self.errors["co2"].append(self.val["co2_ppm"] - env["co2_ppm"])
        if now >= self.next["dht"]:
            self.next["dht"] = now + float(d.get("period_s", 2))
            if r.random() < float(d.get("fail_p", 0.02)):
                self.val.update(temp_valid=False, humidity_valid=False)          # 체크섬 실패·타임아웃
            else:
                t = round(tc + self.t_bias + r.gauss(0.0, float(d.get("noise_c", 0.1))), 1)
                h = round(min(100.0, max(0.0, rh + self.rh_bias + r.gauss(0.0, float(d.get("noise_rh", 0.5))))), 1)
                self.val.update(temp_c=t, humidity_pct=h, temp_valid=True, humidity_valid=True)
                self.errors["temp"].append(t - env["temp_c"])
                self.errors["rh"].append(h - env["humidity_pct"])
        if now >= self.next["lux"]:
            self.next["lux"] = now + float(b.get("period_s", 1))
            v = lx * self.lux_gain * (1.0 + r.gauss(0.0, float(b.get("noise_pct", 1.0)) / 100.0))
            self.val["lux"] = int(round(min(65535, max(0.0, v))))
            self.val["lux_valid"] = True
            self.errors["lux"].append(self.val["lux"] - env["lux"])
        return dict(self.val)

    def error_stats(self) -> dict[str, Any]:
        out = {}
        for k, v in self.errors.items():
            if v:
                out[k] = {"bias": round(sum(v) / len(v), 2), "mae": round(sum(abs(x) for x in v) / len(v), 2), "n": len(v)}
        out["fixed"] = {"co2_bias_ppm": round(self.co2_bias, 1), "co2_gain": round(self.co2_gain, 3), "t_bias_c": round(self.t_bias, 2),
                        "rh_bias": round(self.rh_bias, 2), "lux_gain": round(self.lux_gain, 3)}
        return out
