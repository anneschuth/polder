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

ROL VAN DEZE FETCHER: download het ruwe SKOS/RDF-document per scheme naar
`_cache/tooi/<scheme>.rdf`, en parse het naar een dict met SKOS-concepten zodat
crosswalk-skills kunnen lookuppen op naam of URI. Schrijft NIET naar `data/`.

Tracking issue: https://github.com/anneschuth/polder/issues/TODO-tooi
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path
from typing import Any

import httpx
from lxml import etree

logger = logging.getLogger("polder.fetchers.tooi")

__all__ = [
    "CACHE_DIR",
    "DEFAULT_SCHEMES",
    "DOWNLOAD_BASE",
    "TOOI_BASE",
    "TOOI_IDENTIFIER_BASE",
    "fetch_tooi_concepts",
    "main",
    "parse_skos_rdf",
]

TOOI_BASE = "https://standaarden.overheid.nl/tooi/waardelijsten/"
TOOI_IDENTIFIER_BASE = "https://identifier.overheid.nl/tooi/id/"
DOWNLOAD_BASE = "https://repository.officiele-overheidspublicaties.nl/waardelijsten"
CACHE_DIR = Path("_cache/tooi")
HTTP_TIMEOUT = 60.0
USER_AGENT = (
    "polder-bot/0.1 (https://github.com/anneschuth/polder; anne.schuth@gmail.com)"
)

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

NAMESPACES = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "tooiont": "https://identifier.overheid.nl/tooi/def/ont/",
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
            if (
                not tooi_match
                and not skos_match
                and "HistorischeVersie" in types
            ):
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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="polder-fetch-tooi",
        description=(
            "Download SKOS/RDF van een TOOI concept-scheme naar _cache/tooi/. "
            "Crosswalk-mapping doet een aparte skill."
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
    try:
        result = fetch_tooi_concepts(
            args.scheme,
            expression=args.expression,
            cache_dir=args.cache,
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
