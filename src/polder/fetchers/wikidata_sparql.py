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

Tracking issue: https://github.com/anneschuth/polder/issues/TODO-wikidata
"""

from __future__ import annotations

import sys
from typing import Any

import httpx

__all__ = ["SPARQL_ENDPOINT", "USER_AGENT", "main", "query_sparql"]

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "polder/0.0.1 (https://github.com/anneschuth/polder; anne.schuth@gmail.com)"
HTTP_TIMEOUT = 60.0


def query_sparql(query: str, *, timeout: float = HTTP_TIMEOUT) -> list[dict[str, Any]]:
    """Voer een SPARQL-query uit en geef de bindings terug als lijst van dicts.

    TODO:
    - Retry met backoff bij 429/503 (Wikidata rate-limits zijn streng).
    - Cache responses lokaal (zie `_cache/` voor patroon van andere fetchers).
    - Map URI-bindings naar enkel de Q-id (laatste segment van het IRI).
    """
    headers = {
        "Accept": "application/sparql-results+json",
        "User-Agent": USER_AGENT,
    }
    response = httpx.get(
        SPARQL_ENDPOINT,
        params={"query": query, "format": "json"},
        headers=headers,
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    return list(payload.get("results", {}).get("bindings", []))


def main() -> int:
    """CLI entrypoint. Niet geïmplementeerd."""
    # TODO:
    # 1. CLI-args: --type {gemeente,provincie,waterschap,ministerie,tk-lid}.
    # 2. Voor elke type een ingebakken query (Q-class lookup).
    # 3. Resultaten naar `data/_crosswalks/wikidata-<type>.yaml` schrijven.
    # 4. Match op naam + (waar mogelijk) jurisdictiecode tegen ROO-records.
    # Zie: https://github.com/anneschuth/polder/issues/TODO-wikidata
    print("polder.fetchers.wikidata_sparql: not yet implemented", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
