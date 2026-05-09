"""Fetcher voor Eerste-Kamer-leden via HTML scrape van eerstekamer.nl.

Bron: Eerste Kamer der Staten-Generaal.
Endpoint: https://www.eerstekamer.nl/leden/
Formaat: HTML (server-rendered, redelijk consistente structuur).
Update: bij iedere verkiezing (vierjaarlijks) en tussentijdse mutaties.
Licentie: open (publieke webpagina; redelijk gebruik voor open data).
Dekking: alle huidige Eerste-Kamerleden, hun fracties en commissielidmaatschappen.
Voor historische EK-leden levert deze pagina geen volledige reeks; dat doet KOOP SRU
voor benoemingen-KB's.

Tracking issue: https://github.com/anneschuth/polder/issues/TODO-ek-scrape
"""

from __future__ import annotations

import sys
from typing import Any

import httpx

__all__ = [
    "EK_BASE",
    "EK_LEDEN_URL",
    "fetch_leden_index",
    "main",
    "parse_lid_pagina",
]

EK_BASE = "https://www.eerstekamer.nl"
EK_LEDEN_URL = f"{EK_BASE}/leden"
HTTP_TIMEOUT = 60.0
USER_AGENT = "polder/0.0.1 (https://github.com/anneschuth/polder; anne.schuth@gmail.com)"


def fetch_leden_index(*, timeout: float = HTTP_TIMEOUT) -> str:
    """Haal de HTML van de leden-overzichtspagina op.

    TODO:
    - Bewaar de respons in `_cache/eerstekamer/leden-<date>.html` voor traceability.
    - Respecteer een eventuele robots.txt-vertraging.
    """
    response = httpx.get(
        EK_LEDEN_URL,
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.text


def parse_lid_pagina(html: str) -> dict[str, Any]:
    """Parse een individuele EK-lid-pagina naar dict.

    TODO:
    - Voeg `beautifulsoup4` toe aan `pyproject.toml` (parser keuze: ``lxml`` als backend).
    - Velden: ``naam``, ``fractie``, ``geboortedatum`` (jaartal-only enforcen),
      ``commissies`` (lijst), ``nevenfuncties`` (lijst), ``portretfoto_url``.
    - Map naar polder-record: persoon + mandaat ek-lid + commissielidmaatschappen.
    """
    raise NotImplementedError("EK-lid HTML-parser nog niet geimplementeerd.")


def main() -> int:
    """CLI entrypoint. Niet geïmplementeerd."""
    # TODO:
    # 1. Voeg `beautifulsoup4>=4.12` toe aan pyproject.toml dependencies.
    # 2. fetch_leden_index -> extract individuele lid-URL's.
    # 3. Per lid: fetch -> parse_lid_pagina -> map naar polder-records.
    # 4. Schrijf personen onder data/personen/, mandaten onder data/mandaten/.
    # Zie: https://github.com/anneschuth/polder/issues/TODO-ek-scrape
    print("polder.fetchers.ek_scrape: not yet implemented", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
