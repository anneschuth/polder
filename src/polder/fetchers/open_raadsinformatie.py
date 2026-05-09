"""Fetcher voor Open Raadsinformatie (gemeentelijke bestuurders en raadsleden).

Bron: Open Raadsinformatie (ORI), Open State Foundation.
Endpoint: https://api.openraadsinformatie.nl/v1/elastic/
Formaat: Elasticsearch query DSL als POST-body, response in Popolo ODS-vormgeving.
Update: gestaag (afhankelijk van per-gemeente publicatiekadans, meestal weken).
Licentie: open (Open State Foundation publiceert onder open licentie).
Dekking: 265+ Nederlandse gemeenten, met wethouders, raadsleden, fracties,
commissies en agendapunten/besluiten.

Tracking issue: https://github.com/anneschuth/polder/issues/TODO-open-raadsinformatie
"""

from __future__ import annotations

import sys
from typing import Any

import httpx

__all__ = [
    "ORI_ELASTIC_BASE",
    "fetch_persons_for_gemeente",
    "main",
    "search",
]

ORI_ELASTIC_BASE = "https://api.openraadsinformatie.nl/v1/elastic"
HTTP_TIMEOUT = 60.0
USER_AGENT = "polder/0.0.1 (https://github.com/anneschuth/polder; anne.schuth@gmail.com)"


def search(
    index: str,
    body: dict[str, Any],
    *,
    timeout: float = HTTP_TIMEOUT,
) -> dict[str, Any]:
    """Voer een Elasticsearch _search uit op de ORI-API.

    Args:
        index: bijv. ``"ori_*"`` of ``"ori_persons"``.
        body: ES query DSL als dict.

    TODO:
    - Paginatie via ``search_after`` of ``from``/``size``.
    - Retry met backoff bij 5xx.
    """
    url = f"{ORI_ELASTIC_BASE}/{index}/_search"
    response = httpx.post(
        url,
        json=body,
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def fetch_persons_for_gemeente(gemeente_id: str) -> list[dict[str, Any]]:
    """Haal alle personen (raadsleden, wethouders, burgemeester) voor een gemeente op.

    Args:
        gemeente_id: Popolo organisation-id zoals ORI die hanteert,
            bijv. ``"ori_amsterdam"``.

    TODO:
    - Filter ES-query op ``has_organization.id``.
    - Map Popolo-velden naar polder-records:
      - person.name -> polder personen.<slug>.naam
      - membership.role -> polder mandaat met post-classificatie
        (raadslid, wethouder, burgemeester, commissielid).
      - membership.start_date / end_date -> valid_from / valid_until.
    - Geboortedatum: jaartal-only enforcen (polder schema-regel #6).
    - Burgemeester-mandaten cross-checken met ROO (autoritatief voor benoeming).
    """
    raise NotImplementedError("ORI persons fetcher nog niet geimplementeerd.")


def main() -> int:
    """CLI entrypoint. Niet geïmplementeerd."""
    # TODO:
    # 1. CLI-args: --gemeente <slug-of-id> of --all (let op: 265+ gemeenten = veel calls).
    # 2. Lees ROO-gemeenten als autoritatieve lijst van te scrapen gemeenten.
    # 3. fetch_persons_for_gemeente per gemeente -> map -> schrijf naar
    #    data/personen/ en data/mandaten/<gemeente>/.
    # 4. Wethouders-mandaten: vereisen post-records onder data/posten/<gemeente>/.
    # Zie: https://github.com/anneschuth/polder/issues/TODO-open-raadsinformatie
    print("polder.fetchers.open_raadsinformatie: not yet implemented", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
