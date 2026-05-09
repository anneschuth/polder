"""Fetcher voor Eerste-Kamer-leden via HTML scrape van eerstekamer.nl.

Bron: Eerste Kamer der Staten-Generaal.
Endpoint: https://www.eerstekamer.nl/alle_leden (index) +
https://www.eerstekamer.nl/persoon/<ek-slug> (per lid).
Formaat: HTML (server-rendered, redelijk consistente structuur).
Update: bij iedere verkiezing (vierjaarlijks) en tussentijdse mutaties.
Licentie: open (publieke webpagina; redelijk gebruik voor open data).
Dekking: alle huidige Eerste-Kamerleden (75 zetels), met partij, geboortejaar
en aanvangsdatum van het mandaat. Voor historische EK-leden levert deze pagina
geen volledige reeks; dat doet KOOP SRU voor benoemingen-KB's.

Tracking issue: https://github.com/anneschuth/polder/issues/13
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import yaml
from bs4 import BeautifulSoup

from polder.fetchers.tk_odata import (
    _normalize_initials,
    merge_person,
    slugify_person,
    write_person,
)

logger = logging.getLogger("polder.fetchers.ek_scrape")

__all__ = [
    "EK_BASE",
    "EK_LEDEN_URL",
    "ORG_ID_EERSTE_KAMER",
    "POST_ID_SENATOR",
    "SOURCE_ID",
    "EkLidIndexEntry",
    "build_record",
    "ensure_org_and_post",
    "extract_index_entries",
    "fetch_leden_index",
    "fetch_lid_pagina",
    "main",
    "parse_lid_pagina",
]

EK_BASE = "https://www.eerstekamer.nl"
EK_LEDEN_URL = f"{EK_BASE}/alle_leden"
HTTP_TIMEOUT = 60.0
USER_AGENT = (
    "Mozilla/5.0 (compatible; polder/0.0.1; "
    "+https://github.com/anneschuth/polder; anne.schuth@gmail.com)"
)
SOURCE_ID = "ek_scrape"
ORG_ID_EERSTE_KAMER = "org:eerste-kamer"
POST_ID_SENATOR = "post:senator"

# Maanden naar maandnummer (alleen NL spelling; EK-pagina gebruikt geen Engels).
NL_MONTHS = {
    "januari": 1,
    "februari": 2,
    "maart": 3,
    "april": 4,
    "mei": 5,
    "juni": 6,
    "juli": 7,
    "augustus": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "december": 12,
}


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _today() -> str:
    return date.today().isoformat()


def _normalize_text(value: str) -> str:
    """NFKC + collapse whitespace (incl. nbsp)."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


_TITLE_TOKENS = {
    "dr",
    "drs",
    "mr",
    "ir",
    "ing",
    "prof",
    "ds",
    "mw",
    "ma",
    "msc",
    "bsc",
    "lld",
    "phd",
    "md",
    "bgen",
    "b",
    "d",
    "rn",
}


def _strip_titles(display: str) -> str:
    """Verwijder pre/post titles en haakjes-suffix uit een H1.

    "Dr. M.L. Vos (GroenLinks-PvdA)" → "M.L. Vos"
    "R. van Aelst-den Uijl MA (SP)" → "R. van Aelst-den Uijl"
    "prof. dr. E.B. van Apeldoorn" → "E.B. van Apeldoorn"
    """
    s = re.sub(r"\s*\([^)]*\)\s*$", "", display).strip()
    tokens = s.split()
    # Strip leading title tokens (case-insensitive, with or without trailing dot).
    while tokens:
        tok = tokens[0].rstrip(".").lower()
        if tok in _TITLE_TOKENS:
            tokens.pop(0)
        else:
            break
    # Strip trailing title tokens (post-honorifics like "MA", "MSc").
    while tokens:
        tok = tokens[-1].rstrip(".").lower()
        if tok in _TITLE_TOKENS:
            tokens.pop()
        else:
            break
    return " ".join(tokens).strip()


def _split_initials_and_family(name_no_titles: str) -> tuple[str, str]:
    """Splits "M.L. Vos" → ("M.L.", "Vos"); "R. van Aelst-den Uijl" → ("R.", "van Aelst-den Uijl").

    Initials = leading whitespace-separated tokens that look like X. or X.Y. or X-Y.
    """
    tokens = name_no_titles.split()
    initials_tokens: list[str] = []
    rest_tokens: list[str] = []
    for tok in tokens:
        # Initial-token: only letters and dots, ends with dot.
        if re.fullmatch(r"(?:[A-Z]\.)+", tok):
            initials_tokens.append(tok)
        else:
            rest_tokens = tokens[len(initials_tokens) :]
            break
    initials = "".join(initials_tokens)
    family = " ".join(rest_tokens).strip()
    return initials, family


def _parse_dutch_date(value: str) -> date | None:
    """`13 juni 2023` → date(2023, 6, 13). Returnt None bij onbekende maand."""
    s = _normalize_text(value).lower()
    m = re.match(r"(\d{1,2})\s+([a-z]+)\s+(\d{4})$", s)
    if not m:
        return None
    day, month_name, year = m.group(1), m.group(2), m.group(3)
    month = NL_MONTHS.get(month_name)
    if month is None:
        return None
    try:
        return date(int(year), month, int(day))
    except ValueError:
        return None


def _parse_index_birthdate(value: str) -> date | None:
    """`Geboortedatum: 01-12-1988` → date(1988, 12, 1)."""
    m = re.search(r"(\d{1,2})-(\d{1,2})-(\d{4})", value)
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _slug_from_href(href: str) -> str:
    """`/persoon/r_van_aelst_den_uijl_ma_sp` → `r_van_aelst_den_uijl_ma_sp`."""
    return href.rsplit("/", 1)[-1]


# ---------------------------------------------------------------------------
# Cache + fetch
# ---------------------------------------------------------------------------


def _cache_dir(cache_root: Path) -> Path:
    out = cache_root / "eerstekamer"
    out.mkdir(parents=True, exist_ok=True)
    return out


def fetch_leden_index(
    *,
    timeout: float = HTTP_TIMEOUT,
    cache_root: Path | None = None,
    today: str | None = None,
) -> str:
    """Haal de HTML van /alle_leden op, met dagelijkse cache."""
    today_str = today or _today()
    if cache_root is not None:
        cached = _cache_dir(cache_root) / f"alle_leden-{today_str}.html"
        if cached.exists():
            return cached.read_text(encoding="utf-8")
    response = httpx.get(
        EK_LEDEN_URL,
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
        follow_redirects=True,
    )
    response.raise_for_status()
    text = response.text
    if cache_root is not None:
        cached = _cache_dir(cache_root) / f"alle_leden-{today_str}.html"
        cached.write_text(text, encoding="utf-8")
    return text


def fetch_lid_pagina(
    slug: str,
    *,
    timeout: float = HTTP_TIMEOUT,
    cache_root: Path | None = None,
    today: str | None = None,
    client: httpx.Client | None = None,
) -> str:
    """Haal de HTML van /persoon/<slug> op, met dagelijkse cache."""
    today_str = today or _today()
    if cache_root is not None:
        cached = _cache_dir(cache_root) / f"{slug}-{today_str}.html"
        if cached.exists():
            return cached.read_text(encoding="utf-8")
    url = f"{EK_BASE}/persoon/{slug}"
    headers = {"User-Agent": USER_AGENT}
    if client is not None:
        response = client.get(url, headers=headers, timeout=timeout, follow_redirects=True)
    else:
        response = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    text = response.text
    if cache_root is not None:
        cached = _cache_dir(cache_root) / f"{slug}-{today_str}.html"
        cached.write_text(text, encoding="utf-8")
    return text


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


@dataclass
class EkLidIndexEntry:
    """Eén rij uit /alle_leden."""

    slug: str
    display_name: str
    party: str
    birth_date: date | None


def extract_index_entries(html: str) -> list[EkLidIndexEntry]:
    """Trek de lijst lid-entries uit de /alle_leden-pagina."""
    soup = BeautifulSoup(html, "html.parser")
    entries: list[EkLidIndexEntry] = []
    for li in soup.select("li.persoon"):
        a = li.find("a")
        if not a or not a.get("href"):
            continue
        href = str(a["href"])
        slug = _slug_from_href(href)

        naam_el = li.select_one(".naam")
        display_name = _normalize_text(naam_el.get_text(" ")) if naam_el else ""

        # Party: tweede div in .persoon_bijschrift (eerste = naam-wrapper).
        party = ""
        bijschrift = li.select_one(".persoon_bijschrift")
        if bijschrift:
            divs = bijschrift.find_all("div", recursive=False)
            for div in divs:
                t = _normalize_text(div.get_text(" "))
                # Skip de div die alleen de naam-wrapper bevat (bevat sub-div .naam).
                if div.select_one(".naam"):
                    continue
                if not t or t.startswith("Anci"):
                    continue
                party = t
                break

        text = _normalize_text(li.get_text(" "))
        birth = None
        m = re.search(r"Geboortedatum:\s*(\d{1,2}-\d{1,2}-\d{4})", text)
        if m:
            birth = _parse_index_birthdate(m.group(1))

        if not slug or not display_name:
            continue
        entries.append(
            EkLidIndexEntry(
                slug=slug,
                display_name=display_name,
                party=party,
                birth_date=birth,
            )
        )
    return entries


def parse_lid_pagina(html: str) -> dict[str, Any]:
    """Parse een individuele EK-lid-pagina naar een dict met ruwe velden.

    Returnt keys (allemaal optioneel behalve ``display_name``):
    ``display_name`` (uit H1), ``party`` (uit H1-haakjes), ``intro_full_name``
    (eerste paragraaf voor "is/was"), ``birth_date`` (uit Personalia),
    ``mandaat_start`` (uit Loopbaan of intro), ``mandaat_end``,
    ``gender`` (afgeleid uit "Mevrouw"/"De heer" intro).
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    out: dict[str, Any] = {}

    h1 = soup.find("h1")
    if h1:
        h1_text = _normalize_text(h1.get_text(" "))
        out["display_name"] = h1_text
        m = re.search(r"\(([^)]+)\)\s*$", h1_text)
        if m:
            out["party"] = m.group(1).strip()

    main = soup.find("div", id="main_content_wrapper") or soup.find("main") or soup

    intro_text = ""
    for p in main.find_all("p"):
        t = _normalize_text(p.get_text(" "))
        if not t:
            continue
        intro_text = t
        break
    out["intro"] = intro_text

    # Volledige naam uit intro: alles voor " is " of " was " of " (".
    if intro_text:
        m = re.match(r"^(.+?)\s+(?:\(\d{4}\)\s+)?(?:is|was)\b", intro_text)
        if m:
            candidate = m.group(1).strip()
            # Strip trailing "(jaar)".
            candidate = re.sub(r"\s*\(\d{4}\)\s*$", "", candidate).strip()
            if candidate:
                out["intro_full_name"] = candidate

    # Gender heuristiek uit intro of body.
    body_text = _normalize_text(main.get_text(" "))
    if re.search(r"\bMevrouw\b", body_text) or re.search(r"\bZij\s+is\b", body_text):
        out["gender"] = "f"
    elif re.search(r"\bDe\s+heer\b", body_text) or re.search(r"\bHij\s+is\b", body_text):
        out["gender"] = "m"

    # Geboortedatum uit Personalia: "geboren te <plaats>, <dag> <maand> <jaar>".
    m = re.search(
        r"geboren\s+te\s+[^,]+,\s+(\d{1,2})\s+([a-zA-Z]+)\s+(\d{4})",
        body_text,
    )
    if m:
        d = _parse_dutch_date(f"{m.group(1)} {m.group(2)} {m.group(3)}")
        if d:
            out["birth_date"] = d

    # Mandaat-startdatum: probeer Loopbaan-regel eerst (meest expliciet).
    m = re.search(
        r"Lid\s+Eerste\s+Kamer\s+der\s+Staten-Generaal\s+vanaf\s+"
        r"(\d{1,2}\s+[a-zA-Z]+\s+\d{4})"
        r"(?:\s+tot\s+(\d{1,2}\s+[a-zA-Z]+\s+\d{4}))?",
        body_text,
    )
    if m:
        start = _parse_dutch_date(m.group(1))
        if start:
            out["mandaat_start"] = start
        if m.group(2):
            end = _parse_dutch_date(m.group(2))
            if end:
                out["mandaat_end"] = end
    else:
        # Fallback: intro-zin "is sinds <date> [lid] van de <party>-fractie".
        m = re.search(
            r"(?:sinds|vanaf)\s+(\d{1,2}\s+[a-zA-Z]+\s+\d{4})\s+(?:lid\s+)?(?:van\s+de\s+)?"
            r"[A-Za-z0-9\-/]+-fractie\s+in\s+de\s+Eerste\s+Kamer",
            intro_text,
        )
        if m:
            start = _parse_dutch_date(m.group(1))
            if start:
                out["mandaat_start"] = start

    return out


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------


def _build_full_name(intro_full_name: str | None, display_name_no_titles: str) -> str:
    if intro_full_name:
        return intro_full_name
    return display_name_no_titles


def _build_mandaat(
    *,
    party: str,
    start_date: date,
    end_date: date | None,
    slug: str,
    today: str,
) -> dict[str, Any]:
    role_label = party or "fractie"
    return {
        "id": str(uuid.uuid4()),
        "organization_id": ORG_ID_EERSTE_KAMER,
        "post_id": POST_ID_SENATOR,
        "role": f"Senator voor {role_label}",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat() if end_date else None,
        "appointment": {"decision": "EK-installatie"},
        "sources": [
            {
                "id": SOURCE_ID,
                "url": f"{EK_BASE}/persoon/{slug}",
                "retrieved": today,
            }
        ],
    }


def build_record(
    index_entry: EkLidIndexEntry,
    parsed: dict[str, Any],
    *,
    today: str | None = None,
) -> dict[str, Any] | None:
    """Combineer index- en lidpagina-data tot een polder-personenrecord.

    Returnt None als verplichte velden ontbreken (achternaam, geboortejaar,
    of mandaat-startdatum).
    """
    today_str = today or _today()
    display = parsed.get("display_name") or index_entry.display_name
    if not display:
        return None
    name_no_titles = _strip_titles(display)
    initials_raw, family = _split_initials_and_family(name_no_titles)
    if not family:
        return None

    birth = parsed.get("birth_date") or index_entry.birth_date
    if birth is None:
        return None
    birth_year = birth.year

    party = parsed.get("party") or index_entry.party

    slug = slugify_person(family, initials_raw, birth_year)
    if not slug:
        return None

    initials_norm = _normalize_initials(initials_raw)

    name_block: dict[str, Any] = {
        "full": _build_full_name(parsed.get("intro_full_name"), name_no_titles),
        "family": family,
    }
    if initials_norm:
        name_block["initials"] = initials_norm

    # Pre-honorifics: alles wat we van H1 hebben afgeknipt aan de voorkant,
    # gereconstrueerd door verschil tussen oorspronkelijke H1 en stripped name.
    pre = _extract_pre_honorifics(display)
    if pre:
        name_block["honorifics_pre"] = pre
    post = _extract_post_honorifics(display)
    if post:
        name_block["honorifics_post"] = post

    record: dict[str, Any] = {
        "id": f"person:{slug}",
        "identifiers": {"ek_lid_slug": index_entry.slug},
        "name": name_block,
        "birth": {"year": birth_year},
    }

    gender = parsed.get("gender")
    if gender in ("m", "f", "x"):
        record["gender"] = gender

    mandaat_start = parsed.get("mandaat_start")
    mandaat_end = parsed.get("mandaat_end")
    if mandaat_start is None:
        # Zonder mandaat-start kunnen we geen geldig mandaat schrijven.
        return None
    record["mandaten"] = [
        _build_mandaat(
            party=party,
            start_date=mandaat_start,
            end_date=mandaat_end,
            slug=index_entry.slug,
            today=today_str,
        )
    ]

    record["sources"] = [
        {
            "id": SOURCE_ID,
            "url": f"{EK_BASE}/persoon/{index_entry.slug}",
            "retrieved": today_str,
        }
    ]
    return record


def _extract_pre_honorifics(display: str) -> list[str]:
    s = re.sub(r"\s*\([^)]*\)\s*$", "", display).strip()
    tokens = s.split()
    pre: list[str] = []
    for tok in tokens:
        if re.fullmatch(r"(?:[A-Z]\.)+", tok):
            break
        if tok.rstrip(".").lower() in _TITLE_TOKENS:
            pre.append(tok)
        else:
            break
    return pre


def _extract_post_honorifics(display: str) -> list[str]:
    s = re.sub(r"\s*\([^)]*\)\s*$", "", display).strip()
    tokens = s.split()
    post: list[str] = []
    while tokens:
        tok = tokens[-1].rstrip(".").lower()
        if tok in _TITLE_TOKENS and not re.fullmatch(r"(?:[A-Z]\.)+", tokens[-1]):
            post.insert(0, tokens.pop())
        else:
            break
    return post


# ---------------------------------------------------------------------------
# Schema-stub: persoon-id moet uniek zijn als schema-id, maar identifiers
# laat in het schema alleen wikidata/tk_persoon_id/abd_id/allmanak_id toe.
# We kunnen `ek_lid_slug` niet in `identifiers` zetten zonder schema-aanpassing.
# Voor nu plaatsen we de slug onder `sources[0].url` (behoudt traceability) en
# verwijderen we de identifier uit het record. Schema-uitbreiding is een
# aparte PR.
# ---------------------------------------------------------------------------


def _strip_unsupported_identifiers(record: dict[str, Any]) -> dict[str, Any]:
    allowed = {"wikidata", "tk_persoon_id", "abd_id", "allmanak_id"}
    ids = record.get("identifiers") or {}
    record["identifiers"] = {k: v for k, v in ids.items() if k in allowed}
    if not record["identifiers"]:
        record.pop("identifiers", None)
    return record


# ---------------------------------------------------------------------------
# Org + Post bootstrap
# ---------------------------------------------------------------------------


def ensure_org_and_post(
    data_root: Path,
    *,
    today: str | None = None,
    dry_run: bool = False,
) -> tuple[Path, Path]:
    """Schrijf `org:eerste-kamer` en `post:senator` als ze nog niet bestaan."""
    today_str = today or _today()
    org_path = data_root / "organisaties" / "hoge-colleges" / "eerste-kamer.yaml"
    post_path = data_root / "posten" / "senator.yaml"

    if not org_path.exists():
        org_record = {
            "id": ORG_ID_EERSTE_KAMER,
            "type": "hoge-college",
            "identifiers": {
                "tooi": "https://identifier.overheid.nl/tooi/id/orgaan/oa10000002",
            },
            "classification": "hoge-college",
            "parent_id": None,
            "names": [
                {
                    "value": "Eerste Kamer der Staten-Generaal",
                    "abbr": "EK",
                    "valid_from": "1815-08-24",
                }
            ],
            "contact": {
                "website": "https://www.eerstekamer.nl",
                "bezoekadres": "Binnenhof 22, 2513 AA Den Haag",
            },
            "valid_from": "1815-08-24",
            "valid_until": None,
            "sources": [
                {
                    "id": SOURCE_ID,
                    "url": EK_LEDEN_URL,
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
            "id": POST_ID_SENATOR,
            "organization_id": ORG_ID_EERSTE_KAMER,
            "label": "Lid van de Eerste Kamer",
            "classification": "lid-hcs",
            "seat_count": 75,
            "valid_from": "1815-08-24",
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="polder-fetch-ek-scrape",
        description="Scrape EK-leden van eerstekamer.nl en schrijf polder-personenrecords.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max aantal leden (voor testen).",
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
        "--cache-root",
        type=Path,
        default=Path("_cache"),
        help="Root voor HTML-cache (default: _cache).",
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

    today_str = _today()
    ensure_org_and_post(args.data_root, today=today_str, dry_run=args.dry_run)

    index_html = fetch_leden_index(cache_root=args.cache_root, today=today_str)
    entries = extract_index_entries(index_html)
    if args.limit is not None:
        entries = entries[: args.limit]

    logger.info("Gevonden %d EK-leden in index", len(entries))

    n_current = 0
    n_historisch = 0
    n_skipped = 0
    with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
        for entry in entries:
            try:
                lid_html = fetch_lid_pagina(
                    entry.slug,
                    cache_root=args.cache_root,
                    today=today_str,
                    client=client,
                )
            except httpx.HTTPError as exc:
                logger.warning("Kon %s niet ophalen: %s", entry.slug, exc)
                n_skipped += 1
                continue

            parsed = parse_lid_pagina(lid_html)
            record = build_record(entry, parsed, today=today_str)
            if record is None:
                logger.warning("Skip %s: incomplete data", entry.slug)
                n_skipped += 1
                continue
            record = _strip_unsupported_identifiers(record)

            target = write_person(record, args.out, dry_run=args.dry_run)
            if target.parent.name == "current":
                n_current += 1
            else:
                n_historisch += 1

    print(
        f"Wrote {n_current} current + {n_historisch} historisch + {n_skipped} skipped "
        f"EK-records to {args.out}",
        file=sys.stderr,
    )
    return 0


# Re-export voor tests die ze rechtstreeks willen (hergebruik tk_odata).
__all__ += ["merge_person", "slugify_person", "write_person"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
