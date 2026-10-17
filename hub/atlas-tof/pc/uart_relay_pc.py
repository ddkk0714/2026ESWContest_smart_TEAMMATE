"""노트북에서 도는 임시 중계기: Pi 4 /dev/ttyAMA0 (ESP32 UART2, COBS+CRC 프레임) → TYPE 0x05 만
자세 서비스(deskmate_tofposture)가 받는 'UART\t{json}' 줄로 바꿔 <host>:9100 에 넣는다.
hub 를 건드리지 않고 자세를 보기 위한 것. 보드엔 아무것도 설치하지 않는다.
  python uart_relay_pc.py --ssh root@172.20.10.3 --to 172.20.10.3:9100 [--dump N]
"""
import argparse, json, socket, struct, subprocess, sys, time

SOF, VER = 0xA5, 0x01
HEADER = struct.Struct("<BBBHHI")   # SOF VER TYPE SEQ LEN TS_MS

def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc

def cobs_decode(data: bytes) -> bytes:
    out = bytearray(); i = 0; n = len(data)
    while i < n:
        code = data[i]
        if code == 0: raise ValueError("zero in block")
        i += 1
        block = data[i:i + code - 1]
        if len(block) != code - 1: raise ValueError("truncated")
        out += block; i += code - 1
        if code != 0xFF and i < n: out.append(0)
    return bytes(out)

def decode_frame(enc: bytes):
    raw = cobs_decode(enc)
    if len(raw) < HEADER.size + 2: raise ValueError("short")
    sof, ver, typ, seq, ln, ts = HEADER.unpack_from(raw, 0)
    if sof != SOF or ver != VER: raise ValueError("bad sof/ver")
    body = raw[:HEADER.size + ln]
    if len(raw) < HEADER.size + ln + 2: raise ValueError("len")
    crc = struct.unpack_from("<H", raw, HEADER.size + ln)[0]
    if crc != crc16(body): raise ValueError("crc")
    return typ, seq, ts, raw[HEADER.size:HEADER.size + ln]

class SeqTracker:
    """센서별 u16 frame_seq. 펌웨어 frame_id(u8) 의 증가분을 누적해 랩어라운드 없이 잇는다."""
    def __init__(self): self.last = {}; self.seq = {}
    def next(self, sensor: int, frame_id: int) -> int:
        if sensor not in self.last:
            self.last[sensor] = frame_id; self.seq[sensor] = 1; return 1
        d = (frame_id - self.last[sensor]) & 0xFF
        self.last[sensor] = frame_id
        self.seq[sensor] = (self.seq[sensor] + (d if d else 1)) & 0xFFFF
        return self.seq[sensor]

def to_service_payload(p: bytes, trk: SeqTracker):
    """펌웨어 grid 배치(grid_w,grid_h,sensor,sensors,frame_id,mask,flags,res + i16 dist + u8 st)
    → 서비스 v1 배치(version,sensor_id,flags,w,h,res,frame_seq u16 + u16 dist + u8 st). v1 이면 그대로."""
    if len(p) >= 8 and p[0] == 1:
        return p, p[1], "v1"
    gw, gh, sensor, sensors, fid, mask, flags, _res = struct.unpack_from("<BBBBBBBB", p, 0)
    n = gw * gh
    if n == 0 or n > 64 or len(p) < 8 + 3 * n: raise ValueError(f"grid size {gw}x{gh} len {len(p)}")
    dist = struct.unpack_from(f"<{n}h", p, 8)
    st = p[8 + 2 * n: 8 + 3 * n]
    sflags = 1 | 2 | (4 if flags & 0x01 else 0) | (8 if flags & 0x02 else 0)
    seq = trk.next(sensor, fid)
    out = struct.pack("<BBBBBBH", 1, sensor, sflags, gw, gh, 0, seq)
    out += struct.pack(f"<{n}H", *[(0 if d < 0 else min(d, 0xFFFE)) for d in dist]) + bytes(st)
    return out, sensor, f"grid fid={fid} mask={mask} flags=0x{flags:02x}"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ssh", default="root@172.20.10.3")
    ap.add_argument("--ssh-opt", default="-o HostKeyAlias=172.16.34.146 -o ConnectTimeout=10")
    ap.add_argument("--dev", default="/dev/ttyAMA0")
    ap.add_argument("--to", default="172.20.10.3:9100")
    ap.add_argument("--dump", type=int, default=0, help="N 프레임 풀어서 보여주고 끝 (전송 안 함)")
    a = ap.parse_args()
    cmd = ["ssh", *a.ssh_opt.split(), a.ssh, f"stty -F {a.dev} 115200 raw -echo; exec cat {a.dev}"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
    sock = None
    if not a.dump:
        host, port = a.to.rsplit(":", 1)
        sock = socket.create_connection((host, int(port)), timeout=10)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        print(f"connected to {a.to}", flush=True)
    trk = SeqTracker(); buf = bytearray(); stats = {"frames": 0, "t5": 0, "sent": 0, "bad": 0}
    t_report = time.time(); shown = 0
    while True:
        chunk = proc.stdout.read(512)
        if not chunk: print("ssh stream ended", file=sys.stderr); break
        buf += chunk
        while True:
            z = buf.find(b"\x00")
            if z < 0: break
            enc = bytes(buf[:z]); del buf[:z + 1]
            if not enc: continue
            try:
                typ, seq, ts, payload = decode_frame(enc)
            except ValueError as e:
                stats["bad"] += 1; continue
            stats["frames"] += 1
            if typ != 0x05: continue
            stats["t5"] += 1
            try:
                sp, sensor, note = to_service_payload(payload, trk)
            except ValueError as e:
                stats["bad"] += 1; print("payload?", e, payload[:12].hex(), file=sys.stderr); continue
            if a.dump:
                n = sp[3] * sp[4]
                d = struct.unpack_from(f"<{n}H", sp, 8)
                valid = sum(1 for x in d if 0 < x < 0xFFFF)
                print(f"type5 seq={seq} ts={ts} len={len(payload)} sensor={sensor} {note} -> v1 flags=0x{sp[2]:02x} "
                      f"frame_seq={struct.unpack_from('<H', sp, 6)[0]} valid={valid}/{n} "
                      f"dist(min/med/max)={min(x for x in d if x) if valid else 0}/{sorted(d)[n//2]}/{max(d)}")
                shown += 1
                if shown >= a.dump:
                    proc.kill(); print("stats", stats); return
                continue
            line = "UART\t" + json.dumps({"type": 5, "seq": seq, "ts_ms": ts, "payload_hex": sp.hex()}, separators=(",", ":")) + "\n"
            try:
                sock.sendall(line.encode())
            except OSError as e:
                print("send failed:", e, file=sys.stderr); proc.kill(); return
            stats["sent"] += 1
        if time.time() - t_report >= 5:
            t_report = time.time()
            print(time.strftime("%H:%M:%S"), stats, flush=True)

if __name__ == "__main__":
    main()
