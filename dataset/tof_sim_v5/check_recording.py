"""녹화 파일 점검: 보내기 전에 센서별 프레임 수·주기·끊김·상태값 유무·각본과 겹치는지 확인한다.

    python check_recording.py rec_20261015_1830.jsonl
    python check_recording.py rec_20261015_1830.jsonl --t0 protocol_start_20261015_183105.txt --protocol real_protocol_v1.csv

마지막 줄에 OK 또는 문제 목록(WARN)을 찍는다. numpy 와 같은 폴더의 tof_live.py 만 쓴다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tof_live import Source  # noqa: E402  같은 줄 해석기


def main():
    try:
        sys.stdout.reconfigure(errors="replace")     # a cp949 console must not crash on an odd character
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("record", help="tof_live.py --record 로 만든 파일")
    ap.add_argument("--t0", help="protocol_timer.py 가 쓴 protocol_start_*.txt")
    ap.add_argument("--protocol", help="각본 CSV (길이 확인용)")
    ap.add_argument("--expect-sensors", type=int, default=2)
    args = ap.parse_args()

    per = {}           # port -> dict
    other = {"rule": 0, "hello": 0, "err": [], "info": 0, "bad": 0, "unwrapped": 0}
    with open(args.record, encoding="utf-8", errors="replace") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            port, host = None, None
            if line.startswith('{"host_t"'):
                try:
                    w = json.loads(line)
                except ValueError:
                    other["bad"] += 1
                    continue
                port, host, line = w.get("port"), w.get("host_t"), w.get("raw", "")
            else:
                other["unwrapped"] += 1
            obj = Source._parse(line)
            if obj is None:
                other["bad"] += 1
                continue
            kind = obj.get("t")
            p = per.setdefault(port, {"ms": [], "host": [], "status": None, "zero": 0, "n": 0})
            if kind == "tof" and len(obj.get("d", [])) == 64:
                p["ms"].append(int(obj["ms"]))
                if host is not None:
                    p["host"].append(float(host))
                has = obj.get("has_status") is not False
                p["status"] = has if p["status"] is None else (p["status"] and has)
                p["zero"] += sum(1 for v in obj["d"] if v <= 0)
                p["n"] += 1
            elif kind == "rule":
                other["rule"] += 1
            elif kind == "tof_hello":
                other["hello"] += 1
            elif kind == "tof_err":
                other["err"].append(obj.get("where", ""))
            else:
                other["info"] += 1

    warn = []
    sensors = {k: v for k, v in per.items() if v["n"] > 0}
    print(f"파일 {args.record}: 센서(포트) {len(sensors)}개 {list(sensors)}")
    t_start, t_end = [], []
    for port, p in sensors.items():
        ms = np.array(p["ms"])
        dt = np.diff(ms)
        back = int((dt < 0).sum())
        gaps = int((dt > 1000).sum())
        dur = (ms[-1] - ms[0]) / 1000 if len(ms) > 1 else 0.0
        hz = 1000 / np.median(dt[dt > 0]) if (dt > 0).any() else 0.0
        empty = p["zero"] / (64 * p["n"])
        print(f"  {port}: 프레임 {p['n']}, {dur / 60:.1f}분, {hz:.1f} Hz, 1초 넘는 끊김 {gaps}번, 시계 되감김 {back}번, "
              f"상태값 {'있음' if p['status'] else '없음(거리만)'}, 빈 존 비율 {empty:.0%}")
        if p["host"]:
            t_start.append(min(p["host"])); t_end.append(max(p["host"]))
        if hz and not (9.0 <= hz <= 16.0):
            warn.append(f"{port} 주기 {hz:.1f} Hz (10 또는 15 Hz 여야 함)")
        if gaps or back:
            warn.append(f"{port} 끊김 {gaps}번·되감김 {back}번 (센서가 재시작했거나 USB 가 빠졌음)")
        if not p["status"]:
            warn.append(f"{port} 상태값 없음 - 펌웨어가 F 줄에 거리 64개만 보냄(상태값 64개도 보내 달라고 할 것)")
        if empty > 0.5:
            warn.append(f"{port} 빈 존 {empty:.0%} - 센서가 가려졌거나 사람/책상이 시야에 거의 없음")
    if len(sensors) != args.expect_sensors:
        warn.append(f"센서 {len(sensors)}개 (기대 {args.expect_sensors}개)")
    if other["unwrapped"]:
        warn.append(f"PC 시각 없는 줄 {other['unwrapped']}개 - 옛 tof_live.py 로 녹화했음(이 키트의 tof_live.py 를 쓸 것)")
    if other["err"]:
        warn.append(f"센서 오류 줄 {len(other['err'])}개: {sorted(set(other['err']))[:3]}")
    print(f"  기타 줄: 규칙 판정 {other['rule']}, 시작 안내 {other['hello']}, 오류 {len(other['err'])}, 안내 {other['info']}, 해석 못 함 {other['bad']}")

    if args.t0 and t_start:
        with open(args.t0, encoding="utf-8") as fp:
            t0 = float(fp.readline().split()[0])
        total = None
        if args.protocol:
            import csv
            with open(args.protocol, encoding="utf-8-sig") as fp:
                ends = [float(r[1]) for r in csv.reader(fp) if r and not r[0].startswith("#") and r[0] != "start_s"]
            total = max(ends)
        a, b = min(t_start) - t0, max(t_end) - t0
        print(f"  각본 기준: 녹화가 {a:+.1f} s 에 시작, {b:+.1f} s 에 끝남" + (f" (각본 길이 {total:.0f} s)" if total else ""))
        if a > 0:
            warn.append(f"녹화가 각본보다 {a:.1f} s 늦게 시작했음 - 앞부분 라벨이 빠짐")
        if total and b < total - 2.0:
            warn.append(f"녹화가 각본 끝({total:.0f} s)보다 {total - b:.0f} s 일찍 끝났음")
    elif args.t0:
        warn.append("녹화에 PC 시각이 없어 각본 시작과 맞출 수 없음")

    if warn:
        print("WARN:")
        for w in warn:
            print("  - " + w)
    else:
        print("OK - 보내도 됩니다.")


if __name__ == "__main__":
    main()
