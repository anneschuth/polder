"""Fetcher voor het RWT-register van de Algemene Rekenkamer.

Bron: Algemene Rekenkamer.
Endpoint: https://www.rekenkamer.nl/onderwerpen/instellingen-op-afstand-van-het-rijk/rechtspersonen-met-een-wettelijke-taak
Formaat: HTML (server-rendered Next.js). Per ministerie een ``<h2>`` met daaronder
een ``<ul>`` van RWT-namen. Sommige ``<li>`` items zijn clusters met een
geneste ``<ul>`` van sub-organisaties.
Update: onregelmatig (laatste bijwerking 14 maart 2023 op moment van schrijven).
Licentie: CC0 1.0 Universal (`DCTERMS.rights` op de pagina).
Dekking: alle Rechtspersonen met een Wettelijke Taak (RWT) zoals geregistreerd
door de Rekenkamer. Veel RWT's zijn ook ZBO of agentschap; de RWT-status wordt
hier toegevoegd als source-attribution op het bestaande record.

Strategie:
1. Download de HTML, parse met BeautifulSoup.
2. Per RWT: slugify -> zoek in ``data/organisaties/`` (alle sub-folders) naar
   een record met dezelfde slug of dezelfde naam (case-/accent-insensitief).
3. Bij match: append ``sources`` entry met ``id: ar_rwt`` en
   ``fields: ["rwt-status"]``. Bestaande velden blijven staan.
4. Bij geen match: schrijf een nieuw record onder ``data/organisaties/rwt/``
   met ``type: rwt``, alleen naam + RWT-source.

AVG: deze fetcher raakt geen persoonsgegevens, alleen organisaties.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import unicodedata
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import yaml
from bs4 import BeautifulSoup, Tag

from polder.lib.casing import canonicalize_leading_case

logger = logging.getLogger("polder.fetchers.ar_rwt")

__all__ = [
    "RWT_REGISTER_URL",
    "SOURCE_ID",
    "fetch_register_index",
    "main",
    "match_record",
    "name_key",
    "parse_register",
    "slugify",
]

RWT_REGISTER_URL = (
    "https://www.rekenkamer.nl/onderwerpen/instellingen-op-afstand-van-het-rijk"
    "/rechtspersonen-met-een-wettelijke-taak"
)
SOURCE_ID = "ar_rwt"
HTTP_TIMEOUT = 60.0
USER_AGENT = "polder/0.0.1 (https://github.com/anneschuth/polder; anne.schuth@gmail.com)"

# Tokens/labels die we niet als zelfstandige RWT willen schrijven (cluster-headings,
# kop-zinnen). Sub-items binnen een cluster komen wel binnen via geneste <ul>.
_CLUSTER_HINTS = ("(cluster)",)
_HEADING_HINTS = (
    "keurings- en controle-instellingen",
    "onderwijs en wetenschap",
    "cultuur en media",
)


# ---------------------------------------------------------------------------
# Slug & name helpers
# ---------------------------------------------------------------------------


def slugify(name: str) -> str:
    """Lowercase ASCII slug. Gelijk aan ``polder.fetchers.roo.slugify`` zodat
    we kunnen matchen op slug zonder afhankelijkheid op die module."""
    if not name:
        return ""
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower().replace("&", " en ")
    hyphenated = re.sub(r"[\s_]+", "-", lowered)
    cleaned = re.sub(r"[^a-z0-9-]+", "", hyphenated)
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned


def name_key(name: str) -> str:
    """Normaliseer een naam voor fuzzy matching: lowercase, strip accenten,
    verwijder afkortingen tussen haakjes en interpunctie."""
    if not name:
        return ""
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower()
    # Strip alles tussen haakjes (afkortingen, opmerkingen).
    no_parens = re.sub(r"\([^)]*\)", " ", lowered)
    # Verwijder generieke rechtsvorm-suffixen voor robuustere match.
    no_suffix = re.sub(
        r"\b(b\.?v\.?|n\.?v\.?|stichting|vereniging|cooperatie|holding)\b",
        " ",
        no_parens,
    )
    collapsed = re.sub(r"[^a-z0-9]+", " ", no_suffix).strip()
    return re.sub(r"\s+", " ", collapsed)


def _today() -> str:
    return date.today().isoformat()


# ---------------------------------------------------------------------------
# Fetch & parse
# ---------------------------------------------------------------------------


def fetch_register_index(*, timeout: float = HTTP_TIMEOUT) -> str:
    """Download de RWT-register-pagina. Returnt de ruwe HTML."""
    response = httpx.get(
        RWT_REGISTER_URL,
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.text


def _clean_li_text(li: Tag) -> str:
    """Tekst van een ``<li>`` zonder geneste ``<ul>``-inhoud."""
    parts: list[str] = []
    for child in li.children:
        if isinstance(child, Tag) and child.name == "ul":
            continue
        text = child.get_text(" ", strip=True) if isinstance(child, Tag) else str(child).strip()
        if text:
            parts.append(text)
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _is_cluster(name: str) -> bool:
    lower = name.lower()
    return any(hint in lower for hint in _CLUSTER_HINTS)


def _is_heading(name: str) -> bool:
    lower = name.lower().strip().rstrip(":")
    return lower in _HEADING_HINTS


def parse_register(html: str) -> list[dict[str, Any]]:
    """Parse de RWT-registerpagina naar een lijst RWT-records.

    Elk record heeft:
    - ``name``: officiele naam zoals op de pagina,
    - ``ministerie``: tekst van de ``<h2>`` waaronder de RWT staat,
    - ``cluster``: True voor entries als ``X (cluster)`` (verzamel-entries),
    - ``parent``: naam van het cluster waar de RWT onder hangt (of None).
    """
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup
    records: list[dict[str, Any]] = []

    for h2 in main.find_all("h2"):
        ministerie = h2.get_text(" ", strip=True)
        if not ministerie:
            continue
        # Verzamel alle directe-zus ``<ul>`` totdat we een nieuwe ``<h2>`` tegenkomen.
        # H2's zitten op verschillende dieptes; we lopen vanaf de h2's parent omhoog
        # naar het ``<div>``-blok en pakken alle ``<ul>`` daarbinnen.
        block = h2.parent
        if block is None:
            continue
        for ul in block.find_all("ul", recursive=True):
            # Sla geneste ``<ul>``s over: die handelen we expliciet af binnen de li-loop.
            if ul.find_parent("li"):
                continue
            for li in ul.find_all("li", recursive=False):
                name = _clean_li_text(li)
                if not name or _is_heading(name):
                    continue
                cluster = _is_cluster(name)
                records.append(
                    {
                        "name": name,
                        "ministerie": ministerie,
                        "cluster": cluster,
                        "parent": None,
                    }
                )
                # Sub-items binnen een cluster.
                nested = li.find("ul")
                if nested is not None:
                    for sub_li in nested.find_all("li", recursive=False):
                        sub_name = _clean_li_text(sub_li)
                        if not sub_name or _is_heading(sub_name):
                            continue
                        records.append(
                            {
                                "name": sub_name,
                                "ministerie": ministerie,
                                "cluster": _is_cluster(sub_name),
                                "parent": name,
                            }
                        )
    return records


# ---------------------------------------------------------------------------
# Match against existing data/organisaties
# ---------------------------------------------------------------------------


def _load_existing_index(data_dir: Path) -> dict[str, Path]:
    """Bouw een lookup van ``slug``/``name_key`` -> YAML-pad voor alle
    bestaande organisatie-records onder ``data_dir``. Slug-keys krijgen
    voorrang door als eerste te worden geschreven."""
    index: dict[str, Path] = {}
    if not data_dir.exists():
        return index
    for path in sorted(data_dir.rglob("*.yaml")):
        try:
            with path.open("r", encoding="utf-8") as fh:
                record = yaml.safe_load(fh)
        except yaml.YAMLError:
            continue
        if not isinstance(record, dict):
            continue
        # Slug uit bestandsnaam.
        index.setdefault(path.stem, path)
        for entry in record.get("names") or []:
            value = entry.get("value") if isinstance(entry, dict) else None
            if value:
                index.setdefault(slugify(value), path)
                index.setdefault(name_key(value), path)
                abbr = entry.get("abbr") if isinstance(entry, dict) else None
                if abbr:
                    index.setdefault(slugify(abbr), path)
                    index.setdefault(name_key(abbr), path)
    return index


def match_record(rwt: dict[str, Any], index: dict[str, Path]) -> Path | None:
    """Vind een bestaand YAML-bestand voor een RWT-record. Returnt None bij
    geen match."""
    name = rwt["name"]
    # Strip cluster-suffix voor matching.
    base = re.sub(r"\s*\(cluster\)\s*$", "", name, flags=re.IGNORECASE).strip()
    # Probeer afkorting tussen haakjes apart: "Foo (FOO)" -> probeer "FOO" ook.
    abbr_match = re.search(r"\(([A-Z][A-Za-z0-9.\-/&]{1,20})\)\s*$", base)
    candidates: list[str] = []
    if abbr_match:
        candidates.append(slugify(abbr_match.group(1)))
        candidates.append(name_key(abbr_match.group(1)))
        base_no_abbr = re.sub(r"\s*\([^)]*\)\s*$", "", base).strip()
        candidates.append(slugify(base_no_abbr))
        candidates.append(name_key(base_no_abbr))
    candidates.append(slugify(base))
    candidates.append(name_key(base))
    for key in candidates:
        if key and key in index:
            return index[key]
    return None


# ---------------------------------------------------------------------------
# Merge & write
# ---------------------------------------------------------------------------


def _make_source_entry(retrieved: str | None = None) -> dict[str, Any]:
    return {
        "id": SOURCE_ID,
        "url": RWT_REGISTER_URL,
        "retrieved": retrieved or _today(),
        "fields": ["rwt-status"],
    }


def _add_or_update_source(record: dict[str, Any], retrieved: str | None = None) -> bool:
    """Voeg de ar_rwt-source toe aan ``record["sources"]`` of update ``retrieved``.
    Returnt True als er iets veranderd is."""
    sources = list(record.get("sources") or [])
    new_entry = _make_source_entry(retrieved=retrieved)
    for idx, src in enumerate(sources):
        if isinstance(src, dict) and src.get("id") == SOURCE_ID:
            if src == new_entry:
                return False
            sources[idx] = new_entry
            record["sources"] = sources
            return True
    sources.append(new_entry)
    record["sources"] = sources
    return True


def _slug_for_new_record(name: str) -> str:
    """Slug voor een nieuw RWT-record. Strip cluster-suffix."""
    base = re.sub(r"\s*\(cluster\)\s*$", "", name, flags=re.IGNORECASE).strip()
    return slugify(base) or "rwt-onbekend"


def _build_new_record(rwt: dict[str, Any]) -> dict[str, Any]:
    name = re.sub(r"\s*\(cluster\)\s*$", "", rwt["name"], flags=re.IGNORECASE).strip()
    slug = _slug_for_new_record(rwt["name"])
    org_id = f"org:rwt-{slug}" if not slug.startswith("rwt-") else f"org:{slug}"
    record: dict[str, Any] = {
        "id": org_id,
        "type": "rwt",
        "classification": "rwt",
        "parent_id": None,
        "names": [{"value": canonicalize_leading_case(name), "valid_from": "1900-01-01"}],
        "valid_from": "1900-01-01",
        "valid_until": None,
        "sources": [_make_source_entry()],
    }
    return record


def _ordered_for_dump(record: dict[str, Any]) -> dict[str, Any]:
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


def apply_records(
    rwts: Iterable[dict[str, Any]],
    data_dir: Path,
    *,
    dry_run: bool = False,
) -> tuple[int, int, list[dict[str, Any]]]:
    """Voor elke RWT: match en update bestaande YAML, of schrijf een nieuwe.

    Returnt ``(matched, created, unmatched_review)``.
    """
    index = _load_existing_index(data_dir)
    matched = 0
    created = 0
    review: list[dict[str, Any]] = []
    rwt_dir = data_dir / "rwt"

    for rwt in rwts:
        if rwt.get("cluster") and rwt.get("parent") is None:
            # Cluster-heading zonder sub-items: alleen voor review, niets schrijven.
            review.append({**rwt, "reason": "cluster-heading"})
            continue

        target = match_record(rwt, index)
        if target is not None:
            matched += 1
            try:
                with target.open("r", encoding="utf-8") as fh:
                    record = yaml.safe_load(fh) or {}
            except yaml.YAMLError as exc:
                logger.warning("Kon %s niet lezen: %s", target, exc)
                continue
            changed = _add_or_update_source(record)
            if not changed:
                continue
            record = _ordered_for_dump(record)
            if dry_run:
                print(f"DRY-RUN zou updaten: {target}", file=sys.stderr)
                continue
            with target.open("w", encoding="utf-8") as fh:
                yaml.safe_dump(
                    record,
                    fh,
                    sort_keys=False,
                    default_flow_style=False,
                    allow_unicode=True,
                )
            continue

        # Geen match: maak een nieuw record onder rwt/.
        new_record = _build_new_record(rwt)
        slug = _slug_for_new_record(rwt["name"])
        target = rwt_dir / f"{slug}.yaml"
        if target.exists():
            # Slug-collision met bestaand RWT-record: behandel als match.
            try:
                with target.open("r", encoding="utf-8") as fh:
                    existing = yaml.safe_load(fh) or {}
            except yaml.YAMLError:
                existing = {}
            _add_or_update_source(existing)
            existing = _ordered_for_dump(existing)
            if dry_run:
                print(f"DRY-RUN zou updaten: {target}", file=sys.stderr)
            else:
                with target.open("w", encoding="utf-8") as fh:
                    yaml.safe_dump(
                        existing,
                        fh,
                        sort_keys=False,
                        default_flow_style=False,
                        allow_unicode=True,
                    )
            matched += 1
            index[slug] = target
            continue

        created += 1
        if dry_run:
            print(f"DRY-RUN zou nieuw schrijven: {target}", file=sys.stderr)
            continue
        rwt_dir.mkdir(parents=True, exist_ok=True)
        ordered = _ordered_for_dump(new_record)
        with target.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(
                ordered,
                fh,
                sort_keys=False,
                default_flow_style=False,
                allow_unicode=True,
            )
        index[slug] = target

    return matched, created, review


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="polder-fetch-ar-rwt",
        description=(
            "Download het RWT-register van de Algemene Rekenkamer en koppel "
            "RWT-status aan bestaande organisatie-records (of maak nieuwe)."
        ),
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=Path("data/organisaties"),
        help="Pad naar data/organisaties (default: data/organisaties).",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path("_cache/ar-rwt"),
        help="Cache-dir voor de gedownloade HTML (default: _cache/ar-rwt).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Schrijf niets; rapporteer alleen wat zou gebeuren.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose logging.",
    )
    return parser


def _cache_html(html: str, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / f"rwt-register-{_today()}.html"
    target.write_text(html, encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    try:
        html = fetch_register_index()
    except httpx.HTTPError as exc:
        logger.error("Kon RWT-register niet ophalen: %s", exc)
        return 2

    try:
        _cache_html(html, args.cache)
    except OSError as exc:  # pragma: no cover - filesystem failures
        logger.warning("Kon HTML niet cachen: %s", exc)

    rwts = parse_register(html)
    if not rwts:
        logger.error(
            "Geen RWT's gevonden op %s; HTML-structuur waarschijnlijk gewijzigd.",
            RWT_REGISTER_URL,
        )
        return 3

    matched, created, review = apply_records(rwts, args.data, dry_run=args.dry_run)
    print(
        f"AR-RWT: {len(rwts)} entries verwerkt, {matched} gematcht, "
        f"{created} nieuw, {len(review)} cluster-headings overgeslagen.",
        file=sys.stderr,
    )
    if args.verbose and review:
        for entry in review:
            print(f"  REVIEW: {entry['name']} ({entry.get('reason')})", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
