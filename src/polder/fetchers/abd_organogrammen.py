"""Fetcher voor ABD-management organogrammen op rijksoverheid.nl.

Bron: rijksoverheid.nl per ministerie.
Endpoint-template: https://www.rijksoverheid.nl/ministeries/<ministerie>/organisatie
Formaat: HTML, regelmatig met embedded PDF en/of PNG-organogrammen.
Update: onregelmatig (bij organisatiewijziging of personele mutatie).
Licentie: open (rijksoverheidsdata).
Dekking: ABD-management onder TMG (Topmanagementgroep): directeuren, plv-directeuren,
programmadirecteuren, afdelingshoofden, MT-leden, projectleiders, kwartiermakers.
TMG zelf (SG, DG, IG, NCTV-coordinator) komt primair uit benoemings-KB's via KOOP SRU.

ROL VAN DEZE FETCHER: lever RUWE pagina's (HTML + bijbehorende PDF/PNG-bestanden)
aan in `data/_staging/`. Vision-extractie van organogrammen doet de
``parse-organogram``-skill. Schrijf NOOIT direct naar `data/personen/` of
`data/posten/`.

Classification-mapping (gebruikt door de skill, zie schemas/post.schema.json):
- ``abd-tmg``: SG, DG, IG, hoofd NCTV, hoofd AIVD/MIVD (TMG-functies; meestal niet
  uit organogram maar uit benoemings-KB).
- ``abd-directeur``: directeur of plv-directeur.
- ``abd-afdelingshoofd``: afdelingshoofd of MT-lid op vergelijkbaar niveau.
- ``abd-projectleider``: projectleider of kwartiermaker met ABD-status.

Tracking issue: https://github.com/anneschuth/polder/issues/TODO-abd-organogrammen
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import httpx

__all__ = [
    "MINISTERIES",
    "RIJKSOVERHEID_BASE",
    "STAGING_DIR",
    "discover_organogram_assets",
    "fetch_organisatie_pagina",
    "main",
]

RIJKSOVERHEID_BASE = "https://www.rijksoverheid.nl"
STAGING_DIR = Path("data/_staging/abd-organogrammen")
HTTP_TIMEOUT = 60.0
USER_AGENT = "polder/0.0.1 (https://github.com/anneschuth/polder; anne.schuth@gmail.com)"

# Slugs zoals rijksoverheid.nl ze gebruikt onder /ministeries/.
# TODO: synchroniseer dynamisch met ROO-records voor ministeries i.p.v. hardcoded.
MINISTERIES: tuple[str, ...] = (
    "algemene-zaken",
    "asiel-en-migratie",
    "binnenlandse-zaken-en-koninkrijksrelaties",
    "buitenlandse-zaken",
    "defensie",
    "economische-zaken",
    "financien",
    "infrastructuur-en-waterstaat",
    "justitie-en-veiligheid",
    "klimaat-en-groene-groei",
    "landbouw-visserij-voedselzekerheid-en-natuur",
    "onderwijs-cultuur-en-wetenschap",
    "sociale-zaken-en-werkgelegenheid",
    "volksgezondheid-welzijn-en-sport",
    "volkshuisvesting-en-ruimtelijke-ordening",
)


def fetch_organisatie_pagina(ministerie_slug: str, *, timeout: float = HTTP_TIMEOUT) -> str:
    """Haal de organisatie-pagina van een ministerie op.

    TODO:
    - Bewaar in `data/_staging/abd-organogrammen/<slug>/page-<date>.html`.
    - Volg ETag/Last-Modified voor incremental fetches.
    """
    url = f"{RIJKSOVERHEID_BASE}/ministeries/{ministerie_slug}/organisatie"
    response = httpx.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.text


def discover_organogram_assets(html: str, *, base_url: str) -> list[dict[str, Any]]:
    """Vind organogram-bestanden (PDF/PNG/JPG) op een organisatie-pagina.

    Returns:
        Lijst dicts met ``url``, ``content_type_hint`` (``pdf``/``image``),
        ``alt_text`` en ``link_text`` zodat de skill weet welk bestand wat is.

    TODO:
    - Voeg `beautifulsoup4` toe aan dependencies.
    - Heuristiek: zoek <a href> met "organogram" in tekst of href, plus <img> met
      "organogram" in alt of src.
    - Resolve relatieve URL's via base_url.
    """
    raise NotImplementedError("Organogram-asset discovery nog niet geimplementeerd.")


def main() -> int:
    """CLI entrypoint. Niet geïmplementeerd."""
    # TODO:
    # 1. Per ministerie: fetch_organisatie_pagina -> HTML in _staging/.
    # 2. discover_organogram_assets -> download PDF/PNG naar _staging/<slug>/assets/.
    # 3. Schrijf manifest.yaml per ministerie met alle assets en hun metadata.
    # 4. parse-organogram-skill (vision LLM) leest manifest en extraheert posts/personen.
    # GEEN directe writes naar data/personen/ of data/posten/ hier.
    # Zie: https://github.com/anneschuth/polder/issues/TODO-abd-organogrammen
    print("polder.fetchers.abd_organogrammen: not yet implemented", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
