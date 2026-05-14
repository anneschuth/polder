"""Fetcher voor Staatscourant en Koninklijke Besluiten via KOOP SRU.

Bron: KOOP (Kennis- en Exploitatiecentrum voor Officiele Overheidspublicaties).
Endpoint: https://repository.overheid.nl/sru
Formaat: SRU 2.0 (Search/Retrieve via URL) met XML-resultaten.
Update: live (publicaties verschijnen op werkdagen).
Licentie: open (officiele overheidspublicaties).
Dekking: Staatscourant en Koninklijke Besluiten sinds 2009. Onmisbaar voor het vinden
van benoemings-KB's voor ministers, staatssecretarissen, SG's, DG's en andere
ABD-functionarissen.

ROL VAN DEZE FETCHER: lever RUWE XML aan in `_cache/staatscourant/<jaar>/<maand>/<id>.xml`.
Parsing van benoemings-KB's naar Membership-proposals doet de ``parse-staatscourant``-skill
(LLM, met two-source rule en quote-or-die). Deze fetcher schrijft NOOIT direct naar
`data/`.

Tracking issue: https://github.com/anneschuth/polder/issues/TODO-koop-sru
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections.abc import Iterable
from datetime import date
from pathlib import Path

import httpx
from lxml import etree

logger = logging.getLogger("polder.fetchers.koop_sru")

__all__ = [
    "CACHE_DIR",
    "DEFAULT_RECORD_SCHEMA",
    "SRU_ENDPOINT",
    "build_query",
    "extract_record_metadata",
    "fetch_kb_text",
    "main",
    "search",
]

SRU_ENDPOINT = "https://repository.overheid.nl/sru"
DEFAULT_RECORD_SCHEMA = "gzd"
CACHE_DIR = Path("_cache/staatscourant")
HTTP_TIMEOUT = 90.0
USER_AGENT = "polder-bot/0.1 (https://github.com/anneschuth/polder; anne.schuth@gmail.com)"
PAGE_SIZE = 100

NAMESPACES = {
    "sru": "http://docs.oasis-open.org/ns/search-ws/sruResponse",
    "gzd": "http://standaarden.overheid.nl/sru",
    "dcterms": "http://purl.org/dc/terms/",
    "overheidwetgeving": "http://standaarden.overheid.nl/wetgeving/",
    "overheidop": "http://standaarden.overheid.nl/op/terms/",
}


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def build_query(query: str, *, since: date | None = None) -> str:
    """Bouw een CQL-query op basis van een vrije zoekterm en optionele datum-ondergrens.

    De gebruiker geeft typisch een term als ``benoeming Secretaris-Generaal``. We
    plakken er een ``c.product-area==officielepublicaties`` filter aan vast en, als
    `since` is gezet, ``dt.modified>=YYYY-MM-DD``.
    """
    parts = [f"c.product-area==officielepublicaties AND ({query.strip()})"]
    if since is not None:
        parts.append(f"dt.modified>={since.isoformat()}")
    return " AND ".join(parts)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _http_get(
    url: str,
    *,
    params: dict[str, str] | None = None,
    timeout: float = HTTP_TIMEOUT,
    client: httpx.Client | None = None,
) -> httpx.Response:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/xml"}
    if client is None:
        with httpx.Client(timeout=timeout, follow_redirects=True) as inner:
            return inner.get(url, params=params, headers=headers)
    return client.get(url, params=params, headers=headers)


def _searchretrieve(
    cql: str,
    *,
    start_record: int,
    max_records: int,
    record_schema: str,
    timeout: float,
    client: httpx.Client | None,
) -> bytes:
    params = {
        "operation": "searchRetrieve",
        "version": "2.0",
        "query": cql,
        "maximumRecords": str(max_records),
        "startRecord": str(start_record),
        "recordSchema": record_schema,
    }
    response = _http_get(SRU_ENDPOINT, params=params, timeout=timeout, client=client)
    response.raise_for_status()
    return response.content


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _findtext(elem: etree._Element, xpath: str) -> str | None:
    found = elem.find(xpath, namespaces=NAMESPACES)
    if found is None or found.text is None:
        return None
    text = found.text.strip()
    return text or None


_ID_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_id(value: str) -> str:
    """Vervang slashes en andere onveilige tekens door ``-``."""
    return _ID_RE.sub("-", value).strip("-")


def extract_record_metadata(record: etree._Element) -> dict[str, str | None]:
    """Pak de minimaal nodige velden uit een SRU-record voor caching.

    Returnt dict met ``identifier``, ``url`` (eerste itemUrl met preferred-format),
    ``modified`` (ISO date) en ``date`` (bekendmaking). Velden mogen ``None`` zijn.
    """
    identifier = _findtext(record, ".//dcterms:identifier")
    modified = _findtext(record, ".//dcterms:modified")
    bekendmaakdatum = _findtext(record, ".//dcterms:date")

    # itemUrl-elementen kunnen een attribuut "manifestation" hebben (xml/html/pdf).
    item_url: str | None = None
    for cand in record.iter():
        # SRU-feeds gebruiken zowel `itemUrl` als `gzd:itemUrl`. Match op localname.
        tag = etree.QName(cand.tag).localname
        if tag != "itemUrl" or cand.text is None:
            continue
        manifestation = cand.get("manifestation") or ""
        url_text = cand.text.strip()
        if manifestation.lower() == "xml":
            item_url = url_text
            break
        if item_url is None:
            item_url = url_text

    return {
        "identifier": identifier,
        "url": item_url,
        "modified": modified,
        "date": bekendmaakdatum,
    }


# ---------------------------------------------------------------------------
# Cache write
# ---------------------------------------------------------------------------


def _cache_path_for(record_id: str, *, modified: str | None, base: Path) -> Path:
    """Bouw `<base>/<jaar>/<maand>/<id>.xml`.

    Jaar+maand komen uit ``modified`` als ISO-date beschikbaar is, anders ``unknown``.
    """
    year = "unknown"
    month = "unknown"
    if modified:
        match = re.match(r"^(\d{4})-(\d{2})", modified)
        if match:
            year = match.group(1)
            month = match.group(2)
    return base / year / month / f"{_safe_id(record_id)}.xml"


def _write_xml(path: Path, content: bytes, *, dry_run: bool) -> Path:
    if dry_run:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def search(
    query: str,
    *,
    max_records: int = 100,
    since: date | None = None,
    cache_dir: Path | None = None,
    record_schema: str = DEFAULT_RECORD_SCHEMA,
    timeout: float = HTTP_TIMEOUT,
    client: httpx.Client | None = None,
    dry_run: bool = False,
) -> list[Path]:
    """Voer een SRU-query uit, pageer door alle treffers en schrijf RUWE XML naar cache.

    Args:
        query: vrije zoekterm (CQL-fragment), bijv. ``benoeming Secretaris-Generaal``.
        max_records: harde bovengrens op het aantal documenten dat we ophalen.
        since: filter op ``dt.modified``; alleen records vanaf deze datum.
        cache_dir: doel-directory; default ``_cache/staatscourant``.
        record_schema: SRU recordSchema (``gzd`` voor metadata).
        client: optionele ``httpx.Client`` voor hergebruik / tests.
        dry_run: bereken paden maar schrijf niets.

    Returnt een lijst paden van geschreven (of, in dry-run, voorgestelde) XML-files.
    """
    base = cache_dir or CACHE_DIR
    cql = build_query(query, since=since)

    written: list[Path] = []
    start_record = 1
    page_size = min(PAGE_SIZE, max_records)

    while len(written) < max_records:
        remaining = max_records - len(written)
        page = min(page_size, remaining)
        content = _searchretrieve(
            cql,
            start_record=start_record,
            max_records=page,
            record_schema=record_schema,
            timeout=timeout,
            client=client,
        )
        root = etree.fromstring(content)
        records = root.findall(".//sru:record", namespaces=NAMESPACES)
        if not records:
            break

        for record in records:
            meta = extract_record_metadata(record)
            identifier = meta["identifier"]
            if not identifier:
                logger.debug("Sla record zonder identifier over")
                continue
            # SRU-metadata-fragment cachen onder .sru.xml suffix.
            meta_target = _cache_path_for(identifier, modified=meta["modified"], base=base)
            meta_target = meta_target.with_suffix(".sru.xml")
            record_xml = etree.tostring(record, pretty_print=True, encoding="utf-8")
            _write_xml(meta_target, record_xml, dry_run=dry_run)
            # Daarnaast: de echte besluit-XML downloaden via gzd:itemUrl.
            # Skip stcrt-files zonder gzd:itemUrl en KB-records die geen XML hebben.
            item_url = meta.get("url")
            target = _cache_path_for(identifier, modified=meta["modified"], base=base)
            if item_url and not dry_run and not target.exists():
                try:
                    response = _http_get(item_url, timeout=timeout, client=client)
                    response.raise_for_status()
                    _write_xml(target, response.content, dry_run=dry_run)
                except httpx.HTTPError as exc:
                    logger.warning("Kon besluit-XML voor %s niet ophalen: %s", identifier, exc)
            written.append(target)
            if len(written) >= max_records:
                break

        # Paginatie: als de pagina kleiner was dan gevraagd, is er niks meer.
        if len(records) < page:
            break
        start_record += len(records)

    return written


def fetch_kb_text(
    record_url: str,
    *,
    cache_dir: Path | None = None,
    timeout: float = HTTP_TIMEOUT,
    client: httpx.Client | None = None,
    dry_run: bool = False,
) -> Path:
    """Haal de volledige tekst van een KB op (XML) en cache hem.

    Args:
        record_url: directe URL naar het document op repository.overheid.nl.
        cache_dir: doel-directory; default ``_cache/staatscourant``.
    """
    base = cache_dir or CACHE_DIR
    response = _http_get(record_url, timeout=timeout, client=client)
    response.raise_for_status()

    # Identifier afleiden uit de URL: laatste path-segment zonder extensie.
    last = record_url.rstrip("/").split("/")[-1]
    identifier = re.sub(r"\.(xml|html|pdf)$", "", last, flags=re.IGNORECASE) or "kb"
    target = base / "kb" / f"{_safe_id(identifier)}.xml"
    _write_xml(target, response.content, dry_run=dry_run)
    return target


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_since(value: str) -> date:
    return date.fromisoformat(value)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="polder-fetch-koop",
        description=(
            "Haal Staatscourant- en KB-records op via de KOOP SRU API en cache "
            "ruwe XML in _cache/staatscourant/."
        ),
    )
    parser.add_argument(
        "--query",
        default="benoeming",
        help="Vrije zoekterm (CQL-fragment). Default: 'benoeming'.",
    )
    parser.add_argument(
        "--since",
        type=_parse_since,
        default=None,
        help="Ondergrens op dt.modified (ISO-date), bijv. 2026-01-01.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Max aantal documenten (default: 100).",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=CACHE_DIR,
        help=f"Cache-directory (default: {CACHE_DIR}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Voer query uit en log paden, maar schrijf niets.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging.")
    return parser


def _summarise(paths: Iterable[Path], *, dry_run: bool) -> str:
    lst = list(paths)
    suffix = " (dry-run)" if dry_run else ""
    return f"KOOP SRU: {len(lst)} XML-records gecached{suffix}"


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    try:
        paths = search(
            args.query,
            max_records=args.limit,
            since=args.since,
            cache_dir=args.cache,
            dry_run=args.dry_run,
        )
    except httpx.HTTPError as exc:
        print(f"polder-fetch-koop: HTTP-fout: {exc}", file=sys.stderr)
        return 2
    print(_summarise(paths, dry_run=args.dry_run), file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
