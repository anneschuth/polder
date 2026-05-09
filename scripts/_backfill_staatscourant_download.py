#!/usr/bin/env python
"""Helper voor backfill_staatscourant.sh, fase 1.

Enumereert benoemings/ontslag-KBs in de Staatscourant via de KOOP SRU API
binnen een [since, until]-venster, en download de volledige XML van elk KB
naar ``_cache/staatscourant/<jaar>/<maand>/<identifier>.xml``.

Strategie:
- CQL: ``(dt.type any "benoeming") AND (dt.title any "<terms>") AND
  (c.content-area any "stcrt") AND dt.modified>=<since> AND dt.modified<=<until>``.
- 6-maands-batches om SRU-paginering en geheugen onder controle te houden.
- Voor elk SRU-record: lees ``itemUrl manifestation="xml"``, fetch en cache.
- Idempotent: skip als de doel-XML al bestaat.

Schrijft naar stdout: een samenvatting per batch (jaar-half, count).
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx
from lxml import etree

# We mounten src/ als import-pad voor zowel uv run als directe interpreter.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from polder.fetchers import koop_sru as ks  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("backfill.download")


def parse_iso(value: str) -> date:
    return date.fromisoformat(value)


def half_year_batches(since: date, until: date) -> list[tuple[date, date]]:
    """Genereer (start, end)-tupels van 6 maanden binnen [since, until]."""
    batches: list[tuple[date, date]] = []
    cursor = date(since.year, 1 if since.month <= 6 else 7, 1)
    if cursor < since:
        cursor = since
    while cursor <= until:
        if cursor.month <= 6:
            end = date(cursor.year, 6, 30)
        else:
            end = date(cursor.year, 12, 31)
        if end > until:
            end = until
        batches.append((max(cursor, since), end))
        # Volgende halfjaar
        if cursor.month <= 6:
            cursor = date(cursor.year, 7, 1)
        else:
            cursor = date(cursor.year + 1, 1, 1)
    return batches


def build_cql(*, since: date, until: date, terms: str) -> str:
    """Bouw de CQL-query voor benoemings/ontslag-KB's in Staatscourant.

    ``terms`` is een spatie-gescheiden lijst van titel-keywords (any-match).
    """
    title_clause = f'dt.title any "{terms}"'
    return (
        f'(dt.type any "benoeming") AND ({title_clause}) AND '
        f'(c.content-area any "stcrt") AND '
        f"dt.modified>={since.isoformat()} AND dt.modified<={until.isoformat()}"
    )


def itemurl_xml(record: etree._Element) -> str | None:
    """Pak de ``itemUrl manifestation="xml"`` uit een SRU-record."""
    for elem in record.iter():
        tag = etree.QName(elem.tag).localname
        if tag != "itemUrl" or elem.text is None:
            continue
        if (elem.get("manifestation") or "").lower() == "xml":
            return elem.text.strip()
    return None


def cache_path_for(identifier: str, modified: str | None, base: Path) -> Path:
    return ks._cache_path_for(identifier, modified=modified, base=base)


def sru_path_for(identifier: str, modified: str | None, base: Path) -> Path:
    """Pad waar we het SRU-record-fragment cachen (parallel aan de full XML)."""
    target = ks._cache_path_for(identifier, modified=modified, base=base)
    return target.with_suffix(".sru.xml")


def fetch_full_xml(url: str, client: httpx.Client) -> bytes:
    """Download met exponential backoff op 429/503."""
    import time

    delay = 1.0
    for attempt in range(5):
        response = client.get(
            url,
            headers={
                "User-Agent": ks.USER_AGENT,
                "Accept": "application/xml",
            },
        )
        if response.status_code in (429, 503):
            time.sleep(delay)
            delay *= 2
            continue
        response.raise_for_status()
        return response.content
    response.raise_for_status()
    return response.content


def run_batch(
    *,
    since: date,
    until: date,
    base: Path,
    terms: str,
    client: httpx.Client,
) -> tuple[int, int, int]:
    """Voor één halfjaar: enumereer SRU, download XML, return (records, gedownload, skipped)."""
    cql = build_cql(since=since, until=until, terms=terms)
    log.info("Batch %s..%s: CQL=%s", since, until, cql)

    start_record = 1
    page_size = ks.PAGE_SIZE
    total_records = 0
    downloaded = 0
    skipped = 0

    while True:
        content = ks._searchretrieve(
            cql,
            start_record=start_record,
            max_records=page_size,
            record_schema=ks.DEFAULT_RECORD_SCHEMA,
            timeout=ks.HTTP_TIMEOUT,
            client=client,
        )
        root = etree.fromstring(content)
        records = root.findall(".//sru:record", namespaces=ks.NAMESPACES)
        if not records:
            break

        for record in records:
            total_records += 1
            meta = ks.extract_record_metadata(record)
            identifier = meta["identifier"]
            modified = meta["modified"]
            if not identifier:
                continue

            full_xml_path = cache_path_for(identifier, modified, base)
            if full_xml_path.exists() and full_xml_path.stat().st_size > 200:
                skipped += 1
                continue

            xml_url = itemurl_xml(record) or meta["url"]
            if not xml_url:
                log.debug("Record %s: geen XML-URL", identifier)
                continue

            # Schrijf eerst SRU-fragment voor traceerbaarheid.
            sru_path = sru_path_for(identifier, modified, base)
            sru_path.parent.mkdir(parents=True, exist_ok=True)
            sru_path.write_bytes(
                etree.tostring(record, pretty_print=True, encoding="utf-8")
            )

            try:
                payload = fetch_full_xml(xml_url, client)
            except httpx.HTTPError as exc:
                log.warning("Download mislukt voor %s (%s): %s", identifier, xml_url, exc)
                continue

            full_xml_path.parent.mkdir(parents=True, exist_ok=True)
            full_xml_path.write_bytes(payload)
            downloaded += 1
            # Lichte throttle om 429s te voorkomen op de repo-API.
            import time as _t
            _t.sleep(0.1)

        if len(records) < page_size:
            break
        start_record += len(records)

    log.info(
        "Batch %s..%s: records=%d, downloaded=%d, skipped=%d",
        since,
        until,
        total_records,
        downloaded,
        skipped,
    )
    return total_records, downloaded, skipped


def main() -> int:
    cache_dir = Path(os.environ.get("CACHE_DIR", "_cache/staatscourant"))
    since = parse_iso(os.environ.get("SINCE", "2009-01-01"))
    until = parse_iso(os.environ.get("UNTIL", date.today().isoformat()))
    terms = os.environ.get(
        "QUERY_TERMS",
        "Secretaris-Generaal Directeur-Generaal Inspecteur-Generaal minister staatssecretaris",
    )

    cache_dir.mkdir(parents=True, exist_ok=True)
    timeout = httpx.Timeout(ks.HTTP_TIMEOUT, connect=30.0)
    transport = httpx.HTTPTransport(retries=2)

    grand_total = 0
    grand_downloaded = 0
    grand_skipped = 0

    with httpx.Client(timeout=timeout, transport=transport, follow_redirects=True) as client:
        for batch_since, batch_until in half_year_batches(since, until):
            try:
                t, d, s = run_batch(
                    since=batch_since,
                    until=batch_until,
                    base=cache_dir,
                    terms=terms,
                    client=client,
                )
            except httpx.HTTPError as exc:
                log.error("Batch %s..%s mislukt: %s", batch_since, batch_until, exc)
                continue
            grand_total += t
            grand_downloaded += d
            grand_skipped += s

    log.info(
        "Totaal: records=%d, downloaded=%d, skipped (al gecached)=%d",
        grand_total,
        grand_downloaded,
        grand_skipped,
    )
    print(
        f"phase1_summary records={grand_total} downloaded={grand_downloaded} skipped={grand_skipped}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
