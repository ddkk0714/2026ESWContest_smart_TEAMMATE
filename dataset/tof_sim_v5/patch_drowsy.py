"""Drowsy segments easier to tell from upright on an 8x8 sensor: deeper nods with a trunk slump,
more and longer micro-sleeps, a drooping baseline. usage: patch_drowsy.py <tof-sim dir>"""
import os
import sys

p = os.path.join(sys.argv[1], "tofsim/session.py")
s = open(p, encoding="utf-8").read()
pairs = [
    ('''        amp = np.radians(_u(rng, 15, 42))
        hold = _u(rng, 5, 15) if rng.random() < 0.25 else 0.0     # micro-sleep with the head down
        nods.append((t, fall, pause, recover, amp, hold))
        t += fall + pause + hold + recover + _u(rng, 4.0, 25.0)''',
     '''        amp = np.radians(_u(rng, 25, 55))
        hold = _u(rng, 4, 20) if rng.random() < 0.45 else 0.0     # micro-sleep with the head down
        nods.append((t, fall, pause, recover, amp, hold))
        t += fall + pause + hold + recover + _u(rng, 3.0, 15.0)'''),
    ('''        return Pose(trunk_pitch=deg(_u(rng, -6, 10)), neck_pitch=deg(_u(rng, 4, 15)), arms=_arms("drowsy", rng))''',
     '''        # drowsy baseline: a drooping head and a slightly slumped trunk, even between nods
        return Pose(trunk_pitch=deg(_u(rng, 0, 14)), neck_pitch=deg(_u(rng, 8, 22)), arms=_arms("drowsy", rng))'''),
    ('''                        else:
                            pose.neck_pitch += off
                        flags |= FLAG_NOD''',
     '''                        else:
                            pose.neck_pitch += off
                            pose.trunk_pitch += off * 0.25          # the whole upper body sags with the nod
                        flags |= FLAG_NOD'''),
]
for a, b in pairs:
    if s.count(a) != 1:
        sys.exit(f"expected 1 match, found {s.count(a)}: {a[:80]}")
    s = s.replace(a, b)
open(p, "w", encoding="utf-8", newline="").write(s)
print("patched", p)
