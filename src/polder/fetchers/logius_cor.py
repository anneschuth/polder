"""Fetcher voor het Logius OIN-register (COR: Centrale OIN Raadpleegvoorziening).

Bron: Logius.
Endpoints:
- Public proxy (geen API-key vereist):
  https://oinregister.logius.nl/pubproxy/organisations/public
- Authenticated REST-API:
  https://oinregister.logius.nl/api/v1/organisaties (apikey-header verplicht)

Sinds eind 2024 / begin 2025 serveert ``oinregister.logius.nl/oinregister.csv`` een
SPA-HTML-pagina; de oude bulk-CSV bestaat niet meer onder dat pad. Het Angular-
frontend bouwt de download client-side via de pubproxy. Wij gebruiken diezelfde
pubproxy om alle records gepagineerd op te halen en lokaal als CSV te cachen.

Formaat: JSON per pagina (pageSize hard-cap 100). Velden per record:
- ``oin`` (20 cijfers)
- ``name``
- ``kvkNumber`` (8 cijfers, vaak null voor sub-OINs)
- ``rooId``: TOOI-URI zoals ``https://identifier.overheid.nl/tooi/id/ministerie/mnre1034``
- ``organisationType``: code (``MNRE``, ``GM``, ``PV``, ``WS``, ...)
- ``status``: ``Actief`` / ``Inactief``
- ``creationDate``, ``modificationDate``, ``deactivationDate``

Update: gestaag (mutaties dagelijks mogelijk, geen vaste publicatiekadans).
Licentie: open (Logius publiceert openbare informatie van het OIN-register).
Dekking: alle organisaties met een OIN — overheidsorganisaties en leveranciers
op Digikoppeling. Niet elke polder-organisatie heeft een OIN; matching is
best-effort.

Tracking issue: https://github.com/anneschuth/polder/issues/3
"""

from __future__ import annotations

import argparse
import csv
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

logger = logging.getLogger("polder.fetchers.logius_cor")

__all__ = [
    "COR_API_BASE",
    "CSV_COLUMNS",
    "OIN_CSV_URL",
    "OIN_PUBPROXY_URL",
    "PAGE_SIZE",
    "SOURCE_ID",
    "USER_AGENT",
    "build_oin_index",
    "enrich_organisations",
    "fetch_oin_register",
    "main",
    "match_to_records",
    "merge_oin_into_record",
    "normalize_org_name",
    "rows_from_csv",
    "rows_to_csv",
]

# Canoniek (deprecated) endpoint, gehouden voor backward-compat van public symbols.
OIN_CSV_URL = "https://oinregister.logius.nl/oinregister.csv"
# Daadwerkelijke unauthenticated bron die we gebruiken.
OIN_PUBPROXY_URL = "https://oinregister.logius.nl/pubproxy/organisations/public"
COR_API_BASE = "https://oinregister.logius.nl/api/v1/"
SOURCE_ID = "logius_cor"
USER_AGENT = (
    "polder-bot/0.1 (https://github.com/anneschuth/polder; anne.schuth@gmail.com)"
)
HTTP_TIMEOUT = 60.0
PAGE_SIZE = 100  # API hard-cap
MIN_REQUEST_INTERVAL = 0.2  # weest aardig voor de pubproxy

CSV_COLUMNS = (
    "oin",
    "name",
    "kvk",
    "tooi",
    "organisation_type",
    "status",
    "modification_date",
)


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


_LAST_REQUEST_TIME: list[float] = [0.0]


def _rate_limit() -> None:
    now = time.monotonic()
    delta = now - _LAST_REQUEST_TIME[0]
    if delta < MIN_REQUEST_INTERVAL:
        time.sleep(MIN_REQUEST_INTERVAL - delta)
    _LAST_REQUEST_TIME[0] = time.monotonic()


def _today() -> str:
    return date.today().isoformat()


def _normalise_record(raw: dict[str, Any]) -> dict[str, Any]:
    """Transformeer een API-record naar onze interne representatie."""
    return {
        "oin": (raw.get("oin") or "").strip() or None,
        "name": (raw.get("name") or "").strip() or None,
        "kvk": (raw.get("kvkNumber") or "").strip() or None,
        "tooi": (raw.get("rooId") or "").strip() or None,
        "organisation_type": (raw.get("organisationType") or "").strip() or None,
        "status": (raw.get("status") or "").strip() or None,
        "modification_date": (raw.get("modificationDate") or "").strip() or None,
    }


def fetch_oin_register(
    *,
    timeout: float = HTTP_TIMEOUT,
    client: httpx.Client | None = None,
    page_size: int = PAGE_SIZE,
    max_pages: int | None = None,
) -> list[dict[str, Any]]:
    """Download het volledige OIN-register via de publieke proxy.

    Returnt een lijst dicts met de in ``CSV_COLUMNS`` genoemde velden. De API
    pagineert met ``pageSize`` records per call (hard-cap 100); we lopen door
    tot ``X-Total-Count`` is bereikt of ``max_pages`` is overschreden.
    """
    headers = {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }

    rows: list[dict[str, Any]] = []

    def _fetch_page(page: int) -> tuple[list[dict[str, Any]], int]:
        _rate_limit()
        params = {"page": page, "pageSize": page_size}
        if client is None:
            with httpx.Client(timeout=timeout, follow_redirects=True) as inner:
                response = inner.get(OIN_PUBPROXY_URL, params=params, headers=headers)
        else:
            response = client.get(OIN_PUBPROXY_URL, params=params, headers=headers)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError(
                f"OIN-register response is geen JSON-array (page={page}, type={type(payload).__name__})"
            )
        total_header = response.headers.get("x-total-count") or response.headers.get(
            "X-Total-Count"
        )
        try:
            total = int(total_header) if total_header is not None else len(payload)
        except (TypeError, ValueError):
            total = len(payload)
        return payload, total

    page = 1
    total = -1
    while True:
        payload, total = _fetch_page(page)
        if not payload:
            break
        for raw in payload:
            rows.append(_normalise_record(raw))
        if total > 0 and len(rows) >= total:
            break
        if max_pages is not None and page >= max_pages:
            break
        page += 1

    logger.info("OIN-register: %d records opgehaald (X-Total-Count=%s)", len(rows), total)
    return rows


# ---------------------------------------------------------------------------
# CSV cache
# ---------------------------------------------------------------------------


def rows_to_csv(rows: Iterable[dict[str, Any]], path: Path) -> None:
    """Schrijf records naar een CSV-bestand met vaste kolomvolgorde."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col) or "" for col in CSV_COLUMNS})


def rows_from_csv(path: Path) -> list[dict[str, Any]]:
    """Lees records terug uit een eerder geschreven cache-CSV."""
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            out.append({col: (raw.get(col) or None) for col in CSV_COLUMNS})
    return out


def load_or_fetch(
    cache_dir: Path,
    *,
    today: str | None = None,
    refresh: bool = False,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    """Laad de OIN-CSV uit cache als die er is, anders download en cache hem."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    stamp = today or _today()
    target = cache_dir / f"oinregister-{stamp}.csv"
    if not refresh and target.exists() and target.stat().st_size > 0:
        logger.info("OIN-register cache hit: %s", target)
        return rows_from_csv(target)
    logger.info("Download OIN-register naar %s", target)
    rows = fetch_oin_register(client=client)
    rows_to_csv(rows, target)
    return rows


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _ascii_lower(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return decomposed.encode("ascii", "ignore").decode("ascii").lower()


_ORG_NOISE = (
    "ministerie van ",
    "ministerie ",
    "gemeente ",
    "provincie ",
    "waterschap ",
    "hoogheemraadschap van ",
    "hoogheemraadschap ",
    "wetterskip ",
    "agentschap ",
)


def normalize_org_name(name: str | None) -> str:
    """Normaliseer organisatienaam voor name-matching (lowercase ASCII zonder ruis-prefix)."""
    if not name:
        return ""
    s = _ascii_lower(name)
    s = s.replace("-", " ").replace(",", " ").replace("/", " ")
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


def _is_primary(row: dict[str, Any]) -> bool:
    """Een 'primair' record heeft een TOOI-URI of een organisatietypecode.

    Sub-OINs (bv. 'Ministerie van Defensie - PARESTO') hebben die niet en
    moeten niet meedoen in de fuzzy-naam-index om valse matches te voorkomen.
    """
    return bool(row.get("tooi") or row.get("organisation_type"))


def build_oin_index(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Bouw lookup-indexen op TOOI, KvK en genormaliseerde naam.

    Returnt een dict met sub-dicts ``by_tooi``, ``by_kvk``, ``by_name``.
    Alleen records met status anders dan 'Inactief' tellen mee. Alleen primaire
    records (die een TOOI of typecode hebben) komen in ``by_name`` om botsingen
    met sub-OINs te vermijden.
    """
    by_tooi: dict[str, dict[str, Any]] = {}
    by_kvk: dict[str, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not row.get("oin"):
            continue
        if (row.get("status") or "").lower() == "inactief":
            continue
        tooi = row.get("tooi")
        if tooi:
            by_tooi.setdefault(tooi, row)
        kvk = row.get("kvk")
        if kvk:
            by_kvk.setdefault(kvk, row)
        if _is_primary(row):
            norm = normalize_org_name(row.get("name"))
            if norm:
                by_name.setdefault(norm, row)
    return {"by_tooi": by_tooi, "by_kvk": by_kvk, "by_name": by_name}


def _record_name(record: dict[str, Any]) -> str | None:
    names = record.get("names") or []
    if not names:
        return None
    first = names[0]
    if isinstance(first, dict):
        return first.get("value")
    return None


def _record_identifiers(record: dict[str, Any]) -> dict[str, Any]:
    return record.get("identifiers") or {}


def match_to_records(
    records: Iterable[dict[str, Any]],
    index: dict[str, dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any], str]]:
    """Match polder-organisatie-records aan OIN-rijen.

    Match-volgorde: TOOI > KvK > genormaliseerde naam. Records die al een
    ``identifiers.oin`` hebben worden overgeslagen.
    """
    matches: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    by_tooi = index["by_tooi"]
    by_kvk = index["by_kvk"]
    by_name = index["by_name"]
    for record in records:
        identifiers = _record_identifiers(record)
        if identifiers.get("oin"):
            continue
        row: dict[str, Any] | None = None
        method = ""
        tooi = identifiers.get("tooi")
        if tooi:
            row = by_tooi.get(tooi)
            if row is not None:
                method = "tooi"
        if row is None:
            kvk = identifiers.get("kvk")
            if kvk:
                row = by_kvk.get(str(kvk))
                if row is not None:
                    method = "kvk"
        if row is None:
            name = _record_name(record)
            norm = normalize_org_name(name)
            if norm:
                row = by_name.get(norm)
                if row is not None:
                    method = "name"
        if row is None:
            continue
        matches.append((record, row, method))
    return matches


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------


def _merge_sources(
    existing: list[dict[str, Any]] | None, new: dict[str, Any]
) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for src in existing or []:
        if isinstance(src, dict) and src.get("id"):
            by_id[src["id"]] = dict(src)
    if new.get("id"):
        by_id[new["id"]] = dict(new)
    return list(by_id.values())


def merge_oin_into_record(
    record: dict[str, Any],
    row: dict[str, Any],
    *,
    today: str | None = None,
) -> dict[str, Any]:
    """Voeg ``identifiers.oin = row['oin']`` toe en registreer de bron.

    Bestaande velden in ``identifiers`` worden niet overschreven, behalve voor
    keys die nog leeg of afwezig zijn (KvK / TOOI vullen we mee als ze in het
    OIN-register staan en lokaal ontbreken).
    """
    today_str = today or _today()
    merged = dict(record)
    identifiers = dict(merged.get("identifiers") or {})
    identifiers["oin"] = row["oin"]
    if row.get("kvk") and not identifiers.get("kvk"):
        identifiers["kvk"] = row["kvk"]
    if row.get("tooi") and not identifiers.get("tooi"):
        identifiers["tooi"] = row["tooi"]
    merged["identifiers"] = identifiers
    new_source = {
        "id": SOURCE_ID,
        "url": OIN_PUBPROXY_URL,
        "retrieved": today_str,
        "fields": ["oin"],
    }
    merged["sources"] = _merge_sources(merged.get("sources"), new_source)
    return merged


# ---------------------------------------------------------------------------
# YAML helpers
# ---------------------------------------------------------------------------


_ORG_KEY_ORDER = (
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
)


def _ordered_for_org(record: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in _ORG_KEY_ORDER:
        if key in record:
            out[key] = record[key]
    for key, value in record.items():
        if key not in out:
            out[key] = value
    return out


def _iter_yaml_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(p for p in directory.rglob("*.yaml") if p.is_file())


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


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


def enrich_organisations(
    org_root: Path,
    *,
    cache_dir: Path,
    dry_run: bool = False,
    today: str | None = None,
    rows: list[dict[str, Any]] | None = None,
    refresh: bool = False,
) -> dict[str, int]:
    """Verrijk alle organisatie-records onder ``org_root`` met OIN's.

    Returnt een dict met counts: ``rows``, ``candidates``, ``matched_tooi``,
    ``matched_kvk``, ``matched_name``, ``written``.
    """
    today_str = today or _today()
    if rows is None:
        rows = load_or_fetch(cache_dir, today=today_str, refresh=refresh)
    index = build_oin_index(rows)

    files = _iter_yaml_files(org_root)
    records: list[tuple[Path, dict[str, Any]]] = []
    for path in files:
        data = _read_yaml(path)
        if data is None:
            continue
        records.append((path, data))

    matches = match_to_records((rec for _, rec in records), index)
    by_id = {id(rec): path for path, rec in records}

    stats = {
        "rows": len(rows),
        "candidates": len(records),
        "matched_tooi": 0,
        "matched_kvk": 0,
        "matched_name": 0,
        "written": 0,
    }
    for record, row, method in matches:
        path = by_id[id(record)]
        merged = merge_oin_into_record(record, row, today=today_str)
        merged = _ordered_for_org(merged)
        if method == "tooi":
            stats["matched_tooi"] += 1
        elif method == "kvk":
            stats["matched_kvk"] += 1
        elif method == "name":
            stats["matched_name"] += 1
        if dry_run:
            logger.info(
                "DRY-RUN %s ← oin=%s (%s)", record.get("id"), row.get("oin"), method
            )
        else:
            _write_yaml(path, merged)
        stats["written"] += 1

    logger.info(
        "OIN-enrichment: %d rows, %d records, %d matches (tooi=%d, kvk=%d, name=%d)",
        stats["rows"],
        stats["candidates"],
        stats["written"],
        stats["matched_tooi"],
        stats["matched_kvk"],
        stats["matched_name"],
    )
    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="polder-fetch-logius-cor",
        description=(
            "Verrijk polder-organisatie-records met OIN's uit het Logius "
            "OIN-register (Centrale OIN Raadpleegvoorziening)."
        ),
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
        default=Path("_cache/logius"),
        help="Cache-directory voor de OIN-CSV (default: _cache/logius).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Schrijf niets, log alleen wat geschreven zou worden.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Negeer bestaande cache en haal het register opnieuw op.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Verbose logging."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    org_root: Path = args.data_root / "organisaties"
    stats = enrich_organisations(
        org_root,
        cache_dir=args.cache,
        dry_run=args.dry_run,
        refresh=args.refresh,
    )
    print(
        f"OIN-register: {stats['rows']} rows, {stats['written']}/{stats['candidates']} matched "
        f"(tooi={stats['matched_tooi']}, kvk={stats['matched_kvk']}, name={stats['matched_name']})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
