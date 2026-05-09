#!/usr/bin/env python
"""Helper voor backfill_abd_nieuws.sh, fase 2.

Lijst alle artikel-HTML's in ``_cache/abd-nieuws/<slug>-<YYYY-MM-DD>.html``
binnen een ``[since, until]``-venster waarvoor nog geen proposal in de
maand-staging bestaat. Output: één pad per regel op stdout.

Gebruik:
    _backfill_abd_nieuws_list.py <cache-dir> <since> <until> <staging-dir> <max-cap>

- ``cache-dir``   : ``_cache/abd-nieuws``
- ``since/until`` : ISO-dates (inclusief)
- ``staging-dir`` : ``data/_staging``
- ``max-cap``     : harde bovengrens op aantal terug te geven paden

We slaan ``actueel-*.html`` over (homepages, geen artikel) en filteren artikelen
waarvoor het ``abd_nieuws_url`` al voorkomt in een ``abd-nieuws-*.json``-staging-file.
De artikel-datum komt uit de bestandsnaam-suffix ``-YYYY-MM-DD``.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

DATE_SUFFIX = re.compile(r"-(?P<date>\d{4}-\d{2}-\d{2})$")


def parse_iso(value: str) -> date:
    return date.fromisoformat(value)


def already_proposed(staging_dir: Path) -> set[str]:
    """Verzamel alle ``abd_nieuws_url`` waarden die al in een staging-JSON staan."""
    seen: set[str] = set()
    for p in staging_dir.glob("abd-nieuws-*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, list):
            continue
        for item in data:
            if not isinstance(item, dict):
                continue
            url = item.get("abd_nieuws_url")
            if isinstance(url, str) and url:
                seen.add(url.rstrip("/"))
            # Ook source_identifier (bestandsnaam zonder .html) tellen.
            sid = item.get("source_identifier")
            if isinstance(sid, str) and sid:
                seen.add(sid)
    return seen


def date_from_filename(stem: str) -> date | None:
    m = DATE_SUFFIX.search(stem)
    if not m:
        return None
    try:
        return date.fromisoformat(m.group("date"))
    except ValueError:
        return None


def url_from_stem(stem: str) -> str | None:
    """`<slug>-YYYY-MM-DD` -> canonical ABD-nieuws-URL."""
    m = DATE_SUFFIX.search(stem)
    if not m:
        return None
    iso = m.group("date")
    slug = stem[: m.start()]
    yyyy, mm, dd = iso.split("-")
    return (
        f"https://www.algemenebestuursdienst.nl/actueel/nieuws/"
        f"{yyyy}/{mm}/{dd}/{slug}"
    )


def main(argv: list[str]) -> int:
    if len(argv) != 6:
        print(
            "usage: _backfill_abd_nieuws_list.py <cache-dir> <since> <until> <staging-dir> <max-cap>",
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

    for f in sorted(cache_dir.glob("*.html")):
        if f.name.startswith("actueel-"):
            continue
        stem = f.stem
        article_date = date_from_filename(stem)
        if article_date is None:
            continue
        if article_date < since or article_date > until:
            continue
        url = url_from_stem(stem)
        if url and url.rstrip("/") in seen:
            continue
        if stem in seen:
            continue
        out.append(f)
        if len(out) >= max_cap:
            break

    for p in out:
        print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
