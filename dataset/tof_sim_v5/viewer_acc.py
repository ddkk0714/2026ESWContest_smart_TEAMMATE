"""Per-sensor prediction accuracy of a viewer scene file. usage: viewer_acc.py viewer/arc14.json"""
import json
import sys

d = json.load(open(sys.argv[1], encoding="utf-8"))
keep = [f for f in d["frames"] if not (f["flags"] & 1) and f["t"] >= 3000]
print("model:", d["model"])
if d["model"]:
    for i, s in enumerate(d["sensors"]):
        acc = sum(f["pred"][i] == f["label"] for f in keep) / len(keep)
        print(f"  device {s['az']:+3.0f} deg, sensor {s['si']}: {acc * 100:5.1f}%")
