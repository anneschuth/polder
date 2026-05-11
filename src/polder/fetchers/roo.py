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

logger = logging.getLogger("polder.fetchers.roo")

PRIMARY_URL = "https://organisaties.overheid.nl/archive/exportOO.xml"
# Categorie-specifieke fallback (zelfde host, kleinere payload). Wordt alleen
# gebruikt als de volledige export onbereikbaar is; sinds dit een echte URL is
# (ipv de oude api-organisaties.overheid.nl die 404 gaf) levert hij ook XML.
FALLBACK_URL = "https://organisaties.overheid.nl/archive/exportOO_ministeries.xml"
SOURCE_ID = "roo"
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
    """Map een ROO-type-string op (interne_type, sub_folder, slug_prefix)."""
    if not roo_type:
        return None
    key = roo_type.strip().lower()
    if key in TYPE_MAP:
        return TYPE_MAP[key]
    # Probeer een paar substring-matches voor varianten als
    # "Zelfstandig Bestuursorgaan (ZBO)".
    for known, mapping in TYPE_MAP.items():
        if known in key:
            return mapping
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
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            response = client.get(PRIMARY_URL)
            response.raise_for_status()
            payload = response.content
    except httpx.HTTPError as exc:
        logger.warning("Primaire ROO-download faalde (%s); val terug op REST-API", exc)
        with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            response = client.get(FALLBACK_URL)
            response.raise_for_status()
            payload = response.content

    target.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()[:12]
    logger.info("ROO-export geschreven naar %s (sha256:%s)", target, digest)
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


def parse_organisatie(node: etree._Element) -> dict[str, Any] | None:
    """Parse een enkele organisatie-node naar een Organisatie-record dict."""
    raw_type = _findtext(node, "type", "soort", "organisatietype")
    mapping = roo_type_to_internal(raw_type)
    if mapping is None:
        if raw_type:
            logger.warning("Onbekend ROO-type, sla over: %s", raw_type)
        return None
    internal_type, _sub_folder, prefix = mapping

    name = _findtext(node, "naam", "name", "officielenaam")
    if not name:
        logger.warning("Organisatie zonder naam, sla over (type=%s)", raw_type)
        return None

    abbr = _findtext(node, "afkorting", "abbreviation")
    # ROO-XML zet `systeemId` als attribuut; tests gebruiken een `<id>` child.
    # Beide zijn geldige bronnen voor `roo_id`.
    roo_id = _attr_systeemid(node) or _findtext(
        node, "id", "rooid", "roo_id", "identifier"
    )
    # TOOI-URI staat als attribute `resourceIdentifierTOOI` op de organisatie-
    # node zelf. We lezen alleen het attribute van deze node, niet van nested
    # children (bijv. `<relatieMetMinisterie>` heeft ook een TOOI-attribute en
    # die hoort bij een andere organisatie).
    tooi = _attr_tooi(node) or _findtext(node, "tooi", "tooi_uri", "uri")
    oin = _findtext(node, "oin")
    kvk = _findtext(node, "kvk", "kvknummer")
    rsin = _findtext(node, "rsin", "rsinnummer")
    website = _findtext(node, "website", "url", "homepage")
    bezoekadres = _findtext(node, "bezoekadres", "visitingaddress")
    postadres = _findtext(node, "postadres", "postaladdress")
    email = _findtext(node, "email", "emailadres")
    # ROO's `<startDatum>` is de aanmaakdatum van het legale entity-record, niet
    # de validity-datum van de huidige naam. Voor ministeries levert dat fouten
    # op: EZK en IenW krijgen 2010-10-14 (Rutte I-cabinetdag) terwijl die namen
    # pas in 2017 ontstonden. Wikidata's P571 (inception, gekoppeld aan de naam)
    # is correcter; die wordt door de Wikidata-fetcher ingevuld. Hier gebruiken
    # we een sentinel als er geen betrouwbare bron is. `<opgericht>` en
    # `<valid_from>` worden alleen door test-fixtures gebruikt; echte ROO-XML
    # heeft ze niet, en als ze er wel staan zijn ze al inhoudelijk juist.
    valid_from = _findtext(node, "opgericht", "valid_from") or "1900-01-01"
    valid_until = _findtext(node, "opgeheven", "einddatum", "valid_until")
    parent_roo_id = _findtext(node, "parent", "ouder", "parent_id", "ouderorganisatie")
    parent_org_id: str | None = None

    # Voor organisatieonderdelen: de parent staat als enclosing `<organisatie>`-
    # ancestor in de XML. Bereken zijn `org:`-id rechtstreeks zodat
    # parent_id-resolutie niet afhangt van roo_id-matching.
    if internal_type == "organisatieonderdeel":
        ancestor = _enclosing_organisatie(node)
        if ancestor is not None:
            ancestor_type = _findtext(ancestor, "type", "soort", "organisatietype")
            ancestor_mapping = roo_type_to_internal(ancestor_type)
            ancestor_name = _findtext(
                ancestor, "naam", "name", "officielenaam"
            )
            ancestor_abbr = _findtext(ancestor, "afkorting", "abbreviation")
            if ancestor_mapping is not None and ancestor_name:
                _, _, ancestor_prefix = ancestor_mapping
                ancestor_slug = (
                    slugify(ancestor_abbr)
                    if ancestor_abbr and len(ancestor_abbr) <= 12
                    else slugify(ancestor_name)
                )
                parent_org_id = build_id(ancestor_prefix, ancestor_slug)
            ancestor_roo_id = _attr_systeemid(ancestor) or _findtext(
                ancestor, "id", "rooid", "roo_id", "identifier"
            )
            if ancestor_roo_id and not parent_roo_id:
                parent_roo_id = ancestor_roo_id

    slug = slugify(abbr) if abbr and len(abbr) <= 12 else slugify(name)
    org_id = build_id(prefix, slug)

    # Source-URL per organisatie indien roo_id beschikbaar.
    source_url = (
        f"https://organisaties.overheid.nl/{roo_id}/" if roo_id else PRIMARY_URL
    )

    identifiers: dict[str, Any] = {}
    if oin:
        identifiers["oin"] = oin
    if tooi:
        identifiers["tooi"] = tooi
    if roo_id:
        identifiers["roo_id"] = str(roo_id)
    if kvk is not None:
        identifiers["kvk"] = kvk or None
    if rsin is not None:
        identifiers["rsin"] = rsin or None

    name_entry: dict[str, Any] = {"value": name}
    if abbr:
        name_entry["abbr"] = abbr
    name_entry["valid_from"] = valid_from
    if valid_until:
        name_entry["valid_until"] = valid_until

    contact: dict[str, Any] = {}
    if website:
        contact["website"] = website
    if bezoekadres:
        contact["bezoekadres"] = bezoekadres
    if postadres:
        contact["postadres"] = postadres
    if email:
        contact["email"] = email

    record: dict[str, Any] = {
        "id": org_id,
        "type": internal_type,
    }
    if identifiers:
        record["identifiers"] = identifiers
    record["classification"] = internal_type
    if parent_roo_id:
        # Parent-ID-mapping wordt in een latere pass opgelost (we kennen op dit
        # punt alleen de roo_id van de parent, niet de slug). We slaan het op
        # onder een private key zodat write_records het kan resolven.
        record["_parent_roo_id"] = str(parent_roo_id)
    if parent_org_id:
        # Voor organisatieonderdelen kennen we de parent's slug rechtstreeks
        # uit de XML-ancestry. _resolve_parents pakt deze key direct over.
        record["_parent_org_id"] = parent_org_id
    record["names"] = [name_entry]
    if contact:
        record["contact"] = contact
    record["valid_from"] = valid_from
    record["valid_until"] = valid_until or None
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


def parse_export(path: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    """Parse de ROO-XML naar een lijst Organisatie-records."""
    with path.open("rb") as fh:
        tree = etree.parse(fh)
    root = tree.getroot()

    # Materializeer eerst de generator: nested iter() in parse_organisatie en
    # _findtext kan de outer lxml-iterator corrupten, waardoor de loop te vroeg
    # stopt. list() afhandelen voorkomt dat.
    nodes = list(_iter_organisatie_nodes(root))
    records: list[dict[str, Any]] = []
    for node in nodes:
        record = parse_organisatie(node)
        if record is None:
            continue
        records.append(record)
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
        else:
            if value is not None or key not in merged:
                merged[key] = value

    return merged


def _strip_private(record: dict[str, Any]) -> dict[str, Any]:
    """Verwijder private keys die met `_` beginnen, voor serialisatie."""
    return {k: v for k, v in record.items() if not k.startswith("_")}


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
        if parent_org_id:
            record["parent_id"] = parent_org_id
        elif parent_roo_id and parent_roo_id in by_roo_id:
            record["parent_id"] = by_roo_id[parent_roo_id]
        elif "parent_id" not in record:
            record["parent_id"] = None


def _existing_tooi_to_path(out_dir: Path) -> dict[str, Path]:
    """Bouw index `tooi-id -> bestaand pad` over álle subfolders.

    Gebruikt om te voorkomen dat een organisatieonderdeel-record geschreven
    wordt als er al een echte (gemeente/ministerie/zbo) record met dezelfde
    TOOI-id bestaat. Anders zou dezelfde fysieke organisatie als zowel
    `gemeenten/groningen.yaml` als `organisatieonderdelen/groningen.yaml`
    eindigen.
    """
    index: dict[str, Path] = {}
    if not out_dir.exists():
        return index
    for path in out_dir.rglob("*.yaml"):
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except yaml.YAMLError:
            continue
        tooi = (data.get("identifiers") or {}).get("tooi")
        if tooi:
            index.setdefault(tooi, path)
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

        tooi = (clean.get("identifiers") or {}).get("tooi")
        if (
            sub_folder == "organisatieonderdelen"
            and tooi
            and tooi in tooi_index
            and tooi_index[tooi] != target
        ):
            logger.info(
                "Skip organisatieonderdeel %s: tooi-id al in %s",
                target.relative_to(out_dir),
                tooi_index[tooi].relative_to(out_dir),
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
            tooi_index[tooi] = target
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
