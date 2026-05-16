"""One-off repair: split ORI `Griffier` mandates off the single-seat griffier post.

Before the fix in this branch the Open Raadsinformatie fetcher mapped both
ORI roles `Griffier` and `Raadsgriffier` to the `griffier` classification,
landing every griffie employee on the single-seat
`post:griffier-gemeente-<x>`. A gemeente has one gemeentegriffier, but ORI
labels commissiegriffiers, raadsadviseurs and griffiemedewerkers as
`Griffier` too (Utrecht: 13 people on one post). ORI carries no field that
distinguishes the real griffier from the rest.

A scan over 135 gemeenten with ORI data showed `Raadsgriffier` consistently
marks exactly the real griffier (23 of 24 gemeenten exactly one), while
`Griffier` is multi-seat at 18 gemeenten. The fetcher now maps
`Raadsgriffier` -> `griffier` (single-seat) and `Griffier` ->
`griffiemedewerker` (multi-seat).

This script repairs existing data. Each ORI-derived griffier mandate stores
its original role token in `role` (`"Griffier gemeente X"` or
`"Raadsgriffier gemeente X"`), so the split is data-driven and exact:

- `role` starts with "Raadsgriffier" -> leave on `post:griffier-gemeente-<x>`.
- `role` starts with "Griffier" -> move to
  `post:griffiemedewerker-gemeente-<x>` (created with `seat_count: null`).

It never deletes a person record. Run with `--apply` to write; default is a
dry run.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
PERSONS_DIR = REPO_ROOT / "data" / "personen"
POSTS_DIR = REPO_ROOT / "data" / "posten" / "gemeenten"


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _dump(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(doc, fh, sort_keys=False, allow_unicode=True)


def _role_token(role: str) -> str:
    """`"Griffier gemeente Utrecht"` -> `"Griffier"`."""
    return role.split(" gemeente ", 1)[0].strip() if role else ""


def _griffier_post_to_medewerker(post_id: str) -> str:
    # post:griffier-gemeente-<bare> -> post:griffiemedewerker-gemeente-<bare>
    return post_id.replace(":griffier-gemeente-", ":griffiemedewerker-gemeente-", 1)


def build_medewerker_post(bare: str) -> dict[str, Any]:
    return {
        "id": f"post:griffiemedewerker-gemeente-{bare}",
        "organization_id": f"org:gemeente-{bare}",
        "label": f"Griffiemedewerker {bare.replace('-', ' ').title()}",
        "classification": "griffiemedewerker",
        "seat_count": None,
        "valid_from": "1900-01-01",
        "valid_until": None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write changes; default is a dry run",
    )
    args = parser.parse_args(argv)

    moved = 0
    kept = 0
    touched_persons: set[str] = set()
    new_post_bares: set[str] = set()
    role_tokens: Counter[str] = Counter()

    for person_path in sorted(PERSONS_DIR.glob("*.yaml")):
        doc = _load(person_path)
        if not doc:
            continue
        changed = False
        for mandaat in doc.get("mandaten") or []:
            if not isinstance(mandaat, dict):
                continue
            post_id = mandaat.get("post_id") or ""
            if ":griffier-gemeente-" not in post_id:
                continue
            token = _role_token(mandaat.get("role") or "")
            role_tokens[token] += 1
            if token == "Raadsgriffier":
                kept += 1
                continue
            # Anything else on a griffier post that came from ORI is a
            # griffie employee, not the gemeentegriffier. The fetcher only
            # ever wrote "Griffier" or "Raadsgriffier" here; treat the
            # non-Raadsgriffier remainder as griffiemedewerker.
            new_post = _griffier_post_to_medewerker(post_id)
            bare = post_id.split(":griffier-gemeente-", 1)[1]
            new_post_bares.add(bare)
            mandaat["post_id"] = new_post
            mandaat["role"] = mandaat.get("role", "").replace(
                "Griffier gemeente", "Griffiemedewerker gemeente", 1
            )
            moved += 1
            changed = True
        if changed:
            touched_persons.add(person_path.name)
            if args.apply:
                _dump(person_path, doc)

    new_posts_written = 0
    for bare in sorted(new_post_bares):
        post_path = POSTS_DIR / bare / "griffiemedewerker.yaml"
        if post_path.exists():
            continue
        new_posts_written += 1
        if args.apply:
            _dump(post_path, build_medewerker_post(bare))

    mode = "APPLIED" if args.apply else "DRY RUN"
    print(f"[{mode}] ORI griffie-reclassificatie")
    print(f"  role-tokens op griffier-posten: {dict(role_tokens)}")
    print(f"  mandaten verplaatst -> griffiemedewerker: {moved}")
    print(f"  mandaten behouden op griffier (Raadsgriffier): {kept}")
    print(f"  person-records aangeraakt: {len(touched_persons)}")
    print(f"  nieuwe griffiemedewerker-posten: {new_posts_written}")
    if not args.apply:
        print("  (dry run — herhaal met --apply om te schrijven)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
