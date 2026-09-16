"""UART2 프레임(COBS + CRC-16/CCITT-FALSE) PC 도구 — 펌웨어·Pi 4 C++ 구현 검증용.

    python tools/uart_frame_tool.py vectors                       # 펌웨어/C++ 가 맞춰야 할 test vector 출력
    python tools/uart_frame_tool.py encode mmwave '{"present":true,...}' [--seq 1 --ts 1000]
    python tools/uart_frame_tool.py decode a5010120...            # wire hex(0x00 포함 가능) → 헤더·payload JSON
    python tools/uart_frame_tool.py serial --port COM5 [--baud 115200] [--log logs/uart.jsonl]
                                                                  # ESP32 UART2 를 USB-TTL 로 직접 읽어 프레임 디코딩

코덱은 hub/deskmate_hub/ingest/uart_frame.py 하나만 쓴다(중복 구현 금지).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hub"))

from deskmate_hub.ingest.uart_frame import (  # noqa: E402
    TYPE_ENV, TYPE_HEARTBEAT, TYPE_MMWAVE, Frame, FrameError, cobs_encode, crc16_ccitt_false,
    decode_frame, decode_payload, encode_frame, encode_payload, split_stream,
)

_TYPES = {"mmwave": TYPE_MMWAVE, "env": TYPE_ENV, "heartbeat": TYPE_HEARTBEAT}
_SAMPLES = {
    "mmwave": {"present": True, "motion_state": "still", "motion_level": 12, "distance_cm": 55, "resp_bpm": 15,
               "resp_valid": True, "heart_bpm": None, "heart_valid": False, "drowsy_state": "AWAKE", "valid": True},
    "env": {"co2_ppm": 812, "temp_c": 26.4, "humidity_pct": 48.2, "lux": 310.0,
            "co2_valid": True, "temp_valid": True, "humidity_valid": True, "lux_valid": True},
    "heartbeat": {"uptime_ms": 5000, "fw_version": 3, "mmwave_ok": True},
}


def cmd_vectors(_: argparse.Namespace) -> int:
    print("CRC-16/CCITT-FALSE('123456789') =", f"0x{crc16_ccitt_false(b'123456789'):04X}")
    for raw in (b"\x00", b"\x11\x22\x00\x33", b"\x11\x22\x33\x44"):
        print(f"COBS({raw.hex()}) = {cobs_encode(raw).hex()}")
    for name, ftype in _TYPES.items():
        payload = encode_payload(ftype, _SAMPLES[name])
        frame = Frame(type=ftype, seq=7, ts_ms=1000, payload=payload)
        wire = encode_frame(frame)
        print(f"\n[{name}] type=0x{ftype:02X} seq=7 ts_ms=1000")
        print("  sample  :", json.dumps(_SAMPLES[name], ensure_ascii=False))
        print("  payload :", payload.hex(), f"({len(payload)} B)")
        print("  wire    :", wire.hex(), f"({len(wire)} B, COBS+0x00)")
    return 0


def cmd_encode(args: argparse.Namespace) -> int:
    ftype = _TYPES[args.type]
    data = json.loads(args.json) if args.json else _SAMPLES[args.type]
    wire = encode_frame(Frame(type=ftype, seq=args.seq, ts_ms=args.ts, payload=encode_payload(ftype, data)))
    print(wire.hex())
    return 0


def _print_frame(frame: Frame) -> None:
    try:
        data = decode_payload(frame.type, frame.payload)
    except FrameError as exc:
        data = {"error": str(exc)}
    print(json.dumps({"type": f"0x{frame.type:02X}", "seq": frame.seq, "ts_ms": frame.ts_ms,
                      "payload_hex": frame.payload.hex(), "data": data}, ensure_ascii=False))


def cmd_decode(args: argparse.Namespace) -> int:
    buf = bytearray(bytes.fromhex(args.hex.replace(" ", "")))
    if not buf.endswith(b"\x00"):
        buf += b"\x00"
    ok = 0
    for block in split_stream(buf):
        try:
            _print_frame(decode_frame(block))
            ok += 1
        except FrameError as exc:
            print(f"REJECT {block.hex()}: {exc}", file=sys.stderr)
    return 0 if ok else 1


def cmd_serial(args: argparse.Namespace) -> int:
    import serial  # pyserial

    port = serial.Serial(args.port, args.baud, timeout=0.2)
    buf = bytearray()
    log = open(args.log, "a", encoding="utf-8") if args.log else None
    frames = dropped = 0
    last = time.time()
    print(f"reading {args.port} @ {args.baud} — Ctrl+C to stop", file=sys.stderr)
    try:
        while True:
            buf += port.read(256)
            for block in split_stream(buf):
                try:
                    frame = decode_frame(block)
                except FrameError as exc:
                    dropped += 1
                    if args.verbose:
                        print(f"REJECT {exc}", file=sys.stderr)
                    continue
                frames += 1
                _print_frame(frame)
                if log:
                    log.write(json.dumps({"received": time.time(), "type": frame.type, "seq": frame.seq,
                                          "ts_ms": frame.ts_ms, "payload_hex": frame.payload.hex()}) + "\n")
                    log.flush()
            if time.time() - last >= 10:
                last = time.time()
                print(f"[uart] frames={frames} dropped={dropped}", file=sys.stderr)
    except KeyboardInterrupt:
        pass
    finally:
        port.close()
        if log:
            log.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("vectors").set_defaults(fn=cmd_vectors)
    e = sub.add_parser("encode")
    e.add_argument("type", choices=sorted(_TYPES))
    e.add_argument("json", nargs="?")
    e.add_argument("--seq", type=int, default=1)
    e.add_argument("--ts", type=int, default=0)
    e.set_defaults(fn=cmd_encode)
    d = sub.add_parser("decode")
    d.add_argument("hex")
    d.set_defaults(fn=cmd_decode)
    s = sub.add_parser("serial")
    s.add_argument("--port", required=True)
    s.add_argument("--baud", type=int, default=115200)
    s.add_argument("--log")
    s.add_argument("--verbose", action="store_true")
    s.set_defaults(fn=cmd_serial)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
