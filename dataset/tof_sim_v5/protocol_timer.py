"""녹화 각본 타이머: 지금 할 자세를 화면에 크게 띄우고, 바뀔 때마다 삐 소리를 낸다.

    python protocol_timer.py real_protocol_v1.csv              # 5초 세고 시작
    python protocol_timer.py real_protocol_v1.csv --name 홍길동_정면80cm

tof_live.py --record 를 먼저 켜 둔 뒤 이걸 켠다. 시작하는 순간의 PC 시각을 protocol_start_<시각>.txt 에 적어 두고,
real_from_record.py --t0 <그 파일> 이 각본 시각을 그 순간부터 센다(녹화를 언제 켰든 라벨이 맞는다).
Ctrl+C 로 중간에 멈춰도 된다(멈춘 시각도 파일에 적힌다).
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time


def beep(n=1):
    try:
        import winsound
        for _ in range(n):
            winsound.Beep(1200, 180)
            time.sleep(0.08)
    except Exception:
        sys.stdout.write("\a" * n)
        sys.stdout.flush()


def read_rows(path):
    rows = []
    with open(path, encoding="utf-8-sig") as fp:
        for r in csv.reader(fp):
            if not r or r[0].strip().startswith("#") or r[0].strip() == "start_s":
                continue
            rows.append((float(r[0]), float(r[1]), r[2].strip(), (r[3].strip() if len(r) > 3 else r[2].strip())))
    return sorted(rows)


def main():
    try:
        sys.stdout.reconfigure(errors="replace")     # a cp949 console must not crash on an odd character
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("protocol", help="각본 CSV (start_s,end_s,label,what)")
    ap.add_argument("--name", default="", help="이 녹화의 이름(사람·장치 자리 등). 시작 파일에 같이 적힌다")
    ap.add_argument("--countdown", type=int, default=5)
    args = ap.parse_args()
    rows = read_rows(args.protocol)
    total = max(b for _, b, _, _ in rows)
    print(f"각본 {os.path.basename(args.protocol)}: {len(rows)} 단계, {total / 60:.1f}분. tof_live.py --record 가 켜져 있는지 확인하세요.")
    for k in range(args.countdown, 0, -1):
        print(f"  {k} ...", flush=True)
        beep(1) if k <= 3 else None
        time.sleep(1)
    t0 = time.time()
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(t0))
    start_file = f"protocol_start_{stamp}.txt"
    with open(start_file, "w", encoding="utf-8") as fp:
        fp.write(f"{t0:.4f}\n# protocol {os.path.abspath(args.protocol)}\n# name {args.name}\n")
    print(f"시작 - {start_file} 에 시작 시각을 적었습니다. 녹화 파일과 같이 보내 주세요.\n")
    beep(2)
    current = None
    try:
        while True:
            t = time.time() - t0
            if t >= total:
                break
            i = next((j for j, (a, b, _, _) in enumerate(rows) if a <= t < b), None)
            if i != current:
                current = i
                if i is not None:
                    a, b, label, what = rows[i]
                    nxt = rows[i + 1][3] if i + 1 < len(rows) else "끝"
                    print("=" * 64)
                    print(f"[{int(a) // 60}:{int(a) % 60:02d}~{int(b) // 60}:{int(b) % 60:02d}]  {what}")
                    print(f"  (라벨 {label} · 다음: {nxt})")
                    beep(1)
            if i is not None:
                left = rows[i][1] - t
                sys.stdout.write(f"\r  남은 시간 {left:5.0f} 초   전체 {t / 60:4.1f}/{total / 60:.1f} 분 ")
                sys.stdout.flush()
            time.sleep(0.25)
        print("\n끝. tof_live.py 창에서 Ctrl+C 로 녹화를 멈추세요.")
        beep(3)
    except KeyboardInterrupt:
        print("\n중간에 멈춤.")
    with open(start_file, "a", encoding="utf-8") as fp:
        fp.write(f"# stopped_at {time.time() - t0:.1f} s\n")


if __name__ == "__main__":
    main()
