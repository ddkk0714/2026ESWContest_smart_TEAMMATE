#!/usr/bin/env bash
set -euo pipefail
# Creates only a new marked /tmp directory, never a Docker container or mount.
docker exec deskmate-atlas-atlas-dev-1 python3 -c '
from pathlib import Path
p=Path("/tmp/deskmate-team-thinq-poc-initial-20261025")
if p.exists():
    assert (p/".poc-only").is_file(), "unmarked existing path; refusing to overwrite"
else:
    p.mkdir(mode=0o700)
    (p/".poc-only").write_text("com.deskmate.thinqpoc1\n")
'
