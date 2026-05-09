"""Fetcher voor het TOOI URI-stelsel (Thesauri en Ontologieen voor Overheidsinformatie).

Bron: standaarden.overheid.nl/tooi.
Endpoint: https://standaarden.overheid.nl/tooi/waardelijsten/ (HTML-index)
Bulk download base: https://repository.officiele-overheidspublicaties.nl/waardelijsten/
URI-stelsel: https://identifier.overheid.nl/tooi/id/
Formaat: SKOS in RDF/XML (Turtle en JSON-LD ook beschikbaar via content negotiation).
Update: gestaag (geen vaste cadans, releases via standaarden.overheid.nl).
Licentie: CC0.
Dekking: stabiele URI's voor alle organisatietypes (ministerie, gemeente, provincie,
waterschap, ZBO, agentschap, gemeenschappelijke regeling, hoog college van staat,
adviescollege, openbaar lichaam, etc.) en bijbehorende attributen zoals
overheidsorganisatie-soorten en jurisdictiecodes.

URL-patroon per waardelijst:
    {DOWNLOAD_BASE}/{set}/{expressie}/rdf/{set}_{expressie}.rdf

Voor de organisatie-thesauri heet ``set`` ``rwc_<scheme>_compleet`` (RWC: Register
Waardelijsten Compleet). De expression-versie is een numerieke teller, default
de laatste die we kennen; ``--expression`` om over te rulen.

ROL VAN DEZE FETCHER:

1. download het ruwe SKOS/RDF-document per scheme naar
   `_cache/tooi/<scheme>.rdf`, en parse het naar een dict met SKOS-concepten zodat
   crosswalk-skills kunnen lookuppen op naam of URI.
2. met ``--apply-history`` is TOOI de canonical bron voor de naamhistorie en
   opvolgings-keten van Nederlandse overheidsorganisaties: bestaande
   polder-records krijgen `successor_id`/`predecessor_id` en `valid_until`
   gevuld, en nieuwe historische records worden aangemaakt voor
   organisaties die al opgeheven zijn voor er een polder-record bestond.

TOOI-datamodel (na inspectie van de echte RDF):

* Een live ministerie heeft een record met type `tooiont:Ministerie`,
  een `rdfs:label`, `tooiont:officieleNaamInclSoort`/`ExclSoort`,
  `tooiont:afkorting` en optioneel `tooiont:begindatum`.
* Een opgeheven ministerie heeft daarbovenop `tooiont:einddatum`
  en `prov:invalidatedAtTime`. Dit is een aparte URI met eigen
  ministeriecode (bv. `mnre1040` = oude EZ).
* Een hernoemde periode van een nog levend ministerie staat als
  `tooiont:HistorischeVersie` + `tooiont:Ministerie`, met
  `prov:specializationOf` naar het levende concept en
  `tooiont:einddatumHV` voor het einde van de naamperiode.
* Wijzigings-events zijn aparte concepten (`wzg_*`) met types
  `Samenvoeging`, `Afsplitsing`, `Oprichting`, `Opheffing`,
  `Toestandswijziging` en `Uitbreiding`. Elk gebruikt
  `prov:generated` (output-org) en `prov:invalidated`/`prov:used`
  (input-orgs) plus `tooiont:tijdstipWijziging`.

Tracking issue: https://github.com/anneschuth/issues/TODO-tooi
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import yaml
from lxml import etree

logger = logging.getLogger("polder.fetchers.tooi")

__all__ = [
    "CACHE_DIR",
    "DEFAULT_SCHEMES",
    "DOWNLOAD_BASE",
    "TOOI_BASE",
    "TOOI_IDENTIFIER_BASE",
    "apply_history_to_records",
    "fetch_organisations_with_history",
    "fetch_tooi_concepts",
    "main",
    "parse_history_rdf",
    "parse_skos_rdf",
]

TOOI_BASE = "https://standaarden.overheid.nl/tooi/waardelijsten/"
TOOI_IDENTIFIER_BASE = "https://identifier.overheid.nl/tooi/id/"
DOWNLOAD_BASE = "https://repository.officiele-overheidspublicaties.nl/waardelijsten"
CACHE_DIR = Path("_cache/tooi")
HTTP_TIMEOUT = 60.0
USER_AGENT = "polder-bot/0.1 (https://github.com/anneschuth/polder; anne.schuth@gmail.com)"

# Bekende waardelijsten met (set-naam, default expressie-versie). De versie kan
# via --expression overrulet worden. Up-to-date per 2026-05.
DEFAULT_SCHEMES: dict[str, tuple[str, int]] = {
    "ministeries": ("rwc_ministeries_compleet", 6),
    "gemeenten": ("rwc_gemeenten_compleet", 6),
    "provincies": ("rwc_provincies_compleet", 6),
    "waterschappen": ("rwc_waterschappen_compleet", 6),
    "zbo": ("rwc_zbo_compleet", 6),
    "samenwerkingsorganisaties": ("rwc_samenwerkingsorganisaties_compleet", 6),
    "caribische-openbare-lichamen": ("rwc_caribische_openbare_lichamen_compleet", 6),
    "overige-overheidsorganisaties": ("rwc_overige_overheidsorganisaties_compleet", 6),
}

# Mapping van scheme-naam naar (org-type-string, sub-folder onder data/organisaties).
SCHEME_LAYOUT: dict[str, tuple[str, str, str]] = {
    # scheme -> (type-enum, output sub-folder, slug-prefix na 'org:')
    "ministeries": ("ministerie", "ministeries", "min"),
    "provincies": ("provincie", "provincies", "prov"),
    "waterschappen": ("waterschap", "waterschappen", "ws"),
    "gemeenten": ("gemeente", "gemeenten", "gem"),
    "zbo": ("zbo", "zbo", "zbo"),
}

NAMESPACES = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "tooiont": "https://identifier.overheid.nl/tooi/def/ont/",
    "prov": "http://www.w3.org/ns/prov#",
}

# Resource-types in tooiont/def/ont die we als concept beschouwen. We mappen
# de generieke "een organisatie" types — geen RegisterwaardelijstCompleet en
# geen HistorischeVersie als enig type.
_TOOI_CONCEPT_TYPES = {
    "Ministerie",
    "Gemeente",
    "Provincie",
    "Waterschap",
    "Zbo",
    "Samenwerkingsorganisatie",
    "OverigeOverheidsorganisatie",
    "CaribischOpenbaarLichaam",
    "Organisatie",
}

# Wijzigings-event types in TOOI.
_TOOI_EVENT_TYPES = {
    "Samenvoeging",  # merger: prov:invalidated meerdere -> prov:generated 1
    "Afsplitsing",  # split: prov:used 1 -> prov:generated 1
    "Oprichting",  # creation from scratch: prov:generated 1, geen invalidated
    "Opheffing",  # dissolution: prov:invalidated 1, geen generated
    "Toestandswijziging",  # naamswijziging: prov:used (live) + prov:invalidated (HV)
    "Uitbreiding",  # extension/transition variant
}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _scheme_url(scheme: str, *, expression: int | None = None) -> str:
    """Bouw de RDF/XML-download-URL voor een scheme.

    ``scheme`` mag een korte naam zijn uit ``DEFAULT_SCHEMES`` (zoals
    ``ministeries``) of de volledige set-naam (zoals
    ``rwc_ministeries_compleet``). ``expression`` overschrijft de defaultversie.
    """
    if scheme in DEFAULT_SCHEMES:
        set_name, default_expr = DEFAULT_SCHEMES[scheme]
    else:
        # Onbekende scheme: behandel input als de set-naam zelf.
        set_name = scheme
        default_expr = 1
    expr = expression if expression is not None else default_expr
    return f"{DOWNLOAD_BASE}/{set_name}/{expr}/rdf/{set_name}_{expr}.rdf"


def _http_get(
    url: str,
    *,
    timeout: float = HTTP_TIMEOUT,
    client: httpx.Client | None = None,
) -> httpx.Response:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/rdf+xml, text/turtle;q=0.9, */*;q=0.5",
    }
    if client is None:
        with httpx.Client(timeout=timeout, follow_redirects=True) as inner:
            return inner.get(url, headers=headers)
    return client.get(url, headers=headers)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _qname(tag: str) -> str:
    """``{http://...}localname`` → ``localname``."""
    return etree.QName(tag).localname


def _attr(elem: etree._Element, ns_key: str, name: str) -> str | None:
    return elem.get(f"{{{NAMESPACES[ns_key]}}}{name}")


def _local_value(elem: etree._Element) -> str | None:
    text = (elem.text or "").strip()
    return text or None


def _types_of(elem: etree._Element) -> list[str]:
    """Geef de localnames van alle rdf:type-resources van een element."""
    rdf_ns = NAMESPACES["rdf"]
    types: list[str] = []
    for type_elem in elem.findall(f"{{{rdf_ns}}}type"):
        resource = type_elem.get(f"{{{rdf_ns}}}resource") or ""
        if not resource:
            continue
        # Pak alles na de laatste / of #.
        local = re.split(r"[#/]", resource)[-1]
        if local:
            types.append(local)
    return types


def parse_skos_rdf(content: bytes) -> list[dict[str, Any]]:
    """Parse een TOOI RDF/XML document en geef een lijst concept-dicts terug.

    Werkt met twee dialecten:

    1. SKOS-stijl: ``skos:Concept`` met ``skos:prefLabel`` en ``skos:altLabel``.
    2. TOOI-eigen ontologie: ``rdf:Description`` met ``rdf:type tooiont:Ministerie``
       (of Gemeente, Provincie, ...), ``rdfs:label``, ``tooiont:afkorting``,
       ``tooiont:organisatiecode``.

    Velden per concept:
    - ``uri``: rdf:about
    - ``pref_label``: voorkeursnaam (skos:prefLabel of rdfs:label)
    - ``alt_labels``: lijst skos:altLabel + tooiont:afkorting
    - ``notation``: skos:notation of tooiont:organisatiecode
    - ``in_scheme``: rdf:resource van skos:inScheme (None bij TOOI-stijl)
    - ``broader``: lijst rdf:resource van skos:broader
    - ``types``: lijst rdf:type localnames (handig voor filtering)

    Historische versies (``HistorischeVersie``) worden overgeslagen tenzij ze
    geen ander concept-type hebben (dan komen ze er als historisch concept in).
    """
    root = etree.fromstring(content)
    concepts: list[dict[str, Any]] = []
    skos_ns = NAMESPACES["skos"]
    rdfs_ns = NAMESPACES["rdfs"]
    tooiont_ns = NAMESPACES["tooiont"]
    rdf_root_ns = NAMESPACES["rdf"]

    for elem in root.iter():
        if not isinstance(elem.tag, str):
            continue
        local = _qname(elem.tag)
        is_skos_concept = local == "Concept" and elem.tag.startswith(f"{{{skos_ns}}}")
        is_description = local == "Description" and elem.tag.startswith(f"{{{rdf_root_ns}}}")
        if not (is_skos_concept or is_description):
            continue

        types = _types_of(elem)
        # Een Description is alleen een concept als hij een herkenbaar type heeft.
        if is_description:
            tooi_match = any(t in _TOOI_CONCEPT_TYPES for t in types)
            skos_match = any(t == "Concept" for t in types)
            if not (tooi_match or skos_match):
                continue
            # Sla pure HistorischeVersie zonder ander concept-type over: dat is
            # een legacy-record dat de skill niet als levend concept moet zien.
            if not tooi_match and not skos_match and "HistorischeVersie" in types:
                continue

        uri = _attr(elem, "rdf", "about")
        if not uri:
            continue

        pref_label: str | None = None
        alt_labels: list[str] = []
        notation: str | None = None
        in_scheme: str | None = None
        broader: list[str] = []

        for child in elem:
            child_local = _qname(child.tag)
            ns_uri = etree.QName(child.tag).namespace or ""
            value = _local_value(child)

            if ns_uri == skos_ns and child_local == "prefLabel":
                lang = child.get("{http://www.w3.org/XML/1998/namespace}lang")
                if value and (pref_label is None or lang == "nl"):
                    pref_label = value
            elif ns_uri == skos_ns and child_local == "altLabel":
                if value:
                    alt_labels.append(value)
            elif ns_uri == skos_ns and child_local == "notation":
                notation = value or notation
            elif ns_uri == skos_ns and child_local == "inScheme":
                in_scheme = _attr(child, "rdf", "resource") or in_scheme
            elif ns_uri == skos_ns and child_local == "broader":
                resource = _attr(child, "rdf", "resource")
                if resource:
                    broader.append(resource)
            elif ns_uri == rdfs_ns and child_local == "label" and value:
                # rdfs:label is de canonieke naam in de TOOI-dialect; alleen invullen
                # als skos:prefLabel niet al is gezet.
                if pref_label is None:
                    pref_label = value
            elif ns_uri == tooiont_ns and child_local == "afkorting" and value:
                alt_labels.append(value)
            elif ns_uri == tooiont_ns and child_local == "organisatiecode" and value:
                if notation is None:
                    notation = value

        concepts.append(
            {
                "uri": uri,
                "pref_label": pref_label,
                "alt_labels": alt_labels,
                "notation": notation,
                "in_scheme": in_scheme,
                "broader": broader,
                "types": types,
            }
        )
    return concepts


# ---------------------------------------------------------------------------
# Historie-parsing: levende organisaties + opvolgings-keten
# ---------------------------------------------------------------------------


@dataclass
class TooiOrg:
    """TOOI-organisatie of HistorischeVersie."""

    uri: str
    types: list[str]
    label: str | None = None
    naam_excl_soort: str | None = None
    naam_incl_soort: str | None = None
    afkorting: str | None = None
    organisatiecode: str | None = None
    begin_datum: str | None = None
    eind_datum: str | None = None
    specialization_of: str | None = None  # alleen op HV: huidige (levende) URI
    is_historische_versie: bool = False

    def is_concept(self) -> bool:
        return any(t in _TOOI_CONCEPT_TYPES for t in self.types)


@dataclass
class TooiEvent:
    """Wijzigings-event tussen TOOI-organisaties."""

    uri: str
    event_type: str  # Samenvoeging / Afsplitsing / Oprichting / Opheffing / ...
    generated: list[str] = field(default_factory=list)
    invalidated: list[str] = field(default_factory=list)
    used: list[str] = field(default_factory=list)
    tijdstip: str | None = None  # ISO date


def _date_from_datetime(value: str | None) -> str | None:
    """Pak het YYYY-MM-DD-deel van een ISO datetime."""
    if not value:
        return None
    return value[:10]


def parse_history_rdf(content: bytes) -> tuple[list[TooiOrg], list[TooiEvent]]:
    """Parse organisaties (incl. HV) en wijzigings-events uit een TOOI-RDF.

    Returnt twee lijsten:

    * ``orgs``: alle ``rdf:Description``-blokken met een organisatie-type.
      Inclusief ``HistorischeVersie`` (die heeft ``specialization_of`` gezet).
    * ``events``: alle wijzigings-events (``Samenvoeging``, ``Afsplitsing``,
      ``Oprichting``, ``Opheffing``, ``Toestandswijziging``, ``Uitbreiding``).
    """
    root = etree.fromstring(content)
    orgs: list[TooiOrg] = []
    events: list[TooiEvent] = []

    rdf_ns = NAMESPACES["rdf"]
    rdfs_ns = NAMESPACES["rdfs"]
    tooiont_ns = NAMESPACES["tooiont"]
    prov_ns = NAMESPACES["prov"]

    for elem in root.iter():
        # Sla XML-comments en processing-instructions over.
        if not isinstance(elem.tag, str):
            continue
        local = _qname(elem.tag)
        if not (local == "Description" and elem.tag.startswith(f"{{{rdf_ns}}}")):
            continue
        types = _types_of(elem)
        uri = _attr(elem, "rdf", "about")
        if not uri:
            continue

        is_event = any(t in _TOOI_EVENT_TYPES for t in types)
        is_concept = any(t in _TOOI_CONCEPT_TYPES for t in types)
        is_hv = "HistorischeVersie" in types
        if not (is_event or is_concept or is_hv):
            continue

        if is_event:
            # Pak het meest specifieke event-type (eerste match in de set).
            event_type = next((t for t in types if t in _TOOI_EVENT_TYPES), "Wijziging")
            ev = TooiEvent(uri=uri, event_type=event_type)
            for child in elem:
                ns_uri = etree.QName(child.tag).namespace or ""
                child_local = _qname(child.tag)
                if ns_uri == prov_ns and child_local == "generated":
                    res = _attr(child, "rdf", "resource")
                    if res:
                        ev.generated.append(res)
                elif ns_uri == prov_ns and child_local == "invalidated":
                    res = _attr(child, "rdf", "resource")
                    if res:
                        ev.invalidated.append(res)
                elif ns_uri == prov_ns and child_local == "used":
                    res = _attr(child, "rdf", "resource")
                    if res:
                        ev.used.append(res)
                elif ns_uri == tooiont_ns and child_local == "tijdstipWijziging":
                    ev.tijdstip = _date_from_datetime(_local_value(child))
            events.append(ev)
            continue

        # Organisatie of HistorischeVersie.
        org = TooiOrg(uri=uri, types=types, is_historische_versie=is_hv)
        for child in elem:
            ns_uri = etree.QName(child.tag).namespace or ""
            child_local = _qname(child.tag)
            value = _local_value(child)
            if ns_uri == rdfs_ns and child_local == "label" and value:
                org.label = value
            elif ns_uri == tooiont_ns and child_local == "officieleNaamExclSoort" and value:
                org.naam_excl_soort = value
            elif ns_uri == tooiont_ns and child_local == "officieleNaamInclSoort" and value:
                org.naam_incl_soort = value
            elif ns_uri == tooiont_ns and child_local == "afkorting" and value:
                org.afkorting = value
            elif ns_uri == tooiont_ns and child_local == "organisatiecode" and value:
                org.organisatiecode = value
            elif ns_uri == tooiont_ns and child_local == "begindatum" and value:
                org.begin_datum = value
            elif ns_uri == tooiont_ns and child_local == "einddatum" and value:
                org.eind_datum = value
            elif ns_uri == tooiont_ns and child_local == "einddatumHV" and value:
                org.eind_datum = value
            elif ns_uri == prov_ns and child_local == "specializationOf":
                res = _attr(child, "rdf", "resource")
                if res:
                    org.specialization_of = res
        orgs.append(org)

    return orgs, events


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def fetch_tooi_concepts(
    scheme: str = "ministeries",
    *,
    expression: int | None = None,
    cache_dir: Path | None = None,
    timeout: float = HTTP_TIMEOUT,
    client: httpx.Client | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Download een TOOI-scheme en parse de SKOS-concepten.

    Returnt een dict met ``scheme``, ``url``, ``cache_path`` en ``concepts``.
    Bij ``dry_run=True`` wordt geen bestand geschreven; de XML wordt wel opgehaald
    en gepoold gelaten zodat de aanroeper paden kan inspecteren zonder side-effects.
    """
    base = cache_dir or CACHE_DIR
    url = _scheme_url(scheme, expression=expression)
    safe = _SAFE_RE.sub("-", scheme).strip("-") or "scheme"
    target = base / f"{safe}.rdf"

    response = _http_get(url, timeout=timeout, client=client)
    response.raise_for_status()
    content = response.content

    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    try:
        concepts = parse_skos_rdf(content)
    except etree.XMLSyntaxError as exc:
        logger.warning("TOOI-XML voor %s niet parsebaar: %s", scheme, exc)
        concepts = []

    return {
        "scheme": scheme,
        "url": url,
        "cache_path": target,
        "concepts": concepts,
    }


def fetch_organisations_with_history(
    scheme: str = "ministeries",
    *,
    expression: int | None = None,
    cache_dir: Path | None = None,
    timeout: float = HTTP_TIMEOUT,
    client: httpx.Client | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Download een TOOI-scheme en parse organisaties + wijzigings-events.

    Returnt een dict met ``scheme``, ``url``, ``cache_path``, ``orgs`` en
    ``events``. ``orgs`` zijn :class:`TooiOrg`-instances (incl. historische
    versies); ``events`` zijn :class:`TooiEvent`-instances.
    """
    base = cache_dir or CACHE_DIR
    url = _scheme_url(scheme, expression=expression)
    safe = _SAFE_RE.sub("-", scheme).strip("-") or "scheme"
    target = base / f"{safe}.rdf"

    response = _http_get(url, timeout=timeout, client=client)
    response.raise_for_status()
    content = response.content

    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    try:
        orgs, events = parse_history_rdf(content)
    except etree.XMLSyntaxError as exc:
        logger.warning("TOOI-XML voor %s niet parsebaar: %s", scheme, exc)
        orgs, events = [], []

    return {
        "scheme": scheme,
        "url": url,
        "cache_path": target,
        "orgs": orgs,
        "events": events,
    }


# ---------------------------------------------------------------------------
# Apply naar data/organisaties
# ---------------------------------------------------------------------------


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    """Maak een URL-veilige slug van een naam (zonder soort)."""
    norm = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(c for c in norm if not unicodedata.combining(c))
    lowered = ascii_only.lower().replace("&", " en ")
    return _SLUG_RE.sub("-", lowered).strip("-")


def _concept_url(uri: str) -> str:
    """Bouw een mensvriendelijke detail-URL voor een TOOI-concept."""
    return uri  # de identifier-URI is zelf de canonical URL.


def _ordered_for_dump(record: dict[str, Any]) -> dict[str, Any]:
    """Zet velden in een leesbare volgorde voor YAML-output."""
    order = [
        "id",
        "type",
        "identifiers",
        "classification",
        "parent_id",
        "names",
        "contact",
        "valid_from",
        "valid_until",
        "successor_id",
        "predecessor_id",
        "sources",
    ]
    out: dict[str, Any] = {}
    for key in order:
        if key in record:
            out[key] = record[key]
    for key, value in record.items():
        if key not in out:
            out[key] = value
    return out


def _index_existing_records(
    out_dir: Path, *, sub_folder: str | None = None, org_type: str | None = None
) -> dict[str, Path]:
    """Index polder-records op TOOI-URI.

    Beperk tot bestanden onder ``out_dir/<sub_folder>/`` en records met
    ``type == org_type`` om verkeerd-toegekende TOOI-URIs in andere folders
    (bijv. organisatieonderdelen die per ongeluk naar een ministerie-URI
    wijzen) niet aan te raken.
    """
    index: dict[str, Path] = {}
    if not out_dir.exists():
        return index
    if sub_folder:
        scan_dir = out_dir / sub_folder
        if not scan_dir.exists():
            return index
        iterator = scan_dir.rglob("*.yaml")
    else:
        iterator = out_dir.rglob("*.yaml")
    for path in iterator:
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except (yaml.YAMLError, OSError):
            continue
        if org_type and data.get("type") != org_type:
            continue
        ident = (data.get("identifiers") or {}).get("tooi")
        if isinstance(ident, str) and ident:
            index[ident] = path
    return index


def _today_iso() -> str:
    return date.today().isoformat()


def _build_existing_update(
    org: TooiOrg,
    *,
    successor_slug: str | None,
    predecessor_slugs: list[str],
    today: str,
) -> dict[str, Any]:
    """Patch-velden voor een bestaand polder-record."""
    patch: dict[str, Any] = {}
    if successor_slug:
        patch["successor_id"] = successor_slug
    if predecessor_slugs:
        patch["predecessor_id"] = sorted(set(predecessor_slugs))
    if org.eind_datum:
        patch["valid_until"] = org.eind_datum
    if org.begin_datum:
        patch["valid_from_tooi"] = org.begin_datum  # marker voor merge-logica
    patch["_tooi_uri"] = org.uri
    patch["_tooi_today"] = today
    return patch


def _merge_existing(existing: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Voeg TOOI-velden toe aan een bestaand record. TOOI wint voor de
    successor/predecessor-keten en `valid_until`. `valid_from` blijft staan
    tenzij die ontbreekt."""
    merged = dict(existing)
    if "successor_id" in patch:
        merged["successor_id"] = patch["successor_id"]
    if "predecessor_id" in patch:
        existing_pred = existing.get("predecessor_id") or []
        union = sorted(set(existing_pred) | set(patch["predecessor_id"]))
        merged["predecessor_id"] = union
    if "valid_until" in patch and not existing.get("valid_until"):
        merged["valid_until"] = patch["valid_until"]
    if patch.get("valid_from_tooi") and not existing.get("valid_from"):
        merged["valid_from"] = patch["valid_from_tooi"]
    # TOOI-source bijwerken (dedupe op id).
    today = patch.get("_tooi_today") or _today_iso()
    tooi_uri = patch.get("_tooi_uri")
    sources = list(existing.get("sources") or [])
    found = False
    for src in sources:
        if src.get("id") == "tooi":
            src["url"] = tooi_uri or src.get("url")
            src["retrieved"] = today
            found = True
            break
    if not found and tooi_uri:
        sources.append({"id": "tooi", "url": tooi_uri, "retrieved": today})
    merged["sources"] = sources
    return merged


def _is_close_match(existing: dict[str, Any], patch: dict[str, Any]) -> bool:
    """True als het bestaande record al alle TOOI-velden bevat (idempotentie)."""
    if "successor_id" in patch and existing.get("successor_id") != patch["successor_id"]:
        return False
    if "predecessor_id" in patch:
        existing_pred = set(existing.get("predecessor_id") or [])
        if existing_pred != set(patch["predecessor_id"]):
            return False
    if "valid_until" in patch and not existing.get("valid_until"):
        return False
    # Source moet al `tooi` met huidige URI bevatten.
    sources = existing.get("sources") or []
    has_tooi = any(
        s.get("id") == "tooi" and s.get("url") == patch.get("_tooi_uri") for s in sources
    )
    if not has_tooi:
        return False
    return True


def _build_historic_record(
    org: TooiOrg,
    *,
    org_type: str,
    slug_prefix: str,
    successor_slug: str | None,
    predecessor_slugs: list[str],
    today: str,
    used_slugs: set[str],
) -> tuple[str, dict[str, Any]]:
    """Bouw een nieuw polder-record voor een historische TOOI-organisatie.

    Returnt ``(slug, record_dict)``. Slug is uniek binnen ``used_slugs``.
    """
    name = org.naam_excl_soort or org.label or org.uri
    base_slug = _slugify(name)
    slug = base_slug or "onbekend"
    candidate = slug
    n = 2
    while f"{slug_prefix}-{candidate}" in used_slugs:
        candidate = f"{base_slug}-{n}"
        n += 1
    slug = candidate
    full_id = f"org:{slug_prefix}-{slug}"

    # Best-effort begin- en einddatum.
    valid_from = org.begin_datum or "1900-01-01"
    valid_until = org.eind_datum

    name_value = org.naam_excl_soort or org.label or name
    name_entry: dict[str, Any] = {
        "value": name_value,
        "valid_from": valid_from,
    }
    if org.afkorting:
        name_entry["abbr"] = org.afkorting
    if valid_until:
        name_entry["valid_until"] = valid_until

    record: dict[str, Any] = {
        "id": full_id,
        "type": org_type,
        "identifiers": {"tooi": org.uri},
        "classification": org_type,
        "parent_id": None,
        "names": [name_entry],
        "valid_from": valid_from,
        "valid_until": valid_until,
        "sources": [
            {"id": "tooi", "url": org.uri, "retrieved": today},
        ],
    }
    if successor_slug:
        record["successor_id"] = successor_slug
    if predecessor_slugs:
        record["predecessor_id"] = sorted(set(predecessor_slugs))
    return slug, record


def apply_history_to_records(
    *,
    orgs: list[TooiOrg],
    events: list[TooiEvent],
    out_dir: Path,
    scheme: str,
    today: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Map TOOI-historie op bestaande polder-records en maak nieuwe records aan.

    * Bestaande records (gematched op ``identifiers.tooi``) krijgen
      ``successor_id``/``predecessor_id`` en, bij ontbreken, ``valid_until``.
    * Levende ministeries hebben TOOI als bron toegevoegd.
    * Voor TOOI-organisaties zonder polder-record en met ``eind_datum``
      (oude, opgeheven ministeries — geen ``HistorischeVersie``) wordt
      een nieuw record aangemaakt onder ``out_dir/<sub>/<slug>.yaml``.

    Historische versies (``hv_*``) worden NIET als nieuwe records
    weggeschreven — die representeren een naamsperiode binnen een levende
    organisatie, en zouden een toekomstige uitbreiding van ``names[]``
    rechtvaardigen, niet een eigen record.
    """
    if scheme not in SCHEME_LAYOUT:
        return {
            "updated_existing": 0,
            "created_historic": 0,
            "predecessor_links": 0,
            "successor_links": 0,
            "skipped": len(orgs),
            "reason": f"scheme {scheme!r} not in SCHEME_LAYOUT",
        }
    org_type, sub_folder, slug_prefix = SCHEME_LAYOUT[scheme]
    today_iso = today or _today_iso()
    sub_dir = out_dir / sub_folder
    by_uri: dict[str, TooiOrg] = {o.uri: o for o in orgs}
    existing_by_uri = _index_existing_records(out_dir, sub_folder=sub_folder, org_type=org_type)

    # Stap 1: bouw successor- en predecessor-mapping uit events.
    #
    # Semantiek per event-type:
    #   - Samenvoeging: prov:invalidated orgs eindigen -> prov:generated org.
    #     Beide kanten van de relatie leggen we vast.
    #   - Afsplitsing: prov:used org blijft bestaan; prov:generated is een
    #     afsplitsing met die ene voorganger. Alleen predecessor-link op de
    #     nieuwe org. GEEN successor op de bron-org (die loopt door).
    #   - Oprichting: prov:generated, geen voorganger. Niets te linken.
    #   - Opheffing: prov:invalidated org eindigt zonder opvolger.
    #   - Toestandswijziging: prov:used (live) + prov:invalidated (HV).
    #     Pure naamswijziging op één URI; HV's krijgen we via specializationOf.
    #     Geen URI-keten te leggen.
    #   - Uitbreiding: variant op Toestandswijziging; geen URI-keten.
    succ_of: dict[str, str] = {}
    pred_of: dict[str, list[str]] = {}
    for ev in events:
        if ev.event_type in {"Toestandswijziging", "Uitbreiding", "Oprichting", "Opheffing"}:
            continue
        if ev.event_type == "Samenvoeging":
            sources = ev.invalidated
            targets = ev.generated
            for src in sources:
                if src in by_uri and not by_uri[src].is_historische_versie and targets:
                    succ_of.setdefault(src, targets[0])
            for tgt in targets:
                if tgt in by_uri and not by_uri[tgt].is_historische_versie:
                    preds = [
                        s for s in sources if s in by_uri and not by_uri[s].is_historische_versie
                    ]
                    if preds:
                        pred_of.setdefault(tgt, []).extend(preds)
        elif ev.event_type == "Afsplitsing":
            # prov:used is de levende bron, prov:generated is de afsplitsing.
            sources = ev.used
            targets = ev.generated
            for tgt in targets:
                if tgt in by_uri and not by_uri[tgt].is_historische_versie:
                    preds = [
                        s for s in sources if s in by_uri and not by_uri[s].is_historische_versie
                    ]
                    if preds:
                        pred_of.setdefault(tgt, []).extend(preds)

    # Stap 2: bepaal slug per URI. Bestaande record? gebruik z'n id.
    # Anders bouwen we hieronder een nieuwe slug. We doen dit in twee passes
    # zodat predecessor/successor-IDs naar de uiteindelijke slug verwijzen.
    slug_for_uri: dict[str, str] = {}
    used_slugs: set[str] = set()
    for uri, path in existing_by_uri.items():
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except (yaml.YAMLError, OSError):
            continue
        oid = data.get("id")
        if isinstance(oid, str) and oid.startswith("org:"):
            slug_for_uri[uri] = oid
            # bewaar het slug-deel voor uniqueness
            stripped = oid[len("org:") :]
            used_slugs.add(stripped)

    # Plan nieuwe records voor TOOI-orgs zonder polder-match.
    new_records: list[tuple[Path, dict[str, Any]]] = []
    for org in orgs:
        if org.is_historische_versie:
            continue
        if org.uri in existing_by_uri:
            continue
        # Maak alleen records voor opgeheven organisaties (eind_datum gezet).
        # Levende organisaties zonder polder-record zijn een ander probleem.
        if not org.eind_datum:
            continue
        # Reserveer eerst de slug zodat we hem kunnen referentieren.
        name = org.naam_excl_soort or org.label or org.uri
        base_slug = _slugify(name) or "onbekend"
        candidate = base_slug
        n = 2
        while f"{slug_prefix}-{candidate}" in used_slugs:
            candidate = f"{base_slug}-{n}"
            n += 1
        used_slugs.add(f"{slug_prefix}-{candidate}")
        full_id = f"org:{slug_prefix}-{candidate}"
        slug_for_uri[org.uri] = full_id

    # Stap 3: schrijf updates voor bestaande records en bouw nieuwe records.
    updated_existing = 0
    successor_links = 0
    predecessor_links = 0
    created_historic = 0
    written_paths: list[Path] = []

    for org in orgs:
        if org.is_historische_versie:
            continue
        succ_uri = succ_of.get(org.uri)
        pred_uris = pred_of.get(org.uri, [])
        succ_slug = slug_for_uri.get(succ_uri) if succ_uri else None
        pred_slugs = [slug_for_uri[u] for u in pred_uris if u in slug_for_uri]

        if org.uri in existing_by_uri:
            path = existing_by_uri[org.uri]
            try:
                with path.open("r", encoding="utf-8") as fh:
                    existing = yaml.safe_load(fh) or {}
            except (yaml.YAMLError, OSError):
                continue
            patch = _build_existing_update(
                org,
                successor_slug=succ_slug,
                predecessor_slugs=pred_slugs,
                today=today_iso,
            )
            if _is_close_match(existing, patch):
                continue
            merged = _merge_existing(existing, patch)
            merged = _ordered_for_dump(merged)
            if dry_run:
                logger.info("DRY-RUN zou updaten: %s", path)
            else:
                with path.open("w", encoding="utf-8") as fh:
                    yaml.safe_dump(
                        merged,
                        fh,
                        sort_keys=False,
                        allow_unicode=True,
                        default_flow_style=False,
                    )
                written_paths.append(path)
            updated_existing += 1
            if succ_slug:
                successor_links += 1
            if pred_slugs:
                predecessor_links += len(pred_slugs)
            continue

        # Geen match: nieuwe historische record (alleen als opgeheven).
        if not org.eind_datum:
            continue
        full_id = slug_for_uri.get(org.uri)
        if not full_id:
            # gebeurt niet als slug-allocatie hierboven goed liep
            continue
        slug = full_id[len(f"org:{slug_prefix}-") :]
        valid_from = org.begin_datum or "1900-01-01"
        name_value = org.naam_excl_soort or org.label or org.uri
        name_entry: dict[str, Any] = {
            "value": name_value,
            "valid_from": valid_from,
        }
        if org.afkorting:
            name_entry["abbr"] = org.afkorting
        if org.eind_datum:
            name_entry["valid_until"] = org.eind_datum
        record: dict[str, Any] = {
            "id": full_id,
            "type": org_type,
            "identifiers": {"tooi": org.uri},
            "classification": org_type,
            "parent_id": None,
            "names": [name_entry],
            "valid_from": valid_from,
            "valid_until": org.eind_datum,
            "sources": [{"id": "tooi", "url": org.uri, "retrieved": today_iso}],
        }
        if succ_slug:
            record["successor_id"] = succ_slug
            successor_links += 1
        if pred_slugs:
            record["predecessor_id"] = sorted(set(pred_slugs))
            predecessor_links += len(pred_slugs)
        target = sub_dir / f"{slug}.yaml"
        record = _ordered_for_dump(record)
        new_records.append((target, record))
        created_historic += 1

    if not dry_run:
        for target, record in new_records:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("w", encoding="utf-8") as fh:
                yaml.safe_dump(
                    record,
                    fh,
                    sort_keys=False,
                    allow_unicode=True,
                    default_flow_style=False,
                )
            written_paths.append(target)

    return {
        "updated_existing": updated_existing,
        "created_historic": created_historic,
        "predecessor_links": predecessor_links,
        "successor_links": successor_links,
        "written": [str(p) for p in written_paths],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="polder-fetch-tooi",
        description=(
            "Download SKOS/RDF van een TOOI concept-scheme naar _cache/tooi/. "
            "Met --apply-history wordt de naamhistorie en opvolgings-keten "
            "in data/organisaties bijgewerkt."
        ),
    )
    parser.add_argument(
        "--scheme",
        default="ministeries",
        help=(
            "Scheme-naam (bijv. ministeries, gemeenten, provincies, waterschappen, "
            "zbo). Default: ministeries."
        ),
    )
    parser.add_argument(
        "--expression",
        type=int,
        default=None,
        help="Forceer een specifieke expression-versie (default: laatste bekende).",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=CACHE_DIR,
        help=f"Cache-directory (default: {CACHE_DIR}).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data/organisaties"),
        help="Output-directory voor --apply-history (default: data/organisaties).",
    )
    parser.add_argument(
        "--apply-history",
        action="store_true",
        help=(
            "Pas TOOI-historie toe op data/organisaties: vul successor_id/"
            "predecessor_id, valid_until en maak historische records aan voor "
            "opgeheven organisaties."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max records (currently advisory; --apply-history verwerkt alles).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Haal de RDF op maar schrijf niets naar disk.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # Cache-directory: respect bestaande conventie waarbij --cache uit de top-level
    # CLI ``_cache`` doorgeeft; dan willen we de subfolder ``tooi`` gebruiken.
    cache_dir = args.cache
    if cache_dir.name != "tooi":
        cache_dir = cache_dir / "tooi"

    if args.apply_history:
        try:
            result = fetch_organisations_with_history(
                args.scheme,
                expression=args.expression,
                cache_dir=cache_dir,
                dry_run=False,  # we willen de RDF cachen
            )
        except httpx.HTTPError as exc:
            print(f"polder-fetch-tooi: HTTP-fout: {exc}", file=sys.stderr)
            return 2
        summary = apply_history_to_records(
            orgs=result["orgs"],
            events=result["events"],
            out_dir=args.out,
            scheme=args.scheme,
            dry_run=args.dry_run,
        )
        print(
            f"TOOI {args.scheme}: {len(result['orgs'])} orgs, "
            f"{len(result['events'])} events. "
            f"Bijgewerkt: {summary['updated_existing']}, "
            f"nieuw historisch: {summary['created_historic']}, "
            f"successor-links: {summary['successor_links']}, "
            f"predecessor-links: {summary['predecessor_links']}.",
            file=sys.stderr,
        )
        return 0

    try:
        result = fetch_tooi_concepts(
            args.scheme,
            expression=args.expression,
            cache_dir=cache_dir,
            dry_run=args.dry_run,
        )
    except httpx.HTTPError as exc:
        print(f"polder-fetch-tooi: HTTP-fout: {exc}", file=sys.stderr)
        return 2
    suffix = " (dry-run)" if args.dry_run else ""
    print(
        f"TOOI {result['scheme']}: {len(result['concepts'])} concepten "
        f"uit {result['url']} -> {result['cache_path']}{suffix}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
