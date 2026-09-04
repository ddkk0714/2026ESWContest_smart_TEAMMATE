"""Append the Python Hub and its derived FSM config to an Atlas ELF."""

from __future__ import annotations

import argparse
import json
import tempfile
import zipfile
from pathlib import Path

import yaml


EXCLUDED_STDLIB_PARTS = {
    "dist-packages",
    "ensurepip",
    "idlelib",
    "lib2to3",
    "site-packages",
    "test",
    "tests",
    "tkinter",
    "turtledemo",
    "unittest",
    "venv",
}


def add_python_tree(archive: zipfile.ZipFile, source: Path, destination: str) -> None:
    for item in sorted(source.rglob("*")):
        if not item.is_file() or item.suffix != ".py":
            continue
        archive.write(item, Path(destination) / item.relative_to(source))


def add_pure_stdlib(archive: zipfile.ZipFile, source: Path) -> None:
    """Bundle pure Python stdlib omitted from Atlas's restricted runtime."""
    for item in sorted(source.rglob("*.py")):
        relative = item.relative_to(source)
        if any(part in EXCLUDED_STDLIB_PARTS for part in relative.parts):
            continue
        archive.write(item, relative)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--hub", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--stdlib", type=Path, required=True)
    args = parser.parse_args()

    with args.config.open(encoding="utf-8") as source:
        config = yaml.safe_load(source)

    with tempfile.NamedTemporaryFile(suffix=".zip") as payload:
        with zipfile.ZipFile(payload.name, "w", zipfile.ZIP_DEFLATED) as archive:
            add_pure_stdlib(archive, args.stdlib)
            add_python_tree(archive, args.hub, "deskmate_hub")
            archive.writestr(
                "deskmate_hub/config/fsm.json",
                json.dumps(config, ensure_ascii=False, separators=(",", ":")),
            )
        with args.executable.open("ab") as executable, open(payload.name, "rb") as built:
            executable.write(built.read())


if __name__ == "__main__":
    main()
