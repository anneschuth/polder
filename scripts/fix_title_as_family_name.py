"""Herstel personen waar een titel (MSc/RA/MBA/...) als familienaam landde.

De ORI/ABD-naamparser zette bij sommige records de post-honorific
(`MSc`, `RA`, `RE`, `MBA`, `MA`) als `name.family`, met de echte naam in
`name.given` / `name.full`. Slug en id zijn daardoor ook fout
(`person:msc-p-...`).

Aanpak per record, met de canonieke parser (`parse_person_name`) en
slug-functie (`_person_slug`) — geen tweede parser:

1. Bereken het correcte name-blok en de correcte slug uit `name.full`.
2. Schrijf een gecorrigeerd record onder de juiste slug/id (of voeg samen
   met een al bestaand correct record als dat er is).
3. Verwijs het foute record via ``polder merge person`` naar het
   correcte, zodat mandaten + sources behouden blijven en alle
   referenties meeverhuizen.

``--apply`` voert de merges uit.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from polder.apply import _person_slug
from polder.resolve.names import parse_person_name

TITLES = {
    "msc",
    "ma",
    "ra",
    "re",
    "llm",
    "bsc",
    "drs",
    "mr",
    "ir",
    "phd",
    "bba",
    "mba",
    "ms",
}


_TUSSENVOEGSELS = {
    "van",
    "der",
    "den",
    "de",
    "het",
    "ten",
    "ter",
    "te",
    "op",
    "aan",
    "in",
    "tot",
    "'t",
    "'s",
    "vande",
    "vander",
}


def _readable_family(family_full: str) -> str:
    """`van-der-winden` -> `van der Winden`: tussenvoegsels lowercase,
    hoofdnaam-delen Title-case."""
    parts = family_full.split("-")
    out: list[str] = []
    for tok in parts:
        if tok in _TUSSENVOEGSELS:
            out.append(tok)
        else:
            out.append(tok.capitalize())
    return " ".join(out)


def _corrected_name(full: str) -> dict[str, Any]:
    parsed = parse_person_name(full)
    family = _readable_family(parsed.family_full or parsed.family or "")
    name: dict[str, Any] = {"full": full, "family": family}
    if parsed.given:
        name["given"] = parsed.given.title()
    if parsed.initials:
        name["initials"] = ".".join(c.upper() for c in parsed.initials) + "."
    return name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    person_dir = args.data_root / "personen"
    existing_ids = {
        (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("id"): p
        for p in person_dir.glob("*.yaml")
    }

    plan: list[tuple[Path, str, str, dict[str, Any]]] = []
    for path in sorted(person_dir.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        fam = (doc.get("name", {}).get("family") or "").strip().lower()
        if fam not in TITLES:
            continue
        full = doc.get("name", {}).get("full") or ""
        birth = (doc.get("birth") or {}).get("year")
        new_slug = _person_slug(full, birth)
        if not new_slug:
            print(f"SKIP {path.name}: geen slug uit {full!r}")
            continue
        new_id = f"person:{new_slug}"
        if new_id == doc.get("id"):
            continue
        plan.append((path, doc.get("id", ""), new_id, _corrected_name(full)))

    print(f"titel-records te herstellen: {len(plan)}")
    for _path, old_id, new_id, name in plan:
        exists = "MERGE-INTO-EXISTING" if new_id in existing_ids else "RENAME"
        print(f"  {old_id} -> {new_id}  [{exists}]  family={name['family']!r}")

    if not args.apply:
        print("(dry run — gebruik --apply)")
        return 0

    for path, old_id, new_id, name in plan:
        if new_id not in existing_ids:
            # Geen correct record: maak een stub onder de juiste id/slug
            # met het gecorrigeerde name-blok en een placeholder-source.
            # `polder merge person` verhuist daarna mandaten/sources en
            # herschrijft alle referenties (en verwijdert het foute file).
            seed = {
                "id": new_id,
                "name": name,
                "sources": [
                    {
                        "id": "polder",
                        "url": "https://github.com/anneschuth/polder",
                        "retrieved": "2026-05-16",
                    }
                ],
            }
            new_path = path.with_name(new_id.split(":", 1)[1] + ".yaml")
            new_path.write_text(
                yaml.safe_dump(seed, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            existing_ids[new_id] = new_path

        subprocess.run(
            [
                "uv",
                "run",
                "polder",
                "merge",
                "person",
                old_id,
                new_id,
                "--data",
                str(args.data_root),
                "--apply",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    print(f"hersteld: {len(plan)} records")
    return 0


if __name__ == "__main__":
    sys.exit(main())
