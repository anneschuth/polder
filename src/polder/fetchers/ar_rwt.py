"""Fetcher voor het RWT-register van de Algemene Rekenkamer.

Bron: Algemene Rekenkamer.
Endpoint: https://www.rekenkamer.nl/onderwerpen/rwt-register
Formaat: HTML (overzichtspagina) plus losse pagina's per RWT, soms een Excel-bijlage.
Update: jaarlijks (najaar, met peildatum 1 januari van het lopende jaar).
Licentie: gebruik (overheidssite, redelijk hergebruik voor open data).
Dekking: alle Rechtspersonen met een Wettelijke Taak (RWT). Veel RWT's zijn ook ZBO,
maar niet alle; de Rekenkamer-lijst is autoritatief voor de RWT-status.

Tracking issue: https://github.com/anneschuth/polder/issues/TODO-ar-rwt
"""

from __future__ import annotations

import sys
from typing import Any

import httpx

__all__ = ["RWT_REGISTER_URL", "fetch_register_index", "main", "parse_register"]

RWT_REGISTER_URL = "https://www.rekenkamer.nl/onderwerpen/rwt-register"
HTTP_TIMEOUT = 60.0
USER_AGENT = "polder/0.0.1 (https://github.com/anneschuth/polder; anne.schuth@gmail.com)"


def fetch_register_index(*, timeout: float = HTTP_TIMEOUT) -> str:
    """Haal de HTML van de RWT-registerpagina op.

    TODO:
    - Bewaar de respons in `_cache/ar-rwt/<date>.html`.
    - Detecteer of er een Excel-bijlage is (gewoonlijk eenvoudiger dan HTML-parsen)
      en download die ook.
    """
    response = httpx.get(
        RWT_REGISTER_URL,
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.text


def parse_register(html: str) -> list[dict[str, Any]]:
    """Parse de RWT-registerpagina naar een lijst RWT-records.

    TODO:
    - Voeg `beautifulsoup4` toe aan dependencies (idem als ek_scrape).
    - Velden per RWT: ``naam``, ``minister`` (verantwoordelijk vakdepartement),
      ``wettelijke_grondslag``, ``rechtsvorm``, ``website``, ``rwt_sinds``.
    - Cross-check tegen ROO: zet ``rwt: true`` op matchende organisatie-records.
    - Voor RWT's die niet in ROO staan: maak nieuw record onder
      `data/organisaties/rwt/<slug>.yaml`.
    """
    raise NotImplementedError("RWT-register parsing nog niet geimplementeerd.")


def main() -> int:
    """CLI entrypoint. Niet geïmplementeerd."""
    # TODO:
    # 1. fetch_register_index -> ruwe HTML (en/of Excel-bijlage) in _cache/.
    # 2. parse_register -> lijst dicts.
    # 3. Match tegen bestaande ROO-records; flag mismatches voor handmatige review.
    # 4. Schrijf nieuwe RWT-records of update bestaande organisatie-records.
    # Zie: https://github.com/anneschuth/polder/issues/TODO-ar-rwt
    print("polder.fetchers.ar_rwt: not yet implemented", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
