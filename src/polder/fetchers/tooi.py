"""Fetcher voor het TOOI URI-stelsel (Thesauri en Ontologieen voor Overheidsinformatie).

Bron: standaarden.overheid.nl/tooi.
Endpoint: https://standaarden.overheid.nl/tooi/data/
URI-stelsel: https://identifier.overheid.nl/tooi/id/
Formaat: SKOS/RDF (Turtle, RDF/XML, JSON-LD beschikbaar via content negotiation).
Update: gestaag (geen vaste cadans, releases via standaarden.overheid.nl).
Licentie: CC0.
Dekking: stabiele URI's voor alle organisatietypes (ministerie, gemeente, provincie,
waterschap, ZBO, agentschap, gemeenschappelijke regeling, hoog college van staat,
adviescollege, openbaar lichaam, etc.) en bijbehorende attributen zoals
overheidsorganisatie-soorten en jurisdictiecodes.

Tracking issue: https://github.com/anneschuth/polder/issues/TODO-tooi
"""

from __future__ import annotations

import sys
from typing import Any

import httpx

__all__ = ["TOOI_BASE", "TOOI_IDENTIFIER_BASE", "fetch_tooi_concepts", "main"]

TOOI_BASE = "https://standaarden.overheid.nl/tooi/data/"
TOOI_IDENTIFIER_BASE = "https://identifier.overheid.nl/tooi/id/"
HTTP_TIMEOUT = 60.0


def fetch_tooi_concepts(scheme: str, *, timeout: float = HTTP_TIMEOUT) -> list[dict[str, Any]]:
    """Haal SKOS-concepten op voor een TOOI concept-scheme.

    Args:
        scheme: scheme-identifier (bijv. ``"organisatietypes"``,
            ``"gemeenten"``, ``"provincies"``).

    TODO:
    - Inventariseer beschikbare schemes via de TOOI release-index.
    - Parse Turtle/RDF-XML met rdflib (toevoegen aan dependencies).
    - Map skos:Concept-records naar dicts met velden ``uri``, ``pref_label``,
      ``alt_labels``, ``notation``, ``broader``, ``narrower``.
    - Cache-key per release-versie zodat updates idempotent zijn.
    """
    url = f"{TOOI_BASE.rstrip('/')}/{scheme}"
    headers = {"Accept": "text/turtle, application/rdf+xml;q=0.9"}
    response = httpx.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    raise NotImplementedError(
        "TOOI parsing nog niet geimplementeerd; voeg rdflib toe en parse de respons."
    )


def main() -> int:
    """CLI entrypoint. Niet geïmplementeerd."""
    # TODO:
    # 1. CLI-args: --scheme <naam> of --all.
    # 2. Schrijf naar `data/_crosswalks/tooi-<scheme>.yaml`.
    # 3. Match TOOI-URI's terug naar polder-records via naam + type.
    # Zie: https://github.com/anneschuth/polder/issues/TODO-tooi
    print("polder.fetchers.tooi: not yet implemented", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
