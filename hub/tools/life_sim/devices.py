"""주변기기 mock + 실내 환경 + 날씨 (전부 모의. 실제 명령 계약은 지킨다).

기기(설정 config/devices.yaml `devices`)
  vent_fan      블루투스 선풍기(ESP32+L9110 개인 제작). 휴대폰 문자 0~3 = 끄기/약/중/강. hub 의 control.yaml 은 target vent_fan,
                operation set_power on/off 로 오므로 on → `hub_on_level`(기본 2) 로 받는다. 효과: 체감온도↓·각성↑(작음)·강에서 소음.
  air_purifier  LG ThinQ 공기청정기(읽기 전용 탐색은 feat/device-control-platform-20261019 의 device_gateway). PM↓, CO₂는 못 내린다
                → CO₂ 높음은 "환기(창문)" 권유 카드로 간다.
  desk_lamp     iLink 블루투스 무드등(control/ilink_light.py). 차갑고 밝게 = 각성↑, 따뜻하고 낮게 = 이완. hub 는 set_brightness 로 온다.
  speaker       Atlas A2DP 블루투스 스피커 + ATLAS TTS(VoiceManager1.StartTTS, 한국어 미확인). 깨우기 확률 최대, 몰입 중 방해 거절↑.
  screen        Pi 5 화면 카드(제안 수락·거절·무응답, 자동 실행 알림 + 되돌리기, 정정).
  window        사람이 여는 창문(기기 아님) — 환기 권유 카드를 수락하면 vent_minutes 동안 열린다.

환경 물리(설정 `environment`)는 1 초 단위 1차 모델이다. 숫자는 그럴듯한 가정이지 실측이 아니다.
날씨는 deskmate/sensor/weather(retain) 를 각본이 발행하는 것으로 모사하고, 환기 규칙(`weather_rules`)은 여기서 판정한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DeviceState:
    fan_level: int = 0                 # 0~3
    purifier_level: int = 0            # 0~3
    lamp_on: bool = False
    lamp_brightness: int = 0           # 0~100 (%)
    lamp_mode: str = "off"             # off | cool | warm | hub (hub set_brightness)
    window_open_until: float | None = None
    tts_count: int = 0
    notice_count: int = 0
    card_count: int = 0
    log: list[dict[str, Any]] = field(default_factory=list)


class DeviceWorld:
    def __init__(self, cfg: dict[str, Any], env0: dict[str, Any], weather: dict[str, Any], t0: float):
        self.cfg = cfg
        self.e = dict(cfg.get("environment") or {})
        self.dev_cfg = dict(cfg.get("devices") or {})
        self.rules = dict(cfg.get("weather_rules") or {})
        self.env = {"co2_ppm": float(env0.get("co2_ppm", 850)), "temp_c": float(env0.get("temp_c", 24.0)),
                    "humidity_pct": float(env0.get("humidity_pct", 45.0)), "lux": float(env0.get("lux", 350.0)),
                    "pm25": float(env0.get("pm25", 20.0)), "felt_temp_c": float(env0.get("temp_c", 24.0))}
        self.ambient_lux = float(env0.get("lux", 350.0))
        self.weather = dict(weather)
        self.weather.setdefault("fetched_at", t0)
        self.state = DeviceState()
        if env0.get("window_open"):
            self.state.window_open_until = float("inf")
        self.t = t0
        self.forced: dict[str, float] = {}          # 각본이 강제한 환경값 (co2_ppm 램프 등)

    # ---------------------------------------------------------------- 물리
    def step(self, now: float, *, present: bool, segment_env: dict[str, Any] | None = None, seg_progress: float = 0.0) -> None:
        dt = max(0.0, now - self.t)
        self.t = now
        if dt <= 0:
            return
        e, s, env = self.e, self.state, self.env
        window = s.window_open_until is not None and now < s.window_open_until
        if s.window_open_until is not None and now >= s.window_open_until:
            s.window_open_until = None
            self._log(now, "window", "closed", {})
        # CO2: 사람 발생 − 누설/창문 환기 (선풍기·공기청정기는 못 내린다)
        gen = float(e.get("person_co2_ppm_per_s", 0.07)) if present else 0.0
        rate = float(e.get("window_vent_per_s", 0.0025)) if window else float(e.get("leak_per_s", 0.00003))
        out_co2 = float(e.get("outdoor_co2_ppm", 420.0))
        env["co2_ppm"] += dt * (gen - rate * (env["co2_ppm"] - out_co2))
        # PM2.5: 발생 − 공기청정기 − 창문(바깥 PM 으로 수렴)
        pm_out = float(self.weather.get("pm25", e.get("pm25_outdoor", 25.0)))
        pur = float((e.get("purifier_per_s") or {}).get(str(s.purifier_level), 0.0))
        pm_rate = pur + (float(e.get("window_vent_per_s", 0.0025)) if window else 0.0)
        env["pm25"] += dt * (float(e.get("pm_gen_per_s", 0.002)) * (1.0 if present else 0.3)
                             - pur * env["pm25"] - (rate if window else 0.0) * (env["pm25"] - pm_out))
        env["pm25"] = max(2.0, env["pm25"])
        # 온도: 창문이면 바깥으로 수렴, 아니면 천천히 실내 기준으로. 체감온도 = 온도 + 선풍기 효과
        t_out = float(self.weather.get("temp_c", 18.0))
        couple = float(e.get("window_temp_per_s", 0.001)) if window else float(e.get("room_temp_per_s", 0.0002))
        target = t_out if window else float(e.get("room_temp_c", env["temp_c"]))
        env["temp_c"] += dt * couple * (target - env["temp_c"])
        env["felt_temp_c"] = env["temp_c"] + float((e.get("fan_felt_delta_c") or {}).get(str(s.fan_level), 0.0))
        # 습도: 창문이면 바깥 습도로
        h_out = float(self.weather.get("humidity", 55.0))
        env["humidity_pct"] += dt * (float(e.get("window_hum_per_s", 0.001)) if window else 0.0) * (h_out - env["humidity_pct"])
        # 조도: 주변광 + 램프
        lamp = s.lamp_brightness * float(e.get("lamp_lux_per_pct", 2.5)) if s.lamp_on else 0.0
        env["lux"] = self.ambient_lux + lamp
        # 각본 강제값 (예: co2_to 램프) — 물리 위에 덮는다
        if segment_env:
            if "co2_to" in segment_env and "co2_from" in segment_env:
                env["co2_ppm"] = float(segment_env["co2_from"]) + (float(segment_env["co2_to"]) - float(segment_env["co2_from"])) * seg_progress
            elif "co2_to" in segment_env:
                k = min(1.0, dt * float(e.get("forced_ramp_per_s", 0.002)))
                env["co2_ppm"] += k * (float(segment_env["co2_to"]) - env["co2_ppm"])
            if "lux" in segment_env:
                self.ambient_lux = float(segment_env["lux"])
            if "temp_c" in segment_env:
                env["temp_c"] = float(segment_env["temp_c"])
            if "pm25" in segment_env:
                env["pm25"] = float(segment_env["pm25"])

    def env_sample(self) -> dict[str, Any]:
        return {"co2_ppm": self.env["co2_ppm"], "temp_c": self.env["temp_c"], "humidity_pct": self.env["humidity_pct"],
                "lux": self.env["lux"]}

    # ---------------------------------------------------------------- 명령 (hub control/cmd 계약)
    def apply_command(self, cmd: dict[str, Any], now: float) -> dict[str, Any]:
        """deskmate/control/cmd payload → 기기 상태. 돌려주는 dict 은 반응 모델이 쓸 채널 목록."""
        target, op, value = cmd.get("target_id"), cmd.get("operation"), cmd.get("value")
        s = self.state
        channels: list[str] = []
        if target == "vent_fan" and op == "set_power":
            lvl = int(self.dev_cfg.get("vent_fan", {}).get("hub_on_level", 2)) if str(value).lower() == "on" else 0
            s.fan_level = lvl
            channels.append("fan_high" if lvl >= 3 else "fan_on" if lvl > 0 else "fan_off")
        elif target == "vent_fan" and op == "set_level":
            s.fan_level = max(0, min(3, int(value)))
            channels.append("fan_high" if s.fan_level >= 3 else "fan_on" if s.fan_level > 0 else "fan_off")
        elif target == "desk_lamp" and op == "set_brightness":
            s.lamp_on = int(value) > 0
            s.lamp_brightness = int(value)
            s.lamp_mode = "hub" if s.lamp_on else "off"
            channels.append("lamp_bright" if int(value) >= int(self.dev_cfg.get("desk_lamp", {}).get("bright_pct", 60)) else "lamp_dim")
        elif target == "desk_lamp" and op == "set_power":
            s.lamp_on = str(value).lower() == "on"
            channels.append("lamp_bright" if s.lamp_on else "lamp_off")
        elif target == "air_purifier":
            s.purifier_level = max(0, min(3, int(value))) if op == "set_level" else (2 if str(value).lower() == "on" else 0)
            channels.append("purifier")
        else:
            channels.append("unknown_command")
        self._log(now, "command", f"{target}.{op}={value}", {"gate": cmd.get("gate"), "command_id": cmd.get("command_id")})
        return {"channels": channels, "target": target, "operation": op, "value": value}

    # ---------------------------------------------------------------- life_sim 채널 (hub 밖의 전달 수단)
    def apply_channel(self, channel: str, now: float, payload: dict[str, Any] | None = None) -> None:
        s = self.state
        if channel == "lamp_cool":
            s.lamp_on, s.lamp_mode, s.lamp_brightness = True, "cool", int(self.dev_cfg.get("desk_lamp", {}).get("cool_pct", 90))
        elif channel == "lamp_warm":
            s.lamp_on, s.lamp_mode, s.lamp_brightness = True, "warm", int(self.dev_cfg.get("desk_lamp", {}).get("warm_pct", 35))
        elif channel == "lamp_off":
            s.lamp_on, s.lamp_mode, s.lamp_brightness = False, "off", 0
        elif channel == "tts":
            s.tts_count += 1
        elif channel == "screen_notice":
            s.notice_count += 1
        elif channel == "screen_card":
            s.card_count += 1
        elif channel in ("fan_on", "fan_high", "fan_off"):
            s.fan_level = {"fan_on": int(self.dev_cfg.get("vent_fan", {}).get("hub_on_level", 2)), "fan_high": 3, "fan_off": 0}[channel]
        elif channel == "purifier":
            s.purifier_level = int((payload or {}).get("level", 2))
        elif channel == "window_open":
            minutes = float((payload or {}).get("minutes", self.vent_minutes()))
            s.window_open_until = now + 60.0 * minutes
        self._log(now, "channel", channel, payload or {})

    def undo_command(self, cmd: dict[str, Any], now: float) -> None:
        self.apply_command(cmd, now)

    # ---------------------------------------------------------------- 날씨 규칙
    def season(self) -> str:
        return str(self.weather.get("season", "other"))

    def vent_minutes(self) -> float:
        vm = self.rules.get("vent_minutes") or {}
        return float(vm.get("winter", 5) if self.season() == "winter" else vm.get("other", 15))

    def ventilation_choice(self, co2_ppm: float, pm25: float) -> dict[str, Any]:
        """CO₂ 높음일 때 환기(창문) vs 선풍기 vs 공기청정기. 날씨 규칙: 바람 ≥4 m/s 환기 우선, 비·눈·바깥이 2 ℃ 더우면 환기 제외."""
        w = self.weather
        r = self.rules
        why: list[str] = []
        exclude = False
        if str(w.get("condition")) in (r.get("exclude_conditions") or ["rain", "snow"]):
            exclude, why = True, why + [f"{w.get('condition')}"]
        if float(w.get("temp_c", 18.0)) - self.env["temp_c"] >= float(r.get("exclude_if_outdoor_warmer_c", 2.0)):
            exclude, why = True, why + ["outdoor_warmer"]
        if not exclude and float(w.get("wind_ms", 0.0)) >= float(r.get("wind_vent_ms", 4.0)):
            why.append("windy")
        if not exclude:
            return {"choice": "window", "minutes": self.vent_minutes(), "why": why or ["co2_high"]}
        if pm25 >= float(r.get("pm25_purifier", 35.0)):
            return {"choice": "air_purifier", "why": why + ["pm_high"]}
        return {"choice": "vent_fan", "why": why + ["co2_stays_fan_only_comfort"]}

    def publish_weather(self) -> dict[str, Any]:
        """deskmate/sensor/weather(retain) 모사 payload."""
        return {"temp_c": self.weather.get("temp_c"), "humidity": self.weather.get("humidity"),
                "wind_ms": self.weather.get("wind_ms"), "condition": self.weather.get("condition", "unknown"),
                "season": self.weather.get("season"), "fetched_at": self.weather.get("fetched_at"),
                "source": self.weather.get("source", "sim")}

    def snapshot(self) -> dict[str, Any]:
        s = self.state
        return {"fan": s.fan_level, "purifier": s.purifier_level, "lamp": s.lamp_mode if s.lamp_on else "off",
                "lamp_pct": s.lamp_brightness, "window": s.window_open_until is not None, "tts": s.tts_count,
                "notices": s.notice_count, "cards": s.card_count,
                "co2": round(self.env["co2_ppm"]), "pm25": round(self.env["pm25"], 1), "temp": round(self.env["temp_c"], 1),
                "felt": round(self.env["felt_temp_c"], 1), "lux": round(self.env["lux"])}

    def _log(self, now: float, kind: str, what: str, extra: dict[str, Any]) -> None:
        self.state.log.append({"t": now, "kind": kind, "what": what, **extra})
