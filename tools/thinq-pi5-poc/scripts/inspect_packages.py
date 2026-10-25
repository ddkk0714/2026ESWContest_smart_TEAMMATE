"""Inspect only locally built IPKs. Never installs or activates anything."""

import hashlib
import io
import json
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
report = []
for name, kind in [
    ("com.deskmate.thinqpoc1", "service"),
    ("com.atlas.app.deskmate_thinq_poc", "app"),
]:
    path = ROOT / "artifacts" / (name + ".ipk")
    members = subprocess.check_output(["ar", "t", str(path)]).decode().splitlines()
    data_name = next(x for x in members if x.startswith("data.tar"))
    control_name = next(x for x in members if x.startswith("control.tar"))
    raw = subprocess.check_output(["ar", "p", str(path), data_name])
    assert b"com.atlas.app.deskmate_display" not in raw
    files = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as archive:
        for member in archive:
            assert (
                not member.name.startswith("/") and ".." not in Path(member.name).parts
            )
            if member.isfile():
                files[member.name] = archive.extractfile(member).read()
    assert not any(x.endswith(".conf") or "apparmor" in x.lower() for x in files)
    assert not any(x.endswith((".log", ".env", ".pem", ".key")) for x in files)
    suspicious = []
    for file, body in files.items():
        assert b"com.atlas.app.deskmate_display" not in body, (
            "original app ID leaked into payload"
        )
        if re.search(
            rb"(?:github_pat_[A-Za-z0-9_]{30,}|ghp_[A-Za-z0-9]{25,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY)",
            body,
        ):
            suspicious.append(file)
    assert not suspicious, "secret pattern found (values withheld)"
    meta_name = next(
        x
        for x in files
        if x.endswith("/serviceinfo.json" if kind == "service" else "/appinfo.json")
    )
    meta = json.loads(files[meta_name])
    assert meta["id"] == name
    if kind == "service":
        assert meta["run_after_installation"] is False
        activation = next(x for x in files if x.endswith(name + ".service"))
        assert b"User=root" not in files[activation]
    elfs = []
    with tempfile.TemporaryDirectory(prefix="deskmate-poc-elf-") as directory:
        for file, body in files.items():
            if not body.startswith(b"\x7fELF"):
                continue
            f = Path(directory) / str(len(elfs))
            f.write_bytes(body)
            h = subprocess.check_output(
                ["readelf", "-h", str(f)], stderr=subprocess.PIPE
            ).decode()
            assert "AArch64" in h
            d = subprocess.check_output(
                ["readelf", "-d", str(f)], stderr=subprocess.PIPE
            ).decode()
            elfs.append(
                {
                    "file": file,
                    "needed": re.findall(r"Shared library: \[(.*?)\]", d),
                    "rpath_lines": [
                        x.strip()
                        for x in d.splitlines()
                        if "RPATH" in x or "RUNPATH" in x
                    ],
                }
            )
    assert elfs
    with tarfile.open(
        fileobj=io.BytesIO(
            subprocess.check_output(["ar", "p", str(path), control_name])
        ),
        mode="r:*",
    ) as archive:
        control = next(m for m in archive if Path(m.name).name == "control")
        text = archive.extractfile(control).read().decode()
        arch = next(
            x.split(":", 1)[1].strip()
            for x in text.splitlines()
            if x.startswith("Architecture:")
        )
        # ARC 0.5 writes Architecture: all for native and Flutter packages.
        # This metadata is not CPU proof; mandatory AArch64 checks above cover EVERY ELF.
        assert arch in ("all", "arm64", "aarch64")
    report.append(
        {
            "package": name,
            "size": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "architecture": arch,
            "files": sorted(files),
            "elfs": elfs,
            "board_installed": False,
            "live_ready": False,
            "dbus_policy_included": False,
        }
    )
(ROOT / "artifacts/package-inspection.json").write_text(json.dumps(report, indent=2))
for r in report:
    print(r["package"], r["size"], r["sha256"], "ARM64; Live BLOCKED; board NOT tested")
