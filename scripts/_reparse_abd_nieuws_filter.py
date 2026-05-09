"""Filter input-paden op since/until/limit en sorteer."""
from __future__ import annotations
import sys, re
from pathlib import Path

since = (sys.argv[1] if len(sys.argv) > 1 else "") or None
until = (sys.argv[2] if len(sys.argv) > 2 else "") or None
limit = int((sys.argv[3] if len(sys.argv) > 3 else "0") or 0)

date_re = re.compile(r"-(\d{4}-\d{2}-\d{2})$")
out: list[str] = []
for line in sys.stdin:
    p = line.strip()
    if not p:
        continue
    stem = Path(p).stem
    m = date_re.search(stem)
    if not m:
        continue
    d = m.group(1)
    if since and d < since:
        continue
    if until and d > until:
        continue
    out.append(p)

out.sort()
if limit > 0:
    out = out[:limit]
for p in out:
    print(p)
