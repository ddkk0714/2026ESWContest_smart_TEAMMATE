"""펌웨어 규칙 추정기(TofPosture.cpp)를 PC 에서 돌린다 — replay/tof_replay.cpp 를 빌드해서 쓴다.

컴파일러: PATH 의 g++ 가 있으면 그걸, 없으면 WSL 의 g++ 를 쓴다(이 PC 처럼 Windows 에 gcc 가 없을 때).
판정 코드는 복사하지 않고 펌웨어 소스를 그대로 컴파일하므로 보드와 같은 판정이 나온다.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parents[1]
REPO = HERE.parents[1]
FW_SRC = REPO / "firmware" / "esp32_sensor_node" / "src"
SOURCES = [HERE / "replay" / "tof_replay.cpp", FW_SRC / "preprocess" / "tof" / "TofPosture.cpp",
           FW_SRC / "preprocess" / "tof" / "TofPosture.h"]
OUT_FIELDS = ("posture", "conf", "baseline", "present", "head_mm", "torso_mm", "drop_mm", "chin",
              "nods", "motion")


def _wsl_path(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    return f"/mnt/{s[0].lower()}{s[2:]}" if len(s) > 1 and s[1] == ":" else s


class RuleReplay:
    def __init__(self, build_dir: Path | None = None):
        self.build_dir = build_dir or HERE / "replay" / "build"
        local = shutil.which("g++")
        if local:
            self.mode, self.exe = "local", self.build_dir / "tof_replay.exe"
        elif shutil.which("wsl.exe") or shutil.which("wsl"):
            self.mode, self.exe = "wsl", self.build_dir / "tof_replay"
        else:
            raise RuntimeError("g++ 도 WSL 도 없어 규칙 추정기를 빌드할 수 없다")
        self._build_if_needed()

    def _cmd(self, args: list[str]) -> list[str]:
        return (["wsl.exe", "-e"] + args) if self.mode == "wsl" else args

    def _p(self, p: Path) -> str:
        return _wsl_path(p) if self.mode == "wsl" else str(p)

    def _build_if_needed(self) -> None:
        newest = max(s.stat().st_mtime for s in SOURCES)
        if self.exe.exists() and self.exe.stat().st_mtime >= newest:
            return
        self.build_dir.mkdir(parents=True, exist_ok=True)
        cmd = self._cmd(["g++", "-O2", "-std=gnu++17", "-I", self._p(FW_SRC), self._p(SOURCES[0]),
                         self._p(SOURCES[1]), "-o", self._p(self.exe)])
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"규칙 추정기 빌드 실패:\n{r.stderr}")

    def run(self, ms: np.ndarray, d: np.ndarray, st: np.ndarray,
            config: dict[str, float] | None = None,
            cmds: dict[int, str] | None = None) -> dict[str, np.ndarray]:
        """d·st: (N, h, w) 장면 방향. 세션 하나를 처음부터 끝까지 넣는다(추정기 상태는 매번 새로).

        cmds: 프레임 번호 → 명령('b'·'g'·'r'). 그 프레임 직전에 넣는다(수집기가 보내는 시점과 같게).
        """
        n, h, w = d.shape
        cmds = cmds or {}
        lines = []
        for i, (t, di, si) in enumerate(zip(ms, d, st)):
            if i in cmds:
                lines.append(f"cmd {cmds[i]}")
            lines.append(f"{int(t)} {w} {h} " + " ".join(map(str, di.ravel().tolist())) + " "
                         + " ".join(map(str, si.ravel().tolist())))
        args = [self._p(self.exe)] + [f"{k}={v}" for k, v in (config or {}).items()]
        r = subprocess.run(self._cmd(args), input="\n".join(lines) + "\n", capture_output=True,
                           text=True)
        if r.returncode != 0:
            raise RuntimeError(f"규칙 추정기 실행 실패: {r.stderr}")
        rows = [x.split() for x in r.stdout.splitlines() if x.strip()]
        if len(rows) != n:
            raise RuntimeError(f"출력 {len(rows)}줄 ≠ 입력 {n}프레임")
        out: dict[str, np.ndarray] = {"posture": np.array([x[0] for x in rows], dtype=object)}
        for j, k in enumerate(OUT_FIELDS[1:], start=1):
            out[k] = np.array([float(x[j]) for x in rows])
        return out
