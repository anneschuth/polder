"""Casing-normalisatie voor organisatienamen, post-labels en mandaat-rollen.

Probleem: dezelfde entiteit komt in twee casings voor ("afdeling Toezicht
Rail en Maritiem" naast "Afdeling Toezicht Rail en Maritiem", "minister van
OCW" naast "Minister van OCW"). De bron-data (ROO-XML, ABD-berichten,
Staatscourant) levert wisselende casing aan en niets normaliseert het.

Regel: het eerste teken van een naam/rol/label hoort een hoofdletter te zijn,
want het eerste woord is vrijwel altijd een generieke organisatie-prefix
(afdeling, directie, directoraat-generaal, ...) of een functietitel
(directeur, afdelingshoofd, minister, staatssecretaris, ...).

Uitzondering: een korte, gecureerde set eigennamen/afkortingen die in de bron
bewust met een kleine letter beginnen. Die mogen niet "geuppercased" worden:

- ``pSG ...`` (plaatsvervangend Secretaris-Generaal) — interne hoofdletter
- ``plv. ...`` (afkorting "plaatsvervangend")
- ``het participatiebedrijf``, ``het Werkgeversservicepunt Parkstad``,
  ``het Werkvoorzieningschap Oostelijk Zuid Limburg`` — eigennamen
- ``de Noordelijke Rekenkamer``, ``de Zeeuwse Muziekschool`` — eigennamen
- ``hûs en hiem, ...`` — eigennaam
- ``euregio rijn-maas-noord`` — eigennaam
- ``provinciaal fonds nazorg gesloten stortplaatsen Zuid-Holland`` — eigennaam

Deze functie raakt alleen het eerste alfabetische teken aan; interne casing
blijft ongemoeid. Interne casing-collisions (bv. "Communicatie en
omgevingsmanagement" vs "Communicatie en Omgevingsmanagement") worden niet
hier maar via de gecureerde collision-map in ``fix_casing_cmd`` afgehandeld.
"""

from __future__ import annotations

# Exacte strings die we nooit aanraken. Gecureerd door de inventarisatie van
# alle lowercase-start unieke waarden in data/organisaties, data/posten en
# data/personen (zie fix-casing). Vergelijking is exact (case-sensitive),
# want het zijn vastgestelde eigennaam-vormen.
PROTECTED_EXACT: frozenset[str] = frozenset(
    {
        "het participatiebedrijf",
        "het Werkgeversservicepunt Parkstad",
        "het Werkvoorzieningschap Oostelijk Zuid Limburg",
        "de Noordelijke Rekenkamer",
        "de Zeeuwse Muziekschool",
        "hûs en hiem, welstandsadvisering en monumentenzorg",
        "euregio rijn-maas-noord",
        "provinciaal fonds nazorg gesloten stortplaatsen Zuid-Holland",
    }
)

# Eerste-woord-prefixes die een eigennaam/afkorting inluiden en dus met rust
# gelaten worden, ongeacht de rest van de string. Lowercase vergeleken op het
# eerste token (zonder trailing leesteken).
PROTECTED_LEADING_TOKENS: frozenset[str] = frozenset(
    {
        "psg",  # pSG Cluster — interne hoofdletter is betekenisdragend
        "plv",  # plv. Secretaris-generaal — gevestigde afkorting
    }
)


def _first_alpha_index(s: str) -> int | None:
    for i, ch in enumerate(s):
        if ch.isalpha():
            return i
    return None


def canonicalize_leading_case(value: str | None) -> str:
    """Forceer een hoofdletter op het eerste alfabetische teken, tenzij de
    string een gecureerde eigennaam/afkorting is.

    Idempotent: een al-correcte string komt ongewijzigd terug. Raakt `abbr`
    of andere velden niet aan (caller-verantwoordelijkheid). Werkt voor alle
    drie de velden (org-name, post-label, mandaat-role); de regel is
    identiek.
    """
    if not value:
        return value or ""

    if value in PROTECTED_EXACT:
        return value

    idx = _first_alpha_index(value)
    if idx is None:
        # Geen alfabetisch teken (lege/whitespace/leesteken-only): niets te
        # canonicaliseren. Moet vóór de token-split, anders crasht
        # "".split(None, 1)[0] op whitespace-only input.
        return value
    if value[idx].isupper():
        return value

    first_token = value.split(None, 1)[0].rstrip(".,:;").lower()
    if first_token in PROTECTED_LEADING_TOKENS:
        return value

    return value[:idx] + value[idx].upper() + value[idx + 1 :]
