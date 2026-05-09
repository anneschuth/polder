"""Fetcher voor Wikidata Q-id crosswalks via de SPARQL Query Service.

Bron: Wikidata Query Service.
Endpoint: https://query.wikidata.org/sparql
Formaat: SPARQL Query Results JSON (application/sparql-results+json).
Update: live (Wikidata zelf wordt continu bewerkt).
Licentie: CC0 (Wikidata data).
Dekking: Q-id's voor Nederlandse ministeries, gemeenten, provincies, waterschappen,
TK-leden en andere entiteiten die we willen crosswalken naar polder-records.

Voorbeeld-query (Nederlandse gemeenten, Q11424093 = Nederlandse gemeente):

    SELECT ?org ?orgLabel WHERE {
        ?org wdt:P31 wd:Q11424093 .
        SERVICE wikibase:label {
            bd:serviceParam wikibase:language "nl,en".
        }
    }

Wikimedia User-Agent policy vereist een zinnige UA met contactinfo:
https://meta.wikimedia.org/wiki/User-Agent_policy

Tracking issue: https://github.com/anneschuth/polder/issues/4
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import time
import unicodedata
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import yaml

logger = logging.getLogger("polder.fetchers.wikidata_sparql")

__all__ = [
    "MIN_REQUEST_INTERVAL",
    "ORG_QUERIES",
    "PERSON_QUERY",
    "SOURCE_ID",
    "SPARQL_ENDPOINT",
    "USER_AGENT",
    "build_org_index",
    "build_person_index",
    "extract_qid",
    "main",
    "match_organisations",
    "match_personen",
    "merge_wikidata_into_record",
    "normalize_org_name",
    "parse_org_bindings",
    "parse_person_bindings",
    "query_sparql",
]

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = (
    "polder-bot/0.1 (https://github.com/anneschuth/polder; anne.schuth@gmail.com)"
)
HTTP_TIMEOUT = 180.0
MIN_REQUEST_INTERVAL = 1.0  # seconden tussen calls (Wikimedia rate-limit)
SOURCE_ID = "wikidata"


# ---------------------------------------------------------------------------
# SPARQL queries
# ---------------------------------------------------------------------------
#
# We splitsen organisaties per type: aparte query per Q-class houdt de payload
# klein en maakt het type-veld in het resultaat impliciet.

ORG_QUERIES: dict[str, str] = {
    "ministerie": """
SELECT ?item ?itemLabel ?abbr ?oin WHERE {
  ?item wdt:P31 wd:Q3143387 .
  OPTIONAL { ?item wdt:P1813 ?abbr }
  OPTIONAL { ?item wdt:P9947 ?oin }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "nl,en". }
}
""",
    "gemeente": """
SELECT ?item ?itemLabel ?abbr ?oin WHERE {
  ?item wdt:P31 wd:Q11424093 .
  OPTIONAL { ?item wdt:P1813 ?abbr }
  OPTIONAL { ?item wdt:P9947 ?oin }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "nl,en". }
}
""",
    "provincie": """
SELECT ?item ?itemLabel ?abbr ?oin WHERE {
  ?item wdt:P31 wd:Q134390 .
  OPTIONAL { ?item wdt:P1813 ?abbr }
  OPTIONAL { ?item wdt:P9947 ?oin }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "nl,en". }
}
""",
    "waterschap": """
SELECT ?item ?itemLabel ?abbr ?oin WHERE {
  ?item wdt:P31 wd:Q1502044 .
  OPTIONAL { ?item wdt:P1813 ?abbr }
  OPTIONAL { ?item wdt:P9947 ?oin }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "nl,en". }
}
""",
}


# Alle Tweede Kamerleden (huidig + historisch). P39 = position held,
# Q18887908 = lid van de Tweede Kamer; P9213 = TK-persoon-ID.
PERSON_QUERY = """
SELECT ?person ?personLabel ?tkid ?birthyear ?initials ?family WHERE {
  ?person wdt:P39 wd:Q18887908 .
  OPTIONAL { ?person wdt:P9213 ?tkid }
  OPTIONAL { ?person wdt:P569 ?birth . BIND(YEAR(?birth) AS ?birthyear) }
  OPTIONAL { ?person wdt:P1813 ?initials }
  OPTIONAL { ?person wdt:P734 ?familyEntity .
             ?familyEntity rdfs:label ?family .
             FILTER(LANG(?family) = "nl") }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "nl,en". }
}
"""


# ---------------------------------------------------------------------------
# HTTP + cache
# ---------------------------------------------------------------------------


_LAST_REQUEST_TIME: list[float] = [0.0]


def _rate_limit() -> None:
    """Wacht tot er minimaal MIN_REQUEST_INTERVAL is verstreken sinds de vorige call."""
    now = time.monotonic()
    delta = now - _LAST_REQUEST_TIME[0]
    if delta < MIN_REQUEST_INTERVAL:
        time.sleep(MIN_REQUEST_INTERVAL - delta)
    _LAST_REQUEST_TIME[0] = time.monotonic()


def _query_hash(query: str) -> str:
    """Deterministische cache-key per query-tekst."""
    return hashlib.sha256(query.strip().encode("utf-8")).hexdigest()[:16]


def query_sparql(
    query: str,
    *,
    timeout: float = HTTP_TIMEOUT,
    cache_dir: Path | None = None,
    use_cache: bool = True,
    client: httpx.Client | None = None,
    max_retries: int = 4,
) -> list[dict[str, Any]]:
    """Voer een SPARQL-query uit en geef de bindings terug als lijst van dicts.

    Cached responses landen onder ``cache_dir/<hash>.json``. Met ``use_cache=False``
    wordt de cache genegeerd en altijd opnieuw opgehaald.

    Bij HTTP 429 of 503 wachten we (Retry-After-header indien aanwezig, anders
    exponentiele backoff) en proberen we opnieuw, tot ``max_retries`` pogingen.
    """
    cache_path: Path | None = None
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"{_query_hash(query)}.json"
        if use_cache and cache_path.exists() and cache_path.stat().st_size > 0:
            logger.debug("Wikidata cache hit: %s", cache_path)
            with cache_path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
            return list(payload.get("results", {}).get("bindings", []))

    headers = {
        "Accept": "application/sparql-results+json",
        "User-Agent": USER_AGENT,
    }
    params = {"query": query, "format": "json"}

    def _do_call() -> httpx.Response:
        _rate_limit()
        if client is None:
            with httpx.Client(timeout=timeout, follow_redirects=True) as inner:
                return inner.get(SPARQL_ENDPOINT, params=params, headers=headers)
        return client.get(SPARQL_ENDPOINT, params=params, headers=headers)

    backoff = 2.0
    for attempt in range(max_retries + 1):
        response = _do_call()
        if response.status_code in (429, 503):
            retry_after = response.headers.get("retry-after") if hasattr(response, "headers") else None
            try:
                wait = float(retry_after) if retry_after else backoff
            except (TypeError, ValueError):
                wait = backoff
            logger.warning(
                "Wikidata SPARQL %s op poging %d/%d, wacht %.1fs",
                response.status_code,
                attempt + 1,
                max_retries + 1,
                wait,
            )
            if attempt >= max_retries:
                response.raise_for_status()
            time.sleep(wait)
            backoff = min(backoff * 2, 120.0)
            continue
        response.raise_for_status()
        break

    payload = response.json()

    if cache_path is not None:
        with cache_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)

    return list(payload.get("results", {}).get("bindings", []))


# ---------------------------------------------------------------------------
# Binding parsing
# ---------------------------------------------------------------------------


_QID_RE = re.compile(r"(Q\d+)$")


def extract_qid(uri: str | None) -> str | None:
    """Pak de Q-id uit een Wikidata IRI: `http://www.wikidata.org/entity/Q123` → `Q123`."""
    if not uri:
        return None
    match = _QID_RE.search(uri)
    return match.group(1) if match else None


def _value(binding: dict[str, Any], key: str) -> str | None:
    cell = binding.get(key)
    if not cell:
        return None
    val = cell.get("value")
    if val is None or val == "":
        return None
    return str(val)


def parse_org_bindings(bindings: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map SPARQL-bindings voor organisaties op {qid, label, abbr, oin}."""
    rows: list[dict[str, Any]] = []
    for b in bindings:
        qid = extract_qid(_value(b, "item"))
        if not qid:
            continue
        rows.append(
            {
                "qid": qid,
                "label": _value(b, "itemLabel"),
                "abbr": _value(b, "abbr"),
                "oin": _value(b, "oin"),
            }
        )
    return rows


def parse_person_bindings(bindings: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map SPARQL-bindings voor personen op {qid, label, tkid, birthyear, initials, family}."""
    rows: list[dict[str, Any]] = []
    for b in bindings:
        qid = extract_qid(_value(b, "person"))
        if not qid:
            continue
        birthyear_raw = _value(b, "birthyear")
        birthyear: int | None = None
        if birthyear_raw is not None:
            try:
                birthyear = int(birthyear_raw)
            except ValueError:
                birthyear = None
        rows.append(
            {
                "qid": qid,
                "label": _value(b, "personLabel"),
                "tkid": _value(b, "tkid"),
                "birthyear": birthyear,
                "initials": _value(b, "initials"),
                "family": _value(b, "family"),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _ascii_lower(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return decomposed.encode("ascii", "ignore").decode("ascii").lower()


_TUSSENVOEGSELS = {
    "van",
    "der",
    "den",
    "de",
    "het",
    "te",
    "ten",
    "ter",
    "op",
    "in",
    "aan",
    "bij",
    "tot",
    "uit",
    "voor",
    "vd",
    "vdr",
    "von",
    "le",
    "la",
    "du",
    "el",
    "al",
}


# Prefixen die in polder-organisatienamen voorkomen maar in Wikidata-labels niet
# (en omgekeerd). We strippen ze aan beide kanten voor name-matching.
_ORG_NOISE = (
    "ministerie van ",
    "ministerie ",
    "gemeente ",
    "provincie ",
    "waterschap ",
    "hoogheemraadschap van ",
    "hoogheemraadschap ",
    "wetterskip ",
)


def normalize_org_name(name: str | None) -> str:
    """Normaliseer een organisatienaam voor name-matching.

    Lowercase, ASCII, strip ruis-prefixen, collapse witruimte naar enkele spaties.
    """
    if not name:
        return ""
    s = _ascii_lower(name)
    s = s.replace("-", " ").replace(",", " ")
    s = re.sub(r"\s+", " ", s).strip()
    changed = True
    while changed:
        changed = False
        for prefix in _ORG_NOISE:
            if s.startswith(prefix):
                s = s[len(prefix) :].strip()
                changed = True
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _normalize_initials(value: str | None) -> str:
    if not value:
        return ""
    cleaned = _ascii_lower(value)
    return re.sub(r"[^a-z0-9]+", "", cleaned)


def _normalize_family(value: str | None) -> str:
    """Normaliseer een familienaam: strip tussenvoegsels, ASCII, lowercase."""
    if not value:
        return ""
    base = _ascii_lower(value)
    base = re.sub(r"[^a-z0-9\s-]+", " ", base)
    parts = [p for p in re.split(r"[\s-]+", base) if p]
    family_parts = [p for p in parts if p not in _TUSSENVOEGSELS] or parts
    return "-".join(family_parts).strip("-")


def build_org_index(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Bouw een lookup-index op (oin → row) en (genormaliseerde naam → row).

    Returnt een dict met sub-dicts ``by_oin`` en ``by_name``. Als hetzelfde
    sleutel-veld twee maal voorkomt, wint de eerste; latere worden gelogd.
    """
    by_oin: dict[str, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}
    for row in rows:
        oin = row.get("oin")
        if oin:
            by_oin.setdefault(oin, row)
        label = row.get("label")
        norm = normalize_org_name(label)
        if norm:
            by_name.setdefault(norm, row)
        # Match ook op afkorting (zoals "BZK", "Aa en Maas") — alleen als die
        # geen botsing geeft met een naam.
        abbr = row.get("abbr")
        if abbr:
            abbr_norm = normalize_org_name(abbr)
            if abbr_norm and abbr_norm not in by_name:
                by_name[abbr_norm] = row
    return {"by_oin": by_oin, "by_name": by_name}


def build_person_index(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Bouw een lookup-index voor personen: (tkid → row) en ((family, initials, birthyear) → row)."""
    by_tkid: dict[str, dict[str, Any]] = {}
    by_natural: dict[tuple[str, str, int], dict[str, Any]] = {}
    for row in rows:
        tkid = row.get("tkid")
        if tkid:
            by_tkid.setdefault(tkid, row)
        family = _normalize_family(row.get("family") or row.get("label"))
        initials = _normalize_initials(row.get("initials"))
        birthyear = row.get("birthyear")
        if family and birthyear:
            key = (family, initials, int(birthyear))
            by_natural.setdefault(key, row)
    return {"by_tkid": by_tkid, "by_natural": by_natural}


def _record_org_name(record: dict[str, Any]) -> str | None:
    names = record.get("names") or []
    if not names:
        return None
    return names[0].get("value") if isinstance(names[0], dict) else None


def _record_org_abbr(record: dict[str, Any]) -> str | None:
    names = record.get("names") or []
    if not names:
        return None
    return names[0].get("abbr") if isinstance(names[0], dict) else None


def _record_org_oin(record: dict[str, Any]) -> str | None:
    identifiers = record.get("identifiers") or {}
    return identifiers.get("oin")


def match_organisations(
    records: Iterable[dict[str, Any]],
    index: dict[str, dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any], str]]:
    """Match polder-organisatie-records aan Wikidata-rows.

    Returnt lijst van ``(record, wikidata_row, match_method)``.
    Match-volgorde: OIN > genormaliseerde naam > genormaliseerde afkorting.
    """
    matches: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    by_oin = index["by_oin"]
    by_name = index["by_name"]
    for record in records:
        if (record.get("identifiers") or {}).get("wikidata"):
            # Al gevuld; sla over zodat we lokale waarden niet overschrijven.
            continue
        oin = _record_org_oin(record)
        row = by_oin.get(oin) if oin else None
        method = "oin"
        if row is None:
            name = _record_org_name(record)
            norm = normalize_org_name(name)
            if norm:
                row = by_name.get(norm)
                method = "name"
        if row is None:
            abbr = _record_org_abbr(record)
            abbr_norm = normalize_org_name(abbr)
            if abbr_norm:
                row = by_name.get(abbr_norm)
                method = "abbr"
        if row is None:
            continue
        matches.append((record, row, method))
    return matches


def match_personen(
    records: Iterable[dict[str, Any]],
    index: dict[str, dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any], str]]:
    """Match polder-persoon-records aan Wikidata-rows.

    Match-volgorde: tk_persoon_id > (familienaam + initialen + geboortejaar).
    """
    matches: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    by_tkid = index["by_tkid"]
    by_natural = index["by_natural"]
    for record in records:
        if (record.get("identifiers") or {}).get("wikidata"):
            continue
        tkid = (record.get("identifiers") or {}).get("tk_persoon_id")
        row = by_tkid.get(tkid) if tkid else None
        method = "tkid"
        if row is None:
            name = record.get("name") or {}
            family = _normalize_family(name.get("family"))
            initials = _normalize_initials(name.get("initials"))
            birth = (record.get("birth") or {}).get("year")
            if family and birth is not None:
                key = (family, initials, int(birth))
                row = by_natural.get(key)
                method = "natural"
                if row is None and initials:
                    # Probeer zonder initialen (Wikidata heeft die vaak niet).
                    key2 = (family, "", int(birth))
                    row = by_natural.get(key2)
                    method = "family_birth"
        if row is None:
            continue
        matches.append((record, row, method))
    return matches


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------


def _today() -> str:
    return date.today().isoformat()


def _merge_sources(
    existing: list[dict[str, Any]] | None, new: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for src in existing or []:
        if isinstance(src, dict) and src.get("id"):
            by_id[src["id"]] = dict(src)
    for src in new or []:
        if isinstance(src, dict) and src.get("id"):
            by_id[src["id"]] = dict(src)
    return list(by_id.values())


def merge_wikidata_into_record(
    record: dict[str, Any], qid: str, *, today: str | None = None
) -> dict[str, Any]:
    """Voeg ``identifiers.wikidata = qid`` en de bron toe, zonder bestaande velden te overschrijven."""
    today_str = today or _today()
    merged = dict(record)
    identifiers = dict(merged.get("identifiers") or {})
    identifiers["wikidata"] = qid
    merged["identifiers"] = identifiers
    new_source = {
        "id": SOURCE_ID,
        "url": f"https://www.wikidata.org/wiki/{qid}",
        "retrieved": today_str,
    }
    merged["sources"] = _merge_sources(merged.get("sources"), [new_source])
    return merged


# ---------------------------------------------------------------------------
# YAML helpers
# ---------------------------------------------------------------------------


def _iter_yaml_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(p for p in directory.glob("*.yaml") if p.is_file())


def _read_yaml(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("Kon YAML niet lezen (%s): %s", path, exc)
        return None
    if not isinstance(data, dict):
        return None
    return data


def _write_yaml(path: Path, record: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(
            record,
            fh,
            sort_keys=False,
            default_flow_style=False,
            allow_unicode=True,
        )


def _ordered_for_org(record: dict[str, Any]) -> dict[str, Any]:
    order = [
        "id",
        "type",
        "identifiers",
        "classification",
        "parent_id",
        "names",
        "contact",
        "valid_from",
        "valid_until",
        "sources",
    ]
    out: dict[str, Any] = {}
    for key in order:
        if key in record:
            out[key] = record[key]
    for key, value in record.items():
        if key not in out:
            out[key] = value
    return out


def _ordered_for_person(record: dict[str, Any]) -> dict[str, Any]:
    order = ["id", "identifiers", "name", "birth", "gender", "mandaten", "sources"]
    out: dict[str, Any] = {}
    for key in order:
        if key in record:
            out[key] = record[key]
    for key, value in record.items():
        if key not in out:
            out[key] = value
    return out


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


ORG_FOLDERS: dict[str, str] = {
    "ministerie": "ministeries",
    "gemeente": "gemeenten",
    "provincie": "provincies",
    "waterschap": "waterschappen",
}


def enrich_organisations(
    data_root: Path,
    *,
    cache_dir: Path,
    dry_run: bool = False,
    today: str | None = None,
) -> dict[str, dict[str, int]]:
    """Verrijk organisatie-records met Wikidata Q-id's.

    Returnt per category een dict ``{candidates, matched_oin, matched_name, written}``.
    """
    today_str = today or _today()
    stats: dict[str, dict[str, int]] = {}
    for org_type, folder in ORG_FOLDERS.items():
        folder_path = data_root / folder
        files = _iter_yaml_files(folder_path)
        if not files:
            logger.info("Geen records onder %s, sla over", folder_path)
            continue
        query = ORG_QUERIES[org_type]
        bindings = query_sparql(query, cache_dir=cache_dir)
        rows = parse_org_bindings(bindings)
        index = build_org_index(rows)
        records: list[tuple[Path, dict[str, Any]]] = []
        for path in files:
            data = _read_yaml(path)
            if data is None:
                continue
            records.append((path, data))
        matches = match_organisations((rec for _, rec in records), index)
        # Map record → (path, qid, method).
        by_id = {id(rec): (path, rec) for path, rec in records}
        cat_stats = {
            "candidates": len(records),
            "rows": len(rows),
            "matched_oin": 0,
            "matched_name": 0,
            "matched_abbr": 0,
            "written": 0,
        }
        for record, row, method in matches:
            path, _ = by_id[id(record)]
            qid = row["qid"]
            merged = merge_wikidata_into_record(record, qid, today=today_str)
            merged = _ordered_for_org(merged)
            if method == "oin":
                cat_stats["matched_oin"] += 1
            elif method == "name":
                cat_stats["matched_name"] += 1
            elif method == "abbr":
                cat_stats["matched_abbr"] += 1
            if dry_run:
                logger.info("DRY-RUN %s %s ← %s (%s)", folder, record.get("id"), qid, method)
            else:
                _write_yaml(path, merged)
            cat_stats["written"] += 1
        stats[org_type] = cat_stats
        logger.info(
            "%s: %d records, %d wikidata-rows, %d matches (oin=%d, name=%d, abbr=%d)",
            folder,
            cat_stats["candidates"],
            cat_stats["rows"],
            cat_stats["written"],
            cat_stats["matched_oin"],
            cat_stats["matched_name"],
            cat_stats["matched_abbr"],
        )
    return stats


def enrich_personen(
    data_root: Path,
    *,
    cache_dir: Path,
    dry_run: bool = False,
    today: str | None = None,
) -> dict[str, int]:
    """Verrijk persoon-records met Wikidata Q-id's."""
    today_str = today or _today()
    files: list[Path] = []
    for sub in ("current", "historisch"):
        files.extend(_iter_yaml_files(data_root / sub))
    if not files:
        logger.info("Geen persoonsrecords onder %s, sla over", data_root)
        return {"candidates": 0, "rows": 0, "matched_tkid": 0, "matched_natural": 0, "written": 0}

    bindings = query_sparql(PERSON_QUERY, cache_dir=cache_dir)
    rows = parse_person_bindings(bindings)
    index = build_person_index(rows)

    records: list[tuple[Path, dict[str, Any]]] = []
    for path in files:
        data = _read_yaml(path)
        if data is None:
            continue
        records.append((path, data))
    matches = match_personen((rec for _, rec in records), index)
    by_id = {id(rec): (path, rec) for path, rec in records}

    stats = {
        "candidates": len(records),
        "rows": len(rows),
        "matched_tkid": 0,
        "matched_natural": 0,
        "matched_family_birth": 0,
        "written": 0,
    }
    for record, row, method in matches:
        path, _ = by_id[id(record)]
        qid = row["qid"]
        merged = merge_wikidata_into_record(record, qid, today=today_str)
        merged = _ordered_for_person(merged)
        if method == "tkid":
            stats["matched_tkid"] += 1
        elif method == "natural":
            stats["matched_natural"] += 1
        elif method == "family_birth":
            stats["matched_family_birth"] += 1
        if dry_run:
            logger.info("DRY-RUN persoon %s ← %s (%s)", record.get("id"), qid, method)
        else:
            _write_yaml(path, merged)
        stats["written"] += 1

    logger.info(
        "personen: %d records, %d wikidata-rows, %d matches (tkid=%d, natural=%d, family_birth=%d)",
        stats["candidates"],
        stats["rows"],
        stats["written"],
        stats["matched_tkid"],
        stats["matched_natural"],
        stats["matched_family_birth"],
    )
    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="polder-fetch-wikidata",
        description=(
            "Verrijk polder-organisatie- en persoon-records met Wikidata Q-id's "
            "via de SPARQL Query Service."
        ),
    )
    parser.add_argument("--orgs", action="store_true", help="Alleen organisaties verrijken.")
    parser.add_argument("--personen", action="store_true", help="Alleen personen verrijken.")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Beide (organisaties + personen). Default als geen van --orgs/--personen is gezet.",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("data"),
        help="Root van de data-directory (default: data).",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path("_cache/wikidata"),
        help="Cache-directory voor SPARQL-responses (default: _cache/wikidata).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Schrijf niets, log alleen wat geschreven zou worden.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    do_orgs = args.orgs or args.all or (not args.orgs and not args.personen)
    do_personen = args.personen or args.all or (not args.orgs and not args.personen)

    cache_dir: Path = args.cache
    org_root: Path = args.data_root / "organisaties"
    person_root: Path = args.data_root / "personen"

    total_written = 0
    if do_orgs:
        org_stats = enrich_organisations(org_root, cache_dir=cache_dir, dry_run=args.dry_run)
        for cat, s in org_stats.items():
            print(
                f"{cat}: {s['written']}/{s['candidates']} matched "
                f"(oin={s['matched_oin']}, name={s['matched_name']}, abbr={s['matched_abbr']})",
                file=sys.stderr,
            )
            total_written += s["written"]
    if do_personen:
        ps = enrich_personen(person_root, cache_dir=cache_dir, dry_run=args.dry_run)
        print(
            f"personen: {ps['written']}/{ps['candidates']} matched "
            f"(tkid={ps['matched_tkid']}, natural={ps['matched_natural']}, "
            f"family_birth={ps['matched_family_birth']})",
            file=sys.stderr,
        )
        total_written += ps["written"]

    print(f"Wikidata enrichment: {total_written} records updated", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
