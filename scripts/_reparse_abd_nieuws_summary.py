"""Vat de reparse-log samen op stderr."""
from __future__ import annotations
import json, sys
from collections import Counter
from pathlib import Path

log = Path(sys.argv[1])
if not log.exists():
    print("(geen log)", file=sys.stderr)
    raise SystemExit(0)

stats: Counter[str] = Counter()
deltas: list[dict] = []
for line in log.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or not line.startswith("{"):
        continue
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        continue
    stats[rec.get("status", "?")] += 1
    if rec.get("status") == "changed":
        deltas.extend(rec.get("delta", []))

print(f"    statussen: {dict(stats)}", file=sys.stderr)
if deltas:
    moved_up = sum(1 for d in deltas if (d.get("new_confidence") or 0) > (d.get("old_confidence") or 0))
    moved_dn = sum(1 for d in deltas if (d.get("new_confidence") or 0) < (d.get("old_confidence") or 0))
    print(f"    delta confidences: {len(deltas)} (omhoog {moved_up}, omlaag {moved_dn})", file=sys.stderr)
