"""`polder fix-casing` command.

Trekt de casing recht van organisatienamen (`names[].value`), post-labels
(`label`) en mandaat-rollen (`mandaten[].role`). Twee lagen:

1. **First-letter** — eerste alfabetische teken naar hoofdletter, tenzij de
   string een gecureerde eigennaam/afkorting is. Zie
   ``polder.lib.casing.canonicalize_leading_case``.
2. **Interne casing-collisions** — exact dezelfde entiteit bestond in twee
   casings die óók intern verschillen (bv. "Afdeling Communicatie en
   omgevingsmanagement" vs "... Omgevingsmanagement"). Voor die gevallen is
   geen algoritme veilig; ``COLLISION_MAP`` bevat de handmatig gecureerde
   canonieke vorm per bron-string. Alleen org-names hadden zulke residuele
   collisions (post-labels en rollen vielen volledig samen na de
   first-letter-laag).

Onderhouds-tool, eenmalig over bestaande data. Geen records verwijderd:
puur in-place edit van bestaande veldwaarden (conform polder-regel "geen
records ooit verwijderen").
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
import yaml

from polder.lib.casing import canonicalize_leading_case

# Bron-getrouwe canonieke vorm voor casefold-collisions die ná de
# first-letter-laag nog intern in casing verschillen. Key = elke voorkomende
# variant (zowel oorspronkelijke bron als de tussenstaat van een eerdere
# fix-casing-run), value = de canonieke vorm. Afleidingsregel: neem de
# dominante bron-variant (frequentie, dan lengte), kapitaliseer alleen
# "Gemeenschappelijke Regeling" / "Modulaire Gemeenschappelijke Regeling"
# (de juridische instrumentnaam) en forceer "-generaal" klein ná het
# koppelteken (Nederlandse soortnaam, geen Engelse title). Inhoudswoorden
# blijven verder exact zoals de officiële bron ze schreef — geen eigen
# title-casing. Idempotent: de canon zelf is nooit een key.
COLLISION_MAP: dict[str, str] = {
    "Afdeling Communicatie en Omgevingsmanagement": "Afdeling Communicatie en omgevingsmanagement",
    "Afdeling Infrastructuur en services": "Afdeling Infrastructuur en Services",
    "Afdeling Kennis en advies": "Afdeling Kennis en Advies",
    "Bestuurlijke en Politieke zaken": "Bestuurlijke en Politieke Zaken",
    "CIO office": "CIO Office",
    "College van fractievoorzitters": "College van Fractievoorzitters",
    "Commissie buitenslands gediplomeerden volksgezondheid": "Commissie Buitenslands Gediplomeerden Volksgezondheid",
    "Commissies en Raden": "Commissies en raden",
    "Directie Ruimtelijke ontwikkeling": "Directie Ruimtelijke Ontwikkeling",
    "Directie Wetgeving en Juridische zaken": "Directie Wetgeving en Juridische Zaken",
    "Directoraat-Generaal Langdurige Zorg": "Directoraat-generaal Langdurige Zorg",
    "Directoraat-generaal Landelijk gebied en Stikstof": "Directoraat-generaal Landelijk Gebied en Stikstof",
    "Facilitaire dienst": "Facilitaire Dienst",
    "Gemeenschappelijke Regeling Afvalverwijdering Utrecht": "Gemeenschappelijke Regeling afvalverwijdering Utrecht",
    "Gemeenschappelijke Regeling Nazorg Gesloten Stortplaatsen Bavel-Dorst en Zevenbergen": "Gemeenschappelijke Regeling nazorg gesloten stortplaatsen Bavel-Dorst en Zevenbergen",
    "Gemeenschappelijke Regeling Ombudsman Metropool Amsterdam": "Gemeenschappelijke Regeling ombudsman metropool Amsterdam",
    "Gemeenschappelijke Regeling Regio Foodvalley": "Gemeenschappelijke Regeling Regio FoodValley",
    "Gemeenschappelijke Regeling Schoolverzuim en VSV Regio West-Brabant": "Gemeenschappelijke Regeling schoolverzuim en VSV regio West-Brabant",
    "Gemeenschappelijke Regeling Vixia": "Gemeenschappelijke Regeling VIXIA",
    "Gemeenschappelijke regeling Bedrijfsvoeringsorganisatie Syntrophos": "Gemeenschappelijke Regeling Bedrijfsvoeringsorganisatie Syntrophos",
    "Gemeenschappelijke regeling GGD Zaanstreek-Waterland": "Gemeenschappelijke Regeling GGD Zaanstreek-Waterland",
    "Gemeenschappelijke regeling GGD Zuid Limburg": "Gemeenschappelijke Regeling GGD Zuid Limburg",
    "Gemeenschappelijke regeling Havenschap Groningen Seaports": "Gemeenschappelijke Regeling Havenschap Groningen Seaports",
    "Gemeenschappelijke regeling IJmond Werkt!": "Gemeenschappelijke Regeling IJmond Werkt!",
    "Gemeenschappelijke regeling Regio Hart van Brabant": "Gemeenschappelijke Regeling Regio Hart van Brabant",
    "Gemeenschappelijke regeling Regionaal Archief Rivierenland": "Gemeenschappelijke Regeling Regionaal Archief Rivierenland",
    "Gemeenschappelijke regeling Reinigingsbedrijf Midden Nederland": "Gemeenschappelijke Regeling Reinigingsbedrijf Midden Nederland",
    "Gemeenschappelijke regeling Samenwerking De Bevelanden": "Gemeenschappelijke Regeling Samenwerking De Bevelanden",
    "Gemeenschappelijke regeling Samenwerking de Bevelanden": "Gemeenschappelijke Regeling Samenwerking De Bevelanden",
    "Gemeenschappelijke regeling Schoolverzuim en VSV regio West-Brabant": "Gemeenschappelijke Regeling schoolverzuim en VSV regio West-Brabant",
    "Gemeenschappelijke regeling Veiligheidsregio Gelderland-Zuid": "Gemeenschappelijke Regeling Veiligheidsregio Gelderland-Zuid",
    "Gemeenschappelijke regeling Waterschapsbedrijf Limburg": "Gemeenschappelijke Regeling Waterschapsbedrijf Limburg",
    "Gemeenschappelijke regeling Werkorganisatie Langedijk en Heerhugowaard": "Gemeenschappelijke Regeling Werkorganisatie Langedijk en Heerhugowaard",
    "Gemeenschappelijke regeling Werkplein Fivelingo": "Gemeenschappelijke Regeling Werkplein Fivelingo",
    "Gemeenschappelijke regeling Werkvoorzieningsschap Tomingroep": "Gemeenschappelijke Regeling Werkvoorzieningsschap Tomingroep",
    "Gemeenschappelijke regeling afvalverwijdering Utrecht": "Gemeenschappelijke Regeling afvalverwijdering Utrecht",
    "Gemeenschappelijke regeling bedrijfsvoeringsorganisatie Syntrophos": "Gemeenschappelijke Regeling Bedrijfsvoeringsorganisatie Syntrophos",
    "Gemeenschappelijke regeling huisvesting voortgezet onderwijs in de Liemers 2024": "Gemeenschappelijke Regeling Huisvesting Voortgezet Onderwijs in de Liemers 2024",
    "Gemeenschappelijke regeling nazorg gesloten stortplaatsen Bavel-Dorst en Zevenbergen": "Gemeenschappelijke Regeling nazorg gesloten stortplaatsen Bavel-Dorst en Zevenbergen",
    "Gemeenschappelijke regeling ombudsman metropool Amsterdam": "Gemeenschappelijke Regeling ombudsman metropool Amsterdam",
    "Gemeenschappelijke regeling recreatieschap Drenthe": "Gemeenschappelijke Regeling Recreatieschap Drenthe",
    "Gemeenschappelijke regeling schoolverzuim en VSV regio West-Brabant": "Gemeenschappelijke Regeling schoolverzuim en VSV regio West-Brabant",
    "Gemeenschappelijke regeling werkorganisatie Langedijk en Heerhugowaard": "Gemeenschappelijke Regeling Werkorganisatie Langedijk en Heerhugowaard",
    "Modulaire gemeenschappelijke regeling Rijk van Nijmegen": "Modulaire Gemeenschappelijke Regeling Rijk van Nijmegen",
    "Personeel en organisatie": "Personeel en Organisatie",
    "Plaatsvervangend Secretaris-Generaal": "Plaatsvervangend secretaris-generaal",
    "Plaatsvervangend Secretaris-generaal": "Plaatsvervangend secretaris-generaal",
    "Provinciale organisatie": "Provinciale Organisatie",
    "Secretaris-Generaal": "Secretaris-generaal",
    "Stichting Aanzet": "Stichting AanZet",
    "afdeling Business Control": "Afdeling Business Control",
    "afdeling Energie & Landbouw": "Afdeling Energie & Landbouw",
    "afdeling ICT-diensten en Voorzieningen": "Afdeling ICT-diensten en Voorzieningen",
    "afdeling Toezicht Rail en Maritiem": "Afdeling Toezicht Rail en Maritiem",
    "concerndirectie Informatievoorziening & Databeheersing": "Concerndirectie Informatievoorziening & Databeheersing",
    "directie Algemene Financiële en Economische Politiek": "Directie Algemene Financiële en Economische Politiek",
    "directie Ambtenaar en Organisatie": "Directie Ambtenaar en Organisatie",
    "directie Arbeidsmarkt en Sociaal-Economische Aangelegenheden": "Directie Arbeidsmarkt en Sociaal-Economische Aangelegenheden",
    "directie Bedrijfsvoering": "Directie Bedrijfsvoering",
    "directie Data Center Services (DCS-IV)": "Directie Data Center Services (DCS-IV)",
    "directie Europees, Internationaal en Agro-economisch beleid": "Directie Europees, Internationaal en Agro-economisch beleid",
    "directie Finance & Control": "Directie Finance & Control",
    "directie Handelstoezicht": "Directie Handelstoezicht",
    "directie Inwinning en Gegevensanalyse": "Directie Inwinning en Gegevensanalyse",
    "directie Juridische Zaken": "Directie Juridische Zaken",
    "directie Klantinteractie & Services": "Directie Klantinteractie & Services",
    "directie Particulieren": "Directie Particulieren",
    "directie Regulier Verblijf en Nederlanderschap": "Directie Regulier Verblijf en Nederlanderschap",
    "directie Slachttoezicht": "Directie Slachttoezicht",
    "directoraat-generaal Luchtvaart en Maritieme zaken": "Directoraat-generaal Luchtvaart en Maritieme Zaken",
    "divisie Forensische zorg en Justitiële jeugdinrichtingen": "Divisie Forensische Zorg en Justitiële Jeugdinrichtingen",
    "gemeenschappelijke directie Wetgeving en Juridische Zaken": "Gemeenschappelijke directie Wetgeving en Juridische Zaken",
    "gemeenschappelijke regeling WIHW": "Gemeenschappelijke Regeling WIHW",
}


def _canon(value: str) -> str:
    """COLLISION_MAP wint van de algoritmische first-letter-laag."""
    if value in COLLISION_MAP:
        return COLLISION_MAP[value]
    return canonicalize_leading_case(value)


def _fix_org(d: dict[str, Any]) -> int:
    n = 0
    for entry in d.get("names") or []:
        if not isinstance(entry, dict):
            continue
        v = entry.get("value")
        if isinstance(v, str) and v:
            new = _canon(v)
            if new != v:
                entry["value"] = new
                n += 1
    return n


def _fix_post(d: dict[str, Any]) -> int:
    v = d.get("label")
    if isinstance(v, str) and v:
        new = _canon(v)
        if new != v:
            d["label"] = new
            return 1
    return 0


def _fix_persoon(d: dict[str, Any]) -> int:
    n = 0
    for m in d.get("mandaten") or []:
        if not isinstance(m, dict):
            continue
        v = m.get("role")
        if isinstance(v, str) and v:
            new = _canon(v)
            if new != v:
                m["role"] = new
                n += 1
    return n


_FIXERS = {
    "organisaties": _fix_org,
    "posten": _fix_post,
    "personen": _fix_persoon,
}


def fix_casing(
    data_dir: Annotated[
        Path,
        typer.Option("--data", help="Pad naar data/ root."),
    ] = Path("data"),
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Toon wat zou wijzigen zonder te schrijven."),
    ] = False,
) -> None:
    """Trek casing recht van org-namen, post-labels en mandaat-rollen.

    Eerste alfabetische teken naar hoofdletter (tenzij gecureerde eigennaam),
    plus handmatig gecureerde resolutie van interne casing-collisions.
    Idempotent.
    """
    totals: dict[str, int] = {}
    n_files = 0
    for subdir, fixer in _FIXERS.items():
        base = data_dir / subdir
        if not base.exists():
            continue
        changed = 0
        for yp in sorted(base.rglob("*.yaml")):
            if "_staging" in yp.parts:
                continue
            try:
                d = yaml.safe_load(yp.read_text(encoding="utf-8"))
            except yaml.YAMLError:
                continue
            if not isinstance(d, dict):
                continue
            n = fixer(d)
            if n > 0:
                changed += n
                n_files += 1
                typer.echo(f"  {yp.relative_to(data_dir)}: {n} veld(en)")
                if not dry_run:
                    yp.write_text(
                        yaml.safe_dump(d, sort_keys=False, allow_unicode=True),
                        encoding="utf-8",
                    )
        totals[subdir] = changed

    suffix = " (dry-run)" if dry_run else ""
    typer.echo("")
    for subdir, c in totals.items():
        typer.echo(f"{subdir}: {c} veld(en) rechtgetrokken")
    typer.echo(f"Totaal {sum(totals.values())} velden in {n_files} files{suffix}.")
