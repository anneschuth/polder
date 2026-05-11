"""Wikidata-verrijking voor bestaande polder persoon-records.

Doel: voor records zonder `birth.year`, doe een SPARQL-lookup op Wikidata en
vul birth.year + identifiers.wikidata in als er PRECIES EEN match is. Bij
meerdere matches: skip (geen verkeerde Q-id koppelen, leerd uit eerdere bug).

Strategie:

1. Filter input: alleen records met family+given, zonder birth.year,
   met ORI als source.
2. Per record: `lookup_person_by_name(family, given=given)`.
3. Filter resultaten: alleen kandidaten met birth_year ingevuld.
4. Skip als 0 matches (geen Wikidata-entry voor deze persoon).
5. Skip als >1 match (ambigue, kan verkeerde persoon zijn).
6. Bij 1 match: update record met birth.year + identifiers.wikidata +
   wikidata source-entry.

Output: aantal verrijkt + aantal skipped (reden).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from polder.fetchers.wikidata_sparql import lookup_person_by_name

logger = logging.getLogger("polder.fetchers.wikidata_enrich")


@dataclass
class EnrichStats:
    """Telt verrijkingen per categorie."""

    candidates: int = 0
    enriched: int = 0
    no_matches: int = 0
    ambiguous: int = 0
    errors: int = 0


def _has_ori_source(record: dict[str, Any]) -> bool:
    for src in record.get("sources") or []:
        if isinstance(src, dict) and src.get("id") == "open_raadsinformatie":
            return True
    return False


def _has_wikidata_id(record: dict[str, Any]) -> bool:
    ids = record.get("identifiers") or {}
    return bool(ids.get("wikidata"))


def _today() -> str:
    return date.today().isoformat()


def _add_wikidata_source(record: dict[str, Any], qid: str, today: str) -> None:
    sources = list(record.get("sources") or [])
    for src in sources:
        if isinstance(src, dict) and src.get("id") == "wikidata":
            src["url"] = f"https://www.wikidata.org/wiki/{qid}"
            src["retrieved"] = today
            record["sources"] = sources
            return
    sources.append(
        {
            "id": "wikidata",
            "url": f"https://www.wikidata.org/wiki/{qid}",
            "retrieved": today,
        }
    )
    record["sources"] = sources


def enrich_record(record: dict[str, Any], today: str | None = None) -> tuple[bool, str]:
    """Probeer één record te verrijken via Wikidata.

    Retourneert (was_enriched, reason). reason is een korte string:
    - "no_given": ontbrekende voornaam, kan niet zoeken.
    - "no_matches": geen Wikidata-kandidaten.
    - "no_birth_year": kandidaten zonder birth-year.
    - "ambiguous": meerdere kandidaten met birth-year.
    - "enriched": succes.
    - "already_has_birth": skip, record had al birth.year.
    - "already_has_wikidata": skip, record had al wikidata-id.
    """
    if (record.get("birth") or {}).get("year"):
        return False, "already_has_birth"
    if _has_wikidata_id(record):
        return False, "already_has_wikidata"

    name = record.get("name") or {}
    family = (name.get("family") or "").strip()
    given = (name.get("given") or "").strip()
    if not family or not given:
        return False, "no_given"

    candidates = lookup_person_by_name(family, given=given, endpoint="auto")
    if not candidates:
        return False, "no_matches"

    with_year = [c for c in candidates if c.get("birth_year")]
    if not with_year:
        return False, "no_birth_year"
    if len(with_year) > 1:
        return False, "ambiguous"

    match = with_year[0]
    today_str = today or _today()

    # Update record
    record["birth"] = {"year": int(match["birth_year"])}
    identifiers = dict(record.get("identifiers") or {})
    identifiers["wikidata"] = match["qid"]
    record["identifiers"] = identifiers
    _add_wikidata_source(record, match["qid"], today_str)

    return True, "enriched"


def enrich_ori_records(
    data_dir: Path,
    *,
    limit: int | None = None,
    dry_run: bool = False,
) -> EnrichStats:
    """Loop door data/personen, verrijk ORI-records zonder birth-year.

    `limit`: stop na N kandidaten (voor testen).
    `dry_run`: log wat er zou gebeuren, schrijf niets.
    """
    stats = EnrichStats()
    persons_dir = data_dir / "personen"
    if not persons_dir.exists():
        logger.warning("personen-dir niet gevonden: %s", persons_dir)
        return stats

    for path in sorted(persons_dir.glob("*.yaml")):
        try:
            record = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            stats.errors += 1
            continue
        if not isinstance(record, dict):
            continue
        if not _has_ori_source(record):
            continue
        if (record.get("birth") or {}).get("year"):
            continue
        if _has_wikidata_id(record):
            continue

        stats.candidates += 1
        if limit is not None and stats.candidates > limit:
            break

        try:
            ok, reason = enrich_record(record)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Enrich-fout voor %s: %s", path.name, exc)
            stats.errors += 1
            continue

        if ok:
            stats.enriched += 1
            if not dry_run:
                path.write_text(
                    yaml.safe_dump(
                        record,
                        sort_keys=False,
                        allow_unicode=True,
                        default_flow_style=False,
                    ),
                    encoding="utf-8",
                )
            logger.info("Verrijkt: %s", path.name)
        elif reason == "no_matches":
            stats.no_matches += 1
        elif reason == "ambiguous":
            stats.ambiguous += 1

    return stats
