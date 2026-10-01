"""저장된 ESM JSONL의 응답 지표를 파일별·전체로 집계한다."""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hub"))
from deskmate_hub.esm import calculate_metrics  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DESKMATE ESM 응답 지표 집계")
    parser.add_argument("paths", nargs="+", help="ESM JSONL 파일 또는 glob")
    parser.add_argument("--json", action="store_true", help="JSON 출력")
    args = parser.parse_args(argv)
    paths = sorted({path for pattern in args.paths for path in glob.glob(pattern)})
    if not paths:
        parser.error("일치하는 ESM 파일이 없습니다")

    files = {}
    all_records = []
    for path in paths:
        with open(path, encoding="utf-8") as source:
            records = [json.loads(line) for line in source if line.strip()]
        files[path] = calculate_metrics(records)
        all_records.extend(records)
    output = {"files": files, "overall": calculate_metrics(all_records)}
    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        for name, metrics in [*files.items(), ("전체", output["overall"])]:
            print(name)
            for key, value in metrics.items():
                print(f"  {key}: {value if value is not None else '—'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
