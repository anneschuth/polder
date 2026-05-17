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
import email.utils
import logging
import re
import sys
import time
from collections.abc import Iterable
from datetime import UTC, date, datetime
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

# Adaptieve rate-limiting. `repository.overheid.nl` publiceert geen numeriek
# rate-limit-contract; KOOP zegt enkel "gebruik de API, geen scraping". We
# moeten ons dus aanpassen aan wat de server in de praktijk teruggeeft (429's).
# AIMD-regelaar, zelfde principe als TCP-congestiecontrole: bij een schoon
# antwoord durven we iets sneller, bij een 429 meteen fors gas terug.
RATE_MIN_DELAY = 0.2  # ondergrens tussen besluit-XML-downloads (s)
RATE_START_DELAY = 0.5  # startwaarde; klimt/zakt vanaf hier
RATE_MAX_DELAY = 10.0  # bovengrens; voorbij dit punt is de bron simpelweg traag
RATE_DECAY = 0.9  # multiplicatieve daling na een schoon antwoord
RATE_BACKOFF = 2.0  # multiplicatieve stijging na een 429
MAX_ATTEMPTS = 8  # pogingen per document voordat we het naar failures.log schrijven
MAX_SINGLE_WAIT = 60.0  # cap op één enkele wachtperiode (s)


class _AdaptiveRateLimiter:
    """Stuurt de pauze tussen besluit-XML-downloads zelf bij.

    `wait()` pauzeert het lopende tempo. `on_success()` versnelt langzaam,
    `on_throttled()` remt direct. De regelaar deelt staat over alle records
    binnen één `search()`-run, zodat aanhoudende throttling het tempo
    structureel verlaagt in plaats van per record opnieuw te ontdekken.
    """

    def __init__(self, delay: float = RATE_START_DELAY) -> None:
        self.delay = delay

    def wait(self) -> None:
        if self.delay > 0:
            time.sleep(self.delay)

    def on_success(self) -> None:
        self.delay = max(RATE_MIN_DELAY, self.delay * RATE_DECAY)

    def on_throttled(self) -> None:
        self.delay = min(RATE_MAX_DELAY, self.delay * RATE_BACKOFF)


def _parse_retry_after(value: str | None) -> float | None:
    """Lees een `Retry-After`-header: delta-seconden of een HTTP-datum.

    Retourneert het aantal seconden om te wachten, of ``None`` als de header
    ontbreekt of onleesbaar is.
    """
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    delta = (when - datetime.now(UTC)).total_seconds()
    return max(0.0, delta)


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


def _log_failure(base: Path, identifier: str, item_url: str, reason: str) -> None:
    """Schrijf een definitief mislukte download naar `<base>/failures.log`.

    Eén regel per record zodat een tweede pass de identifiers gericht kan
    herproberen in plaats van ze in de logspam te verliezen.
    """
    line = f"{datetime.now(UTC).isoformat()}\t{identifier}\t{item_url}\t{reason}\n"
    failures = base / "failures.log"
    failures.parent.mkdir(parents=True, exist_ok=True)
    with failures.open("a", encoding="utf-8") as fh:
        fh.write(line)


def _download_besluit_xml(
    item_url: str,
    target: Path,
    identifier: str,
    *,
    base: Path,
    limiter: _AdaptiveRateLimiter,
    timeout: float,
    client: httpx.Client | None,
    dry_run: bool,
) -> bool:
    """Haal één besluit-XML op, met adaptieve rate-limiting en Retry-After.

    Returnt ``True`` bij succes (XML weggeschreven), ``False`` als het document
    na ``MAX_ATTEMPTS`` pogingen niet binnen te halen was; dat geval landt in
    ``failures.log``.
    """
    last_reason = "onbekend"
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = _http_get(item_url, timeout=timeout, client=client)
        except httpx.HTTPError as exc:
            last_reason = f"http-error: {exc}"
            if attempt < MAX_ATTEMPTS - 1:
                limiter.on_throttled()
                time.sleep(min(MAX_SINGLE_WAIT, limiter.delay))
            continue

        if response.status_code == 429:
            limiter.on_throttled()
            retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            wait = retry_after if retry_after is not None else limiter.delay
            wait = min(MAX_SINGLE_WAIT, wait)
            last_reason = f"429 (Retry-After={retry_after})"
            logger.debug(
                "429 op %s, wachten %.1fs (poging %d/%d, delay=%.2f)",
                identifier,
                wait,
                attempt + 1,
                MAX_ATTEMPTS,
                limiter.delay,
            )
            time.sleep(wait)
            continue

        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
            last_reason = f"status {response.status_code}: {exc}"
            if attempt < MAX_ATTEMPTS - 1:
                limiter.on_throttled()
                time.sleep(min(MAX_SINGLE_WAIT, limiter.delay))
            continue

        _write_xml(target, response.content, dry_run=dry_run)
        limiter.on_success()
        return True

    logger.warning(
        "Kon besluit-XML voor %s niet ophalen na %d pogingen: %s",
        identifier,
        MAX_ATTEMPTS,
        last_reason,
    )
    if not dry_run:
        _log_failure(base, identifier, item_url, last_reason)
    return False


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
    limiter = _AdaptiveRateLimiter()

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
            # Skip stcrt-files zonder gzd:itemUrl en KB-records die geen XML
            # hebben. `repository.overheid.nl` rate-limit op de individuele
            # XML-downloads; _download_besluit_xml regelt het tempo adaptief
            # en honoreert Retry-After. Definitief mislukte records gaan naar
            # failures.log voor een gerichte tweede pass.
            item_url = meta.get("url")
            target = _cache_path_for(identifier, modified=meta["modified"], base=base)
            if item_url and not dry_run and not target.exists():
                _download_besluit_xml(
                    item_url,
                    target,
                    identifier,
                    base=base,
                    limiter=limiter,
                    timeout=timeout,
                    client=client,
                    dry_run=dry_run,
                )
                # Adaptieve pauze vóór het volgende record.
                limiter.wait()
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
