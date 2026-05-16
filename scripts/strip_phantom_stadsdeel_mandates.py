"""Strip phantom Amsterdam-stadsdeel mandates.

ORI serves councillors of unrelated gemeenten inside generic-name indices
``ori_west`` / ``ori_noord`` / ``ori_oost``. The fetcher slugified the index
name into a fake gemeente (``org:gemeente-west``, role "Raadslid gemeente
West"), so e.g. a Weststellingwerf councillor got a second phantom mandate.
An earlier repair step rerouted those to ``org:gemeente-amsterdam``.

The source proves these are not real seats: the phantom mandate carries the
*same* ORI person-URL as the person's genuine gemeente mandate (one ORI
record, indexed twice). There is no independent evidence for a second seat.

This removes a mandate iff all of:
  - it is open (end_date is None) and ORI-sourced
  - it targets org:gemeente-amsterdam (rerouted) or the raw
    org:gemeente-west/noord/oost
  - every ORI source URL on it also appears on another mandate for the same
    person that points at a *different* gemeente

The last condition keeps any genuinely independent Amsterdam seat (e.g. an
ABD-sourced directeur) untouched. Person records are never deleted; only the
phantom mandate sub-entry is dropped. ``--apply`` writes; default is dry run.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

SOURCE_ID = "open_raadsinformatie"
PHANTOM_ORGS = {
    "org:gemeente-amsterdam",
    "org:gemeente-west",
    "org:gemeente-noord",
    "org:gemeente-oost",
}


def _ori_urls(mandaat: dict[str, Any]) -> set[str]:
    return {
        s.get("url", "")
        for s in (mandaat.get("sources") or [])
        if (s or {}).get("id") == SOURCE_ID and s.get("url")
    }


def _is_open_ori(mandaat: dict[str, Any]) -> bool:
    return mandaat.get("end_date") is None and bool(_ori_urls(mandaat))


def strip_person(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the list of phantom mandates removed (doc mutated in place)."""
    mandaten = doc.get("mandaten")
    if not isinstance(mandaten, list):
        return []

    # ORI source URLs that anchor a real seat at a non-phantom gemeente.
    real_urls: set[str] = set()
    for m in mandaten:
        if not isinstance(m, dict) or not _is_open_ori(m):
            continue
        org = m.get("organization_id", "")
        if org.startswith("org:gemeente-") and org not in PHANTOM_ORGS:
            real_urls |= _ori_urls(m)

    removed: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    for m in mandaten:
        if (
            isinstance(m, dict)
            and _is_open_ori(m)
            and m.get("organization_id") in PHANTOM_ORGS
            and _ori_urls(m) <= real_urls
            and _ori_urls(m)
        ):
            removed.append(m)
        else:
            kept.append(m)

    if removed:
        doc["mandaten"] = kept
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    person_dir = args.data_root / "personen"
    n_people = n_mandates = 0
    for path in sorted(person_dir.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        removed = strip_person(doc)
        if removed:
            n_people += 1
            n_mandates += len(removed)
            if args.apply:
                path.write_text(
                    yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
                    encoding="utf-8",
                )

    verb = "stripped" if args.apply else "would strip"
    print(f"{verb} {n_mandates} phantom mandates from {n_people} person records")
    if not args.apply:
        print("(dry run — pass --apply to write)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
