"""서버에서 조립한 묶음(assemble_bundle.sh 출력: pi4/ + pc/)에 PC 쪽 파일을 더해 배포 폴더와 zip 을 만든다.

    python package_bundle.py --assembled <pi4·pc 가 있는 폴더> --out <배포 폴더> --zip <zip 경로> --readme <README.md>

더하는 것: deploy-pi4.{bat,ps1,sh}(이 폴더), pc/tof_live.py·requirements.txt(PC 의 sw경진대회/tof-sim-v5 = 레포 dataset/tof_sim_v5), esp32/(펌웨어 이미지·메모),
source/hub-atlas-tof/(소스·스크립트), README.md, bench 점수 json(있으면). 마지막에 SHA256SUMS 를 쓰고 zip 으로 묶는다
(실행 파일·.sh 는 unix 0755, 파일 이름 UTF-8). 기존 배포 폴더가 있으면 지우지 않고 멈춘다.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import stat
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SVC = os.path.dirname(HERE)                                   # hub/atlas-tof
WORK = os.path.abspath(os.path.join(SVC, "..", "..", ".."))   # sw경진대회
EXEC_NAMES = {"deskmate_tofposture", "tofposture_selftest"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--assembled", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--zip", required=True)
    ap.add_argument("--readme", required=True)
    ap.add_argument("--scores", nargs="*", default=[], help="같이 넣을 bench 점수 json")
    ap.add_argument("--tof-live", default=os.path.join(WORK, "tof-sim-v5", "tof_live.py"))
    ap.add_argument("--esp32", default=os.path.join(WORK, "esp32_tof_bringup"))
    a = ap.parse_args()

    if os.path.exists(a.out):
        sys.exit(f"이미 있다(지우지 않음): {a.out}")
    for sub in ("pi4/deskmate_tofposture", "pi4/models/posture_net.bin", "pc/posture_net.onnx"):
        if not os.path.isfile(os.path.join(a.assembled, sub)):
            sys.exit(f"조립 결과가 아니다: {sub} 없음")

    shutil.copytree(os.path.join(a.assembled, "pi4"), os.path.join(a.out, "pi4"))
    shutil.copytree(os.path.join(a.assembled, "pc"), os.path.join(a.out, "pc"))
    for f in ("deploy-pi4.ps1", "deploy-pi4.sh"):
        shutil.copy2(os.path.join(HERE, f), os.path.join(a.out, f))
    with open(os.path.join(a.out, "deploy-pi4.bat"), "w", encoding="ascii", newline="\r\n") as fp:
        fp.write('@echo off\nrem Usage (from cmd or PowerShell, in this folder):  deploy-pi4.bat 172.16.34.146\n'
                 'rem Extra options are passed through, e.g.:          deploy-pi4.bat 172.16.34.146 -NoStart\n'
                 'if "%~1"=="" (\n  echo usage: deploy-pi4.bat BOARD_IP [-NoStart]\n  exit /b 1\n)\n'
                 'powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy-pi4.ps1" -Ip %*\n')
    shutil.copy2(a.tof_live, os.path.join(a.out, "pc", "tof_live.py"))
    with open(os.path.join(a.out, "pc", "requirements.txt"), "w", encoding="ascii", newline="\n") as fp:
        fp.write("numpy>=1.26\nonnxruntime>=1.17\npyserial>=3.5\n")
    os.makedirs(os.path.join(a.out, "esp32"))
    for f in ("tof-bringup-full-4MB.bin", "README.md"):
        shutil.copy2(os.path.join(a.esp32, f), os.path.join(a.out, "esp32", f))
    src_out = os.path.join(a.out, "source", "hub-atlas-tof")
    for d in ("src", "board", "tools", "files"):
        shutil.copytree(os.path.join(SVC, d), os.path.join(src_out, d), ignore=shutil.ignore_patterns("__pycache__"))
    for f in ("CMakeLists.txt", "README.md"):
        shutil.copy2(os.path.join(SVC, f), os.path.join(src_out, f))
    shutil.copy2(a.readme, os.path.join(a.out, "README.md"))
    for s in a.scores:
        shutil.copy2(s, os.path.join(a.out, os.path.basename(s)))

    files = []
    for d, dirs, fs in os.walk(a.out):
        dirs.sort()
        files += [os.path.join(d, f) for f in sorted(fs)]
    lines = []
    for p in files:
        rel = os.path.relpath(p, a.out).replace(os.sep, "/")
        lines.append(f"{hashlib.sha256(open(p, 'rb').read()).hexdigest()}  ./{rel}")
    with open(os.path.join(a.out, "SHA256SUMS"), "w", encoding="ascii", newline="\n") as fp:
        fp.write("\n".join(lines) + "\n")
    files.append(os.path.join(a.out, "SHA256SUMS"))

    top = os.path.basename(os.path.normpath(a.out))
    if os.path.exists(a.zip):
        sys.exit(f"zip 이 이미 있다(지우지 않음): {a.zip}")
    with zipfile.ZipFile(a.zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in files:
            rel = os.path.relpath(p, a.out).replace(os.sep, "/")
            zi = zipfile.ZipInfo(f"{top}/{rel}", date_time=time.localtime(os.path.getmtime(p))[:6])
            zi.compress_type = zipfile.ZIP_DEFLATED
            mode = 0o755 if (os.path.basename(p) in EXEC_NAMES or p.endswith(".sh")) else 0o644
            zi.external_attr = (stat.S_IFREG | mode) << 16
            zi.flag_bits |= 0x800
            with open(p, "rb") as fp:
                z.writestr(zi, fp.read())
    with zipfile.ZipFile(a.zip) as z:
        bad = z.testzip()
    print(f"{a.zip}: {len(files)} files, {os.path.getsize(a.zip) / 1e6:.2f} MB, testzip={bad}")


if __name__ == "__main__":
    main()
