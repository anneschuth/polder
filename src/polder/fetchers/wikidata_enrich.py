"""Wikidata-verrijking voor bestaande polder persoon-records.

Doel: voor records zonder `birth.year`, doe een SPARQL-lookup op Wikidata en
vul birth.year + identifiers.wikidata in als er PRECIES EEN match is met
een PLAUSIBELE birth-year. Bij meerdere matches: skip.

Strategie:

1. Filter input: alleen records met family+given, zonder birth.year,
   met ORI als source.
2. Per record: `lookup_person_by_name(family, given=given)`.
3. Filter resultaten: kandidaten met birth_year tussen MIN_PLAUSIBLE_YEAR
   en MAX_PLAUSIBLE_YEAR (uitgaande van actief raadslid: tussen 18 en 90
   jaar oud nu).
4. Skip als 0 plausibele matches.
5. Skip als >1 plausibele match (ambigue, kan verkeerde persoon zijn).
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


# Plausibele leeftijd-range voor een huidig actief raadslid (ORI-bron).
# Iemand ouder dan 100 of jonger dan 18 is bijna zeker een naamgenoot uit
# Wikidata, niet de huidige politicus. Bereken jaartallen dynamisch op
# basis van vandaag zodat de check niet veroudert.
MAX_AGE_YEARS = 100
MIN_AGE_YEARS = 18


def _plausible_birth_year_range(today: date | None = None) -> tuple[int, int]:
    """Geef (min_year, max_year) terug voor een plausibel huidig raadslid."""
    today = today or date.today()
    return today.year - MAX_AGE_YEARS, today.year - MIN_AGE_YEARS


@dataclass
class EnrichStats:
    """Telt verrijkingen per categorie."""

    candidates: int = 0
    enriched: int = 0
    no_matches: int = 0
    ambiguous: int = 0
    implausible_age: int = 0
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


def _is_plausible_birth_year(year: int | None, today: date | None = None) -> bool:
    """True als `year` plausibel is voor een huidig (ORI) raadslid."""
    if not isinstance(year, int):
        return False
    min_year, max_year = _plausible_birth_year_range(today)
    return min_year <= year <= max_year


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
    plausible = [c for c in with_year if _is_plausible_birth_year(c["birth_year"])]
    if not plausible:
        return False, "implausible_age"
    if len(plausible) > 1:
        return False, "ambiguous"
    with_year = plausible

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
    batch_size: int = 25,
) -> EnrichStats:
    """Loop door data/personen, verrijk ORI-records zonder birth-year.

    Werkt in batch-mode: per batch van `batch_size` records gaat één POST
    naar de Reconciliation API. Dit is ongeveer 25x sneller dan per-record
    calls.

    `limit`: stop na N kandidaten (voor testen).
    `dry_run`: log wat er zou gebeuren, schrijf niets.
    """
    from polder.fetchers.wikidata_sparql import (
        RECONCILIATION_BATCH_SIZE,
        reconciliation_lookup_persons_batch,
    )

    if batch_size is None:
        batch_size = RECONCILIATION_BATCH_SIZE

    stats = EnrichStats()
    persons_dir = data_dir / "personen"
    if not persons_dir.exists():
        logger.warning("personen-dir niet gevonden: %s", persons_dir)
        return stats

    # Stap 1: verzamel alle kandidaat-records met hun pad.
    pending: list[tuple[Path, dict]] = []
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
        name = record.get("name") or {}
        family = (name.get("family") or "").strip()
        given = (name.get("given") or "").strip()
        if not family or not given:
            continue

        pending.append((path, record))
        stats.candidates += 1
        if limit is not None and len(pending) >= limit:
            break

    if not pending:
        return stats

    logger.info("Enrich-pipeline: %d kandidaten in batches van %d", len(pending), batch_size)

    # Stap 2: per batch, één call naar Reconciliation API.
    today_str = _today()
    for batch_start in range(0, len(pending), batch_size):
        batch = pending[batch_start : batch_start + batch_size]
        queries = []
        for _path, record in batch:
            name = record.get("name") or {}
            family = (name.get("family") or "").strip()
            given = (name.get("given") or "").strip()
            initials = (name.get("initials") or "").strip() or None
            queries.append((family, given, initials))

        try:
            results = reconciliation_lookup_persons_batch(queries)
        except Exception as exc:
            logger.warning("Batch %d-%d faalde: %s", batch_start, batch_start + len(batch), exc)
            stats.errors += len(batch)
            continue

        for (path, record), candidates in zip(batch, results, strict=False):
            if not candidates:
                stats.no_matches += 1
                continue
            with_year = [c for c in candidates if c.get("birth_year")]
            if not with_year:
                stats.no_matches += 1
                continue
            plausible = [c for c in with_year if _is_plausible_birth_year(c["birth_year"])]
            if not plausible:
                stats.implausible_age += 1
                continue
            if len(plausible) > 1:
                stats.ambiguous += 1
                continue

            match = plausible[0]
            record["birth"] = {"year": int(match["birth_year"])}
            identifiers = dict(record.get("identifiers") or {})
            identifiers["wikidata"] = match["qid"]
            record["identifiers"] = identifiers
            _add_wikidata_source(record, match["qid"], today_str)

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
            logger.info("Verrijkt: %s (%s, %d)", path.name, match["qid"], match["birth_year"])

        progress = min(batch_start + len(batch), len(pending))
        logger.info(
            "Progress: %d/%d records verwerkt (enriched=%d, no_match=%d, ambiguous=%d)",
            progress,
            len(pending),
            stats.enriched,
            stats.no_matches,
            stats.ambiguous,
        )

    return stats
