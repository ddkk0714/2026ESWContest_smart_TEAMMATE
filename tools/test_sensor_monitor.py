"""sensor_monitor 의 줄 파서·상태 집계(시리얼·HTTP 없이). `python -m pytest tools/test_sensor_monitor.py`."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sensor_monitor import MonitorState, parse_line  # noqa: E402

TOF_FEATURE = (
    '{"t":"tof_feature","ms":3630,"present":false,"posture":"away","posture_confidence":0.00,"motion_score":0.00,'
    '"head_delta_mm":null,"nod_rate_hz":null,"valid_zones":62,"grid_width":16,"grid_height":8,"coverage_ratio":0.06,'
    '"centroid_depth_mm":1071,"head_row_index":0.00,"baseline_ready":false,"drowsy":false,"nods_in_window":0,'
    '"sensors_ok":3,"tx_grid":2,"tx_feature":1}'
)
MMWAVE = (
    '{"t":"mmwave","ms":3630,"present":false,"motion_state":"none","motion_level":0,"distance_cm":null,'
    '"resp_bpm":null,"resp_valid":false,"heart_bpm":null,"heart_valid":false,"drowsy_state":"NOPERSON","valid":false}'
)
ENV = (
    '{"t":"env","ms":5010,"co2_ppm":null,"temp_c":23.60,"humidity_pct":50.20,"lux":490.00,"co2_valid":false,'
    '"temp_valid":true,"humidity_valid":true,"lux_valid":true}'
)
GRID = (
    '{"t":"tof_grid","ms":4000,"frame":7,"sensors_ok":1,"w":2,"h":2,"flags":2,'
    '"s":[{"d":[500,-1,700,800],"st":[5,255,9,5]},null]}'
)


def test_parse_line_json_and_log():
    assert parse_line("") is None
    assert parse_line("   \r") is None
    kind, obj = parse_line(TOF_FEATURE + "\r")
    assert kind == "json" and obj["t"] == "tof_feature" and obj["head_delta_mm"] is None
    assert parse_line("VL53L8CX x2: ok_mask=3 grid=16x8") == ("log", "VL53L8CX x2: ok_mask=3 grid=16x8")
    # `{"t":` 로 시작하지만 깨진 줄(부팅 직후 잘린 줄)은 로그로 보관한다
    assert parse_line('{"t":"env","ms":')[0] == "log"


def test_state_latest_rate_history_and_logs():
    st = MonitorState(now=100.0)
    for i in range(5):
        st.feed(MMWAVE, now=100.0 + i)
    st.feed(ENV, now=104.0)
    st.feed(GRID, now=104.2)
    st.feed("# tof_grid usb on", now=104.3)
    snap = st.snapshot(now=105.0)
    assert snap["latest"]["mmwave"]["drowsy_state"] == "NOPERSON"
    assert snap["counts"] == {"mmwave": 5, "env": 1, "tof_grid": 1}
    assert abs(snap["rate_hz"]["mmwave"] - 1.0) < 1e-9  # 4 간격 / 4 s
    assert snap["rate_hz"]["env"] == 0.0  # 표본 하나로는 주기를 모른다
    assert abs(snap["age_s"]["env"] - 1.0) < 1e-9
    assert len(snap["history"]["mmwave"]) == 5
    assert snap["history"]["mmwave"][0][0] == -5.0  # 지금 기준 상대 시각
    assert set(snap["history"]["env"][0][1]) == {"co2_ppm", "temp_c", "humidity_pct", "lux"}
    assert "tof_grid" not in snap["history"]  # 격자는 추이로 쌓지 않는다
    assert snap["latest"]["tof_grid"]["s"][1] is None
    assert snap["logs"] == [[-0.7, "# tof_grid usb on"]]


def test_history_and_rate_windows_trim():
    st = MonitorState(now=0.0)
    for i in range(0, 90):
        st.feed(TOF_FEATURE, now=float(i))
    snap = st.snapshot(now=89.0)
    assert len(snap["history"]["tof_feature"]) == 61  # 60 s 창(경계 포함)
    assert abs(snap["rate_hz"]["tof_feature"] - 1.0) < 1e-9  # 10 s 창에서 1 Hz
