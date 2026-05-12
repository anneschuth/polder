"""Fetcher voor Wikidata Q-id crosswalks via de SPARQL Query Service.

Bron: Wikidata Query Service.
Endpoint: https://query.wikidata.org/sparql
Formaat: SPARQL Query Results JSON (application/sparql-results+json).
Update: live (Wikidata zelf wordt continu bewerkt).
Licentie: CC0 (Wikidata data).
Dekking: Q-id's voor Nederlandse ministeries, gemeenten, provincies, waterschappen,
TK-leden en andere entiteiten die we willen crosswalken naar polder-records.

Voorbeeld-query (Nederlandse gemeenten, Q11424093 = Nederlandse gemeente):

    SELECT ?org ?orgLabel WHERE {
        ?org wdt:P31 wd:Q11424093 .
        SERVICE wikibase:label {
            bd:serviceParam wikibase:language "nl,en".
        }
    }

Wikimedia User-Agent policy vereist een zinnige UA met contactinfo:
https://meta.wikimedia.org/wiki/User-Agent_policy

Tracking issue: https://github.com/anneschuth/polder/issues/4
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import time
import unicodedata
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any, Literal

import httpx
import yaml

logger = logging.getLogger("polder.fetchers.wikidata_sparql")

__all__ = [
    "ABD_TMG_QUERY",
    "BEWINDSPERSOON_QUERIES",
    "MINISTRY_QID_TO_SLUG",
    "MIN_REQUEST_INTERVAL",
    "NAME_HISTORY_BATCH_SIZE",
    "ORG_QUERIES",
    "PERSON_QUERY",
    "QLEVER_ENDPOINT",
    "SOURCE_ID",
    "SPARQL_ENDPOINT",
    "USER_AGENT",
    "Endpoint",
    "build_abd_tmg_records",
    "build_bewindspersoon_records",
    "build_org_index",
    "build_person_index",
    "enrich_abd_tmg",
    "enrich_bewindspersonen",
    "enrich_organisations",
    "extract_qid",
    "fetch_name_history",
    "lookup_person_by_name",
    "main",
    "match_organisations",
    "match_personen",
    "merge_names_into_record",
    "merge_wikidata_into_record",
    "normalize_org_name",
    "parse_abd_tmg_bindings",
    "parse_bewindspersoon_bindings",
    "parse_name_history_bindings",
    "parse_org_bindings",
    "parse_person_bindings",
    "person_id_from_label",
    "query_sparql",
    "resolve_endpoint",
]

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
QLEVER_ENDPOINT = "https://qlever.cs.uni-freiburg.de/api/wikidata"

# Korte alias voor de endpoints die we ondersteunen. CLI/skill-laag gebruikt
# de alias; lagere helpers verwachten een URL. ``resolve_endpoint`` vertaalt.
# `auto`: probeer QLever eerst (sneller), val terug op WDQS bij timeout/5xx.
Endpoint = Literal["wdqs", "qlever", "auto"]
_ENDPOINT_URLS: dict[str, str] = {
    "wdqs": SPARQL_ENDPOINT,
    "qlever": QLEVER_ENDPOINT,
}
_AUTO_FALLBACK_CHAIN: tuple[str, ...] = ("qlever", "wdqs")

# Process-level circuit breaker: na N opeenvolgende failures op een endpoint
# slaan we hem over tot het einde van de run. Voorkomt dat een hele enrich-loop
# bij elke record dezelfde timeout-wait incasseert.
_ENDPOINT_FAILURE_COUNT: dict[str, int] = {}
_ENDPOINT_CIRCUIT_THRESHOLD = 3


def _endpoint_is_circuit_broken(alias: str) -> bool:
    return _ENDPOINT_FAILURE_COUNT.get(alias, 0) >= _ENDPOINT_CIRCUIT_THRESHOLD


def _mark_endpoint_failure(alias: str) -> None:
    _ENDPOINT_FAILURE_COUNT[alias] = _ENDPOINT_FAILURE_COUNT.get(alias, 0) + 1


def _mark_endpoint_success(alias: str) -> None:
    _ENDPOINT_FAILURE_COUNT[alias] = 0


def reset_endpoint_circuits() -> None:
    """Reset alle circuit-breakers. Voor tests of nieuwe runs."""
    _ENDPOINT_FAILURE_COUNT.clear()


def resolve_endpoint(endpoint: str) -> str:
    """Vertaal een endpoint-alias (`wdqs`/`qlever`) of volledige URL naar een URL.

    `auto` wordt op aliasniveau bewaard; query_sparql interpreteert hem zelf
    en doorloopt de fallback-keten.
    """
    if endpoint == "auto":
        return endpoint
    if endpoint in _ENDPOINT_URLS:
        return _ENDPOINT_URLS[endpoint]
    return endpoint
USER_AGENT = (
    "polder-bot/0.1 (https://github.com/anneschuth/polder; anne.schuth@gmail.com)"
)
HTTP_TIMEOUT = 180.0
MIN_REQUEST_INTERVAL = 2.0  # seconden tussen calls (Wikimedia rate-limit; conservatief tijdens outages)
QLEVER_REQUEST_INTERVAL = 0.2  # QLever heeft geen agressieve throttle
SOURCE_ID = "wikidata"

# QLever vereist expliciete PREFIX-declaraties en ondersteunt geen
# `SERVICE wikibase:label`; we vragen labels via `rdfs:label` met taal-filter.
_QLEVER_PREFIXES = """PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
"""


# ---------------------------------------------------------------------------
# SPARQL queries
# ---------------------------------------------------------------------------
#
# We splitsen organisaties per type: aparte query per Q-class houdt de payload
# klein en maakt het type-veld in het resultaat impliciet.

ORG_QUERIES: dict[str, str] = {
    "ministerie": """
SELECT ?item ?itemLabel ?abbr ?oin WHERE {
  ?item wdt:P31 wd:Q3143387 .
  OPTIONAL { ?item wdt:P1813 ?abbr }
  OPTIONAL { ?item wdt:P9947 ?oin }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "nl,en". }
}
""",
    "gemeente": """
SELECT ?item ?itemLabel ?abbr ?oin WHERE {
  ?item wdt:P31 wd:Q2039348 .
  OPTIONAL { ?item wdt:P1813 ?abbr }
  OPTIONAL { ?item wdt:P9947 ?oin }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "nl,en". }
}
""",
    "provincie": """
SELECT ?item ?itemLabel ?abbr ?oin WHERE {
  ?item wdt:P31 wd:Q134390 .
  OPTIONAL { ?item wdt:P1813 ?abbr }
  OPTIONAL { ?item wdt:P9947 ?oin }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "nl,en". }
}
""",
    "waterschap": """
SELECT ?item ?itemLabel ?abbr ?oin WHERE {
  ?item wdt:P31 wd:Q702081 .
  OPTIONAL { ?item wdt:P1813 ?abbr }
  OPTIONAL { ?item wdt:P9947 ?oin }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "nl,en". }
}
""",
}

# QLever-varianten: zelfde Q-classes maar met expliciete PREFIX-declaraties
# en `rdfs:label` met taal-filter (QLever ondersteunt geen wikibase:label-service).
ORG_QUERIES_QLEVER: dict[str, str] = {
    "ministerie": _QLEVER_PREFIXES + """
SELECT ?item ?itemLabel ?abbr ?oin WHERE {
  ?item wdt:P31 wd:Q3143387 .
  OPTIONAL { ?item wdt:P1813 ?abbr }
  OPTIONAL { ?item wdt:P9947 ?oin }
  OPTIONAL { ?item rdfs:label ?itemLabel . FILTER(LANG(?itemLabel) = "nl") }
}
""",
    "gemeente": _QLEVER_PREFIXES + """
SELECT ?item ?itemLabel ?abbr ?oin WHERE {
  ?item wdt:P31 wd:Q2039348 .
  OPTIONAL { ?item wdt:P1813 ?abbr }
  OPTIONAL { ?item wdt:P9947 ?oin }
  OPTIONAL { ?item rdfs:label ?itemLabel . FILTER(LANG(?itemLabel) = "nl") }
}
""",
    "provincie": _QLEVER_PREFIXES + """
SELECT ?item ?itemLabel ?abbr ?oin WHERE {
  ?item wdt:P31 wd:Q134390 .
  OPTIONAL { ?item wdt:P1813 ?abbr }
  OPTIONAL { ?item wdt:P9947 ?oin }
  OPTIONAL { ?item rdfs:label ?itemLabel . FILTER(LANG(?itemLabel) = "nl") }
}
""",
    "waterschap": _QLEVER_PREFIXES + """
SELECT ?item ?itemLabel ?abbr ?oin WHERE {
  ?item wdt:P31 wd:Q702081 .
  OPTIONAL { ?item wdt:P1813 ?abbr }
  OPTIONAL { ?item wdt:P9947 ?oin }
  OPTIONAL { ?item rdfs:label ?itemLabel . FILTER(LANG(?itemLabel) = "nl") }
}
""",
}


# Alle Tweede Kamerleden (huidig + historisch). P39 = position held,
# Q18887908 = lid van de Tweede Kamer; P9213 = TK-persoon-ID.
PERSON_QUERY = """
SELECT ?person ?personLabel ?tkid ?birthyear ?familyLabel WHERE {
  ?person wdt:P39 wd:Q18887908 .
  OPTIONAL { ?person wdt:P9213 ?tkid }
  OPTIONAL { ?person wdt:P569 ?birth . BIND(YEAR(?birth) AS ?birthyear) }
  OPTIONAL { ?person wdt:P734 ?family }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "nl,en". }
}
"""


# ---------------------------------------------------------------------------
# Bewindspersoon-queries (1945-heden)
# ---------------------------------------------------------------------------
#
# We splitsen per ambtstype. Per query halen we alle holders (P39) op van
# elke positie die instance is van de meta-class. Dat dekt zowel huidige
# (Q19334526 minister, Q1847103 staatssecretaris) als historische posten,
# zolang Wikidata ze als instance van die class heeft.
#
# Ministerie wordt geleverd via P642 ("van toepassing op") op het P39-statement.
# Voor historische posten zoals Q3058109 ("minister-president van Nederland")
# heeft P39 zelf geen P642; we bouwen post-id's dan op basis van de role-Q.

#
# We zoeken posities (?role) waarvan jurisdictie Nederland (P1001 = Q55) is en
# waarvan het label "minister" of "staatssecretaris" bevat. Dit dekt zowel de
# huidige als historische ministerposten in Wikidata, ongeacht hun P31-class.
# Per holder leveren we ?ministry uit de role's eigen P642 als die er is, of
# anders via een NL-fallback (minister-president → AZ etc.) in de mapping.

BEWINDSPERSOON_QUERIES: dict[str, str] = {
    "minister": """
SELECT ?person ?personLabel ?tkid ?birthyear ?familyLabel ?role ?roleLabel ?ministry ?ministryLabel ?start ?end WHERE {
  VALUES ?jurisdiction { wd:Q55 wd:Q29999 }
  ?role wdt:P1001 ?jurisdiction .
  ?role rdfs:label ?roleLabel . FILTER(LANG(?roleLabel) = "nl")
  FILTER(REGEX(LCASE(?roleLabel), "^(nederlands\\\\s+)?minister"))
  FILTER(!CONTAINS(LCASE(?roleLabel), "staatssecretaris"))
  FILTER(!CONTAINS(LCASE(?roleLabel), "ministerie"))
  FILTER(!CONTAINS(LCASE(?roleLabel), "ministerraad"))
  ?person p:P39 ?stmt .
  ?stmt ps:P39 ?role .
  OPTIONAL { ?role wdt:P642 ?ministry }
  OPTIONAL { ?stmt pq:P580 ?start }
  OPTIONAL { ?stmt pq:P582 ?end }
  OPTIONAL { ?person wdt:P9213 ?tkid }
  OPTIONAL { ?person wdt:P569 ?birth . BIND(YEAR(?birth) AS ?birthyear) }
  OPTIONAL { ?person wdt:P734 ?family }
  FILTER(!BOUND(?start) || ?start >= "1945-01-01T00:00:00Z"^^xsd:dateTime)
  SERVICE wikibase:label { bd:serviceParam wikibase:language "nl,en". }
}
""",
    "staatssecretaris": """
SELECT ?person ?personLabel ?tkid ?birthyear ?familyLabel ?role ?roleLabel ?ministry ?ministryLabel ?start ?end WHERE {
  VALUES ?jurisdiction { wd:Q55 wd:Q29999 }
  ?role wdt:P1001 ?jurisdiction .
  ?role rdfs:label ?roleLabel . FILTER(LANG(?roleLabel) = "nl")
  FILTER(CONTAINS(LCASE(?roleLabel), "staatssecretaris"))
  ?person p:P39 ?stmt .
  ?stmt ps:P39 ?role .
  OPTIONAL { ?role wdt:P642 ?ministry }
  OPTIONAL { ?stmt pq:P580 ?start }
  OPTIONAL { ?stmt pq:P582 ?end }
  OPTIONAL { ?person wdt:P9213 ?tkid }
  OPTIONAL { ?person wdt:P569 ?birth . BIND(YEAR(?birth) AS ?birthyear) }
  OPTIONAL { ?person wdt:P734 ?family }
  FILTER(!BOUND(?start) || ?start >= "1945-01-01T00:00:00Z"^^xsd:dateTime)
  SERVICE wikibase:label { bd:serviceParam wikibase:language "nl,en". }
}
""",
}


# ABD-TMG huidige bezetting: SG en DG. De specifieke posities staan in
# Wikidata onder generieke "ambt"-class (Q4164871); we filteren op label
# en jurisdictie NL. roleType wordt afgeleid van het label-prefix.
ABD_TMG_QUERY = """
SELECT ?person ?personLabel ?tkid ?birthyear ?familyLabel ?role ?roleLabel ?ministry ?ministryLabel ?start ?end ?roleType WHERE {
  VALUES ?jurisdiction { wd:Q55 wd:Q29999 }
  ?role wdt:P1001 ?jurisdiction .
  ?role rdfs:label ?roleLabel . FILTER(LANG(?roleLabel) = "nl")
  FILTER(CONTAINS(LCASE(?roleLabel), "secretaris-generaal") || CONTAINS(LCASE(?roleLabel), "directeur-generaal"))
  ?person p:P39 ?stmt .
  ?stmt ps:P39 ?role .
  OPTIONAL { ?role wdt:P642 ?ministry }
  OPTIONAL { ?stmt pq:P580 ?start }
  OPTIONAL { ?stmt pq:P582 ?end }
  OPTIONAL { ?person wdt:P9213 ?tkid }
  OPTIONAL { ?person wdt:P569 ?birth . BIND(YEAR(?birth) AS ?birthyear) }
  OPTIONAL { ?person wdt:P734 ?family }
  FILTER(!BOUND(?end))
  SERVICE wikibase:label { bd:serviceParam wikibase:language "nl,en". }
}
"""


_QLEVER_BW_PREFIXES = """PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX p: <http://www.wikidata.org/prop/>
PREFIX ps: <http://www.wikidata.org/prop/statement/>
PREFIX pq: <http://www.wikidata.org/prop/qualifier/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
"""

# QLever ondersteunt geen wikibase:label-service; we gebruiken rdfs:label met taal-filter.
BEWINDSPERSOON_QUERIES_QLEVER: dict[str, str] = {
    "minister": _QLEVER_BW_PREFIXES + """
SELECT ?person ?personLabel ?tkid ?birthyear ?familyLabel ?role ?roleLabel ?ministry ?ministryLabel ?start ?end WHERE {
  VALUES ?jurisdiction { wd:Q55 wd:Q29999 }
  ?role wdt:P1001 ?jurisdiction .
  ?role rdfs:label ?roleLabel . FILTER(LANG(?roleLabel) = "nl")
  FILTER(REGEX(LCASE(?roleLabel), "^(nederlands\\\\s+)?minister"))
  FILTER(!CONTAINS(LCASE(?roleLabel), "staatssecretaris"))
  FILTER(!CONTAINS(LCASE(?roleLabel), "ministerie"))
  FILTER(!CONTAINS(LCASE(?roleLabel), "ministerraad"))
  ?person p:P39 ?stmt .
  ?stmt ps:P39 ?role .
  OPTIONAL { ?role wdt:P642 ?ministry .
             OPTIONAL { ?ministry rdfs:label ?ministryLabel . FILTER(LANG(?ministryLabel) = "nl") } }
  OPTIONAL { ?stmt pq:P580 ?start }
  OPTIONAL { ?stmt pq:P582 ?end }
  OPTIONAL { ?person wdt:P9213 ?tkid }
  OPTIONAL { ?person wdt:P569 ?birth . BIND(YEAR(?birth) AS ?birthyear) }
  OPTIONAL { ?person wdt:P734 ?family . OPTIONAL { ?family rdfs:label ?familyLabel . FILTER(LANG(?familyLabel) = "nl") } }
  OPTIONAL { ?person rdfs:label ?personLabel . FILTER(LANG(?personLabel) = "nl") }
  FILTER(!BOUND(?start) || ?start >= "1945-01-01T00:00:00Z"^^xsd:dateTime)
}
""",
    "staatssecretaris": _QLEVER_BW_PREFIXES + """
SELECT ?person ?personLabel ?tkid ?birthyear ?familyLabel ?role ?roleLabel ?ministry ?ministryLabel ?start ?end WHERE {
  VALUES ?jurisdiction { wd:Q55 wd:Q29999 }
  ?role wdt:P1001 ?jurisdiction .
  ?role rdfs:label ?roleLabel . FILTER(LANG(?roleLabel) = "nl")
  FILTER(CONTAINS(LCASE(?roleLabel), "staatssecretaris"))
  ?person p:P39 ?stmt .
  ?stmt ps:P39 ?role .
  OPTIONAL { ?role wdt:P642 ?ministry .
             OPTIONAL { ?ministry rdfs:label ?ministryLabel . FILTER(LANG(?ministryLabel) = "nl") } }
  OPTIONAL { ?stmt pq:P580 ?start }
  OPTIONAL { ?stmt pq:P582 ?end }
  OPTIONAL { ?person wdt:P9213 ?tkid }
  OPTIONAL { ?person wdt:P569 ?birth . BIND(YEAR(?birth) AS ?birthyear) }
  OPTIONAL { ?person wdt:P734 ?family . OPTIONAL { ?family rdfs:label ?familyLabel . FILTER(LANG(?familyLabel) = "nl") } }
  OPTIONAL { ?person rdfs:label ?personLabel . FILTER(LANG(?personLabel) = "nl") }
  FILTER(!BOUND(?start) || ?start >= "1945-01-01T00:00:00Z"^^xsd:dateTime)
}
""",
}

ABD_TMG_QUERY_QLEVER = _QLEVER_BW_PREFIXES + """
SELECT ?person ?personLabel ?tkid ?birthyear ?familyLabel ?role ?roleLabel ?ministry ?ministryLabel ?start ?end WHERE {
  VALUES ?jurisdiction { wd:Q55 wd:Q29999 }
  ?role wdt:P1001 ?jurisdiction .
  ?role rdfs:label ?roleLabel . FILTER(LANG(?roleLabel) = "nl")
  FILTER(CONTAINS(LCASE(?roleLabel), "secretaris-generaal") || CONTAINS(LCASE(?roleLabel), "directeur-generaal"))
  ?person p:P39 ?stmt .
  ?stmt ps:P39 ?role .
  OPTIONAL { ?role wdt:P642 ?ministry .
             OPTIONAL { ?ministry rdfs:label ?ministryLabel . FILTER(LANG(?ministryLabel) = "nl") } }
  OPTIONAL { ?stmt pq:P580 ?start }
  OPTIONAL { ?stmt pq:P582 ?end }
  OPTIONAL { ?person wdt:P9213 ?tkid }
  OPTIONAL { ?person wdt:P569 ?birth . BIND(YEAR(?birth) AS ?birthyear) }
  OPTIONAL { ?person wdt:P734 ?family . OPTIONAL { ?family rdfs:label ?familyLabel . FILTER(LANG(?familyLabel) = "nl") } }
  OPTIONAL { ?person rdfs:label ?personLabel . FILTER(LANG(?personLabel) = "nl") }
  FILTER(!BOUND(?end))
}
"""


# ---------------------------------------------------------------------------
# Mapping van ministerie-Q-id naar polder-org-slug
# ---------------------------------------------------------------------------
#
# Bevat (a) huidige ministeries en (b) bekende historische voorgangers.
# Waar Wikidata een historische Q-id geeft die we niet kennen, mappen we 'm
# naar het beste huidige equivalent. Voor onbekende Q-id's slaan we het
# mandaat over (en loggen het), in plaats van een fout te raden.
#
# De huidige set wordt automatisch aangevuld vanuit
# ``data/organisaties/ministeries/*.yaml`` via ``load_ministry_qid_map``,
# zodat lokale bewerkingen automatisch meegenomen worden.

MINISTRY_QID_TO_SLUG: dict[str, str] = {
    # Huidige ministeries (mei 2026; auto-bevestigd uit data/organisaties).
    "Q939757": "min-az",
    "Q1037495": "min-bz",
    "Q2491421": "min-bzk",
    "Q2119820": "min-def",
    "Q2986560": "min-ezk",
    "Q1037511": "min-fin",
    "Q2188136": "min-ienw",
    "Q1041343": "min-jenv",
    "Q127057188": "min-kgg",
    "Q898882": "min-lvvn",
    "Q1049362": "min-ocw",
    "Q1049328": "min-szw",
    "Q95543594": "min-vro",
    "Q1056190": "min-vws",
    "Q127256306": "min-aenm",
    # Bekende historische voorgangers / opvolgers (post-1945) → huidig equivalent.
    # Bewust conservatief; alleen waar de mapping ondubbelzinnig is.
    "Q1064819": "min-ienw",   # Verkeer en Waterstaat → I&W
    "Q1818236": "min-ienw",   # Verkeer en Waterstaat (oud) → I&W
    "Q1063531": "min-ienw",   # VROM (deel) → I&W
    "Q1782184": "min-ienw",   # VenW oud
    "Q2630990": "min-vws",    # WVC → VWS
    "Q2630999": "min-vws",    # WVC variant
    "Q1769526": "min-szw",    # Sociale Zaken oud
    "Q1782183": "min-ocw",    # OCenW
    "Q1782177": "min-ocw",    # O&W oud
    "Q1782182": "min-ezk",    # Economische Zaken oud (single name)
    "Q1782180": "min-ezk",    # Economische Zaken (alias)
    "Q1769528": "min-bz",     # BuZa oud
    "Q1037517": "min-fin",    # Financien (alias)
    "Q1782178": "min-jenv",   # Justitie oud
    "Q1769527": "min-jenv",   # Justitie alias
    "Q1782181": "min-lvvn",   # Landbouw oud
    "Q1782185": "min-lvvn",   # LNV
    "Q1782179": "min-bzk",    # BiZa oud
    "Q1782176": "min-def",    # Defensie alias
    # Min-pres / Algemene Zaken: Q3058109 is de positie zelf (niet een ministerie).
    "Q939757": "min-az",
}


# Q-id's voor historische posten die niet (altijd) een P642-ministerie hebben.
# Map de role-Q direct op een ministerie-slug, zodat we tóch een mandaat
# kunnen bouwen.
ROLE_QID_TO_MINISTRY_SLUG: dict[str, str] = {
    "Q3058109": "min-az",  # minister-president van Nederland
}


def load_ministry_qid_map(data_root: Path) -> dict[str, str]:
    """Bouw {wikidata-qid → slug} uit ``data/organisaties/ministeries/*.yaml``.

    Combineert huidige Q-id's uit YAML met de hand-gecode historische
    voorganger-mapping in ``MINISTRY_QID_TO_SLUG``.
    """
    mapping: dict[str, str] = dict(MINISTRY_QID_TO_SLUG)
    folder = data_root / "organisaties" / "ministeries"
    if not folder.exists():
        return mapping
    for path in folder.glob("*.yaml"):
        data = _read_yaml(path)
        if not data:
            continue
        org_id = data.get("id") or ""
        if not org_id.startswith("org:min-"):
            continue
        slug = org_id[len("org:") :]
        qid = (data.get("identifiers") or {}).get("wikidata")
        if qid:
            mapping[qid] = slug
    return mapping

PERSON_QUERY_QLEVER = _QLEVER_PREFIXES + """
SELECT ?person ?personLabel ?tkid ?birthyear ?familyLabel WHERE {
  ?person wdt:P39 wd:Q18887908 .
  OPTIONAL { ?person wdt:P9213 ?tkid }
  OPTIONAL { ?person wdt:P569 ?birth . BIND(YEAR(?birth) AS ?birthyear) }
  OPTIONAL {
    ?person wdt:P734 ?family .
    OPTIONAL { ?family rdfs:label ?familyLabel . FILTER(LANG(?familyLabel) = "nl") }
  }
  OPTIONAL { ?person rdfs:label ?personLabel . FILTER(LANG(?personLabel) = "nl") }
}
"""


# ---------------------------------------------------------------------------
# Naam-historie (P1448 official_name + P1813 short_name met qualifiers)
# ---------------------------------------------------------------------------
#
# Voor organisaties met een bekende Q-id halen we de hele naam-historie op:
# elke statement in P1448 (official_name) of P1813 (short_name), met
# qualifiers P580 (start time) en P582 (end time). We batchen Q-id's in
# één query via VALUES om HTTP-roundtrips te beperken.

NAME_HISTORY_BATCH_SIZE = 50

_QLEVER_NAME_HISTORY_PREFIXES = """PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX p: <http://www.wikidata.org/prop/>
PREFIX ps: <http://www.wikidata.org/prop/statement/>
PREFIX pq: <http://www.wikidata.org/prop/qualifier/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
"""


def _build_name_history_query(qids: list[str], *, qlever: bool) -> str:
    """Bouw een SPARQL-query die naam-statements + qualifiers ophaalt voor een set Q-id's.

    Voor elke Q-id worden alle P1448 (official_name) en P1813 (short_name)
    statements opgehaald, met qualifiers P580 (start time) en P582 (end time).
    Twee subqueries — één per property — ge-UNIONeerd, met een ?prop-discriminator
    zodat de parser ze uit elkaar kan houden.
    """
    if not qids:
        raise ValueError("qids is leeg")
    values = " ".join(f"wd:{qid}" for qid in qids)
    body = f"""
SELECT ?qid ?prop ?name ?start ?end WHERE {{
  VALUES ?qid {{ {values} }}
  {{
    ?qid p:P1448 ?stmt .
    ?stmt ps:P1448 ?name .
    BIND("official" AS ?prop)
    OPTIONAL {{ ?stmt pq:P580 ?start }}
    OPTIONAL {{ ?stmt pq:P582 ?end }}
    FILTER(LANG(?name) = "nl" || LANG(?name) = "")
  }}
  UNION
  {{
    ?qid p:P1813 ?stmt .
    ?stmt ps:P1813 ?name .
    BIND("short" AS ?prop)
    OPTIONAL {{ ?stmt pq:P580 ?start }}
    OPTIONAL {{ ?stmt pq:P582 ?end }}
    FILTER(LANG(?name) = "nl" || LANG(?name) = "")
  }}
}}
"""
    if qlever:
        return _QLEVER_NAME_HISTORY_PREFIXES + body
    return body


# ---------------------------------------------------------------------------
# HTTP + cache
# ---------------------------------------------------------------------------


_LAST_REQUEST_TIME: list[float] = [0.0]


def _rate_limit(interval: float = MIN_REQUEST_INTERVAL) -> None:
    """Wacht tot er minimaal ``interval`` s is verstreken sinds de vorige call."""
    now = time.monotonic()
    delta = now - _LAST_REQUEST_TIME[0]
    if delta < interval:
        time.sleep(interval - delta)
    _LAST_REQUEST_TIME[0] = time.monotonic()


def _query_hash(query: str) -> str:
    """Deterministische cache-key per query-tekst."""
    return hashlib.sha256(query.strip().encode("utf-8")).hexdigest()[:16]


def query_sparql(
    query: str,
    *,
    timeout: float = HTTP_TIMEOUT,
    cache_dir: Path | None = None,
    use_cache: bool = True,
    client: httpx.Client | None = None,
    max_retries: int = 10,
    endpoint: str = SPARQL_ENDPOINT,
    request_interval: float | None = None,
) -> list[dict[str, Any]]:
    """Voer een SPARQL-query uit en geef de bindings terug als lijst van dicts.

    Cached responses landen onder ``cache_dir/<hash>.json``.

    Endpoint:
    - URL of `qlever`/`wdqs`: één endpoint, retry binnen die endpoint.
    - `auto`: probeer QLever eerst (sneller), val terug op WDQS bij
      timeout/5xx. Cache-check gebeurt vóór elke endpoint-poging zodat
      een hit-bestand de keten kort sluit.
    """
    # Cache-check (idem voor "auto" en losse endpoints)
    cache_path: Path | None = None
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"{_query_hash(query)}.json"
        if use_cache and cache_path.exists() and cache_path.stat().st_size > 0:
            logger.debug("Wikidata cache hit: %s", cache_path)
            with cache_path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
            return list(payload.get("results", {}).get("bindings", []))

    if endpoint == "auto":
        last_exc: Exception | None = None
        for alias in _AUTO_FALLBACK_CHAIN:
            if _endpoint_is_circuit_broken(alias):
                logger.debug("Skip endpoint %s (circuit broken)", alias)
                continue
            try:
                result = query_sparql(
                    query,
                    timeout=min(timeout, 15.0),
                    cache_dir=cache_dir,
                    use_cache=use_cache,
                    client=client,
                    max_retries=1,
                    endpoint=alias,
                    request_interval=request_interval,
                )
                _mark_endpoint_success(alias)
                return result
            except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
                last_exc = exc
                _mark_endpoint_failure(alias)
                logger.warning(
                    "Endpoint %s faalde (%s), val terug op volgende in keten",
                    alias,
                    type(exc).__name__,
                )
        if last_exc:
            raise last_exc
        raise httpx.HTTPError("auto-keten leverde geen response")

    if request_interval is None:
        request_interval = (
            QLEVER_REQUEST_INTERVAL if "qlever" in endpoint else MIN_REQUEST_INTERVAL
        )

    # Endpoint-alias vertalen naar URL
    if endpoint in _ENDPOINT_URLS:
        endpoint = _ENDPOINT_URLS[endpoint]

    headers = {
        "Accept": "application/sparql-results+json",
        "User-Agent": USER_AGENT,
    }
    params = {"query": query, "format": "json"}

    def _do_call() -> httpx.Response:
        _rate_limit(request_interval)
        if client is None:
            with httpx.Client(timeout=timeout, follow_redirects=True) as inner:
                return inner.get(endpoint, params=params, headers=headers)
        return client.get(endpoint, params=params, headers=headers)

    backoff = 2.0
    for attempt in range(max_retries + 1):
        try:
            response = _do_call()
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError) as exc:
            # Tijdens WDQS-outages krijgen we soms timeouts ipv 5xx. Behandel als retry-bar.
            if attempt >= max_retries:
                raise
            wait = min(backoff, 60.0)
            logger.warning(
                "Wikidata SPARQL netwerk-error %s op poging %d/%d, wacht %.1fs",
                type(exc).__name__,
                attempt + 1,
                max_retries + 1,
                wait,
            )
            time.sleep(wait)
            backoff = min(backoff * 2, 120.0)
            continue
        if response.status_code in (429, 502, 503, 504):
            retry_after = response.headers.get("retry-after") if hasattr(response, "headers") else None
            try:
                wait = float(retry_after) if retry_after else backoff
            except (TypeError, ValueError):
                wait = backoff
            # Tijdens een actieve WDQS-outage stuurt Varnish een Retry-After
            # header van honderden tot duizend seconden. Voor 429 respecteren
            # we dat (anders raken we IP-banned), gecapt op 20 minuten zodat
            # we niet eindeloos wachten. Voor 5xx blijven we agressiever.
            if response.status_code == 429:
                wait = min(max(wait, 60.0), 1200.0)
            else:
                wait = min(max(wait, 1.0), 60.0)
            logger.warning(
                "Wikidata SPARQL %s op poging %d/%d, wacht %.1fs",
                response.status_code,
                attempt + 1,
                max_retries + 1,
                wait,
            )
            if attempt >= max_retries:
                response.raise_for_status()
            time.sleep(wait)
            backoff = min(backoff * 2, 120.0)
            continue
        response.raise_for_status()
        # WDQS retourneert tijdens outages soms 200 OK met truncated/corrupte JSON.
        # Als we de body niet kunnen parsen, retry alsof het een transient was.
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            if attempt >= max_retries:
                raise
            wait = min(backoff, 60.0)
            logger.warning(
                "Wikidata SPARQL JSON-parse-error op poging %d/%d (%s), wacht %.1fs",
                attempt + 1,
                max_retries + 1,
                exc,
                wait,
            )
            time.sleep(wait)
            backoff = min(backoff * 2, 120.0)
            continue
        break
    else:
        # max_retries volledig opgebruikt zonder success → expliciet falen.
        raise httpx.HTTPError("Wikidata SPARQL retries uitgeput")

    if cache_path is not None:
        with cache_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)

    return list(payload.get("results", {}).get("bindings", []))


# ---------------------------------------------------------------------------
# Binding parsing
# ---------------------------------------------------------------------------


_QID_RE = re.compile(r"(Q\d+)$")


def extract_qid(uri: str | None) -> str | None:
    """Pak de Q-id uit een Wikidata IRI: `http://www.wikidata.org/entity/Q123` → `Q123`."""
    if not uri:
        return None
    match = _QID_RE.search(uri)
    return match.group(1) if match else None


def _value(binding: dict[str, Any], key: str) -> str | None:
    cell = binding.get(key)
    if not cell:
        return None
    val = cell.get("value")
    if val is None or val == "":
        return None
    return str(val)


def parse_org_bindings(bindings: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map SPARQL-bindings voor organisaties op {qid, label, abbr, oin}."""
    rows: list[dict[str, Any]] = []
    for b in bindings:
        qid = extract_qid(_value(b, "item"))
        if not qid:
            continue
        rows.append(
            {
                "qid": qid,
                "label": _value(b, "itemLabel"),
                "abbr": _value(b, "abbr"),
                "oin": _value(b, "oin"),
            }
        )
    return rows


def _parse_birthyear(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        # Wikidata levert P569 als ISO datetime soms; sla het jaar uit beide vormen.
        if "-" in raw or "T" in raw:
            return int(raw[:4].lstrip("+"))
        return int(raw)
    except ValueError:
        return None


def _parse_iso_date(raw: str | None) -> str | None:
    """Pak `YYYY-MM-DD` uit een Wikidata datetime-literal."""
    if not raw:
        return None
    s = raw.lstrip("+")
    # Voorbeelden: '2010-10-14T00:00:00Z', '1947-01-01T00:00:00Z'
    if len(s) >= 10:
        return s[:10]
    return None


def parse_person_bindings(bindings: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map SPARQL-bindings voor personen op {qid, label, tkid, birthyear, initials, family}."""
    rows: list[dict[str, Any]] = []
    for b in bindings:
        qid = extract_qid(_value(b, "person"))
        if not qid:
            continue
        birthyear_raw = _value(b, "birthyear")
        birthyear: int | None = None
        if birthyear_raw is not None:
            try:
                birthyear = int(birthyear_raw)
            except ValueError:
                birthyear = None
        rows.append(
            {
                "qid": qid,
                "label": _value(b, "personLabel"),
                "tkid": _value(b, "tkid"),
                "birthyear": birthyear,
                "initials": _value(b, "initials"),
                "family": _value(b, "familyLabel") or _value(b, "family"),
            }
        )
    return rows


def _escape_sparql_literal(value: str) -> str:
    """Escape een string voor invoeging als SPARQL-literal (tussen `"..."`)."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _build_lookup_person_query(
    family: str,
    given: str | None,
    initials: str | None,
) -> str:
    """Bouw een QLever-compatibele SPARQL-query voor name-based persoon-lookup.

    Strategie: filter op P31 wd:Q5 (mens) en op rdfs:label dat de family-naam
    bevat. Als ``given`` of ``initials`` zijn meegegeven, voeg een extra
    CONTAINS-filter toe op het label. Levert max 25 kandidaten.
    """
    family_lit = _escape_sparql_literal(family.strip())
    label_filters = [
        f'CONTAINS(LCASE(?label), LCASE("{family_lit}"))',
    ]
    if given:
        label_filters.append(
            f'CONTAINS(LCASE(?label), LCASE("{_escape_sparql_literal(given.strip())}"))'
        )
    elif initials:
        first_letter = initials.strip()[0:1]
        if first_letter and first_letter.isalpha():
            label_filters.append(
                f'CONTAINS(LCASE(?label), LCASE("{_escape_sparql_literal(first_letter)}"))'
            )
    label_filter_clause = " && ".join(label_filters)
    return _QLEVER_PREFIXES + f"""PREFIX schema: <http://schema.org/>
SELECT ?person ?label ?birthyear ?description WHERE {{
  ?person wdt:P31 wd:Q5 .
  ?person rdfs:label ?label .
  FILTER(LANG(?label) = "nl" || LANG(?label) = "en")
  FILTER({label_filter_clause})
  OPTIONAL {{ ?person wdt:P569 ?birth . BIND(YEAR(?birth) AS ?birthyear) }}
  OPTIONAL {{ ?person schema:description ?description .
             FILTER(LANG(?description) = "nl") }}
}}
LIMIT 25
"""


def lookup_person_by_name(
    family: str,
    initials: str | None = None,
    given: str | None = None,
    *,
    endpoint: Endpoint | str = "auto",
    cache_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Zoek personen in Wikidata op naam.

    Returns een lijst kandidaten van de vorm
    ``{qid, label, birth_year, description}``. Lege lijst als geen match.

    Strategie:

    1. SPARQL met FILTER op rdfs:label, beperkt via P31 wd:Q5 (mens). Default
       endpoint is `auto`: QLever eerst, dan WDQS. Cache wordt geraadpleegd
       voor elke poging.
    2. Als beide SPARQL-endpoints falen, probeert deze helper de Wikidata
       Reconciliation API als laatste fallback (`reconciliation_lookup_person`).

    Caller-side scoring (zoals naam-overeenkomst en context-fit) gebeurt buiten
    deze helper. Hier doen we alleen de I/O en parsing.
    """
    if not family or not family.strip():
        raise ValueError("family is verplicht voor lookup_person_by_name")
    # Voor person-lookup proberen we ALLEEN QLever via SPARQL. WDQS heeft een
    # te scherpe limiet op label-FILTER queries (timeout op de meeste calls)
    # en is niet geschikt voor deze vorm van zoekopdracht. Bij QLever-failure
    # gaan we direct naar de Reconciliation API. Bij circuit-broken QLever
    # slaan we SPARQL helemaal over en gaan direct naar Reconciliation.
    if endpoint == "auto":
        if _endpoint_is_circuit_broken("qlever"):
            return reconciliation_lookup_person(family, given=given, initials=initials)
        endpoint_url = QLEVER_ENDPOINT
    else:
        endpoint_url = resolve_endpoint(endpoint)
    query = _build_lookup_person_query(family, given, initials)
    try:
        # Korte timeout en geen retries: bij QLever-down willen we snel
        # de circuit breaker triggeren en doorschakelen naar Reconciliation.
        bindings = query_sparql(
            query,
            cache_dir=cache_dir,
            endpoint=endpoint_url,
            max_retries=0,
            timeout=5.0,
        )
        _mark_endpoint_success("qlever")
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
        _mark_endpoint_failure("qlever")
        logger.warning(
            "SPARQL-lookup faalde voor (%s, %s), val terug op Reconciliation API: %s",
            family,
            given,
            type(exc).__name__,
        )
        return reconciliation_lookup_person(family, given=given, initials=initials)

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for b in bindings:
        qid = extract_qid(_value(b, "person"))
        if not qid or qid in seen:
            continue
        seen.add(qid)
        birthyear_raw = _value(b, "birthyear")
        birth_year: int | None = None
        if birthyear_raw is not None:
            try:
                birth_year = int(birthyear_raw)
            except ValueError:
                birth_year = None
        rows.append(
            {
                "qid": qid,
                "label": _value(b, "label"),
                "birth_year": birth_year,
                "description": _value(b, "description"),
            }
        )
    return rows


# Wikidata Reconciliation API endpoint. Documentatie:
# https://www.wikidata.org/wiki/Wikidata:Tools/OpenRefine/Editing/Reconciliation_API
# We gebruiken de wmcloud-host direct (skip 307-redirect van reconci.link).
RECONCILIATION_ENDPOINT = "https://wikidata-reconciliation.wmcloud.org/nl/api"

# Max queries per batch-POST. De API zelf accepteert er meer maar bij grotere
# batches groeit de kans op timeouts. 25 is een goede sweet-spot.
RECONCILIATION_BATCH_SIZE = 25


def reconciliation_lookup_person(
    family: str,
    *,
    given: str | None = None,
    initials: str | None = None,
    timeout: float = 15.0,
) -> list[dict[str, Any]]:
    """Fallback-lookup via OpenRefine Reconciliation API.

    Werkt fundamenteel anders dan SPARQL: POST een JSON-batch met queries,
    krijg per query een lijst kandidaten terug. We doen één query per call
    omdat onze caller per-record werkt.

    Retourneert dezelfde vorm als `lookup_person_by_name`:
    `{qid, label, birth_year, description}`. `birth_year` is altijd None
    (Reconciliation API levert die niet direct; vereist een tweede call).

    Bij netwerk-error: lege lijst (failure mode is fall-through naar
    "geen kandidaten"). Het is een laatste-redmiddel; we blokkeren een
    enrich-run niet als ook deze faalt.
    """
    query_parts = [family]
    if given:
        query_parts.insert(0, given)
    elif initials:
        query_parts.insert(0, initials)
    query_text = " ".join(query_parts).strip()
    if not query_text:
        return []

    payload = {
        "q0": {
            "query": query_text,
            "type": "Q5",  # mens
            "limit": 25,
        }
    }
    # OpenRefine reconciliation v0.2 verwacht een form-encoded body waarbij
    # `queries` een JSON-string is.
    form_data = {"queries": json.dumps(payload)}
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.post(
                RECONCILIATION_ENDPOINT,
                data=form_data,
                headers={"User-Agent": USER_AGENT},
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("Reconciliation API faalde: %s", exc)
        return []

    result = data.get("q0") or {}
    return _parse_reconciliation_candidates(result.get("result") or [])


def _parse_reconciliation_candidates(candidates: list[dict]) -> list[dict[str, Any]]:
    """Map Reconciliation-result-records naar onze standaard-vorm."""
    rows: list[dict[str, Any]] = []
    for cand in candidates:
        qid = cand.get("id")
        if not qid or not qid.startswith("Q"):
            continue
        description = cand.get("description") or ""
        rows.append(
            {
                "qid": qid,
                "label": cand.get("name"),
                "birth_year": _parse_birth_year_from_description(description),
                "description": description,
            }
        )
    return rows


def reconciliation_lookup_persons_batch(
    queries: list[tuple[str, str | None, str | None]],
    *,
    timeout: float = 30.0,
) -> list[list[dict[str, Any]]]:
    """Batch-lookup: meerdere personen in één POST naar de Reconciliation API.

    Input: lijst van (family, given, initials)-tuples. Output: lijst van
    kandidaten-lijsten in dezelfde volgorde. Empty list per query bij geen
    match. Voor batches > RECONCILIATION_BATCH_SIZE wordt automatisch
    opgedeeld.

    Bij netwerk-error: lege lijsten voor de hele batch (graceful degradation).
    """
    if not queries:
        return []

    results: list[list[dict[str, Any]]] = []

    for chunk_start in range(0, len(queries), RECONCILIATION_BATCH_SIZE):
        chunk = queries[chunk_start : chunk_start + RECONCILIATION_BATCH_SIZE]
        payload: dict[str, dict[str, Any]] = {}
        for i, (family, given, initials) in enumerate(chunk):
            parts = []
            if given:
                parts.append(given)
            elif initials:
                parts.append(initials)
            parts.append(family)
            query_text = " ".join(p for p in parts if p).strip()
            if not query_text:
                continue
            payload[f"q{i}"] = {
                "query": query_text,
                "type": "Q5",
                "limit": 25,
            }

        if not payload:
            results.extend([] for _ in chunk)
            continue

        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                response = client.post(
                    RECONCILIATION_ENDPOINT,
                    data={"queries": json.dumps(payload)},
                    headers={"User-Agent": USER_AGENT},
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "Reconciliation API batch faalde (chunk start=%d, size=%d): %s",
                chunk_start,
                len(chunk),
                exc,
            )
            results.extend([] for _ in chunk)
            continue

        for i in range(len(chunk)):
            result = data.get(f"q{i}") or {}
            results.append(_parse_reconciliation_candidates(result.get("result") or []))

    return results


_BIRTH_YEAR_RX = re.compile(r"\((\d{4})(?:[\-–—][^)]*)?\)")


def _parse_birth_year_from_description(description: str) -> int | None:
    """Probeer een geboortejaar te extraheren uit een Reconciliation-description.

    Voorbeelden:
    - `"Nederlands ondernemer (1946-2025)"` → 1946
    - `"Nederlands voetballer (1904–1979)"` → 1904 (em-dash)
    - `"politicus uit Suriname (1897–1960)"` → 1897
    - `"Nederlandse politicus"` → None (geen jaartal)
    """
    if not description:
        return None
    match = _BIRTH_YEAR_RX.search(description)
    if not match:
        return None
    try:
        year = int(match.group(1))
        # Plausibele bovengrens: dit jaar + 1 (voor jaartal-overgangen aan het
        # einde van het jaar). Ondergrens 1700 is een conservatieve start
        # voor de Republiek der Verenigde Nederlanden.
        from datetime import date as _date_class

        if 1700 <= year <= _date_class.today().year + 1:
            return year
    except (ValueError, TypeError):
        pass
    return None


def parse_bewindspersoon_bindings(
    bindings: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Map bewindspersoon-bindings op rijen met persoon + post + mandaat-velden.

    Per binding krijg je één mandaat. Personen kunnen meerdere malen voorkomen
    (één rij per ambtsperiode).
    """
    rows: list[dict[str, Any]] = []
    for b in bindings:
        person_qid = extract_qid(_value(b, "person"))
        role_qid = extract_qid(_value(b, "role"))
        if not person_qid or not role_qid:
            continue
        rows.append(
            {
                "person_qid": person_qid,
                "person_label": _value(b, "personLabel"),
                "tkid": _value(b, "tkid"),
                "birthyear": _parse_birthyear(_value(b, "birthyear")),
                "family": _value(b, "familyLabel") or _value(b, "family"),
                "role_qid": role_qid,
                "role_label": _value(b, "roleLabel"),
                "ministry_qid": extract_qid(_value(b, "ministry")),
                "ministry_label": _value(b, "ministryLabel"),
                "start": _parse_iso_date(_value(b, "start")),
                "end": _parse_iso_date(_value(b, "end")),
            }
        )
    return rows


def parse_abd_tmg_bindings(
    bindings: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Map ABD-TMG bindings; role_type_qid wordt afgeleid van het role-label.

    Wikidata's SG/DG-meta-classes (Q2003810, Q126658544) worden niet via
    ``wdt:P31`` gelinkt aan de specifieke posities; we kennen alleen de
    posities zelf. Daarom bepalen we sg vs dg op basis van het label-prefix.
    """
    rows: list[dict[str, Any]] = []
    for b in bindings:
        person_qid = extract_qid(_value(b, "person"))
        role_qid = extract_qid(_value(b, "role"))
        if not person_qid or not role_qid:
            continue
        role_label = _value(b, "roleLabel") or ""
        rl_lower = role_label.lower()
        # Specifiek matchen: "secretaris-generaal" → SG; "directeur-generaal" → DG.
        # Maar "plaatsvervangend secretaris-generaal" of "directeur" zonder generaal valt af.
        role_type_qid: str | None = None
        if "secretaris-generaal" in rl_lower:
            role_type_qid = "Q2003810"
        elif "directeur-generaal" in rl_lower:
            role_type_qid = "Q126658544"
        # Fallback: expliciete roleType-binding (oude WDQS-query).
        if role_type_qid is None:
            role_type_qid = extract_qid(_value(b, "roleType"))
        rows.append(
            {
                "person_qid": person_qid,
                "person_label": _value(b, "personLabel"),
                "tkid": _value(b, "tkid"),
                "birthyear": _parse_birthyear(_value(b, "birthyear")),
                "family": _value(b, "familyLabel") or _value(b, "family"),
                "role_qid": role_qid,
                "role_label": role_label or None,
                "ministry_qid": extract_qid(_value(b, "ministry")),
                "ministry_label": _value(b, "ministryLabel"),
                "start": _parse_iso_date(_value(b, "start")),
                "end": _parse_iso_date(_value(b, "end")),
                "role_type_qid": role_type_qid,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _ascii_lower(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return decomposed.encode("ascii", "ignore").decode("ascii").lower()


_TUSSENVOEGSELS = {
    "van",
    "der",
    "den",
    "de",
    "het",
    "te",
    "ten",
    "ter",
    "op",
    "in",
    "aan",
    "bij",
    "tot",
    "uit",
    "voor",
    "vd",
    "vdr",
    "von",
    "le",
    "la",
    "du",
    "el",
    "al",
}


# Prefixen die in polder-organisatienamen voorkomen maar in Wikidata-labels niet
# (en omgekeerd). We strippen ze aan beide kanten voor name-matching.
_ORG_NOISE = (
    "ministerie van ",
    "ministerie ",
    "gemeente ",
    "provincie ",
    "waterschap ",
    "hoogheemraadschap van ",
    "hoogheemraadschap ",
    "wetterskip ",
)


def normalize_org_name(name: str | None) -> str:
    """Normaliseer een organisatienaam voor name-matching.

    Lowercase, ASCII, strip ruis-prefixen, collapse witruimte naar enkele spaties.
    """
    if not name:
        return ""
    s = _ascii_lower(name)
    s = s.replace("-", " ").replace(",", " ")
    s = re.sub(r"\s+", " ", s).strip()
    changed = True
    while changed:
        changed = False
        for prefix in _ORG_NOISE:
            if s.startswith(prefix):
                s = s[len(prefix) :].strip()
                changed = True
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _normalize_initials(value: str | None) -> str:
    """Compact-vorm voor matching-keys. Zie polder.lib.initials.compact_initials."""
    from polder.lib.initials import compact_initials

    return compact_initials(value)


def _normalize_family(value: str | None) -> str:
    """Normaliseer een familienaam: strip tussenvoegsels, ASCII, lowercase."""
    if not value:
        return ""
    base = _ascii_lower(value)
    base = re.sub(r"[^a-z0-9\s-]+", " ", base)
    parts = [p for p in re.split(r"[\s-]+", base) if p]
    family_parts = [p for p in parts if p not in _TUSSENVOEGSELS] or parts
    return "-".join(family_parts).strip("-")


def build_org_index(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Bouw een lookup-index op (oin → row) en (genormaliseerde naam → row).

    Returnt een dict met sub-dicts ``by_oin`` en ``by_name``. Als hetzelfde
    sleutel-veld twee maal voorkomt, wint de eerste; latere worden gelogd.
    """
    by_oin: dict[str, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}
    for row in rows:
        oin = row.get("oin")
        if oin:
            by_oin.setdefault(oin, row)
        label = row.get("label")
        norm = normalize_org_name(label)
        if norm:
            by_name.setdefault(norm, row)
        # Match ook op afkorting (zoals "BZK", "Aa en Maas") — alleen als die
        # geen botsing geeft met een naam.
        abbr = row.get("abbr")
        if abbr:
            abbr_norm = normalize_org_name(abbr)
            if abbr_norm and abbr_norm not in by_name:
                by_name[abbr_norm] = row
    return {"by_oin": by_oin, "by_name": by_name}


def build_person_index(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Bouw een lookup-index voor personen.

    Drie views:
    - `by_tkid`: tk_persoon_id -> row (één-op-één)
    - `by_natural`: (family, initials, birthyear) -> row (één-op-één,
      eerste-wint bij conflict)
    - `by_family_birth`: (family, birthyear) -> list[row] (collectie zodat de
      caller kan zien of er meerdere kandidaten zijn voordat hij koppelt)
    """
    by_tkid: dict[str, dict[str, Any]] = {}
    by_natural: dict[tuple[str, str, int], dict[str, Any]] = {}
    by_family_birth: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for row in rows:
        tkid = row.get("tkid")
        if tkid:
            by_tkid.setdefault(tkid, row)
        family = _normalize_family(row.get("family") or row.get("label"))
        initials = _normalize_initials(row.get("initials"))
        birthyear = row.get("birthyear")
        if family and birthyear:
            yr = int(birthyear)
            by_natural.setdefault((family, initials, yr), row)
            by_family_birth.setdefault((family, yr), []).append(row)
    return {
        "by_tkid": by_tkid,
        "by_natural": by_natural,
        "by_family_birth": by_family_birth,
    }


def _record_org_name(record: dict[str, Any]) -> str | None:
    names = record.get("names") or []
    if not names:
        return None
    return names[0].get("value") if isinstance(names[0], dict) else None


def _record_org_abbr(record: dict[str, Any]) -> str | None:
    names = record.get("names") or []
    if not names:
        return None
    return names[0].get("abbr") if isinstance(names[0], dict) else None


def _record_org_oin(record: dict[str, Any]) -> str | None:
    identifiers = record.get("identifiers") or {}
    return identifiers.get("oin")


def match_organisations(
    records: Iterable[dict[str, Any]],
    index: dict[str, dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any], str]]:
    """Match polder-organisatie-records aan Wikidata-rows.

    Returnt lijst van ``(record, wikidata_row, match_method)``.
    Match-volgorde: OIN > genormaliseerde naam > genormaliseerde afkorting.
    """
    matches: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    by_oin = index["by_oin"]
    by_name = index["by_name"]
    for record in records:
        if (record.get("identifiers") or {}).get("wikidata"):
            # Al gevuld; sla over zodat we lokale waarden niet overschrijven.
            continue
        oin = _record_org_oin(record)
        row = by_oin.get(oin) if oin else None
        method = "oin"
        if row is None:
            name = _record_org_name(record)
            norm = normalize_org_name(name)
            if norm:
                row = by_name.get(norm)
                method = "name"
        if row is None:
            abbr = _record_org_abbr(record)
            abbr_norm = normalize_org_name(abbr)
            if abbr_norm:
                row = by_name.get(abbr_norm)
                method = "abbr"
        if row is None:
            continue
        matches.append((record, row, method))
    return matches


def match_personen(
    records: Iterable[dict[str, Any]],
    index: dict[str, dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any], str]]:
    """Match polder-persoon-records aan Wikidata-rows.

    Match-volgorde: tk_persoon_id > (familienaam + initialen + geboortejaar).
    """
    matches: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    by_tkid = index["by_tkid"]
    by_natural = index["by_natural"]
    by_family_birth = index.get("by_family_birth", {})
    for record in records:
        if (record.get("identifiers") or {}).get("wikidata"):
            continue
        tkid = (record.get("identifiers") or {}).get("tk_persoon_id")
        row = by_tkid.get(tkid) if tkid else None
        method = "tkid"
        if row is None:
            name = record.get("name") or {}
            family = _normalize_family(name.get("family"))
            initials = _normalize_initials(name.get("initials"))
            birth = (record.get("birth") or {}).get("year")
            if family and birth is not None:
                yr = int(birth)
                row = by_natural.get((family, initials, yr))
                method = "natural"
                if row is None and initials:
                    # Family + birth-year fallback. Alleen koppelen als er
                    # PRECIES ÉÉN Wikidata-kandidaat is met die (family, year);
                    # anders zou een naamgenoot een verkeerde Q-id krijgen
                    # (zoals Dijk-1985: Emiel vs Jimmy beide bestaan).
                    candidates = by_family_birth.get((family, yr), [])
                    if len(candidates) == 1:
                        row = candidates[0]
                        method = "family_birth"
                    elif len(candidates) > 1:
                        logger.debug(
                            "Skip family_birth match voor %s: %d kandidaten op (%s, %d)",
                            record.get("id"),
                            len(candidates),
                            family,
                            yr,
                        )
        if row is None:
            continue
        matches.append((record, row, method))
    return matches


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------


def _today() -> str:
    return date.today().isoformat()


def _merge_sources(
    existing: list[dict[str, Any]] | None, new: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for src in existing or []:
        if isinstance(src, dict) and src.get("id"):
            by_id[src["id"]] = dict(src)
    for src in new or []:
        if isinstance(src, dict) and src.get("id"):
            by_id[src["id"]] = dict(src)
    return list(by_id.values())


def merge_wikidata_into_record(
    record: dict[str, Any], qid: str, *, today: str | None = None
) -> dict[str, Any]:
    """Voeg ``identifiers.wikidata = qid`` en de bron toe, zonder bestaande velden te overschrijven."""
    today_str = today or _today()
    merged = dict(record)
    identifiers = dict(merged.get("identifiers") or {})
    identifiers["wikidata"] = qid
    merged["identifiers"] = identifiers
    new_source = {
        "id": SOURCE_ID,
        "url": f"https://www.wikidata.org/wiki/{qid}",
        "retrieved": today_str,
    }
    merged["sources"] = _merge_sources(merged.get("sources"), [new_source])
    return merged


# ---------------------------------------------------------------------------
# Naam-historie parser + merge
# ---------------------------------------------------------------------------
#
# Per Q-id verzamelen we alle P1448-statements (en P1813 voor afkortingen) en
# zetten die om in name-entries volgens organisatie.schema.json:
# {value, abbr?, valid_from, valid_until?}.
#
# Default ``valid_from`` als geen P580-qualifier aanwezig is: ``1900-01-01``.
# Schema vereist een datum; we kunnen geen None laten staan.

_NAME_HISTORY_DEFAULT_FROM = "1900-01-01"


def parse_name_history_bindings(
    bindings: Iterable[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Aggregeer SPARQL-bindings per Q-id tot een lijst rauwe naam-statements.

    Returnt ``{qid: [{value, prop, valid_from, valid_until}, ...]}`` waarbij
    ``prop`` ∈ {"official", "short"}. Statements zonder waarde of zonder Q-id
    worden overgeslagen. Duplicaten (zelfde value/prop/start/end) worden
    gededupliceerd.
    """
    result: dict[str, list[dict[str, Any]]] = {}
    seen: dict[str, set[tuple[str, str, str | None, str | None]]] = {}
    for b in bindings:
        qid = extract_qid(_value(b, "qid"))
        if not qid:
            continue
        value = _value(b, "name")
        if not value:
            continue
        prop = _value(b, "prop") or "official"
        start = _parse_iso_date(_value(b, "start"))
        end = _parse_iso_date(_value(b, "end"))
        key = (prop, value, start, end)
        if qid not in seen:
            seen[qid] = set()
        if key in seen[qid]:
            continue
        seen[qid].add(key)
        result.setdefault(qid, []).append(
            {
                "value": value,
                "prop": prop,
                "valid_from": start,
                "valid_until": end,
            }
        )
    return result


def _build_name_variants(
    raw: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Combineer official- en short-name statements tot polder name-entries.

    Strategie:
      * Elke ``official``-statement levert een name-entry op met ``value``.
      * Een ``short``-statement met overlappende periode (zelfde of bevattend)
        koppelen we als ``abbr`` aan de bijbehorende official-entry.
      * Short-only statements (zonder matchende official) worden overgeslagen
        (we willen geen entry waarvan ``value`` een afkorting is).

    Output is gesorteerd op ``valid_from`` ascending; ``None`` valid_until
    blijft behouden.
    """
    officials = [r for r in raw if r["prop"] == "official"]
    shorts = [r for r in raw if r["prop"] == "short"]

    def overlap(a_from: str | None, a_until: str | None,
                b_from: str | None, b_until: str | None) -> bool:
        # Behandel onbekende grenzen als open: "" -> very early / very late.
        # End-dates zijn half-open (exclusive): A's end == B's start telt niet
        # als overlap (anders koppelt EZ-short aan EZK-official op 2017-10-26).
        a_lo = a_from or "0000-00-00"
        a_hi = a_until or "9999-99-99"
        b_lo = b_from or "0000-00-00"
        b_hi = b_until or "9999-99-99"
        return not (a_hi <= b_lo or b_hi <= a_lo)

    # Dedup officials op (genormaliseerde value): bij meerdere statements voor
    # dezelfde naam (komt voor als de naam meerdere periodes is gebruikt of
    # als Wikidata duplicate statements heeft) merge naar één entry met de
    # vroegste start en de laatste end. Zo voorkomen we YAML-rommel.
    by_value: dict[str, dict[str, Any]] = {}
    for off in officials:
        key = (off["value"] or "").strip().lower()
        if not key:
            continue
        cur = by_value.get(key)
        if cur is None:
            by_value[key] = dict(off)
            continue
        # Merge: vroegste start, laatste end. None-end (open) wint van een
        # concrete end-date.
        cur_from = cur.get("valid_from") or "9999-99-99"
        new_from = off.get("valid_from") or "9999-99-99"
        if new_from < cur_from:
            cur["valid_from"] = off.get("valid_from")
        cur_until = cur.get("valid_until")
        new_until = off.get("valid_until")
        if cur_until is None or new_until is None:
            cur["valid_until"] = None
        elif new_until > cur_until:
            cur["valid_until"] = new_until

    entries: list[dict[str, Any]] = []
    for off in by_value.values():
        entry: dict[str, Any] = {
            "value": off["value"],
            "valid_from": off["valid_from"] or _NAME_HISTORY_DEFAULT_FROM,
        }
        if off["valid_until"]:
            entry["valid_until"] = off["valid_until"]
        else:
            entry["valid_until"] = None
        # Zoek een matching short. Om collision te vermijden bij meerdere
        # officials met overlappende short: de eerste hit is genoeg.
        for sh in shorts:
            if overlap(off["valid_from"], off["valid_until"],
                       sh["valid_from"], sh["valid_until"]):
                entry["abbr"] = sh["value"]
                break
        entries.append(entry)
    # Sorteer op valid_from (oudst eerst).
    entries.sort(key=lambda e: e.get("valid_from") or "")
    return entries


def fetch_name_history(
    qids: Iterable[str],
    *,
    cache_dir: Path | None = None,
    endpoint: str = QLEVER_ENDPOINT,
    batch_size: int = NAME_HISTORY_BATCH_SIZE,
) -> dict[str, list[dict[str, Any]]]:
    """Haal naam-historie op voor een verzameling Wikidata-Q-id's.

    Returnt ``{qid: [name_variant, ...]}``. Voor Q-id's zonder P1448/P1813
    statements ontbreken in het resultaat (geen lege lijst).

    Q-id's worden gebatched in groepen van ``batch_size`` om SPARQL-queries
    onder de URL/timeout-limiet te houden.
    """
    qid_list = sorted({q for q in qids if q})
    if not qid_list:
        return {}
    endpoint_url = resolve_endpoint(endpoint)
    qlever = "qlever" in endpoint_url
    out: dict[str, list[dict[str, Any]]] = {}
    for i in range(0, len(qid_list), batch_size):
        batch = qid_list[i : i + batch_size]
        query = _build_name_history_query(batch, qlever=qlever)
        try:
            bindings = query_sparql(query, cache_dir=cache_dir, endpoint=endpoint_url)
        except httpx.HTTPError as exc:
            logger.warning(
                "Naam-historie-query faalde voor batch %d-%d: %s",
                i,
                i + len(batch),
                exc,
            )
            continue
        raw_per_qid = parse_name_history_bindings(bindings)
        for qid, raw in raw_per_qid.items():
            entries = _build_name_variants(raw)
            if entries:
                out[qid] = entries
    return out


def _name_key(entry: dict[str, Any]) -> str:
    """Normaliseer een name-entry voor matching: ASCII-lower, ruis-prefix strip."""
    return normalize_org_name(entry.get("value"))


def merge_names_into_record(
    record: dict[str, Any],
    name_history: list[dict[str, Any]],
) -> tuple[dict[str, Any], bool]:
    """Voeg historische namen toe aan ``record["names"]``. Returnt ``(merged, changed)``.

    Behoud-regels:
      * Bestaande names-entries blijven onaangeroerd qua ``value`` en ``abbr``
        (die zijn vaak met de hand geredigeerd of komen uit ROO).
      * Een Wikidata-entry wordt overgeslagen als de polder-records al een
        entry heeft met dezelfde genormaliseerde naam. We updaten dan wél
        ontbrekende ``valid_from``/``valid_until`` op de bestaande entry.
      * Wikidata-entries die niet matchen worden toegevoegd aan ``names[]``.
      * Resultaat wordt gesorteerd op ``valid_from`` ascending.

    Idempotent: als alle Wikidata-entries al matchen op naam én de
    valid_from/valid_until-velden zijn al gelijk, wordt ``changed=False``
    en wordt het record niet gewijzigd.
    """
    if not name_history:
        return record, False
    merged = dict(record)
    existing = list(merged.get("names") or [])
    by_key: dict[str, dict[str, Any]] = {}
    for e in existing:
        if isinstance(e, dict):
            k = _name_key(e)
            if k:
                by_key.setdefault(k, e)

    new_entries: list[dict[str, Any]] = []
    changed = False
    for wd in name_history:
        key = _name_key(wd)
        if not key:
            continue
        if key in by_key:
            # Bestaande entry: vul ontbrekende perioden aan vanuit Wikidata.
            existing_entry = by_key[key]
            if "valid_from" not in existing_entry and wd.get("valid_from"):
                existing_entry["valid_from"] = wd["valid_from"]
                changed = True
            if (
                "valid_until" not in existing_entry
                and wd.get("valid_until") is not None
            ):
                existing_entry["valid_until"] = wd["valid_until"]
                changed = True
            continue
        # Nieuwe entry uit Wikidata.
        entry: dict[str, Any] = {
            "value": wd["value"],
            "valid_from": wd.get("valid_from") or _NAME_HISTORY_DEFAULT_FROM,
        }
        if wd.get("abbr"):
            entry["abbr"] = wd["abbr"]
        if wd.get("valid_until") is not None:
            entry["valid_until"] = wd["valid_until"]
        new_entries.append(entry)
        changed = True

    if not changed:
        return record, False

    combined = existing + new_entries
    # Zorg dat elke entry een valid_from heeft (schema-vereist).
    for e in combined:
        if isinstance(e, dict) and not e.get("valid_from"):
            e["valid_from"] = _NAME_HISTORY_DEFAULT_FROM

    def sort_key(e: dict[str, Any]) -> str:
        return e.get("valid_from") or ""

    combined.sort(key=sort_key)
    merged["names"] = combined
    return merged, True


# ---------------------------------------------------------------------------
# Bewindspersoon / ABD record-bouw
# ---------------------------------------------------------------------------


def _slug_initials(initials: str | None) -> str:
    """Voor het slug-veld: `M.P.` → `mp`. Schema-strict normaliseert naar `M.P.`."""
    if not initials:
        return ""
    return re.sub(r"[^a-z0-9]+", "", _ascii_lower(initials))


def _format_initials(value: str | None) -> str | None:
    """Bouw `M.P.`-stijl initialen uit ruwe input (label, given name, etc.)."""
    if not value:
        return None
    cleaned = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    letters = re.findall(r"\b([A-Za-z])", cleaned)
    if not letters:
        return None
    return "".join(f"{ch.upper()}." for ch in letters)


def _slug_family(family: str | None) -> str:
    """Strip tussenvoegsels, ASCII, dash-join. Voor de polder-id slug."""
    if not family:
        return ""
    base = _ascii_lower(family)
    base = re.sub(r"[^a-z0-9\s-]+", " ", base)
    parts = [p for p in re.split(r"[\s-]+", base) if p]
    family_parts = [p for p in parts if p not in _TUSSENVOEGSELS] or parts
    slug = "-".join(family_parts)
    return re.sub(r"-+", "-", slug).strip("-")


_INITIALS_RE = re.compile(r"^([A-Z]\.?)+$")


def person_id_from_label(
    label: str | None,
    family: str | None,
    initials: str | None,
    birthyear: int | None,
    qid: str,
) -> tuple[str, dict[str, str]]:
    """Bouw een polder ``person:<slug>``-id en de bijbehorende name-block-velden.

    Initialen worden alléén in de slug en het name-block opgenomen als
    ``initials`` daadwerkelijk een initialen-string is (`M.`, `M.P.` of `MP`).
    Een voornaam zoals "Mark" wordt niet automatisch initialen.

    Returnt ``(slug, name_block)``. Als familienaam ontbreekt, fall back op
    Q-id (``person:q12345``) zodat we tenminste een geldige id hebben.
    """
    label = (label or "").strip()
    fam = family or ""
    given = ""
    if not fam and label:
        parts = label.split()
        if len(parts) >= 2:
            fam = parts[-1]
            given = " ".join(parts[:-1])
        else:
            fam = label
    elif label and family:
        # Probeer given uit label te halen door family eraf te halen.
        if label.endswith(family):
            given = label[: -len(family)].strip()
        elif label != family:
            parts = label.split()
            if parts and parts[0] != family:
                given = parts[0]
    inits_norm: str | None = None
    if initials:
        cleaned = unicodedata.normalize("NFKD", initials).encode("ascii", "ignore").decode("ascii")
        compact = re.sub(r"[^A-Za-z]", "", cleaned)
        if compact and compact == compact.upper() and len(compact) <= 4:
            # Het is écht een initialen-string als 'M.', 'M.P.' of 'MP' (max 4 letters).
            inits_norm = "".join(f"{ch.upper()}." for ch in compact)
    fam_slug = _slug_family(fam)
    init_slug = _slug_initials(inits_norm) if inits_norm else ""
    pieces: list[str] = []
    if fam_slug:
        pieces.append(fam_slug)
    if init_slug:
        pieces.append(init_slug)
    if birthyear is not None:
        pieces.append(str(birthyear))
    slug = "-".join(pieces) if pieces else qid.lower()
    slug = re.sub(r"[^a-z0-9-]+", "", slug).strip("-") or qid.lower()
    name_block: dict[str, str] = {"family": fam.strip() or label or qid}
    name_block["full"] = label or (fam.strip() if fam else qid)
    if inits_norm:
        name_block["initials"] = inits_norm
    if given:
        name_block["given"] = given.strip()
    return slug, name_block


# Substring-mapping van role-label fragmenten naar polder-slug.
# Volgorde is belangrijk: langere matches eerst.
_LABEL_FRAGMENTS_TO_SLUG: list[tuple[str, str]] = [
    ("algemene oorlogvoering", "min-az"),
    ("algemene zaken", "min-az"),
    ("buitenlandse zaken", "min-bz"),
    ("buitenlandse handel", "min-bz"),
    ("ontwikkelingssamenwerking", "min-bz"),
    ("ontwikkelingshulp", "min-bz"),
    ("binnenlandse zaken en koninkrijksrelaties", "min-bzk"),
    ("binnenlandse zaken", "min-bzk"),
    ("koninkrijksrelaties", "min-bzk"),
    ("wonen, wijken en integratie", "min-bzk"),
    ("wonen en rijksdienst", "min-bzk"),
    ("grote steden", "min-bzk"),
    ("defensie", "min-def"),
    ("oorlog", "min-def"),
    ("marine", "min-def"),
    ("economische zaken en klimaat", "min-ezk"),
    ("economische zaken, landbouw en innovatie", "min-ezk"),
    ("economische zaken", "min-ezk"),
    ("klimaat en energie", "min-kgg"),
    ("klimaat en groene groei", "min-kgg"),
    ("financien", "min-fin"),
    ("financiën", "min-fin"),
    ("fiscaliteit", "min-fin"),
    ("toeslagen", "min-fin"),
    ("infrastructuur en waterstaat", "min-ienw"),
    ("verkeer en waterstaat", "min-ienw"),
    ("verkeer en energie", "min-ienw"),
    ("verkeer", "min-ienw"),
    ("waterstaat", "min-ienw"),
    ("openbare werken", "min-ienw"),
    ("scheepvaart", "min-ienw"),
    ("justitie en veiligheid", "min-jenv"),
    ("justitie", "min-jenv"),
    ("rechtsbescherming", "min-jenv"),
    ("vreemdelingenzaken", "min-jenv"),
    ("integratie, jeugdbescherming, preventie en reclassering", "min-jenv"),
    ("landbouw, visserij, voedselzekerheid en natuur", "min-lvvn"),
    ("landbouw, natuurbeheer en visserij", "min-lvvn"),
    ("landbouw en visserij", "min-lvvn"),
    ("landbouw, nijverheid en handel", "min-lvvn"),
    ("landbouw", "min-lvvn"),
    ("natuur en stikstof", "min-lvvn"),
    ("onderwijs, cultuur en wetenschap", "min-ocw"),
    ("onderwijs en wetenschappen", "min-ocw"),
    ("onderwijs, kunsten en wetenschappen", "min-ocw"),
    ("onderwijs en wetenschap", "min-ocw"),
    ("basis- en voortgezet onderwijs", "min-ocw"),
    ("basis en voortgezet onderwijs", "min-ocw"),
    ("cultuur en media", "min-ocw"),
    ("wetenschapsbeleid", "min-ocw"),
    ("media", "min-ocw"),
    ("sociale zaken en werkgelegenheid", "min-szw"),
    ("sociale zaken en volksgezondheid", "min-szw"),
    ("sociale zaken", "min-szw"),
    ("werk en participatie", "min-szw"),
    ("volkshuisvesting en ruimtelijke ordening", "min-vro"),
    ("volkshuisvesting, ruimtelijke ordening en milieubeheer", "min-vro"),
    ("volkshuisvesting", "min-vro"),
    ("ruimtelijke ordening", "min-vro"),
    ("volksgezondheid, welzijn en sport", "min-vws"),
    ("volksgezondheid en milieuhygiene", "min-vws"),
    ("welzijn, volksgezondheid en cultuur", "min-vws"),
    ("medische zorg", "min-vws"),
    ("langdurige zorg", "min-vws"),
    ("jeugd en preventie", "min-vws"),
    ("gehandicaptenzaken", "min-vws"),
    ("cultuur, recreatie en maatschappelijk werk", "min-vws"),
    ("asiel en migratie", "min-aenm"),
    ("digitale economie en soevereiniteit", "min-ezk"),
    ("koninksrijkrelaties en digitalisering", "min-bzk"),
    ("digitalisering", "min-bzk"),
    ("mijnbouw", "min-ezk"),
    ("nederlands-antilliaanse zaken", "min-bzk"),
    ("inlichtingen- en veiligheidsdienst", "min-az"),
    # min-pres = min-az
    ("minister-president", "min-az"),
]


def _ministry_slug_from_label(label: str | None) -> str | None:
    """Pak een polder-ministerie-slug uit een role-label via substring-matching."""
    if not label:
        return None
    s = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode("ascii").lower()
    for fragment, slug in _LABEL_FRAGMENTS_TO_SLUG:
        if fragment in s:
            return slug
    return None


def _ministry_qid_to_slug(
    qid: str | None,
    role_qid: str | None,
    mapping: dict[str, str],
    role_label: str | None = None,
) -> str | None:
    """Map een ministerie-Q-id (of role-Q als fallback) naar een polder-slug.

    Volgorde:
      1. Directe Q-id-mapping
      2. Hand-gecode mapping voor specifieke role-Q's (bv. minister-president)
      3. Substring-match op het role-label
    """
    if qid and qid in mapping:
        return mapping[qid]
    if role_qid and role_qid in ROLE_QID_TO_MINISTRY_SLUG:
        return ROLE_QID_TO_MINISTRY_SLUG[role_qid]
    return _ministry_slug_from_label(role_label)


def _post_id_for_role(
    role_label: str | None,
    role_qid: str | None,
    ministry_slug: str | None,
    role_kind: str,
) -> tuple[str, str, str]:
    """Bepaal post_id, label en classification voor een mandaat.

    role_kind ∈ {minister, staatssecretaris, sg, dg}.
    """
    if role_kind == "minister":
        # Minister-president is een aparte post.
        if role_qid == "Q3058109":
            return ("post:minister-president", "Minister-president van Nederland", "bewindspersoon")
        if ministry_slug:
            return (
                f"post:minister-{ministry_slug}",
                role_label or f"Minister van {ministry_slug}",
                "bewindspersoon",
            )
        return ("post:minister-overig", role_label or "Minister", "bewindspersoon")
    if role_kind == "staatssecretaris":
        if ministry_slug:
            return (
                f"post:staatssecretaris-{ministry_slug}",
                role_label or f"Staatssecretaris van {ministry_slug}",
                "bewindspersoon",
            )
        return (
            "post:staatssecretaris-overig",
            role_label or "Staatssecretaris",
            "bewindspersoon",
        )
    if role_kind == "sg":
        if ministry_slug:
            return (
                f"post:sg-{ministry_slug}",
                role_label or f"Secretaris-generaal van {ministry_slug}",
                "abd-tmg",
            )
        return ("post:sg-overig", role_label or "Secretaris-generaal", "abd-tmg")
    if role_kind == "dg":
        if ministry_slug:
            return (
                f"post:dg-{ministry_slug}",
                role_label or f"Directeur-generaal van {ministry_slug}",
                "abd-tmg",
            )
        return ("post:dg-overig", role_label or "Directeur-generaal", "abd-tmg")
    raise ValueError(f"Onbekend role_kind: {role_kind!r}")


def _post_path(post_id: str, role_kind: str) -> Path:
    """Plaats voor de YAML van een post.

    Bewindspersoon-posten: ``data/posten/<role_kind>s/<slug>.yaml``.
    """
    slug = post_id[len("post:") :]
    folder_map = {
        "minister": "ministers",
        "staatssecretaris": "staatssecretarissen",
        "sg": "abd-sg",
        "dg": "abd-dg",
    }
    folder = folder_map[role_kind]
    return Path("data") / "posten" / folder / f"{slug}.yaml"


def _ensure_post(
    posten_root: Path,
    post_id: str,
    role_kind: str,
    label: str,
    organization_id: str,
    classification: str,
    *,
    valid_from: str | None = None,
    dry_run: bool = False,
) -> bool:
    """Schrijf een post-YAML als die nog niet bestaat. Returnt True als nieuw."""
    folder_map = {
        "minister": "ministers",
        "staatssecretaris": "staatssecretarissen",
        "sg": "abd-sg",
        "dg": "abd-dg",
    }
    folder = posten_root / folder_map[role_kind]
    slug = post_id[len("post:") :]
    target = folder / f"{slug}.yaml"
    if target.exists():
        return False
    if dry_run:
        return True
    folder.mkdir(parents=True, exist_ok=True)
    record = {
        "id": post_id,
        "organization_id": organization_id,
        "label": label,
        "classification": classification,
        "valid_from": valid_from or "1945-01-01",
        "valid_until": None,
    }
    _write_yaml(target, record)
    return True


def _mandaat_id(person_qid: str, role_qid: str, ministry_slug: str | None, start: str | None) -> str:
    """Deterministisch mandaat-id zodat re-runs idempotent zijn."""
    payload = "|".join([person_qid, role_qid, ministry_slug or "", start or ""])
    h = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"wd-{h}"


_MIN_BIRTH_YEAR = 1850  # schema-grens
_MIN_PERSON_AGE_LIMIT = 130  # geen records voor mensen ouder dan dit (filter ruis)


def build_bewindspersoon_records(
    rows: Iterable[dict[str, Any]],
    role_kind: str,
    *,
    ministry_qid_to_slug: dict[str, str],
    today: str,
) -> dict[str, dict[str, Any]]:
    """Aggregeer SPARQL-rijen tot {qid: persoon-record} met mandaten.

    Output is een tussenrepresentatie waarin we per persoon-Q-id alle
    mandaten verzamelen. De caller mergt deze met bestaande YAML's.

    P39-statements zonder P580 (start-tijd) worden overgeslagen. Dat zijn
    incomplete Wikidata-statements waar we vroeger een sentinel-datum
    `1945-01-01` op plakten, wat tot data-rommel leidde. Per fetcher-run
    loggen we het aantal als INFO-melding.
    """
    skipped_no_start: list[str] = []
    by_person: dict[str, dict[str, Any]] = {}
    posts_seen: dict[str, dict[str, Any]] = {}
    for row in rows:
        qid = row["person_qid"]
        person_label = row.get("person_label")
        family = row.get("family")
        birthyear = row.get("birthyear")
        if person_label and not family:
            # Geen P734-familie? Dan splitsen we op laatste woord.
            family = person_label.split()[-1] if person_label else None
        if birthyear is None:
            # Zonder geboortejaar kunnen we geen valide birth-block bouwen;
            # we slaan zulke records over (slug zou ambigu worden).
            continue
        if birthyear < _MIN_BIRTH_YEAR:
            # Schema vereist birth.year >= 1850. Voor wie eerder geboren is
            # (vooroorlogse ministers), slaan we het over — past niet bij
            # 1945-heden backfill.
            continue
        slug, name_block = person_id_from_label(
            person_label, family, None, birthyear, qid
        )
        person_record = by_person.setdefault(
            qid,
            {
                "id": f"person:{slug}",
                "identifiers": {"wikidata": qid},
                "name": name_block,
                "birth": {"year": birthyear},
                "mandaten": [],
                "sources": [
                    {
                        "id": SOURCE_ID,
                        "url": f"https://www.wikidata.org/wiki/{qid}",
                        "retrieved": today,
                    }
                ],
            },
        )
        if row.get("tkid"):
            person_record["identifiers"].setdefault("tk_persoon_id", row["tkid"])
        ministry_slug = _ministry_qid_to_slug(
            row.get("ministry_qid"),
            row.get("role_qid"),
            ministry_qid_to_slug,
            row.get("role_label"),
        )
        if ministry_slug is None:
            # Onbekend ministerie; sla het mandaat over (we loggen via caller).
            continue
        start = row.get("start")
        if not start:
            skipped_no_start.append(f"{qid}#{row.get('role_qid')}")
            continue
        post_id, post_label, classification = _post_id_for_role(
            row.get("role_label"), row.get("role_qid"), ministry_slug, role_kind
        )
        organization_id = f"org:{ministry_slug}"
        posts_seen[post_id] = {
            "label": post_label,
            "classification": classification,
            "organization_id": organization_id,
            "role_kind": role_kind,
            "valid_from": start,
        }
        mandaat = {
            "id": _mandaat_id(qid, row["role_qid"], ministry_slug, start),
            "organization_id": organization_id,
            "post_id": post_id,
            "role": post_label,
            "start_date": start,
            "sources": [
                {
                    "id": SOURCE_ID,
                    "url": f"https://www.wikidata.org/wiki/{qid}#P39",
                    "retrieved": today,
                }
            ],
        }
        if row.get("end"):
            mandaat["end_date"] = row["end"]
        else:
            mandaat["end_date"] = None
        person_record["mandaten"].append(mandaat)
    if skipped_no_start:
        sample = ", ".join(skipped_no_start[:5])
        suffix = f" (sample: {sample})" if sample else ""
        logger.info(
            "Wikidata: %d P39-statement(s) overgeslagen wegens ontbrekende P580%s",
            len(skipped_no_start),
            suffix,
        )
    return {"persons": by_person, "posts": posts_seen}


def build_abd_tmg_records(
    rows: Iterable[dict[str, Any]],
    *,
    ministry_qid_to_slug: dict[str, str],
    today: str,
) -> dict[str, dict[str, Any]]:
    """Aggregeer ABD-TMG SPARQL-rijen.

    role_type_qid bepaalt of het SG (Q2003810) of DG (Q126658544) is.

    P39-statements zonder P580 worden overgeslagen; zie
    `build_bewindspersoon_records` voor de rationale.
    """
    skipped_no_start: list[str] = []
    by_person: dict[str, dict[str, Any]] = {}
    posts_seen: dict[str, dict[str, Any]] = {}
    for row in rows:
        qid = row["person_qid"]
        person_label = row.get("person_label")
        family = row.get("family")
        birthyear = row.get("birthyear")
        if person_label and not family:
            family = person_label.split()[-1] if person_label else None
        role_type = row.get("role_type_qid")
        role_kind = "sg" if role_type == "Q2003810" else "dg" if role_type == "Q126658544" else None
        if role_kind is None:
            continue
        # Voor ABD-TMG zonder birthyear: we laten de persoon weg om geen
        # ambigue records te maken (validate vereist birth.year).
        if birthyear is None:
            continue
        if birthyear < _MIN_BIRTH_YEAR:
            continue
        slug, name_block = person_id_from_label(
            person_label, family, None, birthyear, qid
        )
        person_record = by_person.setdefault(
            qid,
            {
                "id": f"person:{slug}",
                "identifiers": {"wikidata": qid},
                "name": name_block,
                "birth": {"year": birthyear},
                "mandaten": [],
                "sources": [
                    {
                        "id": SOURCE_ID,
                        "url": f"https://www.wikidata.org/wiki/{qid}",
                        "retrieved": today,
                    }
                ],
            },
        )
        if row.get("tkid"):
            person_record["identifiers"].setdefault("tk_persoon_id", row["tkid"])
        ministry_slug = _ministry_qid_to_slug(
            row.get("ministry_qid"),
            row.get("role_qid"),
            ministry_qid_to_slug,
            row.get("role_label"),
        )
        if ministry_slug is None:
            continue
        start = row.get("start")
        if not start:
            skipped_no_start.append(f"{qid}#{row.get('role_qid')}")
            continue
        post_id, post_label, classification = _post_id_for_role(
            row.get("role_label"), row.get("role_qid"), ministry_slug, role_kind
        )
        organization_id = f"org:{ministry_slug}"
        posts_seen[post_id] = {
            "label": post_label,
            "classification": classification,
            "organization_id": organization_id,
            "role_kind": role_kind,
            "valid_from": start,
        }
        mandaat = {
            "id": _mandaat_id(qid, row["role_qid"], ministry_slug, start),
            "organization_id": organization_id,
            "post_id": post_id,
            "role": post_label,
            "start_date": start,
            "end_date": row.get("end"),
            "sources": [
                {
                    "id": SOURCE_ID,
                    "url": f"https://www.wikidata.org/wiki/{qid}#P39",
                    "retrieved": today,
                }
            ],
        }
        person_record["mandaten"].append(mandaat)
    if skipped_no_start:
        sample = ", ".join(skipped_no_start[:5])
        suffix = f" (sample: {sample})" if sample else ""
        logger.info(
            "Wikidata ABD-TMG: %d P39-statement(s) overgeslagen wegens ontbrekende P580%s",
            len(skipped_no_start),
            suffix,
        )
    return {"persons": by_person, "posts": posts_seen}


# ---------------------------------------------------------------------------
# YAML helpers
# ---------------------------------------------------------------------------


def _iter_yaml_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(p for p in directory.glob("*.yaml") if p.is_file())


def _read_yaml(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("Kon YAML niet lezen (%s): %s", path, exc)
        return None
    if not isinstance(data, dict):
        return None
    return data


def _write_yaml(path: Path, record: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(
            record,
            fh,
            sort_keys=False,
            default_flow_style=False,
            allow_unicode=True,
        )


def _ordered_for_org(record: dict[str, Any]) -> dict[str, Any]:
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


def _ordered_for_person(record: dict[str, Any]) -> dict[str, Any]:
    order = ["id", "identifiers", "name", "birth", "gender", "mandaten", "sources"]
    out: dict[str, Any] = {}
    for key in order:
        if key in record:
            out[key] = record[key]
    for key, value in record.items():
        if key not in out:
            out[key] = value
    return out


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


ORG_FOLDERS: dict[str, str] = {
    "ministerie": "ministeries",
    "gemeente": "gemeenten",
    "provincie": "provincies",
    "waterschap": "waterschappen",
}


def enrich_organisations(
    data_root: Path,
    *,
    cache_dir: Path,
    dry_run: bool = False,
    today: str | None = None,
    endpoint: str = SPARQL_ENDPOINT,
    include_name_history: bool = False,
    name_history_categories: tuple[str, ...] = ("ministerie",),
) -> dict[str, dict[str, int]]:
    """Verrijk organisatie-records met Wikidata Q-id's.

    Returnt per category een dict ``{candidates, matched_oin, matched_name, written}``.

    Met ``include_name_history=True`` wordt voor records die al een wikidata-Q-id
    hebben (of er net een gekregen hebben) ook de naam-historie opgehaald via
    P1448/P1813 + qualifiers. Default beperkt tot ministeries; uitbreiden naar
    gemeenten/provincies kan, maar levert daar zelden iets op.
    """
    today_str = today or _today()
    use_qlever = "qlever" in endpoint
    queries = ORG_QUERIES_QLEVER if use_qlever else ORG_QUERIES
    stats: dict[str, dict[str, int]] = {}
    for org_type, folder in ORG_FOLDERS.items():
        folder_path = data_root / folder
        files = _iter_yaml_files(folder_path)
        if not files:
            logger.info("Geen records onder %s, sla over", folder_path)
            continue
        query = queries[org_type]
        try:
            bindings = query_sparql(query, cache_dir=cache_dir, endpoint=endpoint)
        except httpx.HTTPError as exc:
            logger.warning("Wikidata-query voor %s faalde: %s — sla over", org_type, exc)
            stats[org_type] = {
                "candidates": len(files),
                "rows": 0,
                "matched_oin": 0,
                "matched_name": 0,
                "matched_abbr": 0,
                "written": 0,
                "names_updated": 0,
                "error": 1,
            }
            continue
        rows = parse_org_bindings(bindings)
        index = build_org_index(rows)
        records: list[tuple[Path, dict[str, Any]]] = []
        for path in files:
            data = _read_yaml(path)
            if data is None:
                continue
            records.append((path, data))
        matches = match_organisations((rec for _, rec in records), index)
        # Map record → (path, qid, method).
        by_id = {id(rec): (path, rec) for path, rec in records}
        cat_stats = {
            "candidates": len(records),
            "rows": len(rows),
            "matched_oin": 0,
            "matched_name": 0,
            "matched_abbr": 0,
            "written": 0,
            "names_updated": 0,
        }
        # Houd per qid een lijst (path, record) bij voor de eventuele
        # name-history pass. Meerdere records kunnen dezelfde Q-id hebben
        # (huidig + historisch ministerie van dezelfde naam) — die willen we
        # allemaal bijwerken.
        post_match_records: dict[str, list[tuple[Path, dict[str, Any]]]] = {}
        for record, row, method in matches:
            path, _ = by_id[id(record)]
            qid = row["qid"]
            merged = merge_wikidata_into_record(record, qid, today=today_str)
            merged = _ordered_for_org(merged)
            if method == "oin":
                cat_stats["matched_oin"] += 1
            elif method == "name":
                cat_stats["matched_name"] += 1
            elif method == "abbr":
                cat_stats["matched_abbr"] += 1
            if dry_run:
                logger.info("DRY-RUN %s %s ← %s (%s)", folder, record.get("id"), qid, method)
            else:
                _write_yaml(path, merged)
            cat_stats["written"] += 1
            post_match_records.setdefault(qid, []).append((path, merged))
        # Voor records die al een wikidata-id hadden, voeg ze ook toe aan de
        # name-history pool — die hebben de Q-id immers al.
        already_added_paths = {
            p for entries in post_match_records.values() for p, _ in entries
        }
        for path, rec in records:
            qid = (rec.get("identifiers") or {}).get("wikidata")
            if qid and path not in already_added_paths:
                post_match_records.setdefault(qid, []).append((path, rec))
        stats[org_type] = cat_stats

        # Naam-historie verwerking (per category, alleen als gevraagd).
        if (
            include_name_history
            and org_type in name_history_categories
            and post_match_records
        ):
            history = fetch_name_history(
                list(post_match_records.keys()),
                cache_dir=cache_dir,
                endpoint=endpoint,
            )
            for qid, name_history in history.items():
                for path, rec in post_match_records.get(qid, []):
                    new_record, changed = merge_names_into_record(rec, name_history)
                    if not changed:
                        continue
                    new_record = _ordered_for_org(new_record)
                    cat_stats["names_updated"] += 1
                    if dry_run:
                        logger.info(
                            "DRY-RUN name-history %s ← %d entries",
                            new_record.get("id"),
                            len(name_history),
                        )
                    else:
                        _write_yaml(path, new_record)

        logger.info(
            "%s: %d records, %d wikidata-rows, %d matches (oin=%d, name=%d, abbr=%d), %d names-updated",
            folder,
            cat_stats["candidates"],
            cat_stats["rows"],
            cat_stats["written"],
            cat_stats["matched_oin"],
            cat_stats["matched_name"],
            cat_stats["matched_abbr"],
            cat_stats["names_updated"],
        )
    return stats


def enrich_personen(
    data_root: Path,
    *,
    cache_dir: Path,
    dry_run: bool = False,
    today: str | None = None,
    endpoint: str = SPARQL_ENDPOINT,
) -> dict[str, int]:
    """Verrijk persoon-records met Wikidata Q-id's."""
    today_str = today or _today()
    use_qlever = "qlever" in endpoint
    person_query = PERSON_QUERY_QLEVER if use_qlever else PERSON_QUERY
    files: list[Path] = list(_iter_yaml_files(data_root))
    if not files:
        logger.info("Geen persoonsrecords onder %s, sla over", data_root)
        return {"candidates": 0, "rows": 0, "matched_tkid": 0, "matched_natural": 0, "written": 0}

    try:
        bindings = query_sparql(person_query, cache_dir=cache_dir, endpoint=endpoint)
    except httpx.HTTPError as exc:
        logger.warning("Wikidata-query voor personen faalde: %s — sla over", exc)
        return {
            "candidates": len(files),
            "rows": 0,
            "matched_tkid": 0,
            "matched_natural": 0,
            "matched_family_birth": 0,
            "written": 0,
            "error": 1,
        }
    rows = parse_person_bindings(bindings)
    index = build_person_index(rows)

    records: list[tuple[Path, dict[str, Any]]] = []
    for path in files:
        data = _read_yaml(path)
        if data is None:
            continue
        records.append((path, data))
    matches = match_personen((rec for _, rec in records), index)
    by_id = {id(rec): (path, rec) for path, rec in records}

    stats = {
        "candidates": len(records),
        "rows": len(rows),
        "matched_tkid": 0,
        "matched_natural": 0,
        "matched_family_birth": 0,
        "written": 0,
    }
    for record, row, method in matches:
        path, _ = by_id[id(record)]
        qid = row["qid"]
        merged = merge_wikidata_into_record(record, qid, today=today_str)
        merged = _ordered_for_person(merged)
        if method == "tkid":
            stats["matched_tkid"] += 1
        elif method == "natural":
            stats["matched_natural"] += 1
        elif method == "family_birth":
            stats["matched_family_birth"] += 1
        if dry_run:
            logger.info("DRY-RUN persoon %s ← %s (%s)", record.get("id"), qid, method)
        else:
            _write_yaml(path, merged)
        stats["written"] += 1

    logger.info(
        "personen: %d records, %d wikidata-rows, %d matches (tkid=%d, natural=%d, family_birth=%d)",
        stats["candidates"],
        stats["rows"],
        stats["written"],
        stats["matched_tkid"],
        stats["matched_natural"],
        stats["matched_family_birth"],
    )
    return stats


# ---------------------------------------------------------------------------
# Bewindspersoon / ABD-TMG enrichment
# ---------------------------------------------------------------------------


def _index_existing_persons(person_root: Path) -> dict[str, tuple[Path, dict[str, Any]]]:
    """Index alle bestaande person-yaml's op (tkid, wikidata, natural-key).

    Returnt {key: (path, record)} waarbij key:
      - ``tk:<id>`` voor TK-persoon-id
      - ``wd:<qid>`` voor Wikidata-id
      - ``nk:<family_slug>:<birthyear>`` als natural fallback
    """
    index: dict[str, tuple[Path, dict[str, Any]]] = {}
    for path in _iter_yaml_files(person_root):
        data = _read_yaml(path)
        if not data:
            continue
        ids = data.get("identifiers") or {}
        tkid = ids.get("tk_persoon_id")
        wd = ids.get("wikidata")
        family = (data.get("name") or {}).get("family") or ""
        birth = (data.get("birth") or {}).get("year")
        fam_slug = _slug_family(family)
        if tkid:
            index[f"tk:{tkid}"] = (path, data)
        if wd:
            index[f"wd:{wd}"] = (path, data)
        if fam_slug and birth is not None:
            index[f"nk:{fam_slug}:{birth}"] = (path, data)
    return index


def _merge_mandaten(
    existing: list[dict[str, Any]] | None,
    new: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge mandaten op (post_id, start_date). Bij dubbel: bestaande wint qua
    sources (we voegen wikidata-source toe), end_date wordt geüpdatet als
    bestaande null is en nieuwe een waarde heeft.
    """
    by_key: dict[tuple[str, str | None], dict[str, Any]] = {}
    for m in existing or []:
        if not isinstance(m, dict):
            continue
        key = (m.get("post_id") or "", m.get("start_date"))
        by_key[key] = dict(m)
    for m in new:
        key = (m.get("post_id") or "", m.get("start_date"))
        if key in by_key:
            current = by_key[key]
            # Voeg wikidata-source toe als ontbreekt.
            current_sources = current.get("sources") or []
            new_sources = m.get("sources") or []
            current["sources"] = _merge_sources(current_sources, new_sources)
            # Update end_date als die nu null is en nieuwe waarde geeft.
            if current.get("end_date") in (None, "") and m.get("end_date"):
                current["end_date"] = m["end_date"]
            by_key[key] = current
        else:
            by_key[key] = dict(m)
    return list(by_key.values())


def _merge_into_existing_person(
    existing: dict[str, Any],
    fresh: dict[str, Any],
    *,
    today: str,
) -> dict[str, Any]:
    """Voeg wikidata-id, mandaten en sources van ``fresh`` toe aan ``existing``."""
    merged = dict(existing)
    identifiers = dict(merged.get("identifiers") or {})
    fresh_ids = fresh.get("identifiers") or {}
    if fresh_ids.get("wikidata") and not identifiers.get("wikidata"):
        identifiers["wikidata"] = fresh_ids["wikidata"]
    if fresh_ids.get("tk_persoon_id") and not identifiers.get("tk_persoon_id"):
        identifiers["tk_persoon_id"] = fresh_ids["tk_persoon_id"]
    merged["identifiers"] = identifiers
    if "name" not in merged or not merged["name"].get("family"):
        merged["name"] = fresh["name"]
    if "birth" not in merged and "birth" in fresh:
        merged["birth"] = fresh["birth"]
    merged["mandaten"] = _merge_mandaten(merged.get("mandaten"), fresh.get("mandaten") or [])
    merged["sources"] = _merge_sources(merged.get("sources"), fresh.get("sources") or [])
    return merged


def _person_target_path(
    person_root: Path, record: dict[str, Any]
) -> Path:
    """Personen liggen vlak onder ``person_root``."""
    slug = record["id"][len("person:") :]
    return person_root / f"{slug}.yaml"


def enrich_bewindspersonen(
    data_root: Path,
    *,
    cache_dir: Path,
    dry_run: bool = False,
    today: str | None = None,
    endpoint: str = SPARQL_ENDPOINT,
) -> dict[str, dict[str, int]]:
    """Backfill ministers en staatssecretarissen 1945-heden via Wikidata.

    Returnt per role_kind een dict met counters: rows, persons, mandaten,
    bootstrapped_posts, written.
    """
    today_str = today or _today()
    use_qlever = "qlever" in endpoint
    queries = BEWINDSPERSOON_QUERIES_QLEVER if use_qlever else BEWINDSPERSOON_QUERIES
    person_root = data_root / "personen"
    posten_root = data_root / "posten"
    ministry_qid_to_slug = load_ministry_qid_map(data_root)

    stats: dict[str, dict[str, int]] = {}
    person_index = _index_existing_persons(person_root)

    for role_kind, query in queries.items():
        try:
            bindings = query_sparql(query, cache_dir=cache_dir, endpoint=endpoint)
        except httpx.HTTPError as exc:
            logger.warning("Wikidata-query voor %s faalde: %s — sla over", role_kind, exc)
            stats[role_kind] = {"rows": 0, "error": 1}
            continue
        rows = parse_bewindspersoon_bindings(bindings)
        bundle = build_bewindspersoon_records(
            rows, role_kind, ministry_qid_to_slug=ministry_qid_to_slug, today=today_str
        )
        kind_stats = {
            "rows": len(rows),
            "persons": 0,
            "new_persons": 0,
            "merged_persons": 0,
            "mandaten": 0,
            "bootstrapped_posts": 0,
            "written": 0,
        }

        # Bootstrap posten.
        for post_id, info in bundle["posts"].items():
            created = _ensure_post(
                posten_root,
                post_id,
                info["role_kind"],
                info["label"],
                info["organization_id"],
                info["classification"],
                valid_from=info.get("valid_from"),
                dry_run=dry_run,
            )
            if created:
                kind_stats["bootstrapped_posts"] += 1

        # Merge / write personen.
        for qid, fresh in bundle["persons"].items():
            kind_stats["persons"] += 1
            kind_stats["mandaten"] += len(fresh.get("mandaten") or [])
            existing_path = None
            existing_record = None
            tkid = (fresh.get("identifiers") or {}).get("tk_persoon_id")
            if tkid and f"tk:{tkid}" in person_index:
                existing_path, existing_record = person_index[f"tk:{tkid}"]
            elif f"wd:{qid}" in person_index:
                existing_path, existing_record = person_index[f"wd:{qid}"]
            else:
                fam = (fresh.get("name") or {}).get("family") or ""
                birth = (fresh.get("birth") or {}).get("year")
                key = f"nk:{_slug_family(fam)}:{birth}"
                if key in person_index:
                    existing_path, existing_record = person_index[key]

            # Sla nieuwe records over die geen mandaten opleveren (alle mandaten
            # gefilterd door onbekend ministerie) — anders krijg je rommel-records.
            if existing_record is None and not (fresh.get("mandaten") or []):
                continue

            if existing_record is not None:
                merged = _merge_into_existing_person(existing_record, fresh, today=today_str)
                merged = _ordered_for_person(merged)
                target = existing_path or _person_target_path(person_root, merged)
                kind_stats["merged_persons"] += 1
            else:
                merged = _ordered_for_person(fresh)
                target = _person_target_path(person_root, merged)
                kind_stats["new_persons"] += 1

            if dry_run:
                logger.info(
                    "DRY-RUN bewindspersoon %s ← %s (%d mandaten)",
                    merged["id"],
                    qid,
                    len(merged.get("mandaten") or []),
                )
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                _write_yaml(target, merged)
                # Hou de index actueel zodat een volgende role_kind ook merget.
                person_index[f"wd:{qid}"] = (target, merged)
                if tkid:
                    person_index[f"tk:{tkid}"] = (target, merged)
                fam = (merged.get("name") or {}).get("family") or ""
                birth = (merged.get("birth") or {}).get("year")
                if fam and birth is not None:
                    person_index[f"nk:{_slug_family(fam)}:{birth}"] = (target, merged)
            kind_stats["written"] += 1

        stats[role_kind] = kind_stats
        logger.info(
            "%s: %d rows → %d personen (%d nieuw, %d merged), %d mandaten, %d nieuwe posten",
            role_kind,
            kind_stats["rows"],
            kind_stats["persons"],
            kind_stats["new_persons"],
            kind_stats["merged_persons"],
            kind_stats["mandaten"],
            kind_stats["bootstrapped_posts"],
        )
    return stats


def enrich_abd_tmg(
    data_root: Path,
    *,
    cache_dir: Path,
    dry_run: bool = False,
    today: str | None = None,
    endpoint: str = SPARQL_ENDPOINT,
) -> dict[str, int]:
    """Backfill huidige SG/DG-bezetting via Wikidata."""
    today_str = today or _today()
    use_qlever = "qlever" in endpoint
    query = ABD_TMG_QUERY_QLEVER if use_qlever else ABD_TMG_QUERY
    person_root = data_root / "personen"
    posten_root = data_root / "posten"
    ministry_qid_to_slug = load_ministry_qid_map(data_root)
    person_index = _index_existing_persons(person_root)

    try:
        bindings = query_sparql(query, cache_dir=cache_dir, endpoint=endpoint)
    except httpx.HTTPError as exc:
        logger.warning("Wikidata ABD-TMG query faalde: %s — sla over", exc)
        return {"rows": 0, "error": 1}

    rows = parse_abd_tmg_bindings(bindings)
    bundle = build_abd_tmg_records(
        rows, ministry_qid_to_slug=ministry_qid_to_slug, today=today_str
    )
    stats = {
        "rows": len(rows),
        "persons": 0,
        "new_persons": 0,
        "merged_persons": 0,
        "mandaten": 0,
        "bootstrapped_posts": 0,
        "written": 0,
    }

    for post_id, info in bundle["posts"].items():
        if _ensure_post(
            posten_root,
            post_id,
            info["role_kind"],
            info["label"],
            info["organization_id"],
            info["classification"],
            valid_from=info.get("valid_from"),
            dry_run=dry_run,
        ):
            stats["bootstrapped_posts"] += 1

    for qid, fresh in bundle["persons"].items():
        stats["persons"] += 1
        stats["mandaten"] += len(fresh.get("mandaten") or [])
        existing_path = None
        existing_record = None
        tkid = (fresh.get("identifiers") or {}).get("tk_persoon_id")
        if tkid and f"tk:{tkid}" in person_index:
            existing_path, existing_record = person_index[f"tk:{tkid}"]
        elif f"wd:{qid}" in person_index:
            existing_path, existing_record = person_index[f"wd:{qid}"]
        else:
            fam = (fresh.get("name") or {}).get("family") or ""
            birth = (fresh.get("birth") or {}).get("year")
            key = f"nk:{_slug_family(fam)}:{birth}"
            if key in person_index:
                existing_path, existing_record = person_index[key]

        # Sla nieuwe records zonder mandaten over (Wikidata heeft vaak SG/DG-personen
        # zonder ministerie-link).
        if existing_record is None and not (fresh.get("mandaten") or []):
            continue

        if existing_record is not None:
            merged = _merge_into_existing_person(existing_record, fresh, today=today_str)
            merged = _ordered_for_person(merged)
            target = existing_path or _person_target_path(person_root, merged)
            stats["merged_persons"] += 1
        else:
            merged = _ordered_for_person(fresh)
            target = _person_target_path(person_root, merged)
            stats["new_persons"] += 1
        if dry_run:
            logger.info("DRY-RUN abd-tmg %s ← %s", merged["id"], qid)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_yaml(target, merged)
            person_index[f"wd:{qid}"] = (target, merged)
            if tkid:
                person_index[f"tk:{tkid}"] = (target, merged)
        stats["written"] += 1

    logger.info(
        "abd-tmg: %d rows → %d personen, %d mandaten, %d nieuwe posten",
        stats["rows"],
        stats["persons"],
        stats["mandaten"],
        stats["bootstrapped_posts"],
    )
    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="polder-fetch-wikidata",
        description=(
            "Verrijk polder-organisatie- en persoon-records met Wikidata Q-id's "
            "via de SPARQL Query Service."
        ),
    )
    parser.add_argument("--orgs", action="store_true", help="Alleen organisaties verrijken.")
    parser.add_argument("--personen", action="store_true", help="Alleen personen verrijken.")
    parser.add_argument(
        "--bewindspersonen",
        action="store_true",
        help="Backfill ministers en staatssecretarissen (1945-heden).",
    )
    parser.add_argument(
        "--abd-tmg",
        action="store_true",
        help="Huidige ABD-TMG (SG/DG) bezetting via Wikidata.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Alles (organisaties + personen + bewindspersonen + abd-tmg).",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("data"),
        help="Root van de data-directory (default: data).",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path("_cache/wikidata"),
        help="Cache-directory voor SPARQL-responses (default: _cache/wikidata).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Schrijf niets, log alleen wat geschreven zou worden.",
    )
    parser.add_argument(
        "--endpoint",
        choices=("wdqs", "qlever"),
        default="wdqs",
        help=(
            "SPARQL-endpoint: 'wdqs' (Wikidata Query Service, default) of "
            "'qlever' (QLever-mirror, sneller en zonder rate-limit)."
        ),
    )
    parser.add_argument(
        "--include-name-history",
        action="store_true",
        help=(
            "Haal voor organisaties met een Q-id ook de naam-historie op "
            "(P1448/P1813 + P580/P582 qualifiers) en merge in names[]. "
            "Default: alleen ministeries."
        ),
    )
    parser.add_argument(
        "--name-history-categories",
        default="ministerie",
        help=(
            "Comma-separated lijst van organisatie-categorieën waarvoor "
            "naam-historie wordt opgehaald (ministerie,gemeente,provincie,waterschap). "
            "Default: ministerie."
        ),
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

    any_flag = args.orgs or args.personen or args.bewindspersonen or args.abd_tmg
    do_orgs = args.orgs or args.all or not any_flag
    do_personen = args.personen or args.all or not any_flag
    do_bewindspersonen = args.bewindspersonen or args.all
    do_abd_tmg = args.abd_tmg or args.all

    cache_dir: Path = args.cache
    org_root: Path = args.data_root / "organisaties"
    person_root: Path = args.data_root / "personen"
    endpoint_url = _ENDPOINT_URLS[args.endpoint]

    total_written = 0
    if do_orgs:
        name_history_cats = tuple(
            c.strip() for c in args.name_history_categories.split(",") if c.strip()
        )
        org_stats = enrich_organisations(
            org_root,
            cache_dir=cache_dir,
            dry_run=args.dry_run,
            endpoint=endpoint_url,
            include_name_history=args.include_name_history,
            name_history_categories=name_history_cats,
        )
        for cat, s in org_stats.items():
            print(
                f"{cat}: {s['written']}/{s['candidates']} matched "
                f"(oin={s['matched_oin']}, name={s['matched_name']}, "
                f"abbr={s['matched_abbr']}), names_updated={s.get('names_updated', 0)}",
                file=sys.stderr,
            )
            total_written += s["written"]
    if do_personen:
        ps = enrich_personen(
            person_root, cache_dir=cache_dir, dry_run=args.dry_run, endpoint=endpoint_url
        )
        print(
            f"personen: {ps['written']}/{ps['candidates']} matched "
            f"(tkid={ps['matched_tkid']}, natural={ps['matched_natural']}, "
            f"family_birth={ps['matched_family_birth']})",
            file=sys.stderr,
        )
        total_written += ps["written"]
    if do_bewindspersonen:
        bw = enrich_bewindspersonen(
            args.data_root, cache_dir=cache_dir, dry_run=args.dry_run, endpoint=endpoint_url
        )
        for kind, s in bw.items():
            print(
                f"{kind}: {s.get('persons', 0)} personen "
                f"({s.get('new_persons', 0)} nieuw, {s.get('merged_persons', 0)} merged), "
                f"{s.get('mandaten', 0)} mandaten, "
                f"{s.get('bootstrapped_posts', 0)} nieuwe posten",
                file=sys.stderr,
            )
            total_written += s.get("written", 0)
    if do_abd_tmg:
        abd = enrich_abd_tmg(
            args.data_root, cache_dir=cache_dir, dry_run=args.dry_run, endpoint=endpoint_url
        )
        print(
            f"abd-tmg: {abd.get('persons', 0)} personen, "
            f"{abd.get('mandaten', 0)} mandaten, "
            f"{abd.get('bootstrapped_posts', 0)} nieuwe posten",
            file=sys.stderr,
        )
        total_written += abd.get("written", 0)

    print(f"Wikidata enrichment: {total_written} records updated", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
