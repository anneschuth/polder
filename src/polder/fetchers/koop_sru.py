"""Fetcher voor Staatscourant en Koninklijke Besluiten via KOOP SRU.

Bron: KOOP (Kennis- en Exploitatiecentrum voor Officiele Overheidspublicaties).
Endpoint: https://repository.overheid.nl/sru
Formaat: SRU 2.0 (Search/Retrieve via URL) met XML-resultaten.
Update: live (publicaties verschijnen op werkdagen).
Licentie: open (officiele overheidspublicaties).
Dekking: Staatscourant en Koninklijke Besluiten sinds 2009. Onmisbaar voor het vinden
van benoemings-KB's voor ministers, staatssecretarissen, SG's, DG's en andere
ABD-functionarissen.

ROL VAN DEZE FETCHER: lever RUWE XML aan. Parsing van benoemings-KB's naar
Membership-proposals doet de ``parse-staatscourant``-skill (LLM, met two-source rule
en quote-or-die). Deze fetcher schrijft NOOIT direct naar `data/`. Output landt in
`_cache/koop-sru/` of `data/_staging/` afhankelijk van CLI-flag.

Tracking issue: https://github.com/anneschuth/polder/issues/TODO-koop-sru
"""

from __future__ import annotations

import sys

import httpx
from lxml import etree

__all__ = [
    "DEFAULT_RECORD_SCHEMA",
    "SRU_ENDPOINT",
    "fetch_kb_text",
    "main",
    "search",
]

SRU_ENDPOINT = "https://repository.overheid.nl/sru"
DEFAULT_RECORD_SCHEMA = "gzd"
HTTP_TIMEOUT = 90.0
USER_AGENT = "polder/0.0.1 (https://github.com/anneschuth/polder; anne.schuth@gmail.com)"


def search(
    query: str,
    *,
    max_records: int = 100,
    start_record: int = 1,
    record_schema: str = DEFAULT_RECORD_SCHEMA,
    timeout: float = HTTP_TIMEOUT,
) -> list[etree._Element]:
    """Voer een SRU-query uit en geef de individuele record-elementen terug.

    Args:
        query: CQL-query, bijv.
            ``c.product-area==officielepublicaties AND dt.type==Koninklijk besluit``.
        max_records: maximum per call (KOOP-limiet typisch 1000).
        start_record: 1-based offset voor paginering.
        record_schema: SRU recordSchema (``gzd`` voor metadata, ``oai_dc`` voor Dublin Core).

    TODO:
    - Paginatie-helper bouwen die via ``nextRecordPosition`` doorbladert.
    - Retry met backoff bij 5xx.
    - Records terugleveren met namespace-aware parsing zodat de skill XPath kan doen.
    """
    params = {
        "operation": "searchRetrieve",
        "version": "2.0",
        "query": query,
        "maximumRecords": str(max_records),
        "startRecord": str(start_record),
        "recordSchema": record_schema,
    }
    response = httpx.get(
        SRU_ENDPOINT,
        params=params,
        headers={"User-Agent": USER_AGENT, "Accept": "application/xml"},
        timeout=timeout,
    )
    response.raise_for_status()
    root = etree.fromstring(response.content)
    namespaces = {"sru": "http://docs.oasis-open.org/ns/search-ws/sruResponse"}
    return root.findall(".//sru:record", namespaces=namespaces)


def fetch_kb_text(url: str, *, timeout: float = HTTP_TIMEOUT) -> str:
    """Haal de volledige tekst van een KB op (XML/HTML met body).

    Args:
        url: directe URL naar het KB-document op repository.overheid.nl.

    TODO:
    - Bepaal of de KB-body in XML of HTML zit en kies parser.
    - Bewaar ruwe respons in `_cache/koop-sru/kb/<identifier>.xml`.
    - Respecteer ETag/If-Modified-Since voor incremental fetches.
    """
    response = httpx.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.text


def main() -> int:
    """CLI entrypoint. Niet geïmplementeerd."""
    # TODO:
    # 1. CLI-args: --query "<CQL>" --since YYYY-MM-DD --max-records N --out DIR.
    # 2. search() -> verzamel records -> bewaar lijstje als JSON met identifier+url.
    # 3. fetch_kb_text() per record -> ruwe XML in _cache/koop-sru/.
    # 4. Geef pad terug zodat parse-staatscourant-skill kan beginnen.
    # GEEN parsing naar Membership-proposals hier (LLM-werk, doet de skill).
    # Zie: https://github.com/anneschuth/polder/issues/TODO-koop-sru
    print("polder.fetchers.koop_sru: not yet implemented", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
