"""Fetcher voor het Gegevensmagazijn van de Tweede Kamer (OData v4).

Bron: Tweede Kamer der Staten-Generaal.
Endpoint: https://gegevensmagazijn.tweedekamer.nl/OData/v4/2.0/
Formaat: OData v4 + Atom SyncFeed (near-realtime).
Update: near-realtime (mutaties zichtbaar binnen minuten na vergaderbesluit).
Licentie: open (publieke data, geen aparte licentie-eisen voor hergebruik).
Dekking: TK-personen (Kamerleden), fracties, fractiezetels (samenstelling fracties
over tijd), commissies, vanaf 2008-09-01.

Library: tkapi (https://github.com/openkamer/tkapi), al opgenomen in pyproject.toml.

Tracking issue: https://github.com/anneschuth/polder/issues/1
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import unicodedata
import uuid
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from tkapi import TKApi
    from tkapi.fractie import FractieZetelPersoon
    from tkapi.persoon import Persoon

logger = logging.getLogger("polder.fetchers.tk_odata")

__all__ = [
    "ORG_ID_TWEEDE_KAMER",
    "POST_ID_KAMERLID",
    "SOURCE_ID",
    "TK_DATA_START",
    "TK_ODATA_BASE",
    "build_mandaat",
    "ensure_org_and_post",
    "fetch_persons_with_fractiezetels",
    "main",
    "merge_person",
    "person_to_polder_record",
    "slugify_person",
    "write_person",
]

TK_ODATA_BASE = "https://gegevensmagazijn.tweedekamer.nl/OData/v4/2.0/"
TK_DATA_START = date(2008, 9, 1)
SOURCE_ID = "tk_odata"
ORG_ID_TWEEDE_KAMER = "org:tweede-kamer"
POST_ID_KAMERLID = "post:kamerlid"


# ---------------------------------------------------------------------------
# Slug helpers
# ---------------------------------------------------------------------------


def _ascii_lower(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return decomposed.encode("ascii", "ignore").decode("ascii").lower()


def _strip_initials(initials: str) -> str:
    """`M.P.` of `M. P.` of `S.A.M.` → `mp`, `sam`."""
    if not initials:
        return ""
    cleaned = _ascii_lower(initials)
    return re.sub(r"[^a-z0-9]+", "", cleaned)


_HEX8_RE = re.compile(r"^[0-9a-f]{8}$")


def slugify_person(
    family: str,
    initials: str,
    birth_year: int | None,
    *,
    fallback_uuid: str | None = None,
) -> str:
    """Bouw stabiele slug `<familienaam>-<initialen>-<suffix>`.

    `family` mag tussenvoegsels bevatten zoals "van der Linden". Die laten we
    bewust uit de slug, omdat tussenvoegsels vaak inconsistent worden ingevoerd
    (van/Van, der/Der). De familienaam in `name.family` blijft wel intact.

    Suffix-keuze:

    1. ``birth_year`` aanwezig: gebruik het 4-cijferige jaartal (huidige
       conventie wint, ook als ``fallback_uuid`` ook is meegegeven).
    2. Alleen ``fallback_uuid``: neem de eerste 8 hex-tekens (lowercase) van de
       UUID. Geschikt als input een UUIDv7 is — de eerste 8 hex-chars dragen
       voldoende entropie voor uniciteit binnen polder-schaal.
    3. Geen van beide: ``ValueError``.
    """
    base = _ascii_lower(family or "")
    base = re.sub(r"[^a-z0-9\s-]+", " ", base)
    parts = [p for p in re.split(r"\s+", base) if p]
    # Strip Nederlandse tussenvoegsels.
    tussenvoegsels = {
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
    family_parts = [p for p in parts if p not in tussenvoegsels] or parts
    family_slug = "-".join(family_parts)
    family_slug = re.sub(r"-+", "-", family_slug).strip("-")
    initials_slug = _strip_initials(initials)

    if birth_year is not None:
        suffix = str(birth_year)
    elif fallback_uuid is not None:
        cleaned = fallback_uuid.strip().lower().replace("-", "")
        if len(cleaned) < 8 or not _HEX8_RE.match(cleaned[:8]):
            raise ValueError("fallback_uuid moet minstens 8 hex-tekens bevatten (0-9a-f)")
        suffix = cleaned[:8]
    else:
        raise ValueError("geboortejaar of fallback_uuid vereist voor slugify_person")

    pieces = [p for p in (family_slug, initials_slug, suffix) if p]
    return "-".join(pieces)


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------


def _today() -> str:
    return date.today().isoformat()


def _person_url(persoon_id: str) -> str:
    return f"{TK_ODATA_BASE}Persoon({persoon_id})"


def _normalize_gender(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip().lower()
    if v in {"man", "m", "male"}:
        return "m"
    if v in {"vrouw", "v", "f", "female"}:
        return "f"
    if v in {"x", "onbekend", "n", "non-binair"}:
        return "x"
    return None


def _normalize_initials(value: str | None) -> str | None:
    """Re-export van polder.lib.initials.format_initials voor backwards-compat."""
    from polder.lib.initials import format_initials

    return format_initials(value)


def _full_name(persoon: Persoon) -> str:
    """`Roepnaam Tussenvoegsel Achternaam` als roepnaam bekend, anders voornamen."""
    given = (persoon.roepnaam or persoon.voornamen or "").strip()
    tussen = (persoon.tussenvoegsel or "").strip()
    family = (persoon.achternaam or "").strip()
    pieces = [p for p in (given, tussen, family) if p]
    return " ".join(pieces)


def _family_with_tussenvoegsel(persoon: Persoon) -> str:
    tussen = (persoon.tussenvoegsel or "").strip()
    family = (persoon.achternaam or "").strip()
    return f"{tussen} {family}".strip() if tussen else family


def build_mandaat(
    *,
    fzp: FractieZetelPersoon,
    fractie_naam: str,
    fractie_afkorting: str,
    today: str | None = None,
) -> dict[str, Any]:
    """Map een FractieZetelPersoon-zetel naar een polder-mandaat dict."""
    today_str = today or _today()
    start = fzp.van
    end = fzp.tot_en_met
    role_label = fractie_afkorting or fractie_naam or "fractie"
    return {
        "id": str(uuid.uuid4()),
        "organization_id": ORG_ID_TWEEDE_KAMER,
        "post_id": POST_ID_KAMERLID,
        "role": f"Kamerlid voor {role_label}",
        "start_date": start.isoformat() if start else TK_DATA_START.isoformat(),
        "end_date": end.isoformat() if end else None,
        "appointment": {"decision": "TK-installatie"},
        "sources": [
            {
                "id": SOURCE_ID,
                "url": f"{TK_ODATA_BASE}FractieZetelPersoon({fzp.id})",
                "retrieved": today_str,
            }
        ],
    }


def person_to_polder_record(
    persoon: Persoon,
    mandaten: list[dict[str, Any]],
    *,
    today: str | None = None,
) -> dict[str, Any] | None:
    """Map een tkapi.Persoon naar een polder-personenrecord.

    Returnt ``None`` als de persoon niet bruikbaar is (geen geboortedatum,
    geen achternaam — vereisten voor stabiele slug en schema-conformiteit).
    """
    today_str = today or _today()
    family = (persoon.achternaam or "").strip()
    if not family:
        return None
    geboortedatum = persoon.geboortedatum
    if geboortedatum is None:
        return None
    initials_raw = (persoon.initialen or "").strip()
    initials_norm = _normalize_initials(initials_raw)
    birth_year = geboortedatum.year
    slug = slugify_person(family, initials_raw, birth_year)
    if not slug:
        return None

    name_block: dict[str, Any] = {
        "full": _full_name(persoon),
        "family": _family_with_tussenvoegsel(persoon),
    }
    given = (persoon.roepnaam or persoon.voornamen or "").strip()
    if given:
        name_block["given"] = given
    if initials_norm:
        name_block["initials"] = initials_norm
    titels = (persoon.titels or "").strip()
    if titels:
        # titels-veld bevat doorgaans pre-honorifics ("dr.", "mr.").
        name_block["honorifics_pre"] = [titels]

    record: dict[str, Any] = {
        "id": f"person:{slug}",
        "identifiers": {"tk_persoon_id": persoon.id},
        "name": name_block,
        "birth": {"year": birth_year},
    }

    gender = _normalize_gender(persoon.geslacht)
    if gender is not None:
        record["gender"] = gender

    if mandaten:
        record["mandaten"] = mandaten

    record["sources"] = [
        {
            "id": SOURCE_ID,
            "url": _person_url(persoon.id),
            "retrieved": today_str,
        }
    ]
    return record


# ---------------------------------------------------------------------------
# Fetch (live calls)
# ---------------------------------------------------------------------------


def fetch_persons_with_fractiezetels(
    api: TKApi,
    *,
    since: date | None = TK_DATA_START,
    limit: int | None = None,
    today: str | None = None,
    include_persons_without_mandaten: bool = False,
) -> list[dict[str, Any]]:
    """Haal personen op die een fractiezetel hebben (gehad) sinds ``since``.

    ``since=None`` haalt de volledige historie binnen (alle zetels, geen
    datum-filter). Met de default 2008-09-01 wordt oud-historie afgesneden.

    ``include_persons_without_mandaten``: als True, schrijven we ook
    persoon-records waarvan alle zetels weggefilterd zijn. Default False:
    geen verweesde persoon-records meer (anders krijg je ~100 records van
    oud-Kamerleden zonder mandaten).

    Returnt een lijst polder-records (al via ``person_to_polder_record`` gemapt).
    """
    from tkapi.fractie import FractieZetelPersoon
    from tkapi.persoon import Persoon

    today_str = today or _today()

    persoon_filter = Persoon.create_filter()
    persoon_filter.filter_has_fractiezetel()
    personen: list[Persoon] = api.get_personen(filter=persoon_filter, max_items=limit)

    records: list[dict[str, Any]] = []
    skipped_no_mandates = 0
    for persoon in personen:
        # Per persoon: alle FractieZetelPersoon-records (zetels in tijd).
        zetel_filter = FractieZetelPersoon.create_filter()
        zetel_filter.add_filter_str(f"Persoon/Id eq {persoon.id}")
        zetels: list[FractieZetelPersoon] = api.get_items(FractieZetelPersoon, filter=zetel_filter)

        mandaten: list[dict[str, Any]] = []
        for zetel in zetels:
            try:
                fractie = zetel.fractie
            except Exception as exc:
                logger.debug("Geen fractie voor zetel %s: %s", zetel.id, exc)
                continue
            # Filter op since: skip zetels die volledig voor since liggen.
            # Met since=None laten we alles staan.
            if since is not None and zetel.tot_en_met is not None and zetel.tot_en_met < since:
                continue
            mandaten.append(
                build_mandaat(
                    fzp=zetel,
                    fractie_naam=fractie.naam if fractie else "",
                    fractie_afkorting=fractie.afkorting if fractie else "",
                    today=today_str,
                )
            )

        if not mandaten and not include_persons_without_mandaten:
            skipped_no_mandates += 1
            continue

        record = person_to_polder_record(persoon, mandaten, today=today_str)
        if record is None:
            continue
        records.append(record)
    if skipped_no_mandates:
        logger.info(
            "TK fetcher: %d persoon-records geskipt (geen zetels binnen since=%s)",
            skipped_no_mandates,
            since,
        )
    return records


# ---------------------------------------------------------------------------
# Merge & write
# ---------------------------------------------------------------------------


def _has_active_mandaat(record: dict[str, Any]) -> bool:
    for mandaat in record.get("mandaten") or []:
        if mandaat.get("end_date") is None:
            return True
    return False


def _target_path(out_dir: Path, record: dict[str, Any]) -> Path:
    slug = record["id"].split(":", 1)[1]
    return out_dir / f"{slug}.yaml"


def _merge_identifiers(
    existing: dict[str, Any] | None, new: dict[str, Any] | None
) -> dict[str, Any]:
    out = dict(existing or {})
    for key, value in (new or {}).items():
        if value is not None and value != "":
            out[key] = value
        elif key not in out:
            out[key] = value
    return out


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


def _merge_mandaten(
    existing: list[dict[str, Any]] | None, new: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """Match op (post_id, start_date) — vervang nieuwe waar match, behoud rest."""
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for mandaat in existing or []:
        if not isinstance(mandaat, dict):
            continue
        key = (mandaat.get("post_id", ""), mandaat.get("start_date", ""))
        by_key[key] = dict(mandaat)
    for mandaat in new or []:
        key = (mandaat.get("post_id", ""), mandaat.get("start_date", ""))
        if key in by_key:
            merged = dict(by_key[key])
            merged.update(mandaat)
            # Behoud bestaand mandaat-id (UUID) als die er is.
            if by_key[key].get("id"):
                merged["id"] = by_key[key]["id"]
            merged["sources"] = _merge_sources(by_key[key].get("sources"), mandaat.get("sources"))
            by_key[key] = merged
        else:
            by_key[key] = dict(mandaat)
    return sorted(by_key.values(), key=lambda m: m.get("start_date", ""))


def merge_person(existing: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge bestaande persoon-record met nieuwe TK-data.

    TK-data wint voor velden die hij vult, lokale toevoegingen blijven staan.
    """
    if not existing:
        return dict(new)

    merged: dict[str, Any] = dict(existing)
    for key, value in new.items():
        if key == "identifiers":
            merged["identifiers"] = _merge_identifiers(merged.get("identifiers"), value)
        elif key == "sources":
            merged["sources"] = _merge_sources(merged.get("sources"), value)
        elif key == "mandaten":
            merged["mandaten"] = _merge_mandaten(merged.get("mandaten"), value)
        elif key == "name":
            current = dict(merged.get("name") or {})
            for nk, nv in (value or {}).items():
                if nv:
                    current[nk] = nv
            merged["name"] = current
        else:
            if value is not None or key not in merged:
                merged[key] = value
    return merged


def _ordered_for_dump(record: dict[str, Any]) -> dict[str, Any]:
    order = ["id", "identifiers", "name", "birth", "gender", "mandaten", "sources"]
    out: dict[str, Any] = {}
    for key in order:
        if key in record:
            out[key] = record[key]
    for key, value in record.items():
        if key not in out:
            out[key] = value
    return out


def write_person(
    record: dict[str, Any],
    out_dir: Path,
    *,
    dry_run: bool = False,
) -> Path:
    """Schrijf een persoon-record. Personen liggen vlak onder ``out_dir``."""
    target = _target_path(out_dir, record)

    existing: dict[str, Any] = {}
    if target.exists():
        with target.open("r", encoding="utf-8") as fh:
            existing = yaml.safe_load(fh) or {}

    merged = merge_person(existing, record)
    merged = _ordered_for_dump(merged)

    if dry_run:
        print(f"DRY-RUN zou schrijven: {target}", file=sys.stderr)
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(merged, fh, sort_keys=False, default_flow_style=False, allow_unicode=True)
    return target


# ---------------------------------------------------------------------------
# Org + Post bootstrap
# ---------------------------------------------------------------------------


def ensure_org_and_post(
    data_root: Path,
    *,
    today: str | None = None,
    dry_run: bool = False,
) -> tuple[Path, Path]:
    """Schrijf `org:tweede-kamer` en `post:kamerlid` als ze nog niet bestaan."""
    today_str = today or _today()
    org_path = data_root / "organisaties" / "hoge-colleges" / "tweede-kamer.yaml"
    post_path = data_root / "posten" / "kamerlid.yaml"

    if not org_path.exists():
        org_record = {
            "id": ORG_ID_TWEEDE_KAMER,
            "type": "hoge-college",
            "identifiers": {
                "tooi": "https://identifier.overheid.nl/tooi/id/orgaan/oa10000001",
            },
            "classification": "hoge-college",
            "parent_id": None,
            "names": [
                {
                    "value": "Tweede Kamer der Staten-Generaal",
                    "abbr": "TK",
                    "valid_from": "1815-08-24",
                }
            ],
            "contact": {
                "website": "https://www.tweedekamer.nl",
                "bezoekadres": "Plein 2, 2511 CR Den Haag",
            },
            "valid_from": "1815-08-24",
            "valid_until": None,
            "sources": [
                {
                    "id": SOURCE_ID,
                    "url": TK_ODATA_BASE,
                    "retrieved": today_str,
                }
            ],
        }
        if not dry_run:
            org_path.parent.mkdir(parents=True, exist_ok=True)
            with org_path.open("w", encoding="utf-8") as fh:
                yaml.safe_dump(
                    org_record,
                    fh,
                    sort_keys=False,
                    default_flow_style=False,
                    allow_unicode=True,
                )

    if not post_path.exists():
        post_record = {
            "id": POST_ID_KAMERLID,
            "organization_id": ORG_ID_TWEEDE_KAMER,
            "label": "Lid van de Tweede Kamer",
            "classification": "kamerlid",
            "seat_count": 150,
            "valid_from": "1956-01-01",
            "valid_until": None,
        }
        if not dry_run:
            post_path.parent.mkdir(parents=True, exist_ok=True)
            with post_path.open("w", encoding="utf-8") as fh:
                yaml.safe_dump(
                    post_record,
                    fh,
                    sort_keys=False,
                    default_flow_style=False,
                    allow_unicode=True,
                )

    return org_path, post_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_since(value: str) -> date:
    return date.fromisoformat(value)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="polder-fetch-tk-odata",
        description=(
            "Haal TK-Kamerleden + fractiezetels op uit het Gegevensmagazijn "
            "en schrijf polder-personenrecords."
        ),
    )
    parser.add_argument(
        "--since",
        type=_parse_since,
        default=TK_DATA_START,
        help=f"Ondergrens (ISO date). Default: {TK_DATA_START.isoformat()}",
    )
    parser.add_argument(
        "--all-history",
        action="store_true",
        help=(
            "Haal alle TK-historie binnen (overschrijft --since=None). "
            "Levert duizenden extra oude zetels op."
        ),
    )
    parser.add_argument(
        "--include-persons-without-mandaten",
        action="store_true",
        help=(
            "Schrijf ook persoon-records waarvan alle zetels weggefilterd zijn. "
            "Default: skip (anders krijg je verweesde records van oud-Kamerleden)."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max aantal personen (voor testen).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data/personen"),
        help="Output-directory (default: data/personen).",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("data"),
        help="Root van data/ voor org+post bootstrap (default: data).",
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

    from tkapi import TKApi

    api = TKApi(verbose=args.verbose)
    ensure_org_and_post(args.data_root, dry_run=args.dry_run)

    effective_since: date | None = None if args.all_history else args.since
    records = fetch_persons_with_fractiezetels(
        api,
        since=effective_since,
        limit=args.limit,
        include_persons_without_mandaten=args.include_persons_without_mandaten,
    )

    n_current = 0
    n_historisch = 0
    for record in records:
        write_person(record, args.out, dry_run=args.dry_run)
        if _has_active_mandaat(record):
            n_current += 1
        else:
            n_historisch += 1

    print(
        f"Wrote {n_current} current + {n_historisch} historisch persoon-records to {args.out}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
