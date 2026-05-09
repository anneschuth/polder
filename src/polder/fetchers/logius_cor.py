"""Fetcher voor het Logius OIN-register (COR: Centrale Organisatie-Registratie).

Bron: Logius.
Endpoints:
- CSV-download: https://oinregister.logius.nl/oinregister.csv
- REST-API:     https://portaal.digikoppeling.nl/registers/corApi/

Formaat: CSV (download) of JSON (REST).
Update: gestaag (mutaties dagelijks mogelijk, geen vaste publicatiekadans).
Licentie: open (Logius publiceert CC0-vergelijkbaar; check per release).
Dekking: OIN (Organisatie Identificatie Nummer) per overheidsorganisatie of leverancier
die op Digikoppeling is aangesloten. Niet alle ROO-organisaties hebben een OIN; matching
is best-effort op naam + KvK-nummer.

Tracking issue: https://github.com/anneschuth/polder/issues/TODO-logius-cor
"""

from __future__ import annotations

import sys
from typing import Any

import httpx

__all__ = [
    "COR_API_BASE",
    "OIN_CSV_URL",
    "fetch_oin_register",
    "main",
    "match_to_roo",
]

OIN_CSV_URL = "https://oinregister.logius.nl/oinregister.csv"
COR_API_BASE = "https://portaal.digikoppeling.nl/registers/corApi/"
HTTP_TIMEOUT = 60.0


def fetch_oin_register(*, timeout: float = HTTP_TIMEOUT) -> list[dict[str, Any]]:
    """Download de OIN-register CSV en geef rijen terug als lijst van dicts.

    TODO:
    - Bepaal exacte CSV-encoding (UTF-8 vs Latin-1) en delimiter.
    - Documenteer kolomnamen: ``oin``, ``naam``, ``kvk_nummer``, ``rsin``,
      ``vestigingsnummer``, ``hoofdvestiging``, ``soort_registratie``.
    - Bewaar ruwe CSV in `_cache/logius-oin/<date>.csv` voor traceability.
    """
    response = httpx.get(OIN_CSV_URL, timeout=timeout)
    response.raise_for_status()
    raise NotImplementedError("OIN-CSV parsing nog niet geimplementeerd.")


def match_to_roo(
    oin_records: list[dict[str, Any]],
    roo_records: list[dict[str, Any]],
) -> dict[str, str]:
    """Match OIN-records aan ROO-records.

    Returns:
        Mapping van ROO-id (bijv. ``"min-bzk"``) naar OIN-string.

    TODO:
    - Match-strategie: exact op KvK-nummer als beide records dat veld hebben,
      anders fuzzy op gestripte/genormaliseerde naam.
    - Log onmatched records aan beide kanten naar `_cache/logius-oin/unmatched.yaml`.
    - Threshold voor fuzzy match (Levenshtein of token-set ratio) documenteren.
    """
    raise NotImplementedError("OIN-naar-ROO matching nog niet geimplementeerd.")


def main() -> int:
    """CLI entrypoint. Niet geïmplementeerd."""
    # TODO:
    # 1. Download CSV via `fetch_oin_register`.
    # 2. Lees ROO-records uit `data/organisaties/`.
    # 3. Match en schrijf crosswalk naar `data/_crosswalks/oin.yaml`.
    # 4. Voeg `oin: <nummer>` toe aan ROO-records waar match betrouwbaar is.
    # Zie: https://github.com/anneschuth/polder/issues/TODO-logius-cor
    print("polder.fetchers.logius_cor: not yet implemented", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
