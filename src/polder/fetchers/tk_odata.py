"""Fetcher voor het Gegevensmagazijn van de Tweede Kamer (OData v4).

Bron: Tweede Kamer der Staten-Generaal.
Endpoint: https://gegevensmagazijn.tweedekamer.nl/OData/v4/2.0/
Formaat: OData v4 + Atom SyncFeed (near-realtime).
Update: near-realtime (mutaties zichtbaar binnen minuten na vergaderbesluit).
Licentie: open (publieke data, geen aparte licentie-eisen voor hergebruik).
Dekking: TK-personen (Kamerleden), fracties, fractiezetels (samenstelling fracties
over tijd), commissies, vanaf 2008-09-01.

Library: tkapi (https://github.com/openkamer/tkapi), al opgenomen in pyproject.toml.

Tracking issue: https://github.com/anneschuth/polder/issues/TODO-tk-odata
"""

from __future__ import annotations

import sys
from datetime import date
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tkapi import TKApi

__all__ = [
    "TK_DATA_START",
    "TK_ODATA_BASE",
    "fetch_fractie_zetels",
    "fetch_fractions",
    "fetch_persons",
    "fraction_to_polder_record",
    "main",
    "person_to_polder_record",
]

TK_ODATA_BASE = "https://gegevensmagazijn.tweedekamer.nl/OData/v4/2.0/"
TK_DATA_START = date(2008, 9, 1)


def fetch_persons(api: TKApi) -> list[dict[str, Any]]:
    """Haal alle TK-personen op via tkapi.

    TODO:
    - Filter op personen die ooit Kamerlid zijn geweest sinds TK_DATA_START
      (skip oud-leden van voor het OData-tijdperk).
    - Velden: ``id`` (TK persoon-id, GUID), ``achternaam``, ``voornamen``,
      ``initialen``, ``geslacht``, ``geboortedatum``, ``geboorteland``,
      ``woonplaats``, ``functie``.
    - Geboortedatum: alleen jaartal bewaren (polder schema-regel #6).
    """
    raise NotImplementedError("TK persons fetcher nog niet geimplementeerd.")


def fetch_fractions(api: TKApi) -> list[dict[str, Any]]:
    """Haal alle TK-fracties op (historisch, dus inclusief opgeheven fracties).

    TODO:
    - Velden: ``id``, ``naam_nl``, ``afkorting``, ``aantal_zetels``, ``datum_actief``,
      ``datum_inactief``.
    - Map afkorting (VVD, D66, ...) naar interne fractie-id voor stabiele references.
    """
    raise NotImplementedError("TK fractions fetcher nog niet geimplementeerd.")


def fetch_fractie_zetels(api: TKApi, since: date = TK_DATA_START) -> list[dict[str, Any]]:
    """Haal fractiezetel-bezettingen op (welk persoon zat wanneer in welke fractie).

    Args:
        since: ondergrens voor ``tot_en_met`` of ``van``-datum.

    TODO:
    - tkapi-modelnaam: ``FractieZetelPersoon`` (persoon op een fractiezetel) en
      ``FractieZetelVacature`` (vacante zetel).
    - Levert events: persoon X bezet fractiezetel Y van datum A tot datum B.
    - Output: lijst dicts met ``persoon_id``, ``fractie_id``, ``van``, ``tot``.
    """
    raise NotImplementedError("TK fractiezetels fetcher nog niet geimplementeerd.")


def person_to_polder_record(person: dict[str, Any]) -> dict[str, Any]:
    """Map een TK-persoonrecord naar een polder ``personen/<slug>.yaml`` record.

    TODO:
    - Slug: ``<voornaam>-<achternaam>`` met polder.fetchers.roo.slugify.
    - ``ids.tk_persoon_id``: TK GUID.
    - ``sources[]``: één entry met ``source: "tk-odata"`` en URL naar OData-resource.
    - ``birth_year``: jaartal-only.
    """
    raise NotImplementedError("TK persoon-mapping nog niet geimplementeerd.")


def fraction_to_polder_record(fraction: dict[str, Any]) -> dict[str, Any]:
    """Map een TK-fractierecord naar een polder ``organisaties/fracties/<slug>.yaml``.

    TODO:
    - Slug: afkorting in lowercase (vvd, d66, groenlinks-pvda).
    - ``ids.tk_fractie_id``: TK GUID.
    - ``valid_from`` / ``valid_until`` op basis van ``datum_actief`` / ``datum_inactief``.
    """
    raise NotImplementedError("TK fractie-mapping nog niet geimplementeerd.")


def main() -> int:
    """CLI entrypoint. Niet geïmplementeerd."""
    # TODO:
    # 1. Maak TKApi-instance (tkapi.TKApi(verbose=False)).
    # 2. fetch_persons -> map -> schrijf naar data/personen/.
    # 3. fetch_fractions -> map -> schrijf naar data/organisaties/fracties/.
    # 4. fetch_fractie_zetels -> map naar mandaten/posten -> schrijf naar data/mandaten/.
    # Zie: https://github.com/anneschuth/polder/issues/TODO-tk-odata
    print("polder.fetchers.tk_odata: not yet implemented", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
