"""Migratie: corrigeer ROO source-URLs in bestaande YAML's.

Twee bugs werden ontdekt na de Phase 1+3 commits:

1. **Organisatie-URLs**: `https://organisaties.overheid.nl/{roo_id}/`
   geeft 404. ROO eist een non-empty path-segment ná de roo_id (bv.
   `/{roo_id}/Gemeente_Helmond` of `/{roo_id}/x` — alleen de roo_id
   telt voor de server, het suffix kan vrij gekozen worden).

2. **Medewerker-URLs**: `/{roo_id}/medewerker/{med_sysid}/` is een
   verzonnen pad-structuur die helemaal niet bestaat. Resolver schreef
   die in mandaat.sources[]. Vervang door de org-URL; behoud de
   medewerker-link via een `roo_medewerker_id={sysid}` field-entry.

Alle locaties waar URL gefixt moet worden:
- `data/organisaties/**/*.yaml` → `sources[].url` met id=roo
- `data/posten/**/*.yaml`       → `sources[].url` met id=roo
- `data/personen/*.yaml`        → `mandaten[].sources[].url` met id=roo

Run: `uv run python scripts/fix_roo_source_urls.py [--dry-run]`.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

URL_RX = re.compile(r"^https://organisaties\.overheid\.nl/(\d+)/(.*)$")
ROOT_RX = re.compile(r"^https://organisaties\.overheid\.nl/?$")


def _slug_for_org(org_id: str | None) -> str:
    """Geef een geldig URL-suffix uit `org:<slug>`. Fallback `x`."""
    if not org_id or not org_id.startswith("org:"):
        return "x"
    slug = org_id[len("org:") :]
    return slug or "x"


_MEDEWERKER_RX = re.compile(r"^https://organisaties\.overheid\.nl/(?:(\d+)/)?medewerker/(\d+)/?$")


def _fix_url(current: str, org_id_for_fallback: str | None) -> tuple[str | None, str | None]:
    """Geef (nieuwe_url, medewerker_sysid) terug. medewerker_sysid alleen als
    de oude URL het verzonnen `/medewerker/<sysid>/`-pad bevatte (dan moeten
    we 'm later in de fields zetten als fingerprint)."""
    # Variant A: `/medewerker/{sysid}/`-pad
    m_med = _MEDEWERKER_RX.match(current)
    if m_med:
        med_sysid = m_med.group(2)
        roo_id = m_med.group(1) or "x"
        return (
            f"https://organisaties.overheid.nl/{roo_id}/{_slug_for_org(org_id_for_fallback)}",
            med_sysid,
        )

    # Variant B: `/{roo_id}/` (zonder suffix)
    m = URL_RX.match(current)
    if m and not m.group(2):
        roo_id = m.group(1)
        return (
            f"https://organisaties.overheid.nl/{roo_id}/{_slug_for_org(org_id_for_fallback)}",
            None,
        )

    # Variant C: root-URL (geen roo_id) — laten staan, kan niets aan opgehaald worden.
    return None, None


def _fix_sources_list(sources: list, *, org_id_for_fallback: str | None) -> int:
    """Wijzig `sources` in-place. Geef aantal gefixte URLs.

    Bonus-actie: als de oude URL een `/medewerker/<sysid>/`-pad had, voegen
    we `roo_medewerker_id=<sysid>` toe aan `fields[]` zodat de link tussen
    bron en specifieke medewerker behouden blijft (de URL zelf wijst nu naar
    de organisatie omdat ROO geen publieke medewerker-URLs heeft).
    """
    n = 0
    for s in sources:
        if not isinstance(s, dict):
            continue
        if s.get("id") != "roo":
            continue
        url = s.get("url")
        if not isinstance(url, str):
            continue
        new_url, med_sysid = _fix_url(url, org_id_for_fallback)
        if new_url and new_url != url:
            s["url"] = new_url
            n += 1
        if med_sysid:
            fields = s.get("fields") or []
            fingerprint = f"roo_medewerker_id={med_sysid}"
            if fingerprint not in fields:
                fields.append(fingerprint)
                s["fields"] = fields
    return n


def _process_organisatie(data: dict) -> int:
    """org-yaml: `sources[]` op top-level. org_id is de eigen id."""
    if not isinstance(data.get("sources"), list):
        return 0
    return _fix_sources_list(data["sources"], org_id_for_fallback=data.get("id"))


def _process_post(data: dict) -> int:
    """post-yaml: `sources[]` op top-level. org_id = organization_id."""
    if not isinstance(data.get("sources"), list):
        return 0
    return _fix_sources_list(data["sources"], org_id_for_fallback=data.get("organization_id"))


def _process_person(data: dict) -> int:
    """person-yaml: `mandaten[].sources[]`. org_id = mandaat.organization_id."""
    n = 0
    for m in data.get("mandaten") or []:
        if not isinstance(m, dict):
            continue
        srcs = m.get("sources")
        if not isinstance(srcs, list):
            continue
        n += _fix_sources_list(srcs, org_id_for_fallback=m.get("organization_id"))
    return n


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if not args.data.exists():
        print(f"Data-directory bestaat niet: {args.data}", file=sys.stderr)
        return 2

    targets: list[tuple[Path, str]] = []
    for p in (args.data / "organisaties").rglob("*.yaml"):
        targets.append((p, "organisatie"))
    for p in (args.data / "posten").rglob("*.yaml"):
        targets.append((p, "post"))
    for p in (args.data / "personen").rglob("*.yaml"):
        targets.append((p, "person"))

    n_files_changed = 0
    n_urls_changed = 0
    samples: list[tuple[str, int]] = []

    for path, kind in targets:
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(data, dict):
            continue

        if kind == "organisatie":
            n = _process_organisatie(data)
        elif kind == "post":
            n = _process_post(data)
        else:
            n = _process_person(data)

        if n == 0:
            continue
        n_files_changed += 1
        n_urls_changed += n
        if len(samples) < 5:
            samples.append((str(path.relative_to(args.data.parent)), n))

        if not args.dry_run:
            with path.open("w", encoding="utf-8") as fh:
                yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)

    print(f"Files scanned: {len(targets)}")
    print(f"Files changed: {n_files_changed}{'  (dry-run)' if args.dry_run else ''}")
    print(f"URLs fixed:    {n_urls_changed}")
    print()
    print("Samples:")
    for name, count in samples:
        print(f"  {name}: {count} URLs")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
