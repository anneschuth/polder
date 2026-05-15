"""Migratie: persoon.name.initials opwaarderen vanuit name.given.

Sommige fetchers (oude TK-records, ABD, Open Raadsinformatie) schreven alleen
de eerste initiaal in `name.initials` terwijl `name.given` (of een initial
sequence er in) de volle reeks bevat. Bijvoorbeeld:

    name.initials = "M."
    name.given    = "M.F.W."
    → upgrade naar name.initials = "M.F.W."

Voor matching tegen ROO (`H.J. Derks`) en andere bronnen is de volle reeks
beslissend. Een record opwaarderen kost niets aan correctheid (we gooien
geen info weg) en verhoogt de identificeerbaarheid.

Skip-criteria:
- `name.given` is geen initial-sequence (`H.J.`-vorm) maar een roepnaam
  (`Henkjan`). Dan kunnen we geen full-initials reconstrueren.
- `name.initials` is al ≥ 2 letters.
- `name.full` levert betere initials dan `name.given` — die nemen we dan.

Run: `uv run python scripts/upgrade_initials_from_given.py [--dry-run]`.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

INIT_SEQ_RX = re.compile(r"^((?:[A-Z]\.){2,})")
SINGLE_INIT_RX = re.compile(r"^[A-Z]\.\s*$")


def _full_init_from_text(text: str) -> str | None:
    """Geef `H.J.` als de tekst begint met een initial-sequence van ≥ 2 letters."""
    if not text:
        return None
    m = INIT_SEQ_RX.match(text.strip())
    if m:
        return m.group(1)
    return None


def upgrade_record(data: dict) -> tuple[bool, str | None]:
    """Geef (changed, new_initials) terug. Wijzigt `data` in-place.

    Werkt zowel als initials helemaal ontbreken als wanneer ze ingekort
    zijn (bv. `P.A.` terwijl `given='P.A.L.'` verraadt dat de volle
    reeks 3 letters is). De candidate uit `given` of `full` wint alleen
    als die strict langer is dan de bestaande.
    """
    name = data.get("name")
    if not isinstance(name, dict):
        return False, None
    current = (name.get("initials") or "").strip()
    current_letters = re.sub(r"[^A-Za-z]", "", current)

    given = (name.get("given") or "").strip()
    full = (name.get("full") or "").strip()

    candidate = _full_init_from_text(given)
    if not candidate:
        first_token = full.split()[0] if full else ""
        candidate = _full_init_from_text(first_token)

    if not candidate:
        return False, None

    new_letters = re.sub(r"[^A-Za-z]", "", candidate)
    if len(new_letters) <= len(current_letters):
        return False, None

    name["initials"] = candidate
    return True, candidate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data",
        type=Path,
        default=Path("data/personen"),
        help="Persons-directory (default: data/personen).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Rapporteer alleen, schrijf niet.",
    )
    args = parser.parse_args(argv)

    data_dir = args.data
    if not data_dir.exists():
        print(f"Personen-directory bestaat niet: {data_dir}", file=sys.stderr)
        return 2

    n_total = 0
    n_changed = 0
    samples: list[tuple[str, str, str]] = []

    for p in sorted(data_dir.glob("*.yaml")):
        n_total += 1
        try:
            with p.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(data, dict):
            continue

        old = (data.get("name") or {}).get("initials")
        changed, new = upgrade_record(data)
        if not changed:
            continue
        n_changed += 1
        if len(samples) < 10:
            samples.append((p.name, old or "", new or ""))
        if not args.dry_run:
            with p.open("w", encoding="utf-8") as fh:
                yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)

    print(f"Total persons scanned: {n_total}")
    print(f"Upgraded: {n_changed}{'  (dry-run, nothing written)' if args.dry_run else ''}")
    print()
    print("Samples:")
    for name, old, new in samples:
        print(f"  {name}: {old!r} → {new!r}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
