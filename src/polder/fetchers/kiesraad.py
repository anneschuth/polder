"""Fetcher voor verkiezingsuitslagen en kandidaatlijsten van de Kiesraad.

Bron: Kiesraad, gepubliceerd via data.overheid.nl (CKAN).
Endpoint: https://data.overheid.nl (zoek op authority "Kiesraad").
Formaat: per verkiezing een dataset met CSV en/of EML-XML
(Election Markup Language, NL-profiel).
Update: per verkiezing (TK, EK, EP, PS, GR, WS-bestuur, BES-eilandsraden,
referenda).
Licentie: open (CC0 / Public Domain Mark op de meeste datasets).
Dekking: officiele uitslagen, kandidaatlijsten, samenstelling van vertegenwoordigende
organen direct na de uitslag. Per verkiezing aparte dataset; geen unified API.

Tracking issue: https://github.com/anneschuth/polder/issues/TODO-kiesraad
"""

from __future__ import annotations

import sys
from typing import Any

import httpx

__all__ = [
    "DATA_OVERHEID_API",
    "KIESRAAD_AUTHORITY",
    "fetch_kandidaatlijst",
    "list_datasets",
    "main",
]

DATA_OVERHEID_API = "https://data.overheid.nl/data/api/3/action"
KIESRAAD_AUTHORITY = "http://standaarden.overheid.nl/owms/terms/Kiesraad"
HTTP_TIMEOUT = 60.0
USER_AGENT = "polder/0.0.1 (https://github.com/anneschuth/polder; anne.schuth@gmail.com)"


def list_datasets(*, timeout: float = HTTP_TIMEOUT) -> list[dict[str, Any]]:
    """Lijst Kiesraad-datasets op data.overheid.nl via CKAN package_search.

    TODO:
    - Filter via ``fq=authority:"<KIESRAAD_AUTHORITY>"``.
    - Paginatie via ``rows`` + ``start``.
    - Velden per dataset: ``name``, ``title``, ``notes``, ``resources[]``
      (URL's naar CSV/XML).
    """
    url = f"{DATA_OVERHEID_API}/package_search"
    response = httpx.get(
        url,
        params={"fq": f'authority:"{KIESRAAD_AUTHORITY}"', "rows": "200"},
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
    )
    response.raise_for_status()
    return list(response.json().get("result", {}).get("results", []))


def fetch_kandidaatlijst(verkiezing_id: str) -> list[dict[str, Any]]:
    """Haal kandidaatlijst voor een specifieke verkiezing op.

    Args:
        verkiezing_id: dataset-name op data.overheid.nl, bijv.
            ``"verkiezingsuitslag-tweede-kamer-2023"``.

    TODO:
    - Resolve dataset -> resources -> kies EML-XML als beschikbaar (rijker dan CSV).
    - Parse EML met lxml; namespace ``urn:oasis:names:tc:evs:schema:eml``.
    - Map kandidaten naar polder-records (persoon + kandidaatschap-mandaat).
    - Onderscheid voorkeurstemmen vs lijststemmen.
    """
    raise NotImplementedError("Kiesraad kandidaatlijst-parser nog niet geimplementeerd.")


def main() -> int:
    """CLI entrypoint. Niet geïmplementeerd."""
    # TODO:
    # 1. CLI-args: --verkiezing <id> of --since YYYY voor alle verkiezingen vanaf jaar.
    # 2. list_datasets -> filter op type verkiezing en jaar.
    # 3. fetch_kandidaatlijst per verkiezing -> bewaar EML in _cache/kiesraad/.
    # 4. Map naar polder-records: kandidaten worden personen + mandaat-kandidaat,
    #    gekozenen krijgen mandaat-volksvertegenwoordiger met juiste classificatie.
    # Zie: https://github.com/anneschuth/polder/issues/TODO-kiesraad
    print("polder.fetchers.kiesraad: not yet implemented", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
