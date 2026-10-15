"""VL53L8CX 수집기: ESP32(env:tof_logger) USB 줄 → 세션 폴더.

  python tof_log.py --port COM5 --user soyeon --protocol      # 지시 자세 프로토콜(정답 라벨)
  python tof_log.py --port COM5 --user soyeon --minutes 30    # 자유 기록(규칙 판정만)
  python tof_log.py --fake --user test --protocol --fast      # 하드웨어 없이 배관 확인

끝나면 inspect_session.py 로 규칙 판정 정확도와 방향을 확인한다.
"""
from __future__ import annotations

import argparse
import sys
import time

from tofl.fake import FakeSource
from tofl.protocol import Schedule, load_config
from tofl.recorder import SerialSource, Status, record
from tofl.session import SessionWriter


def _beep() -> None:
    try:
        import winsound

        winsound.Beep(880, 150)
    except Exception:  # noqa: BLE001 — 소리는 있으면 좋은 것
        pass


def _printer(quiet_beep: bool):
    def show(st: Status) -> None:
        if st.step_changed and st.now is not None:
            print(f"\n>>> {st.now.text}", flush=True)
            if not quiet_beep:
                _beep()
        rule = st.rule or {}
        remain = f"{st.now.remain_s:4.1f}s" if st.now else "     "
        err = f" err={st.errors}" if st.errors else ""
        print(f"\r  {st.elapsed_s:6.1f}s 남음 {remain} | 프레임 {st.frames:6d} {st.fps:4.1f}fps "
              f"갭 {st.seq_gaps} | 규칙 {rule.get('posture', '-'):12s} "
              f"기준선 {'O' if rule.get('baseline') else '-'}{err}   ", end="", flush=True)
    return show


def _event(obj: dict) -> None:
    if obj["t"] == "tof_hello":
        print(f"\n센서 연결: {obj.get('sensor')} {obj.get('w')}x{obj.get('h')} {obj.get('hz')} Hz "
              f"flip_rows={obj.get('flip_rows')} flip_cols={obj.get('flip_cols')}", flush=True)
    else:
        print(f"\n센서 오류: {obj.get('where')} code={obj.get('code')} "
              "(init 이면 배선·전원·LPn 확인, 3초마다 다시 시도)", flush=True)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--port", help="ESP32 시리얼 포트 (예: COM5)")
    src.add_argument("--fake", action="store_true", help="가짜 L8 로 배관만 확인")
    ap.add_argument("--user", required=True, help="세션 폴더 이름에 쓰는 사람 ID(영문)")
    ap.add_argument("--baud", type=int, default=921600)
    ap.add_argument("--protocol", action="store_true", help="config.yaml 프로토콜로 지시 자세 기록")
    ap.add_argument("--minutes", type=float, help="자유 기록 길이(프로토콜 없이)")
    ap.add_argument("--config", help="프로토콜 설정 파일 (기본 config.yaml)")
    ap.add_argument("--tag", help="세션 폴더 꼬리표 (기본 protocol/free/fake)")
    ap.add_argument("--note", default="", help="meta.json 에 남길 메모(설치 높이·거리 등)")
    ap.add_argument("--fast", action="store_true", help="--fake 를 실제 시간 대신 즉시 돌림")
    ap.add_argument("--quiet", action="store_true", help="단계 바뀔 때 삑 소리 끄기")
    a = ap.parse_args()
    if not a.protocol and a.minutes is None:
        ap.error("--protocol 또는 --minutes 중 하나는 필요합니다")
    if a.fast and not a.fake:
        ap.error("--fast 는 --fake 에서만")

    schedule = Schedule.from_config(load_config(a.config)) if a.protocol else None
    tag = a.tag or ("fake" if a.fake else "protocol" if a.protocol else "free")
    meta = {"source": "fake" if a.fake else f"serial {a.port} @ {a.baud}", "note": a.note,
            "protocol": schedule.snapshot() if schedule else None,
            "max_seconds": a.minutes * 60 if a.minutes else None}
    writer = SessionWriter(a.user, tag, meta)

    if a.fake:
        t0 = time.time()

        def posture_at(t: float) -> str | None:
            if schedule is None:
                return "upright"
            now = schedule.at(t - (t0 + 0.5))  # 가짜 소스의 첫 프레임 = t0 + 0.5
            return now.prompt if now else None

        source = FakeSource(posture_at, realtime=not a.fast, t0=t0)
    else:
        source = SerialSource(a.port, a.baud)

    if schedule:
        print(f"프로토콜 {len(schedule.steps)}단계, 약 {schedule.total_s / 60:.1f}분. "
              "센서 첫 프레임부터 시작합니다. Ctrl+C 로 중단(그때까지는 저장됨).")
    print(f"저장: {writer.folder}")
    reason = "done"
    try:
        st = record(source, writer, schedule=schedule,
                    max_seconds=a.minutes * 60 if a.minutes else None,
                    on_status=None if a.fast else _printer(a.quiet), send=source.send,
                    on_event=None if a.fast else _event)
    except KeyboardInterrupt:
        reason = "interrupted"
        st = None
    finally:
        source.close()
        writer.close(end_reason=reason)
    print()
    print(f"끝 ({reason}). 프레임 {writer.stats['frames']}, seq 갭 {writer.stats['seq_gaps']}"
          f"(빠진 프레임 {writer.stats['missing_frames']}), 센서 오류 {writer.stats['errors']}")
    if st is not None and st.last_error:
        print(f"마지막 센서 오류: {st.last_error}")
    print(f"확인: python inspect_session.py \"{writer.folder}\"")
    return 0 if writer.stats["frames"] > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
