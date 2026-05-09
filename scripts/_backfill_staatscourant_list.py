#!/usr/bin/env python
"""Helper voor backfill_staatscourant.sh, fase 2.

Lijst alle XML-bestanden in ``_cache/staatscourant/<jaar>/<maand>/*.xml``
binnen een [since, until]-venster waarvoor nog geen proposal in de
staging-output zit. Output: één pad per regel op stdout.

Gebruik:
    _backfill_staatscourant_list.py <cache-dir> <since> <until> <staging-dir> <max-cap>

- ``cache-dir``   : `_cache/staatscourant`
- ``since/until`` : ISO-dates (inclusief)
- ``staging-dir`` : `data/_staging`
- ``max-cap``     : harde bovengrens op aantal terug te geven paden

We slaan ``*.sru.xml`` over (alleen SRU-fragmenten) en filteren KBs waarvoor
het identifier al voorkomt in een staatscourant-*.json proposal. De
identifier wordt afgeleid uit de bestandsnaam (zonder extensie).
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path


def parse_iso(value: str) -> date:
    return date.fromisoformat(value)


def already_proposed(staging_dir: Path) -> set[str]:
    """Verzamel alle identifiers die al in een staging-JSON staan.

    We matchen op ``decision_reference`` of ``staatscourant_url`` substring
    voor de identifier (bv. ``stcrt-2025-12345``).
    """
    seen: set[str] = set()
    for p in staging_dir.glob("staatscourant-*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, list):
            continue
        for item in data:
            if not isinstance(item, dict):
                continue
            for key in ("staatscourant_url", "decision_reference", "source_identifier"):
                val = item.get(key)
                if isinstance(val, str):
                    # Pak de identifier-substring stcrt-YYYY-...
                    for chunk in val.replace("/", " ").split():
                        if chunk.startswith("stcrt-"):
                            seen.add(chunk.split(".")[0])
    return seen


def main(argv: list[str]) -> int:
    if len(argv) != 6:
        print(
            "usage: _backfill_staatscourant_list.py <cache-dir> <since> <until> <staging-dir> <max-cap>",
            file=sys.stderr,
        )
        return 2
    cache_dir = Path(argv[1])
    since = parse_iso(argv[2])
    until = parse_iso(argv[3])
    staging_dir = Path(argv[4])
    max_cap = int(argv[5])

    if not cache_dir.exists():
        return 0

    seen = already_proposed(staging_dir)
    out: list[Path] = []

    # _cache/staatscourant/<year>/<month>/<id>.xml
    for year_dir in sorted(cache_dir.iterdir()):
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        year = int(year_dir.name)
        for month_dir in sorted(year_dir.iterdir()):
            if not month_dir.is_dir() or not month_dir.name.isdigit():
                continue
            month = int(month_dir.name)
            # Approximate dt.modified-window check op map-niveau.
            month_start = date(year, month, 1)
            if month < 12:
                month_end = date(year, month + 1, 1)
            else:
                month_end = date(year + 1, 1, 1)
            if month_end <= since or month_start > until:
                continue
            for f in sorted(month_dir.glob("*.xml")):
                if f.name.endswith(".sru.xml"):
                    continue
                identifier = f.stem
                if identifier in seen:
                    continue
                out.append(f)
                if len(out) >= max_cap:
                    break
            if len(out) >= max_cap:
                break
        if len(out) >= max_cap:
            break

    for p in out:
        print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
