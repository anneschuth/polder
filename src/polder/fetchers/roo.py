"""Fetcher voor het Register Overheidsorganisaties (ROO).

Download de dagelijkse `exportOO.xml`, parseert de organisatie-records en schrijft
ze als YAML onder `data/organisaties/<sub-folder>/<slug>.yaml`.

Bron: https://organisaties.overheid.nl/archive/exportOO.xml (CC0). Het bestand
wordt dagelijks opnieuw gegenereerd door KOOP en bevat alle organisaties uit ROO,
het GR-register en de Woo-index. Per categorie zijn er ook losse bestanden
beschikbaar onder `https://organisaties.overheid.nl/archive/exportOO_<categorie>.xml`
(bijvoorbeeld `exportOO_gemeenten.xml`, `exportOO_ministeries.xml`).

Per-organisatie-API loopt via TOOI-URI, zie
https://standaarden.overheid.nl/tooi/doc/tooi-registers/. Sinds juli 2025 wordt
ROO ook ontsloten via het Federatief Datastelsel (FDS); die endpoint is hier
nog niet ingebouwd.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import re
import sys
import unicodedata
from collections.abc import Iterable, Iterator
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import yaml
from lxml import etree

from polder.lib.casing import canonicalize_leading_case

logger = logging.getLogger("polder.fetchers.roo")

ROO_PUBLIC_BASE = "https://organisaties.overheid.nl"
PRIMARY_URL = f"{ROO_PUBLIC_BASE}/archive/exportOO.xml"
# Categorie-specifieke fallback (zelfde host, kleinere payload). Wordt alleen
# gebruikt als de volledige export onbereikbaar is; sinds dit een echte URL is
# (ipv de oude api-organisaties.overheid.nl die 404 gaf) levert hij ook XML.
FALLBACK_URL = f"{ROO_PUBLIC_BASE}/archive/exportOO_ministeries.xml"
SOURCE_ID = "roo"

# ROO's `<startDatum>` is de aanmaakdatum van het legale entity-record,
# niet de validity-datum van de huidige naam (EZK/IenW kregen daardoor
# 2010-10-14 terwijl die namen pas in 2017 ontstonden). Als er geen
# betrouwbare bron is gebruiken we deze sentinel; Wikidata's P571 of een
# handmatige correctie vult de echte datum. `merge_yaml` overschrijft een
# bestaande betere waarde NOOIT met deze sentinel.
SENTINEL_VALID_FROM = "1900-01-01"


def roo_org_url(roo_id: str | int | None, slug: str | None) -> str:
    """Bouw een resolvende ROO-organisatie-URL.

    De ROO-website eist een non-empty path-segment ná de roo_id. `/<roo_id>/`
    zonder suffix geeft 404. Het maakt niet uit wát het suffix is — alleen
    de roo_id telt voor de server (verifieerbaar: `curl /21849/x` → 200).
    Polder gebruikt z'n eigen slug als suffix omdat die altijd
    `[a-z0-9-]+` is en stabiel over runs.

    Geen roo_id: fallback naar de export-URL.
    """
    if not roo_id:
        return PRIMARY_URL
    return f"{ROO_PUBLIC_BASE}/{roo_id}/{slug or 'x'}"


HTTP_TIMEOUT = 60.0

# ROO-type (lowercase, gestripped) → (interne type-enum, sub-folder, slug-prefix).
TYPE_MAP: dict[str, tuple[str, str, str]] = {
    "ministerie": ("ministerie", "ministeries", "min"),
    "agentschap": ("agentschap", "agentschappen", "agentschap"),
    "zelfstandig bestuursorgaan": ("zbo", "zbo", "zbo"),
    "zbo": ("zbo", "zbo", "zbo"),
    "rwt": ("rwt", "rwt", "rwt"),
    "rechtspersoon met een wettelijke taak": ("rwt", "rwt", "rwt"),
    "hoog college van staat": ("hoge-college", "hoge-colleges", "hoge-college"),
    "gemeente": ("gemeente", "gemeenten", "gemeente"),
    "provincie": ("provincie", "provincies", "prov"),
    "waterschap": ("waterschap", "waterschappen", "waterschap"),
    "gemeenschappelijke regeling": (
        "gemeenschappelijke-regeling",
        "gemeenschappelijke-regelingen",
        "gr",
    ),
    "adviescollege": ("adviescollege", "adviescolleges", "adviescollege"),
    "openbaar lichaam bes": (
        "caribisch-openbaar-lichaam",
        "caribisch-nederland",
        "bes",
    ),
    "openbaar lichaam": (
        "caribisch-openbaar-lichaam",
        "caribisch-nederland",
        "bes",
    ),
    "inspectie": ("inspectie", "inspecties", "inspectie"),
    "rechterlijke instantie": (
        "rechterlijke-instantie",
        "rechterlijke-macht",
        "rechtbank",
    ),
    "rechtbank": ("rechterlijke-instantie", "rechterlijke-macht", "rechtbank"),
    "gerechtshof": ("rechterlijke-instantie", "rechterlijke-macht", "hof"),
    "openbaar ministerie": ("openbaar-ministerie", "politie-om", "om"),
    "politie": ("politie", "politie-om", "politie"),
    "rechtspraak": ("rechterlijke-instantie", "rechterlijke-macht", "rechtbank"),
    "regionaal samenwerkingsorgaan": (
        "gemeenschappelijke-regeling",
        "gemeenschappelijke-regelingen",
        "gr",
    ),
    "grensoverschrijdend regionaal samenwerkingsorgaan": (
        "gemeenschappelijke-regeling",
        "gemeenschappelijke-regelingen",
        "gr",
    ),
    "grensoverschrijdende gemeenschappelijke regeling": (
        "gemeenschappelijke-regeling",
        "gemeenschappelijke-regelingen",
        "gr",
    ),
    "landelijk dekkende samenwerkingen": (
        "gemeenschappelijke-regeling",
        "gemeenschappelijke-regelingen",
        "gr",
    ),
    "openbaar lichaam voor beroep en bedrijf": ("zbo", "zbo", "pbo"),
    "provinciale rekenkamer": ("hoge-college", "hoge-colleges", "rekenkamer"),
    "kabinet van de koning": ("hoge-college", "hoge-colleges", "kabinet"),
    "interdepartementale commissie": ("adviescollege", "adviescolleges", "commissie"),
    "externe commissie": ("adviescollege", "adviescolleges", "commissie"),
    "koepelorganisatie": ("zbo", "zbo", "koepel"),
    "brandweer": ("gemeenschappelijke-regeling", "gemeenschappelijke-regelingen", "brandweer"),
    "bestuur": ("zbo", "zbo", "bestuur"),
    "management": ("zbo", "zbo", "management"),
    # "Organisatie met overheidsbemoeienis" is een vergaarbak voor stichtingen,
    # verenigingen, BV's onder overheidsinvloed. Modelleren als "rwt"-achtig.
    "organisatie met overheidsbemoeienis": ("rwt", "rwt", "oovb"),
    "overheidsstichting of -vereniging": ("rwt", "rwt", "stichting"),
    # Directies, divisies, afdelingen en bureaus binnen ministeries, agentschappen
    # of ZBO's. Modelleren als top-level org-record met `parent_id` naar de
    # enclosing organisatie. Zie issue #24.
    "organisatieonderdeel": (
        "organisatieonderdeel",
        "organisatieonderdelen",
        "onderdeel",
    ),
}


# ---------------------------------------------------------------------------
# Slug helpers
# ---------------------------------------------------------------------------


def slugify(name: str) -> str:
    """Lowercase ASCII slug. Vervangt accenten, drukt non-[a-z0-9-] weg."""
    if not name:
        return ""
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower()
    # Vervang & door 'en'.
    lowered = lowered.replace("&", " en ")
    # Spaces en underscores → hyphens.
    hyphenated = re.sub(r"[\s_]+", "-", lowered)
    # Strip alles wat niet [a-z0-9-] is.
    cleaned = re.sub(r"[^a-z0-9-]+", "", hyphenated)
    # Collapse herhaalde hyphens en strip aan de randen.
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned


def roo_type_to_internal(roo_type: str | None) -> tuple[str, str, str] | None:
    """Map een ROO-type-string op (interne_type, sub_folder, slug_prefix).

    Substring-fallback (bv. `"zelfstandig bestuursorgaan (ZBO)"`) probeert
    de langste TYPE_MAP-key het eerst, zodat `"rechterlijke instantie"`
    wint van `"rechtspraak"` als beide matchen — onafhankelijk van
    dict-iteratie-volgorde.
    """
    if not roo_type:
        return None
    key = roo_type.strip().lower()
    if key in TYPE_MAP:
        return TYPE_MAP[key]
    for known in sorted(TYPE_MAP, key=len, reverse=True):
        if known in key:
            return TYPE_MAP[known]
    return None


def build_id(prefix: str, slug: str) -> str:
    """Combineer prefix en slug zonder dubbele prefix."""
    if not slug:
        return f"org:{prefix}"
    if slug == prefix or slug.startswith(f"{prefix}-"):
        return f"org:{slug}"
    return f"org:{prefix}-{slug}"


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


def _today() -> str:
    return date.today().isoformat()


def download_export(cache_dir: Path, *, today: str | None = None) -> Path:
    """Download de ROO-XML naar `cache_dir`. Returnt het pad."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    stamp = today or _today()
    target = cache_dir / f"roo-export-{stamp}.xml"
    if target.exists() and target.stat().st_size > 0:
        logger.info("ROO-export al gecached: %s", target)
        return target

    logger.info("Download ROO-export van %s", PRIMARY_URL)
    degraded = False
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            response = client.get(PRIMARY_URL)
            response.raise_for_status()
            payload = response.content
    except httpx.HTTPError as exc:
        # FALLBACK_URL is `exportOO_ministeries.xml` — een SUBSET (alleen
        # ministeries). Een run hierop is gedegradeerd: ~95% van de
        # organisaties ontbreekt. Luid loggen zodat downstream NOOIT een
        # "organisatie verdwenen"-conclusie op deze data baseert.
        logger.error(
            "Primaire ROO-download faalde (%s); val terug op ministeries-only "
            "subset %s. DIT IS EEN GEDEGRADEERDE RUN — niet alle organisaties "
            "aanwezig, geen deletes/diff-conclusies op baseren.",
            exc,
            FALLBACK_URL,
        )
        degraded = True
        with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            response = client.get(FALLBACK_URL)
            response.raise_for_status()
            payload = response.content

    # Atomic write: schrijf naar .tmp + os.replace zodat een gekild proces
    # geen half-geschreven cache-file achterlaat die `target.exists() and
    # st_size > 0` permanent als "geldig" beschouwt.
    import os
    import tempfile

    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(cache_dir))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise

    digest = hashlib.sha256(payload).hexdigest()[:12]
    logger.info(
        "ROO-export geschreven naar %s (sha256:%s)%s",
        target,
        digest,
        " [GEDEGRADEERD: ministeries-only]" if degraded else "",
    )
    return target


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------


def _localname(tag: str) -> str:
    """Strip XML-namespace van een tag."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _text(node: etree._Element | None) -> str | None:
    if node is None:
        return None
    if node.text is None:
        return None
    value = node.text.strip()
    return value or None


def _findtext(node: etree._Element, *names: str) -> str | None:
    """Vind de eerste matchende child (case-insensitive op localname)."""
    targets = {n.lower() for n in names}
    for child in node.iter():
        if _localname(child.tag).lower() in targets:
            value = _text(child)
            if value:
                return value
    return None


def _attr_systeemid(node: etree._Element) -> str | None:
    """Geef de waarde van het `systeemId`-attribuut (in iedere namespace)."""
    for key, value in node.attrib.items():
        if _localname(key).lower() == "systeemid" and value:
            return value
    return None


def _attr_tooi(node: etree._Element) -> str | None:
    """Geef de waarde van het `resourceIdentifierTOOI`-attribuut (any namespace).

    De ROO-export zet de TOOI-URI als attribute op `<organisatie>` zelf, bv.
    `p:resourceIdentifierTOOI="https://identifier.overheid.nl/tooi/id/oorg/oorg12350"`.
    """
    for key, value in node.attrib.items():
        if _localname(key).lower() == "resourceidentifiertooi" and value:
            return value.strip() or None
    return None


def _attr_owms(node: etree._Element) -> str | None:
    for key, value in node.attrib.items():
        if _localname(key).lower() == "resourceidentifierowms" and value:
            return value.strip() or None
    return None


def _attr_by_localname(node: etree._Element, localname: str) -> str | None:
    target = localname.lower()
    for key, value in node.attrib.items():
        if _localname(key).lower() == target and value:
            return value.strip() or None
    return None


def _direct_children(node: etree._Element, name: str) -> list[etree._Element]:
    """Yield direct children met deze localname (case-insensitive)."""
    target = name.lower()
    return [c for c in node if _localname(c.tag).lower() == target]


def _direct_child(node: etree._Element, name: str) -> etree._Element | None:
    children = _direct_children(node, name)
    return children[0] if children else None


def _direct_text(node: etree._Element | None, name: str) -> str | None:
    """Text van een direct child (geen deep iter, voorkomt cross-contamination
    tussen genest <organisatie>-blokken en hun parent)."""
    if node is None:
        return None
    child = _direct_child(node, name)
    return _text(child)


def _enclosing_organisatie(node: etree._Element) -> etree._Element | None:
    """Wandel omhoog tot de eerstvolgende `<organisatie>`-ancestor en geef die."""
    candidates = {"organisatie", "organization", "overheidsorganisatie"}
    parent = node.getparent()
    while parent is not None:
        if _localname(parent.tag).lower() in candidates:
            return parent
        parent = parent.getparent()
    return None


def _iter_organisatie_nodes(root: etree._Element) -> Iterator[etree._Element]:
    """Yield alle organisatie-achtige nodes onder root."""
    candidates = {"organisatie", "organization", "overheidsorganisatie"}
    seen: set[int] = set()
    for elem in root.iter():
        local = _localname(elem.tag).lower()
        if local in candidates and id(elem) not in seen:
            seen.add(id(elem))
            yield elem


# ---------------------------------------------------------------------------
# Extraction helpers — één per ROO XML-blok
# ---------------------------------------------------------------------------


# Mapping van `<resourceIdentifier @naam>`-waarden op de polder-identifier-key.
# ROO levert deze elf identifier-types via `<identificatiecodes><resourceIdentifier
# p:naam="X">value</resourceIdentifier>`. We slaan `systeemId` en
# `Organisatiecode` ook op (resp. roo_id en organisatiecode); de TOOI/OWMS-URIs
# worden hieruit gehaald als de attribute-vorm op de organisatie-node ontbreekt.
_RI_NAAM_TO_KEY: dict[str, str] = {
    "systeemid": "roo_id",
    "kvk-nummer": "kvk",
    "rsin": "rsin",
    "resourceidentifiertooi": "tooi",
    "resourceidentifierowms": "owms",
    "organisatiecode": "organisatiecode",
    "oin": "oin",
    "ictu-code": "ictu",
    "atu": "atu",
    "btw-nummer": "btw",
    "loonheffingennummer": "loonheffing",
}


def _extract_identifiers(node: etree._Element) -> dict[str, Any]:
    """Bouw `identifiers`-dict uit `<identificatiecodes>` + losse children.

    Werkt voor zowel echte ROO-XML (`<identificatiecodes><resourceIdentifier
    p:naam="OIN">…`) als voor test-fixtures met losse `<oin>`/`<kvk>`-children.
    Roo-id en TOOI vallen ook terug op de attribute-vorm op de organisatie-
    node (`p:systeemId` en `p:resourceIdentifierTOOI`).
    """
    identifiers: dict[str, Any] = {}

    # Pad 1: <identificatiecodes><resourceIdentifier p:naam="..."> blokken.
    for ic in _direct_children(node, "identificatiecodes"):
        for ri in _direct_children(ic, "resourceIdentifier"):
            naam = _attr_by_localname(ri, "naam")
            value = _text(ri)
            if not naam or not value:
                continue
            key = _RI_NAAM_TO_KEY.get(naam.lower())
            if key:
                identifiers[key] = value

    # Pad 2: losse child-elementen (test-fixtures, oudere XML-versies).
    for tag, key in [
        ("oin", "oin"),
        ("kvk", "kvk"),
        ("kvknummer", "kvk"),
        ("rsin", "rsin"),
        ("rsinnummer", "rsin"),
        ("tooi", "tooi"),
        ("owms", "owms"),
        ("ictu", "ictu"),
        ("atu", "atu"),
        ("btw", "btw"),
        ("loonheffing", "loonheffing"),
    ]:
        if key in identifiers:
            continue
        value = _direct_text(node, tag)
        if value:
            identifiers[key] = value

    # Pad 3: attribute-vorm op de organisatie-node zelf.
    if "tooi" not in identifiers:
        tooi_attr = _attr_tooi(node)
        if tooi_attr:
            identifiers["tooi"] = tooi_attr
    if "owms" not in identifiers:
        owms_attr = _attr_owms(node)
        if owms_attr:
            identifiers["owms"] = owms_attr
    if "roo_id" not in identifiers:
        sysid = _attr_systeemid(node) or _direct_text(node, "id") or _direct_text(node, "rooid")
        if sysid:
            identifiers["roo_id"] = str(sysid)

    return identifiers


_ADDRESS_FIELD_MAP: dict[str, str] = {
    "adrestype": "type",
    "toelichting": "toelichting",
    "openbareruimte": "openbare_ruimte",
    "huisnummer": "huisnummer",
    "toevoeging": "huisnummer_toevoeging",
    "postbus": "postbus",
    "postcode": "postcode",
    "woonplaats": "woonplaats",
    "provincie": "provincie",
    "regio": "regio",
    "land": "land",
    "terattentievan": "ter_attentie_van",
    "antwoordnummer": "antwoordnummer",
}


def _parse_adres(adres: etree._Element) -> dict[str, str] | None:
    out: dict[str, str] = {}
    for child in adres:
        local = _localname(child.tag).lower()
        key = _ADDRESS_FIELD_MAP.get(local)
        if not key:
            continue
        value = _text(child)
        if value:
            out[key] = value
    return out or None


# Schema-enum voor adresType (organisatie.schema.json $defs.address.type).
_KNOWN_ADDRESS_TYPES = frozenset(
    {
        "Bezoekadres",
        "Postadres",
        "Woo-Adres",
        "Vestigingsadres",
        "Bezoekadres Omgevingsloket",
        "Postadres Omgevingsloket",
    }
)


def _extract_addresses(node: etree._Element) -> list[dict[str, str]]:
    container = _direct_child(node, "adressen")
    if container is None:
        return []
    result: list[dict[str, str]] = []
    for adres in _direct_children(container, "adres"):
        parsed = _parse_adres(adres)
        if not parsed or not parsed.get("type"):
            continue
        if parsed["type"] not in _KNOWN_ADDRESS_TYPES:
            logger.warning(
                "Onbekend adresType %r (niet in schema-enum); sla adres over. "
                "Voeg toe aan schema + _KNOWN_ADDRESS_TYPES als ROO dit "
                "structureel levert.",
                parsed["type"],
            )
            continue
        result.append(parsed)
    return result


def _format_address_inline(addr: dict[str, str]) -> str:
    """Render legacy plain-text adres voor backwards-compat met `bezoekadres`/
    `postadres`-string-velden."""
    street_parts: list[str] = []
    if addr.get("openbare_ruimte"):
        street_parts.append(addr["openbare_ruimte"].strip())
    if addr.get("huisnummer"):
        street_parts.append(addr["huisnummer"])
    if addr.get("huisnummer_toevoeging"):
        street_parts.append(addr["huisnummer_toevoeging"])
    street = " ".join(street_parts)
    if not street and addr.get("postbus"):
        street = f"Postbus {addr['postbus']}"
    city_parts: list[str] = []
    if addr.get("postcode"):
        city_parts.append(addr["postcode"])
    if addr.get("woonplaats"):
        city_parts.append(addr["woonplaats"])
    city = " ".join(city_parts)
    pieces = [p for p in (street, city) if p]
    return ", ".join(pieces)


def _extract_contact_block(node: etree._Element) -> dict[str, Any]:
    """Parse `<contact>` met telefoonnummers, emailadressen, internetadressen,
    contactformulieren naar gestructureerde lijsten."""
    contact_node = _direct_child(node, "contact")
    if contact_node is None:
        return {}

    result: dict[str, Any] = {}

    phones = []
    tel_container = _direct_child(contact_node, "telefoonnummers")
    if tel_container is not None:
        for tel in _direct_children(tel_container, "telefoonnummer"):
            nummer = _direct_text(tel, "nummer")
            label = _direct_text(tel, "label")
            if nummer:
                entry: dict[str, str] = {"nummer": nummer}
                if label:
                    entry["label"] = label
                phones.append(entry)
    if phones:
        result["phones"] = phones

    emails = []
    em_container = _direct_child(contact_node, "emailadressen")
    if em_container is not None:
        for em in _direct_children(em_container, "emailadres"):
            email = _direct_text(em, "email")
            label = _direct_text(em, "label")
            if email:
                entry = {"email": email}
                if label:
                    entry["label"] = label
                emails.append(entry)
    if emails:
        result["emails"] = emails

    internet = []
    ia_container = _direct_child(contact_node, "internetadressen")
    if ia_container is not None:
        for ia in _direct_children(ia_container, "internetadres"):
            url = _direct_text(ia, "url")
            label = _direct_text(ia, "label")
            if url:
                entry = {"url": url}
                if label:
                    entry["label"] = label
                internet.append(entry)
    if internet:
        result["internet_addresses"] = internet

    forms = []
    cf_container = _direct_child(contact_node, "contactformulieren")
    if cf_container is not None:
        for cf in _direct_children(cf_container, "contactformulier"):
            url = _direct_text(cf, "url")
            label = _direct_text(cf, "label")
            if url:
                entry = {"url": url}
                if label:
                    entry["label"] = label
                forms.append(entry)
    if forms:
        result["contact_forms"] = forms

    sm_container = _direct_child(contact_node, "socialMedia")
    socials = []
    if sm_container is not None:
        for sm in _direct_children(sm_container, "socialmedium"):
            entry = {}
            for tag, key in [
                ("platform", "platform"),
                ("gebruikersnaam", "gebruikersnaam"),
                ("url", "url"),
            ]:
                v = _direct_text(sm, tag)
                if v:
                    entry[key] = v
            if entry:
                socials.append(entry)
    if socials:
        result["social_media"] = socials

    fax = _direct_text(contact_node, "fax")
    if fax:
        result["fax"] = fax

    beschrijving = _direct_text(contact_node, "beschrijving")
    if beschrijving:
        result["beschrijving"] = beschrijving

    return result


def _extract_grondslagen(container: etree._Element | None) -> list[dict[str, str]]:
    if container is None:
        return []
    result = []
    for g in _direct_children(container, "wettelijkeGrondslag"):
        opschrift = _direct_text(g, "opschrift")
        referentie = _direct_text(g, "referentie")
        entry: dict[str, str] = {}
        if opschrift:
            entry["opschrift"] = opschrift
        if referentie:
            entry["referentie"] = referentie
        if entry:
            result.append(entry)
    return result


# Schema-enum voor classificatie-types (organisatie.schema.json). ROO kan
# een nieuw type introduceren; dan filteren we het weg met een warning in
# plaats van de hele organisatie hard te laten falen op schema-validatie
# tijdens de dagelijkse run.
_KNOWN_CLASSIFICATION_TYPES = frozenset(
    {"Woo", "WNT-instelling", "Overheidswerkgever", "CAO", "Pensioenfonds", "Arbeidsvoorwaarden"}
)


def _extract_classifications(node: etree._Element) -> list[dict[str, Any]]:
    container = _direct_child(node, "classificaties")
    if container is None:
        return []
    out: list[dict[str, Any]] = []
    for c in _direct_children(container, "classificatie"):
        ctype = _attr_by_localname(c, "type")
        if not ctype:
            continue
        if ctype not in _KNOWN_CLASSIFICATION_TYPES:
            logger.warning(
                "Onbekend classificatie-type %r (niet in schema-enum); "
                "sla over. Voeg toe aan schema + _KNOWN_CLASSIFICATION_TYPES "
                "als ROO dit structureel levert.",
                ctype,
            )
            continue
        entry: dict[str, Any] = {"type": ctype}
        url = _attr_by_localname(c, "url")
        if url:
            entry["url"] = url
        text = _text(c)
        if text:
            entry["value"] = text
        eind = _direct_text(c, "eindDatum")
        if eind:
            entry["eind_datum"] = eind
        grondslagen = _extract_grondslagen(_direct_child(c, "wettelijkeGrondslagen"))
        if grondslagen:
            entry["wettelijke_grondslagen"] = grondslagen
        out.append(entry)
    return out


def _extract_geografie(node: etree._Element) -> dict[str, Any]:
    g = _direct_child(node, "geografie")
    if g is None:
        return {}
    out: dict[str, Any] = {}

    def _to_number(text: str | None) -> float | None:
        if not text:
            return None
        try:
            return float(text.replace(",", "."))
        except ValueError:
            return None

    opp_node = _direct_child(g, "oppervlakte")
    if opp_node is not None:
        opp_raw = _text(opp_node)
        if opp_raw:
            out["oppervlakte"] = opp_raw
        opp_eenheid = _attr_by_localname(opp_node, "eenheid")
        if opp_eenheid:
            out["oppervlakte_eenheid"] = opp_eenheid
        opp_num = _to_number(opp_raw)
        if opp_num is not None:
            out["oppervlakte_km2"] = opp_num
    inw = _direct_text(g, "aantalInwoners")
    if inw:
        try:
            out["aantal_inwoners"] = int(inw)
        except ValueError:
            pass
    inw_node = _direct_child(g, "inwoners")
    if inw_node is not None:
        inw_raw = _text(inw_node)
        if inw_raw:
            out["inwoners"] = inw_raw
        inw_eenheid = _attr_by_localname(inw_node, "eenheid")
        if inw_eenheid:
            out["inwoners_eenheid"] = inw_eenheid
        inw_num = _to_number(inw_raw)
        if inw_num is not None:
            out["inwoners_per_km2"] = inw_num
    plaatsen_text = _direct_text(g, "bevatPlaatsen")
    if plaatsen_text:
        out["bevat_plaatsen_raw"] = plaatsen_text
        plaatsen = [p.strip() for p in plaatsen_text.split(",") if p.strip()]
        if plaatsen:
            out["bevat_plaatsen"] = plaatsen
    return out


def _extract_council(node: etree._Element) -> dict[str, Any]:
    raad = _direct_child(node, "raad")
    if raad is None:
        return {}
    out: dict[str, Any] = {}
    total = _direct_text(raad, "totaalZetels")
    if total:
        try:
            out["total_seats"] = int(total)
        except ValueError:
            pass
    parties_container = _direct_child(raad, "partijen")
    parties: list[dict[str, Any]] = []
    if parties_container is not None:
        for p in _direct_children(parties_container, "partij"):
            naam = _direct_text(p, "naam")
            zetels = _direct_text(p, "aantalZetels")
            if not naam:
                continue
            entry: dict[str, Any] = {"naam": naam}
            try:
                entry["aantal_zetels"] = int(zetels) if zetels else 0
            except ValueError:
                entry["aantal_zetels"] = 0
            parties.append(entry)
    if parties:
        out["parties"] = parties
    return out


def _extract_org_ref(elem: etree._Element | None) -> dict[str, Any]:
    """Extract org-referentie. ROO heeft twee shapes:
    - Direct text: `<bronhouder p:systeemId="X">Heemstede</bronhouder>`
    - Child <naam>: `<deelnemendeOrganisatie p:systeemId="X"><naam>Heemstede</naam>...`
    Beide ondersteunen we; de `<naam>`-child wint als beide aanwezig zijn.
    """
    if elem is None:
        return {}
    ref: dict[str, Any] = {}
    naam_child = _direct_text(elem, "naam")
    text = naam_child or _text(elem)
    if text:
        ref["naam"] = text
    sysid = _attr_systeemid(elem)
    if sysid:
        ref["roo_id"] = sysid
    tooi = _attr_tooi(elem)
    if tooi:
        ref["tooi"] = tooi
    owms = _attr_owms(elem)
    if owms:
        ref["owms"] = owms
    return ref


def _extract_relation_to_ministerie(node: etree._Element) -> dict[str, Any]:
    return _extract_org_ref(_direct_child(node, "relatieMetMinisterie"))


def _extract_hoort_bij_gr(node: etree._Element) -> dict[str, Any]:
    return _extract_org_ref(_direct_child(node, "hoortBijGemeenschappelijkeRegeling"))


def _extract_evaluations(node: etree._Element) -> list[dict[str, Any]]:
    container = _direct_child(node, "evaluatieverslagen")
    if container is None:
        return []
    out = []
    for ev in _direct_children(container, "evaluatieverslag"):
        entry: dict[str, Any] = {}
        for tag, key in [
            ("datum", "datum"),
            ("kamerstuknummer", "kamerstuknummer"),
            ("referentie", "referentie"),
            ("naamRapport", "naam_rapport"),
        ]:
            v = _direct_text(ev, tag)
            if v:
                entry[key] = v
        if entry:
            out.append(entry)
    return out


def _extract_doorlichtingen(node: etree._Element) -> list[dict[str, Any]]:
    container = _direct_child(node, "doorlichtingen")
    if container is None:
        return []
    out = []
    for d in _direct_children(container, "doorlichting"):
        entry: dict[str, Any] = {}
        for tag, key in [
            ("datum", "datum"),
            ("naamRapport", "naam_rapport"),
            ("referentie", "referentie"),
        ]:
            v = _direct_text(d, tag)
            if v:
                entry[key] = v
        if entry:
            out.append(entry)
    return out


def _extract_policy_areas(node: etree._Element) -> list[dict[str, Any]]:
    container = _direct_child(node, "beleidsterreinen")
    if container is None:
        return []
    out = []
    for bt in _direct_children(container, "beleidsterrein"):
        naam = _text(bt)
        if not naam:
            continue
        entry: dict[str, Any] = {"naam": naam}
        tooi = _attr_by_localname(bt, "resourceIdentifier")
        if tooi:
            entry["tooi"] = tooi
        out.append(entry)
    return out


def _extract_kaderwet(node: etree._Element) -> dict[str, Any]:
    """`<kaderwet>` heeft variabele structuur (kaderwetAdviescollege,
    afwijkendeBepalingKaderwet, …). We mirroren de XML als geneste dict."""
    kw = _direct_child(node, "kaderwet")
    if kw is None:
        return {}
    return _xml_to_dict(kw) or {}


def _extract_woo(node: etree._Element) -> dict[str, Any]:
    """`<woo>` is een rijk blok: wooInformatie/urls, wooIndex/documentLocatie[],
    wooVerzoek, wooContactpersoon. Mirror als dict."""
    woo = _direct_child(node, "woo")
    if woo is None:
        return {}
    return _xml_to_dict(woo) or {}


def _extract_organogram_url(node: etree._Element) -> str | None:
    og = _direct_child(node, "organogram")
    if og is None:
        return None
    return _direct_text(og, "url")


def _extract_afspraak(node: etree._Element) -> dict[str, str]:
    af = _direct_child(node, "afspraak")
    if af is None:
        return {}
    out: dict[str, str] = {}
    for tag, key in [
        ("email", "email"),
        ("telefoonnummer", "telefoonnummer"),
        ("url", "url"),
    ]:
        v = _direct_text(af, tag)
        if v:
            out[key] = v
    return out


def _extract_description(node: etree._Element) -> dict[str, str]:
    ob = _direct_child(node, "organisatieBeschrijving")
    if ob is None:
        return {}
    out: dict[str, str] = {}
    text = _direct_text(ob, "beschrijvingText")
    url = _direct_text(ob, "url")
    if text:
        out["text"] = text
    if url:
        out["url"] = url
    return out


def _xml_to_dict(elem: etree._Element) -> Any:
    """Generieke XML→dict converter voor blokken waarvan we de schema-shape
    niet rigide modelleren (kaderwet, woo, personeel).

    Regels:
    - Element met alleen tekst → string.
    - Element met children → dict {childlocalname: value}, met repeated
      children gegroepeerd in een lijst.
    - Attributen worden onder `_attrs` opgeslagen als ze niet leeg zijn.
    """
    children = list(elem)
    text = _text(elem)
    attrs = {_localname(k): v for k, v in elem.attrib.items() if v}

    if not children:
        if attrs:
            out: dict[str, Any] = {}
            if text:
                out["_text"] = text
            out["_attrs"] = attrs
            return out
        return text

    grouped: dict[str, Any] = {}
    for child in children:
        key = _localname(child.tag)
        value = _xml_to_dict(child)
        if value is None:
            continue
        if key in grouped:
            existing = grouped[key]
            if isinstance(existing, list):
                existing.append(value)
            else:
                grouped[key] = [existing, value]
        else:
            grouped[key] = value
    if text and "_text" not in grouped:
        grouped["_text"] = text
    if attrs:
        grouped["_attrs"] = attrs
    return grouped or None


# ---------------------------------------------------------------------------
# Type-resolution: <types> bevat één of meer <type> children
# ---------------------------------------------------------------------------


def _resolve_type(node: etree._Element) -> str | None:
    """Geef de eerste type-string uit <type>, <types><type>, of <soort>."""
    direct = _direct_text(node, "type") or _direct_text(node, "soort")
    if direct:
        return direct
    types_container = _direct_child(node, "types")
    if types_container is not None:
        for t in _direct_children(types_container, "type"):
            v = _text(t)
            if v:
                return v
    return None


# ---------------------------------------------------------------------------
# parse_organisatie + parse_gemeenschappelijke_regeling
# ---------------------------------------------------------------------------


def parse_organisatie(node: etree._Element) -> dict[str, Any] | None:
    """Parse een enkele organisatie-node naar een Organisatie-record dict.

    Polder is een strict superset van ROO: alle XML-velden worden gemirrord
    naar het YAML-record. Zie `docs/roo_field_map.md` voor de mapping.
    """
    raw_type = _resolve_type(node)
    mapping = roo_type_to_internal(raw_type)
    if mapping is None:
        if raw_type:
            logger.warning("Onbekend ROO-type, sla over: %s", raw_type)
        return None
    internal_type, _sub_folder, prefix = mapping

    name = _direct_text(node, "naam") or _direct_text(node, "officielenaam")
    if not name:
        logger.warning("Organisatie zonder naam, sla over (type=%s)", raw_type)
        return None

    abbr = _direct_text(node, "afkorting")
    identifiers = _extract_identifiers(node)
    roo_id = identifiers.get("roo_id")

    website = (
        _direct_text(node, "website") or _direct_text(node, "url") or _direct_text(node, "homepage")
    )
    legacy_bezoekadres = _direct_text(node, "bezoekadres")
    legacy_postadres = _direct_text(node, "postadres")
    legacy_email = _direct_text(node, "email") or _direct_text(node, "emailadres")
    # ROO's `<startDatum>` is de aanmaakdatum van het legale entity-record, niet
    # de validity-datum van de huidige naam. Voor ministeries levert dat fouten
    # op: EZK en IenW krijgen 2010-10-14 (Rutte I-cabinetdag) terwijl die namen
    # pas in 2017 ontstonden. Wikidata's P571 (inception, gekoppeld aan de naam)
    # is correcter; die wordt door de Wikidata-fetcher ingevuld. Hier gebruiken
    # we een sentinel als er geen betrouwbare bron is. `<opgericht>` en
    # `<valid_from>` worden alleen door test-fixtures gebruikt; echte ROO-XML
    # heeft ze niet, en als ze er wel staan zijn ze al inhoudelijk juist.
    valid_from = (
        _direct_text(node, "opgericht") or _direct_text(node, "valid_from") or SENTINEL_VALID_FROM
    )
    valid_until = (
        _direct_text(node, "opgeheven")
        or _direct_text(node, "einddatum")
        or _direct_text(node, "valid_until")
    )
    parent_roo_id = (
        _direct_text(node, "parent")
        or _direct_text(node, "ouder")
        or _direct_text(node, "parent_id")
        or _direct_text(node, "ouderorganisatie")
    )
    parent_org_id: str | None = None

    # Voor organisatieonderdelen: de parent staat als enclosing `<organisatie>`-
    # ancestor in de XML. Bereken zijn `org:`-id rechtstreeks zodat
    # parent_id-resolutie niet afhangt van roo_id-matching.
    if internal_type == "organisatieonderdeel":
        ancestor = _enclosing_organisatie(node)
        if ancestor is not None:
            ancestor_type = _resolve_type(ancestor)
            ancestor_mapping = roo_type_to_internal(ancestor_type)
            ancestor_name = _direct_text(ancestor, "naam") or _direct_text(
                ancestor, "officielenaam"
            )
            ancestor_abbr = _direct_text(ancestor, "afkorting")
            if ancestor_mapping is not None and ancestor_name:
                _, _, ancestor_prefix = ancestor_mapping
                ancestor_slug = (
                    slugify(ancestor_abbr)
                    if ancestor_abbr and len(ancestor_abbr) <= 12
                    else slugify(ancestor_name)
                )
                parent_org_id = build_id(ancestor_prefix, ancestor_slug)
            ancestor_roo_id = _attr_systeemid(ancestor) or _direct_text(ancestor, "id")
            if ancestor_roo_id and not parent_roo_id:
                parent_roo_id = ancestor_roo_id

    slug = slugify(abbr) if abbr and len(abbr) <= 12 else slugify(name)
    org_id = build_id(prefix, slug)

    source_url = roo_org_url(roo_id, slug)

    name_entry: dict[str, Any] = {"value": name}
    if abbr:
        name_entry["abbr"] = abbr
    name_entry["valid_from"] = valid_from
    if valid_until:
        name_entry["valid_until"] = valid_until

    # Adressen: gestructureerd én legacy plain-text.
    structured_addresses = _extract_addresses(node)
    contact: dict[str, Any] = {}
    if website:
        contact["website"] = website
    if structured_addresses:
        # Genereer plain-text uit gestructureerde data; XML-fixtures zonder
        # <adressen> kunnen direct hun legacy-velden meegeven.
        for addr in structured_addresses:
            inline = _format_address_inline(addr)
            if not inline:
                continue
            if addr.get("type") == "Bezoekadres" and "bezoekadres" not in contact:
                contact["bezoekadres"] = inline
            elif addr.get("type") == "Postadres" and "postadres" not in contact:
                contact["postadres"] = inline
        contact["addresses"] = structured_addresses
    if legacy_bezoekadres and "bezoekadres" not in contact:
        contact["bezoekadres"] = legacy_bezoekadres
    if legacy_postadres and "postadres" not in contact:
        contact["postadres"] = legacy_postadres

    contact_block = _extract_contact_block(node)
    contact.update(contact_block)
    if "email" not in contact:
        if contact.get("emails"):
            contact["email"] = contact["emails"][0]["email"]
        elif legacy_email:
            contact["email"] = legacy_email

    record: dict[str, Any] = {
        "id": org_id,
        "type": internal_type,
    }
    subtype = _direct_text(node, "subtype")
    if subtype:
        record["subtype"] = subtype
    subname = _direct_text(node, "subnaam")
    if subname:
        record["subname"] = subname
    if identifiers:
        record["identifiers"] = identifiers
    record["classification"] = internal_type

    legal_form = _direct_text(node, "rechtsvorm")
    if legal_form:
        record["legal_form"] = legal_form
    zbo_kind = _direct_text(node, "soortZbo")
    if zbo_kind:
        record["zbo_kind"] = zbo_kind
    advisory_kind = _direct_text(node, "soortAdviescollege")
    if advisory_kind:
        record["advisory_kind"] = advisory_kind

    relation = _extract_relation_to_ministerie(node)
    if relation:
        record["relation_to_ministerie"] = relation
    hoort_bij = _extract_hoort_bij_gr(node)
    if hoort_bij:
        record["hoort_bij_gemeenschappelijke_regeling"] = hoort_bij

    if parent_roo_id:
        record["_parent_roo_id"] = str(parent_roo_id)
    if parent_org_id:
        record["_parent_org_id"] = parent_org_id
    record["names"] = [name_entry]

    description = _extract_description(node)
    if description:
        record["description"] = description

    policy_areas = _extract_policy_areas(node)
    if policy_areas:
        record["policy_areas"] = policy_areas
    kaderwet = _extract_kaderwet(node)
    if kaderwet:
        record["kaderwet"] = kaderwet
    grondslagen = _extract_grondslagen(_direct_child(node, "wettelijkeGrondslagen"))
    if grondslagen:
        record["wettelijke_grondslagen"] = grondslagen
    taken = _direct_text(node, "takenEnBevoegdheden")
    if taken:
        record["taken_en_bevoegdheden"] = taken
    evaluations = _extract_evaluations(node)
    if evaluations:
        record["evaluations"] = evaluations
    doorlichtingen = _extract_doorlichtingen(node)
    if doorlichtingen:
        record["doorlichtingen"] = doorlichtingen
    classifications = _extract_classifications(node)
    if classifications:
        record["classifications"] = classifications
    woo = _extract_woo(node)
    if woo:
        record["woo"] = woo
    organogram_url = _extract_organogram_url(node)
    if organogram_url:
        record["organogram_url"] = organogram_url
    afspraak = _extract_afspraak(node)
    if afspraak:
        record["afspraak"] = afspraak
    geography = _extract_geografie(node)
    if geography:
        record["geography"] = geography
    council = _extract_council(node)
    if council:
        record["council"] = council
    personeel_node = _direct_child(node, "personeel")
    if personeel_node is not None and (len(personeel_node) > 0 or _text(personeel_node)):
        personeel = _xml_to_dict(personeel_node)
        if personeel:
            record["personnel"] = personeel if isinstance(personeel, dict) else {"_text": personeel}

    if contact:
        record["contact"] = contact
    record["valid_from"] = valid_from
    record["valid_until"] = valid_until or None

    last_mutation = _direct_text(node, "datumMutatie")
    if last_mutation:
        record["last_mutation"] = last_mutation
    last_verified = _direct_text(node, "datumTerVerificatie")
    if last_verified:
        record["last_verified"] = last_verified
    roo_start_datum = _direct_text(node, "startDatum")
    if roo_start_datum:
        record["roo_start_datum"] = roo_start_datum

    record["sources"] = [
        {
            "id": SOURCE_ID,
            "url": source_url,
            "retrieved": _today(),
        }
    ]
    record["_sub_folder"] = _sub_folder
    record["_slug"] = slug
    return record


# ---------------------------------------------------------------------------
# Gemeenschappelijke regelingen (apart blok in XML)
# ---------------------------------------------------------------------------


def parse_gemeenschappelijke_regeling(node: etree._Element) -> dict[str, Any] | None:
    """Parse een `<regeling>` uit `<gemeenschappelijkeRegelingen>`.

    GR-records gaan naar `data/organisaties/gemeenschappelijke-regelingen/` net
    als gewone GR-organisaties; we mergen op `roo_id` of slug-basis.
    """
    titel = _direct_text(node, "titel") or _direct_text(node, "naam")
    if not titel:
        return None

    sysid = _attr_systeemid(node) or _direct_text(node, "id")
    citeertitel = _direct_text(node, "citeertitel")
    samen = _direct_child(node, "samenwerkingsvorm")
    samen_afkorting = _attr_by_localname(samen, "afkorting") if samen is not None else None

    # GR-slug: titel-gebaseerd. `parse_export` zal achteraf bij collisions
    # een roo_id-suffix toevoegen (~290 regelingen hebben identieke titels —
    # verschillende versies, zie nextVersion/previousVersion).
    slug = slugify(citeertitel or titel)
    org_id = build_id("gr", slug)

    source_url = roo_org_url(sysid, slug)

    identifiers: dict[str, Any] = {}
    if sysid:
        identifiers["roo_id"] = str(sysid)

    valid_from = _direct_text(node, "datumInwerkingtreding") or SENTINEL_VALID_FROM
    valid_until = _direct_text(node, "datumUitwerkingtreding")

    # Voor de naam-entry: GRs hebben geen eigen afkorting in ROO, maar de
    # samenwerkingsvorm-afkorting (BVO/RSO/etc.) is een nuttige type-flag.
    # We zetten 'm alleen op `name.abbr` als die echt iets specifieks is —
    # in de praktijk laat ROO dat aan ons over.
    name_entry: dict[str, Any] = {"value": titel, "valid_from": valid_from}
    if valid_until:
        name_entry["valid_until"] = valid_until

    gr_meta: dict[str, Any] = {}
    if citeertitel:
        gr_meta["citeertitel"] = citeertitel
    doel = _direct_text(node, "doel")
    if doel:
        gr_meta["doel"] = doel
    if samen is not None:
        sv: dict[str, str] = {}
        sv_value = _text(samen)
        if sv_value:
            sv["value"] = sv_value
        if samen_afkorting:
            sv["afkorting"] = samen_afkorting
        if sv:
            gr_meta["samenwerkingsvorm"] = sv

    bv = _direct_child(node, "bevoegdheidsverkrijgingen")
    if bv is not None:
        items = [_text(c) for c in _direct_children(bv, "bevoegdheidsverkrijging") if _text(c)]
        if items:
            gr_meta["bevoegdheidsverkrijgingen"] = [i for i in items if i]

    for tag, key in [
        ("regionaalSamenwerkingsorgaan", "regionaal_samenwerkingsorgaan"),
        ("bronhouder", "bronhouder"),
        ("archiefzorgdrager", "archiefzorgdrager"),
    ]:
        ref = _extract_org_ref(_direct_child(node, tag))
        if ref:
            gr_meta[key] = ref

    taalcode = _direct_text(node, "taalcode")
    if taalcode:
        gr_meta["taalcode"] = taalcode
    registratiehouder = _direct_text(node, "registratiehouder")
    if registratiehouder:
        gr_meta["registratiehouder"] = registratiehouder

    deelnemers = _direct_child(node, "deelnemendeOrganisaties")
    if deelnemers is not None:
        entries = []
        for d in deelnemers:
            ref = _extract_org_ref(d)
            entry = dict(ref)  # naam/roo_id/tooi/owms

            for tag, key in [
                ("toetredingsDatum", "toetredings_datum"),
                ("uittredingsDatum", "uittredings_datum"),
                ("verdeelsleutel", "verdeelsleutel"),
            ]:
                v = _direct_text(d, tag)
                if v:
                    entry[key] = v

            # bestuursorganen — lijst van bestuursorgaan-elementen, elk met
            # tekst (naam) en owms-attribute.
            bo_container = _direct_child(d, "bestuursorganen")
            if bo_container is not None:
                bos = []
                for bo in _direct_children(bo_container, "bestuursorgaan"):
                    naam = _text(bo)
                    owms = _attr_owms(bo)
                    tooi = _attr_tooi(bo)
                    item: dict[str, str] = {}
                    if naam:
                        item["naam"] = naam
                    if owms:
                        item["owms"] = owms
                    if tooi:
                        item["tooi"] = tooi
                    if item:
                        bos.append(item)
                if bos:
                    entry["bestuursorganen"] = bos

            if entry:
                entries.append(entry)
        if entries:
            gr_meta["deelnemende_organisaties"] = entries

    instell = _direct_child(node, "instellingsbesluiten")
    if instell is not None:
        items = [_text(r) for r in _direct_children(instell, "referentie") if _text(r)]
        if items:
            gr_meta["instellingsbesluiten"] = [i for i in items if i]

    grondslagen = _extract_grondslagen(_direct_child(node, "wettelijkeGrondslagen"))
    if grondslagen:
        gr_meta["wettelijke_grondslagen"] = grondslagen

    bevoegdheden_node = _direct_child(node, "bevoegdheden")
    if bevoegdheden_node is not None:
        items = []
        for b in _direct_children(bevoegdheden_node, "bevoegdheid"):
            entry: dict[str, str] = {}
            kop = _direct_text(b, "kopArtikel")
            inhoud = _direct_text(b, "inhoudArtikel")
            if kop:
                entry["kop_artikel"] = kop
            if inhoud:
                entry["inhoud_artikel"] = inhoud
            if entry:
                items.append(entry)
        if items:
            gr_meta["bevoegdheden"] = items

    for tag, key in [
        ("datumInwerkingtreding", "datum_inwerkingtreding"),
        ("datumUitwerkingtreding", "datum_uitwerkingtreding"),
        ("afwijkingRegeling", "afwijking_regeling"),
        ("begrotingsDatum", "begrotings_datum"),
        ("startDatum", "start_datum"),
    ]:
        v = _direct_text(node, tag)
        if v:
            gr_meta[key] = v

    # next_version / previous_version: object met systeemId-attribute + naam.
    for tag, key in [("nextVersion", "next_version"), ("previousVersion", "previous_version")]:
        elem = _direct_child(node, tag)
        if elem is not None:
            entry: dict[str, str] = {}
            sysid_v = _attr_systeemid(elem)
            if sysid_v:
                entry["roo_id"] = sysid_v
            naam = _text(elem)
            if naam:
                entry["naam"] = naam
            if entry:
                gr_meta[key] = entry

    record: dict[str, Any] = {
        "id": org_id,
        "type": "gemeenschappelijke-regeling",
    }
    if identifiers:
        record["identifiers"] = identifiers
    record["classification"] = "gemeenschappelijke-regeling"
    record["names"] = [name_entry]

    structured_addresses = _extract_addresses(node)
    contact: dict[str, Any] = {}
    if structured_addresses:
        for addr in structured_addresses:
            inline = _format_address_inline(addr)
            if not inline:
                continue
            if addr.get("type") == "Bezoekadres" and "bezoekadres" not in contact:
                contact["bezoekadres"] = inline
            elif addr.get("type") == "Postadres" and "postadres" not in contact:
                contact["postadres"] = inline
        contact["addresses"] = structured_addresses
    contact.update(_extract_contact_block(node))
    if contact:
        record["contact"] = contact

    policy_areas = _extract_policy_areas(node)
    if policy_areas:
        record["policy_areas"] = policy_areas

    if gr_meta:
        record["gr_meta"] = gr_meta

    record["valid_from"] = valid_from
    record["valid_until"] = valid_until or None

    last_mutation = _direct_text(node, "datumMutatie")
    if last_mutation:
        record["last_mutation"] = last_mutation
    last_verified = _direct_text(node, "datumTerVerificatie")
    if last_verified:
        record["last_verified"] = last_verified

    record["sources"] = [
        {
            "id": SOURCE_ID,
            "url": source_url,
            "retrieved": _today(),
        }
    ]
    record["_sub_folder"] = "gemeenschappelijke-regelingen"
    record["_slug"] = slug
    return record


def _iter_regeling_nodes(root: etree._Element) -> Iterator[etree._Element]:
    """Yield alle `<regeling>`-nodes onder root (uit
    `<gemeenschappelijkeRegelingen>` en grensoverschrijdende variant)."""
    for elem in root.iter():
        if _localname(elem.tag).lower() == "regeling":
            yield elem


def parse_export(path: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    """Parse de ROO-XML naar een lijst Organisatie-records.

    Verwerkt zowel `<organisatie>`-nodes als `<regeling>`-nodes
    (gemeenschappelijke regelingen).
    """
    with path.open("rb") as fh:
        tree = etree.parse(fh)
    root = tree.getroot()

    # Materializeer eerst de generator: nested iter() in parse_organisatie en
    # extraction-helpers kan de outer lxml-iterator corrupten.
    org_nodes = list(_iter_organisatie_nodes(root))
    reg_nodes = list(_iter_regeling_nodes(root))

    records: list[dict[str, Any]] = []
    for node in org_nodes:
        record = parse_organisatie(node)
        if record is None:
            continue
        records.append(record)
        if limit is not None and len(records) >= limit:
            return records
    gr_records: list[dict[str, Any]] = []
    for node in reg_nodes:
        record = parse_gemeenschappelijke_regeling(node)
        if record is None:
            continue
        gr_records.append(record)

    # GR slug-collision resolution: ~290 regelingen delen een titel met een
    # andere (versies, fusies). Per slug-collision: voeg `-<roo_id>` toe aan
    # álle records met die slug, zodat ze stabiel uit elkaar worden gehouden.
    slug_counts: dict[str, int] = {}
    for r in gr_records:
        slug_counts[r["_slug"]] = slug_counts.get(r["_slug"], 0) + 1
    for r in gr_records:
        if slug_counts[r["_slug"]] > 1:
            roo_id = (r.get("identifiers") or {}).get("roo_id")
            if roo_id:
                new_slug = f"{r['_slug']}-{roo_id}"
                r["_slug"] = new_slug
                r["id"] = build_id("gr", new_slug)
        records.append(r)
        if limit is not None and len(records) >= limit:
            break
    return records


# ---------------------------------------------------------------------------
# Merge & write
# ---------------------------------------------------------------------------


def merge_yaml(existing: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge: ROO wint voor velden die hij vult, lokaal blijft staan voor de rest.

    Speciale behandeling:
    - `identifiers`: union; nieuwe waarden overschrijven oude waarden alleen als ze niet leeg zijn.
    - `names`: lijst wordt vervangen, behalve dat lokaal toegevoegde entries blijven staan
      (matching op `value` + `valid_from`).
    - `sources`: dedupe op `id`; ROO-source updatet `retrieved`.
    """
    if not existing:
        return dict(new)

    merged: dict[str, Any] = dict(existing)

    for key, value in new.items():
        if key == "identifiers":
            current = dict(merged.get("identifiers") or {})
            for ident_key, ident_val in (value or {}).items():
                if ident_val is not None and ident_val != "":
                    current[ident_key] = ident_val
                elif ident_key not in current:
                    current[ident_key] = ident_val
            merged["identifiers"] = current
        elif key == "names":
            new_names = list(value or [])
            existing_names = list(merged.get("names") or [])
            seen = {(n.get("value"), n.get("valid_from")) for n in new_names}
            for entry in existing_names:
                key_tuple = (entry.get("value"), entry.get("valid_from"))
                if key_tuple not in seen:
                    new_names.append(entry)
                    seen.add(key_tuple)
            merged["names"] = new_names
        elif key == "contact":
            current = dict(merged.get("contact") or {})
            for ck, cv in (value or {}).items():
                if cv:
                    current[ck] = cv
            merged["contact"] = current
        elif key == "sources":
            existing_sources = list(merged.get("sources") or [])
            by_id = {src.get("id"): dict(src) for src in existing_sources}
            for src in value or []:
                by_id[src.get("id")] = dict(src)
            merged["sources"] = list(by_id.values())
        elif key.startswith("_"):
            # Private key (sub_folder, slug, parent_roo_id, parent_org_id):
            # altijd vervangen.
            merged[key] = value
        elif key in ("valid_from", "valid_until"):
            # ROO's `<startDatum>` is onbetrouwbaar als naam-validity; de
            # fetcher zet daarom de sentinel "1900-01-01" wanneer er geen
            # betrouwbare bron is. Een eerdere fetch óf een handmatige
            # correctie óf de Wikidata-fetcher (P571) kan een echte datum
            # hebben gezet. We mogen die NOOIT overschrijven met de
            # sentinel — dat is data-verlies bij elke her-fetch.
            existing_val = merged.get(key)
            if value == SENTINEL_VALID_FROM and existing_val not in (
                None,
                "",
                SENTINEL_VALID_FROM,
            ):
                pass  # behoud de bestaande, betere waarde
            elif value is not None or key not in merged:
                merged[key] = value
        else:
            if value is not None or key not in merged:
                merged[key] = value

    return merged


def _strip_private(record: dict[str, Any]) -> dict[str, Any]:
    """Verwijder private keys die met `_` beginnen, voor serialisatie."""
    return {k: v for k, v in record.items() if not k.startswith("_")}


def _canonicalize_names(record: dict[str, Any]) -> None:
    """Trek de casing van `names[].value` recht, in-place.

    Centrale chokepoint: alle ROO-record-paden (organisatie, GR,
    organisatieonderdeel) lopen via `write_records`, dus hier
    canonicaliseren dekt ze allemaal — robuuster dan per call-site.
    Raakt `abbr` of andere velden niet aan.
    """
    for entry in record.get("names") or []:
        if isinstance(entry, dict):
            v = entry.get("value")
            if isinstance(v, str) and v:
                entry["value"] = canonicalize_leading_case(v)


def _ordered_for_dump(record: dict[str, Any]) -> dict[str, Any]:
    """Zet velden in een leesbare volgorde voor YAML-output."""
    order = [
        "id",
        "type",
        "subtype",
        "subname",
        "identifiers",
        "classification",
        "legal_form",
        "zbo_kind",
        "advisory_kind",
        "parent_id",
        "relation_to_ministerie",
        "hoort_bij_gemeenschappelijke_regeling",
        "names",
        "description",
        "policy_areas",
        "kaderwet",
        "wettelijke_grondslagen",
        "taken_en_bevoegdheden",
        "evaluations",
        "doorlichtingen",
        "classifications",
        "woo",
        "organogram_url",
        "afspraak",
        "geography",
        "council",
        "personnel",
        "gr_meta",
        "contact",
        "valid_from",
        "valid_until",
        "last_mutation",
        "last_verified",
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


def _resolve_parents(records: Iterable[dict[str, Any]]) -> None:
    """Vervang `_parent_roo_id` door `parent_id` (org:<slug>) waar mogelijk."""
    by_roo_id: dict[str, str] = {}
    for record in records:
        identifiers = record.get("identifiers") or {}
        roo_id = identifiers.get("roo_id")
        if roo_id:
            by_roo_id[str(roo_id)] = record["id"]

    for record in records:
        parent_roo_id = record.pop("_parent_roo_id", None)
        parent_org_id = record.pop("_parent_org_id", None)
        own_id = record.get("id")
        candidate: str | None = None
        if parent_org_id:
            candidate = parent_org_id
        elif parent_roo_id and parent_roo_id in by_roo_id:
            candidate = by_roo_id[parent_roo_id]
        if candidate == own_id:
            # Self-loop: ROO levert soms een onderdeel waarvan de parent
            # naar zichzelf resolveert. Behandel als "geen parent".
            logger.debug("Skip self-loop parent_id voor %s", own_id)
            candidate = None
        if candidate is not None:
            record["parent_id"] = candidate
        elif "parent_id" not in record:
            record["parent_id"] = None


def _existing_tooi_to_path(out_dir: Path) -> dict[str, tuple[Path, str | None]]:
    """Bouw index `tooi-id -> (bestaand pad, record-id)` over álle subfolders.

    Gebruikt om te voorkomen dat een organisatieonderdeel-record geschreven
    wordt als er al een echte (gemeente/ministerie/zbo) record met dezelfde
    TOOI-id bestaat. Anders zou dezelfde fysieke organisatie als zowel
    `gemeenten/groningen.yaml` als `organisatieonderdelen/groningen.yaml`
    eindigen. De record-id wordt meegenomen zodat `parent_id`-referenties
    van overgeslagen records geremapt kunnen worden naar het bewaarde record.
    """
    index: dict[str, tuple[Path, str | None]] = {}
    if not out_dir.exists():
        return index
    # `sorted` zodat bij twee records met dezelfde TOOI-id deterministisch
    # bepaald wordt welke "wint" — anders is dat filesystem-afhankelijk.
    for path in sorted(out_dir.rglob("*.yaml")):
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except yaml.YAMLError:
            continue
        tooi = (data.get("identifiers") or {}).get("tooi")
        if tooi:
            index.setdefault(tooi, (path, data.get("id")))
    return index


def write_records(
    records: list[dict[str, Any]],
    out_dir: Path,
    *,
    dry_run: bool = False,
) -> int:
    """Schrijf records als YAML onder `out_dir/<sub_folder>/<slug>.yaml`.

    Een record van type `organisatieonderdeel` wordt overgeslagen als er al
    een record in een andere subfolder is met dezelfde TOOI-id; dat
    voorkomt dat een gemeente als zowel `gemeenten/X.yaml` als
    `organisatieonderdelen/X.yaml` eindigt.
    """
    _resolve_parents(records)
    tooi_index = _existing_tooi_to_path(out_dir)

    # Pass 1: bepaal welke organisatieonderdeel-records overgeslagen worden
    # wegens een TOOI-duplicate, en bouw een remap van de overgeslagen
    # record-id naar de id van het bewaarde record. Zonder deze remap blijven
    # `parent_id`-referenties van kinderen naar het overgeslagen record
    # dangling (validator-errors "Onbekende organisatie-referentie").
    parent_remap: dict[str, str] = {}
    for record in records:
        sub_folder = record.get("_sub_folder")
        slug = record.get("_slug")
        if sub_folder != "organisatieonderdelen" or not slug:
            continue
        tooi = ((record.get("identifiers") or {}).get("tooi")) if record else None
        if not tooi or tooi not in tooi_index:
            continue
        target = out_dir / sub_folder / f"{slug}.yaml"
        existing_path, existing_id = tooi_index[tooi]
        if existing_path == target:
            continue
        own_id = record.get("id")
        if own_id and existing_id and own_id != existing_id:
            parent_remap[own_id] = existing_id

    if parent_remap:
        # Volg ketens A->B, B->E tot een fixed point. Zonder dit blijft
        # parent_id hangen op B (zelf een overgeslagen record-id) wanneer
        # de remap-target van het ene overgeslagen record gelijk is aan de
        # id van een ander overgeslagen record -> opnieuw dangling.
        def _chase(start: str) -> str:
            seen = {start}
            cur = start
            while cur in parent_remap:
                nxt = parent_remap[cur]
                if nxt in seen:  # cycle: laat ongemoeid
                    return start
                seen.add(nxt)
                cur = nxt
            return cur

        resolved_remap = {key: _chase(key) for key in parent_remap}
        for record in records:
            parent_id = record.get("parent_id")
            if parent_id in resolved_remap:
                record["parent_id"] = resolved_remap[parent_id]

    n_written = 0
    n_skipped_duplicate = 0
    for record in records:
        sub_folder = record.get("_sub_folder")
        slug = record.get("_slug")
        if not sub_folder or not slug:
            logger.warning("Record zonder sub_folder/slug, sla over: %s", record.get("id"))
            continue
        target_dir = out_dir / sub_folder
        target = target_dir / f"{slug}.yaml"

        clean = _strip_private(record)
        _canonicalize_names(clean)

        tooi = (clean.get("identifiers") or {}).get("tooi")
        if (
            sub_folder == "organisatieonderdelen"
            and tooi
            and tooi in tooi_index
            and tooi_index[tooi][0] != target
        ):
            logger.info(
                "Skip organisatieonderdeel %s: tooi-id al in %s",
                target.relative_to(out_dir),
                tooi_index[tooi][0].relative_to(out_dir),
            )
            n_skipped_duplicate += 1
            continue

        if target.exists():
            try:
                with target.open("r", encoding="utf-8") as fh:
                    existing = yaml.safe_load(fh) or {}
            except yaml.YAMLError as exc:
                logger.warning("Kon bestaande YAML niet lezen (%s): %s", target, exc)
                existing = {}
            clean = merge_yaml(existing, clean)

        clean = _ordered_for_dump(clean)

        if dry_run:
            print(f"DRY-RUN zou schrijven: {target}", file=sys.stderr)
            n_written += 1
            continue

        target_dir.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(
                clean,
                fh,
                sort_keys=False,
                default_flow_style=False,
                allow_unicode=True,
            )
        if tooi:
            tooi_index[tooi] = (target, clean.get("id"))
        n_written += 1
    if n_skipped_duplicate:
        logger.info(
            "ROO write: %d organisatieonderdelen overgeslagen wegens TOOI-duplicate",
            n_skipped_duplicate,
        )
    return n_written


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="polder-fetch-roo",
        description="Download het ROO-export-XML en schrijf Organisatie YAML-records.",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path("_cache"),
        help="Cache-directory voor de XML-download (default: _cache)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data/organisaties"),
        help="Output-directory voor YAML-records (default: data/organisaties)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max aantal records (voor testen).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Schrijf niets, print alleen wat geschreven zou worden.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose logging.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    cache_path = download_export(args.cache)
    records = parse_export(cache_path, limit=args.limit)
    n_written = write_records(records, args.out, dry_run=args.dry_run)
    print(
        f"Wrote {n_written} organisatie-records to {args.out}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
