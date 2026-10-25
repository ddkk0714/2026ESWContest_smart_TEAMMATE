#!/usr/bin/env bash
set -euo pipefail
# Existing container only. Always create a NEW marked directory; no mount changes.
docker exec deskmate-atlas-atlas-dev-1 python3 -c '
from pathlib import Path
import tempfile
p=Path(tempfile.mkdtemp(prefix="deskmate-thinq-pat-",dir="/tmp"))
p.chmod(0o700)
(p/".poc-only").write_text("com.deskmate.thinqpoc1\n")
print(p)
'
