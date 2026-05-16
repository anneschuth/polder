"""Consolideer near-duplicate mandaten die de echte dedup-tool mist.

``polder dedup`` matcht alleen exacte ``(post, org, start, end)`` tuples.
Twee bug-patronen produceren near-duplicates die daar doorheen glippen,
allebei een open mandaat met een afgesloten tegenhanger op dezelfde
post+org en een startdatum binnen ~31 dagen:

  Subgroep 1 — de afgesloten tegenhanger is nul-duur (start == end).
    Dat is een ORI-artefact (een membership zonder echte looptijd dat
    als 1-dagsmandaat landde). Het open mandaat is de echte zetel.
    Fix: verwijder het nul-duur-mandaat, behoud het open.

  Subgroep 2 — de afgesloten tegenhanger is een echte periode.
    Het open mandaat is een import-duplicaat van dezelfde benoeming
    (besluitdatum vs ambtsaanvaarding, een paar dagen ertussen) met een
    vergeten einddatum. Fix: laat het open mandaat vallen en merge zijn
    bronnen in de afgesloten tegenhanger, die de juiste periode heeft.

Records worden nooit verwijderd; alleen duplicaat-mandaat-subentries
geconsolideerd, conform de dedup-regel. ``--apply`` schrijft.
"""

from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path
from typing import Any

import yaml


def _d(value: str | None) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(value or "")
    except (ValueError, TypeError):
        return None


def _merge_sources(
    a: list[dict[str, Any]] | None, b: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for src in (a or []) + (b or []):
        key = (src.get("id", ""), src.get("url", ""))
        prev = by_key.get(key)
        if prev is None or src.get("retrieved", "") > prev.get("retrieved", ""):
            by_key[key] = src
    return list(by_key.values())


def consolidate(doc: dict[str, Any]) -> tuple[int, int]:
    """Return (subgroep1_removed, subgroep2_consolidated)."""
    mandaten = doc.get("mandaten")
    if not isinstance(mandaten, list):
        return (0, 0)

    drop_ids: set[int] = set()  # id() van te verwijderen mandaat-dicts
    g1 = g2 = 0

    for o in mandaten:
        if not isinstance(o, dict) or o.get("end_date") is not None:
            continue
        if id(o) in drop_ids:
            continue
        o_start = _d(o.get("start_date"))
        if o_start is None:
            continue
        for c in mandaten:
            if c is o or not isinstance(c, dict) or c.get("end_date") is None:
                continue
            if id(c) in drop_ids:
                continue
            if c.get("post_id") != o.get("post_id"):
                continue
            if c.get("organization_id") != o.get("organization_id"):
                continue
            c_start, c_end = _d(c.get("start_date")), _d(c.get("end_date"))
            if c_start is None or c_end is None:
                continue
            if abs((o_start - c_start).days) > 31:
                continue
            if c_start == c_end:
                # Subgroep 1: afgesloten tegenhanger is nul-duur junk.
                drop_ids.add(id(c))
                g1 += 1
            else:
                # Subgroep 2: open mandaat is de import-duplicaat.
                c["sources"] = _merge_sources(c.get("sources"), o.get("sources"))
                drop_ids.add(id(o))
                g2 += 1
            break

    if drop_ids:
        doc["mandaten"] = [m for m in mandaten if id(m) not in drop_ids]
    return (g1, g2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    tot1 = tot2 = files = 0
    for path in sorted((args.data_root / "personen").glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        g1, g2 = consolidate(doc)
        if g1 or g2:
            files += 1
            tot1 += g1
            tot2 += g2
            if args.apply:
                path.write_text(
                    yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
                    encoding="utf-8",
                )

    verb = "consolideerde" if args.apply else "zou consolideren"
    print(
        f"{verb}: {tot1} nul-duur-junk verwijderd, "
        f"{tot2} open import-duplicaten samengevoegd, over {files} records"
    )
    if not args.apply:
        print("(dry run — gebruik --apply om te schrijven)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
