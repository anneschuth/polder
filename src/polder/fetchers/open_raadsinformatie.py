"""Fetcher voor Open Raadsinformatie (gemeentelijke bestuurders en raadsleden).

Bron: Open Raadsinformatie (ORI), Open State Foundation.
Endpoint: https://api.openraadsinformatie.nl/v1/elastic/<index>/_search
Formaat: Elasticsearch query DSL als POST-body, response in Popolo-achtige
records (Person + Membership + Organization).
Update: gestaag (afhankelijk van per-gemeente publicatiekadans, weken-cyclus).
Licentie: open (Open State Foundation publiceert onder open licentie).
Dekking: 310+ Nederlandse gemeenten en stadsdelen, met wethouders, raadsleden,
fracties, commissies en agendapunten.

ORI-indices zijn per gemeente (`ori_utrecht`, `ori_amsterdam_zuid`, ...). Niet
alle gemeenten gebruiken hetzelfde data-schema: Utrecht (iBabs) levert keurige
`@type: Person`/`@type: Membership` records met `role`-veld; Amsterdam levert
ruwe documenten zonder `@type`. Deze fetcher targets de Popolo-stijl indices.

Tracking issue: https://github.com/anneschuth/polder/issues/16
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
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import yaml

logger = logging.getLogger("polder.fetchers.open_raadsinformatie")

__all__ = [
    "ORI_ELASTIC_BASE",
    "PAGE_SIZE",
    "RATE_LIMIT_DELAY",
    "ROLE_TO_CLASSIFICATION",
    "SOURCE_ID",
    "build_mandaat",
    "ensure_org_and_posts",
    "fetch_persons_for_gemeente",
    "main",
    "ori_index_for_gemeente",
    "parse_person",
    "person_to_polder_record",
    "search",
    "slugify_person",
]

ORI_ELASTIC_BASE = "https://api.openraadsinformatie.nl/v1/elastic"
HTTP_TIMEOUT = 60.0
PAGE_SIZE = 500
RATE_LIMIT_DELAY = 0.5  # seconden tussen requests (2 req/sec).
SOURCE_ID = "open_raadsinformatie"
USER_AGENT = "polder/0.0.1 (https://github.com/anneschuth/polder; anne.schuth@gmail.com)"

# Mapping van ORI-rolnaam (zoals in `Membership.role`) naar polder
# post-classificatie. Rollen die geen gemeentelijk mandaat representeren
# (Member, Voorzitter zonder context, Gastspreker, ...) worden geskipt.
#
# Griffier vs Raadsgriffier: een gemeente heeft één gemeentegriffier, maar
# ORI labelt de hele griffie (commissiegriffiers, raadsadviseurs,
# griffiemedewerkers) als `Griffier` zonder onderscheidend veld — geen
# sub-organisatie, geen functie-detail, geen startdatum. Een scan over 135
# gemeenten met ORI-data: `Griffier` is multi-seat bij 18 gemeenten
# (Utrecht 12, Berkelland 5), terwijl `Raadsgriffier` consistent precies de
# echte griffier markeert (23 van 24 gemeenten exact 1; de twee labels
# sluiten elkaar uit op één gemeente na). Daarom: `Raadsgriffier` naar de
# single-seat `griffier`-post, `Griffier` naar de multi-seat
# `griffiemedewerker`-post.
ROLE_TO_CLASSIFICATION: dict[str, str] = {
    "Raadslid": "raadslid",
    "Wethouder": "wethouder",
    "Burgemeester": "burgemeester",
    "Gemeentesecretaris": "gemeentesecretaris",
    "Raadsgriffier": "griffier",
    "Griffier": "griffiemedewerker",
}

# Classificaties met precies één zetel per organisatie (`post.seat_count == 1`).
# `griffiemedewerker` staat hier bewust niet in: de griffie heeft meerdere
# medewerkers.
_SINGLE_SEAT_CLASSIFICATIONS: frozenset[str] = frozenset(
    {"burgemeester", "gemeentesecretaris", "griffier"}
)


# ---------------------------------------------------------------------------
# Slug helpers
# ---------------------------------------------------------------------------


def _ascii_lower(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return decomposed.encode("ascii", "ignore").decode("ascii").lower()


_TUSSENVOEGSELS = frozenset(
    {
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
)


def _strip_tussenvoegsels(family: str) -> str:
    base = _ascii_lower(family or "")
    base = re.sub(r"[^a-z0-9\s-]+", " ", base)
    parts = [p for p in re.split(r"\s+", base) if p]
    family_parts = [p for p in parts if p not in _TUSSENVOEGSELS] or parts
    family_slug = "-".join(family_parts)
    return re.sub(r"-+", "-", family_slug).strip("-")


def _initials_slug(initials: str) -> str:
    if not initials:
        return ""
    cleaned = _ascii_lower(initials)
    return re.sub(r"[^a-z0-9]+", "", cleaned)


def slugify_person(family: str, initials: str, ori_id: str) -> str:
    """Bouw stabiele slug `<family>-<initials>-<ori_id>`.

    ORI heeft geen geboortedatum, dus we gebruiken de ORI-numerieke id als
    laatste segment. Dat is stabiel zolang ORI de id niet wijzigt en uniek
    binnen de polder-namespace (ORI-id's zijn 7-cijferig en globaal uniek).
    """
    family_slug = _strip_tussenvoegsels(family)
    init_slug = _initials_slug(initials)
    pieces = [p for p in (family_slug, init_slug, str(ori_id)) if p]
    return "-".join(pieces)


# ---------------------------------------------------------------------------
# ORI index resolution
# ---------------------------------------------------------------------------


def _normalize_gemeente_slug(value: str) -> str:
    """`gemeente-utrecht`, `org:gemeente-utrecht`, `utrecht` → `utrecht`."""
    if value.startswith("org:"):
        value = value.split(":", 1)[1]
    if value.startswith("gemeente-"):
        value = value[len("gemeente-") :]
    return value


def ori_index_for_gemeente(gemeente_slug: str) -> str:
    """Map polder-gemeente-slug naar ORI-index naam.

    ORI is inconsistent in zijn naamgeving: meeste samengestelde namen krijgen
    een underscore (`ori_den_haag_<datestamp>`, `ori_baarle_nassau_<datestamp>`)
    maar een aantal houden de hyphen vast (`ori_alphen-chaam_<datestamp>`,
    `ori_amsterdam_nieuw-west_<datestamp>`). Voor gemeenten met een hyphen in
    de slug genereren we daarom beide varianten als komma-separated index-lijst,
    zodat ES via wildcard alle reële indices matcht.
    """
    bare = _normalize_gemeente_slug(gemeente_slug)
    if "-" not in bare:
        return f"ori_{bare}*"
    underscore = bare.replace("-", "_")
    # Komma-separated lijst: ES probeert beide varianten.
    return f"ori_{bare}*,ori_{underscore}*"


# ---------------------------------------------------------------------------
# HTTP / search
# ---------------------------------------------------------------------------


def search(
    index: str,
    body: dict[str, Any],
    *,
    timeout: float = HTTP_TIMEOUT,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Voer een Elasticsearch _search uit op de ORI-API.

    Args:
        index: bijv. ``"ori_utrecht"`` of ``"ori_*"``.
        body: ES query DSL als dict.
        client: optionele httpx-client voor connection-reuse en rate limiting.
    """
    url = f"{ORI_ELASTIC_BASE}/{index}/_search"
    headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
    if client is not None:
        response = client.post(url, json=body, headers=headers, timeout=timeout)
    else:
        response = httpx.post(url, json=body, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _scan_all(
    index: str,
    query: dict[str, Any],
    *,
    page_size: int = PAGE_SIZE,
    client: httpx.Client | None = None,
    rate_limit_delay: float = RATE_LIMIT_DELAY,
) -> list[dict[str, Any]]:
    """Haal alle hits op via `from`/`size` paginatie tot `total`."""
    hits: list[dict[str, Any]] = []
    body = {"size": page_size, "from": 0, "query": query, "sort": ["_doc"]}
    while True:
        response = search(index, body, client=client)
        page = response.get("hits", {}).get("hits", [])
        if not page:
            break
        hits.extend(page)
        if len(page) < page_size:
            break
        body["from"] = body["from"] + page_size  # type: ignore[operator]
        # ES staat default max 10000 from+size toe.
        if body["from"] >= 10000:
            logger.warning("Bereikt max ES from=10000 voor index %s; stop met scrollen", index)
            break
        if rate_limit_delay:
            time.sleep(rate_limit_delay)
    return hits


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def _cache_path(gemeente_slug: str, today: str, cache_dir: Path) -> Path:
    bare = _normalize_gemeente_slug(gemeente_slug)
    return cache_dir / f"{bare}-{today}.json"


def _load_cache(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Cache lezen mislukt %s: %s", path, exc)
        return None


def _save_cache(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _today() -> str:
    return date.today().isoformat()


def _ori_url(ori_id: str) -> str:
    return f"https://id.openraadsinformatie.nl/{ori_id}"


def _normalize_given(given: str) -> tuple[str, str | None, str | None]:
    """Extracteer (given, initials_hint, tussenvoegsel) uit ORI-given-strings.

    ORI levert samengestelde given-strings die meerdere stukjes informatie
    bevatten. We splitsen in drie:

    - `'L.S. (Larissa)'` → `('Larissa', 'L.S.', None)`
    - `'P. (Paul)'` → `('Paul', 'P.', None)`
    - `'A.M. (Alies) van'` → `('Alies', 'A.M.', 'van')`
    - `'Paul'` → `('Paul', None, None)`
    - `'P.'` → `('P.', None, None)` (geen roepnaam, given blijft initialen)
    """
    if not given:
        return given, None, None
    raw = given.strip()
    if not raw:
        return raw, None, None

    m = re.search(r"\(([^)]+)\)", raw)
    if not m:
        return raw, None, None

    nickname = m.group(1).strip()
    if not nickname or len(nickname) <= 1:
        return raw, None, None

    prefix = raw[: m.start()].strip()
    suffix = raw[m.end() :].strip()

    initials_hint: str | None = None
    if prefix and re.fullmatch(r"(?:[A-Za-zÀ-ÿ]\.)+", prefix):
        letters = re.findall(r"[A-Za-zÀ-ÿ]", prefix)
        if letters:
            initials_hint = "".join(f"{ch.upper()}." for ch in letters)

    tussenvoegsel = suffix or None

    return nickname, initials_hint, tussenvoegsel


def _strip_family_from_name(name_string: str, family: str) -> str:
    """Geef het stuk van `name_string` terug dat NIET de family is.

    Voor 2024+ gecombineerde achternamen (zoals `family='Mulder de Vries'`)
    werkt simpele space-splits niet — we moeten de hele family-string uit
    name strippen. Dit retourneert wat overblijft, typisch given+tussenvoegsel
    + eventuele parens.

    Handelt ook de ORI comma-form `'Schilderman, Susanne'` af.
    """
    if not name_string or not family:
        return name_string
    raw = name_string.strip()

    if "," in raw:
        _fam_part, given_part = raw.split(",", 1)
        return given_part.strip()

    # Probeer family aan het eind weg te strippen. Hou rekening met
    # whitespace variaties en case.
    fam_match = re.search(rf"\b{re.escape(family)}\b\s*$", raw, re.IGNORECASE)
    if fam_match:
        return raw[: fam_match.start()].strip()

    # Family is niet aan het eind (ORI-comma-vorm met family-eerst zou hier
    # komen, of een fout). Probeer family overal in de string weg te halen.
    stripped = re.sub(rf"\b{re.escape(family)}\b", "", raw, flags=re.IGNORECASE).strip()
    if stripped:
        return stripped
    return raw


def _extract_tussenvoegsel(name_string: str, family: str) -> str | None:
    """Geef het tussenvoegsel terug dat tussen de roepnaam en `family` zit in `name_string`.

    ORI levert `name='A.M. (Alies) van Weperen'` en `family_name='Weperen'`. De
    "van" zit tussen "(Alies)" en "Weperen". Strategie: zoek de positie van
    `family` in `name_string` en kijk wat ervoor staat, voorbij de roepnaam.

    Returns None als geen plausibel tussenvoegsel gevonden.
    """
    if not name_string or not family:
        return None
    # Vind family in de string (case-insensitive, woord-grens).
    fam_match = re.search(rf"\b{re.escape(family)}\b", name_string, re.IGNORECASE)
    if not fam_match:
        return None
    before_family = name_string[: fam_match.start()].strip()
    if not before_family:
        return None

    # Strip optionele "(roepnaam)" en initialen-prefix.
    before_family = re.sub(r"\([^)]+\)", " ", before_family)
    before_family = re.sub(r"(?:[A-Za-zÀ-ÿ]\.)+", " ", before_family)  # initialen
    before_family = re.sub(r"\s+", " ", before_family).strip()

    # Strip eventuele resterende voornaam-tokens. Heuristiek: alle tokens die
    # KLEIN beginnen blijven over (tussenvoegsels), tokens die HOOFDLETTERS
    # bevatten zijn voornamen die toch nog tussen haakjes-eraan-gestript moeten
    # worden. Hou alleen kleine-letter-tokens en common multi-word-tussenvoegsels.
    tokens = before_family.split()
    if not tokens:
        return None
    # Pak tail van tokens die kleine-letter of apostrof beginnen.
    tail: list[str] = []
    for tok in reversed(tokens):
        if not tok:
            continue
        first = tok[0]
        # Tussenvoegsels beginnen met kleine letter ("van", "de"), of met
        # apostrof ("'t", "'s").
        if first.islower() or first in ("'", "‘"):  # noqa: RUF001
            tail.insert(0, tok)
        else:
            break
    if not tail:
        return None
    return " ".join(tail)


def _split_name(raw_name: str) -> tuple[str, str]:
    """Splits ORI `name` veld in (family, given).

    ORI levert namen meestal als `Schilderman, Susanne` (achternaam, voornaam),
    maar soms als `Susanne Schilderman` of zelfs vol met initialen
    (`G.C. (Gerrit) Weerheim`). We pakken de comma-vorm als die er is.
    Roepnaam-normalisatie + initials-hint gebeurt in `parse_person`.
    """
    raw = (raw_name or "").strip()
    if "," in raw:
        family, given = raw.split(",", 1)
        return family.strip(), given.strip()
    # `G.C. (Gerrit) Achternaam` — laatste woord = achternaam.
    parts = raw.split()
    if not parts:
        return "", ""
    family = parts[-1]
    given = " ".join(parts[:-1]).strip()
    return family, given


_EMAIL_ROLE_PREFIXES = (
    "raadslid.",
    "wethouder.",
    "burgemeester.",
    "gemeentesecretaris.",
    "griffier.",
    "fractievoorzitter.",
)


def _name_from_email(email: str | None, family: str) -> tuple[str | None, str | None]:
    """Extracteer (given, family_hint) uit een functionele raads-email.

    ORI levert vaak `email='raadslid.gerrion.vanelmpt@roerdalen.nl'`. Het
    local-part bevat `<rol>.<voornaam>.<familienaam>` of `<voornaam>.<familienaam>`.
    Voor records waar `family_name` ontbreekt of de `name` 1 woord is, geeft
    deze helper een goede gok voor de voornaam.

    Retourneert (given, family_hint). family_hint is alleen niet-None als de
    email een family-naam bevat die NIET overeenkomt met de aangeleverde
    family (signaleert dat onze family-extractie er mogelijk naast zit).
    """
    if not email or "@" not in email:
        return None, None
    local = email.split("@", 1)[0].lower()
    for prefix in _EMAIL_ROLE_PREFIXES:
        if local.startswith(prefix):
            local = local[len(prefix) :]
            break
    if "." not in local:
        return None, None
    parts = [p for p in local.split(".") if p]
    if len(parts) < 2:
        return None, None
    # Heuristiek: laatste segment is family (na dropoff role-prefix), eerste is given.
    # Voor 'vanderlinden' of 'van-der-linden' patronen: blijft 1 woord.
    given_guess = parts[0].capitalize()
    family_guess = parts[-1]
    # Strip dubbele family-deeltjes ("vanderlinden" → "Linden" als family al die vorm heeft)
    # Skip: verzin geen complexe normalisatie. We retourneren alleen given.
    family_norm = family.lower().replace(" ", "").replace("-", "")
    if family_norm and family_norm not in family_guess.lower():
        # Mismatch: family in email is anders dan onze family. Niet vertrouwen op
        # given-guess want dit kan een geheel andere persoon zijn.
        return None, None
    return given_guess, None


def _initials_from_given(given: str) -> str:
    """`Susanne` → `S.`; `Marie-Antoinette` → `M.A.`; `(Gerrit)` → ''."""
    if not given:
        return ""
    cleaned = re.sub(r"\(.*?\)", " ", given)  # haal `(roepnaam)` weg
    cleaned = unicodedata.normalize("NFKD", cleaned).encode("ascii", "ignore").decode("ascii")
    # Pak eerste letter van elk woord/koppelteken-segment.
    tokens = re.split(r"[\s\-]+", cleaned)
    letters = [t[0].upper() for t in tokens if t and t[0].isalpha()]
    if not letters:
        return ""
    return "".join(f"{ch}." for ch in letters)


def parse_person(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Map ORI-Person source dict naar polder-personenrecord (zonder mandaten).

    Returnt ``None`` als de persoon onbruikbaar is (geen achternaam, geen id).
    """
    ori_id = str(raw.get("@id") or raw.get("id") or "").strip()
    if not ori_id:
        return None
    raw_name = raw.get("name") or ""
    family_explicit = (raw.get("family_name") or "").strip()

    # Als ORI een expliciete family_name levert (sorteer-key), gebruiken we die
    # autoritatief. Dat is cruciaal voor 2024+ gecombineerde achternamen zoals
    # 'Mulder de Vries' waar onze comma/space-split het niet kan raden.
    if family_explicit:
        family = family_explicit
        given_split = _strip_family_from_name(raw_name, family_explicit)
    else:
        family_split, given_split = _split_name(raw_name)
        family = family_split

    if not family:
        return None

    # Splits given in (roepnaam, initials_hint, tussenvoegsel-uit-parens).
    given, initials_hint, tussenvoegsel_from_given = _normalize_given(given_split)

    # Aanvullende detectie: ORI levert vaak `family_name='Weperen'` (zonder
    # tussenvoegsel) en `name='A.M. (Alies) van Weperen'` (met tussenvoegsel).
    # Diff de twee om "van" te vinden.
    tussenvoegsel_from_name = _extract_tussenvoegsel(raw_name, family)
    tussenvoegsel = tussenvoegsel_from_given or tussenvoegsel_from_name

    # Als de space-split van `name` het tussenvoegsel in `given` heeft gegooid
    # (bv. `name='Henk van der Linden'`, family_name='Linden' → given_split
    # was 'Henk van der'), strip dat tussenvoegsel-deel weg uit `given`.
    if given and tussenvoegsel and given.lower().endswith(tussenvoegsel.lower()):
        stripped = given[: -len(tussenvoegsel)].strip()
        if stripped:
            given = stripped

    # Fallback voor records waar ORI alleen family levert (zoals Haas-4580272):
    if not given:
        email = (raw.get("email") or "").strip()
        given_from_email, _ = _name_from_email(email, family)
        if given_from_email:
            given = given_from_email

    initials = initials_hint or _initials_from_given(given)

    slug = slugify_person(family, initials, ori_id)
    if not slug:
        return None

    # Bouw full-name: roepnaam + tussenvoegsel + family.
    parts_full = [p for p in (given, tussenvoegsel, family) if p]
    full = " ".join(parts_full) if parts_full else family
    name_block: dict[str, Any] = {
        "full": full,
        "family": family,
    }
    if tussenvoegsel:
        name_block["tussenvoegsel"] = tussenvoegsel
    if given:
        name_block["given"] = given
    if initials:
        name_block["initials"] = initials

    record: dict[str, Any] = {
        "id": f"person:{slug}",
        "identifiers": {},
        "name": name_block,
    }
    return record


def _ori_mandaat_id(membership_id: str, organization_id: str, post_id: str) -> str:
    """Deterministische mandaat-id voor één ORI-bezetting.

    Een ``uuid.uuid4()`` per run maakte elke nachtelijke rerun een nieuw
    open mandaat aan voor dezelfde persoon op dezelfde zetel (issue #64),
    ook in het pad waar ``_merge_mandaten`` niet wordt bereikt (de
    persoon-resolutie kiest tussen runs een andere slug door
    initialen-drift, zie #46/#47/#55).

    ORI levert per Membership een stabiele ``@id`` (zichtbaar in elke
    bestaande source-url, ``id.openraadsinformatie.nl/<membership_id>``).
    Die identificeert precies één persoon op één post en is stabiel tussen
    runs, dus daaruit afgeleid is de id idempotent én uniek per bezetter.
    Dat laatste is essentieel: één post zoals
    ``post:raadslid-gemeente-druten`` heeft tientallen gelijktijdige
    bezetters; een id puur uit ``(org, post)`` zou die allemaal op één id
    laten botsen.

    Fallback op ``(organization_id, post_id)`` alleen wanneer ORI geen
    membership-id levert (zeldzaam, en dan is meervoudige bezetting van
    diezelfde post sowieso niet te onderscheiden).
    """
    basis = membership_id.strip() or f"{organization_id}|{post_id}"
    digest = hashlib.sha1(f"ori|{basis}".encode()).hexdigest()
    return f"mandate-ori-{digest[:16]}"


def build_mandaat(
    *,
    raw_membership: dict[str, Any],
    gemeente_slug: str,
    today: str | None = None,
) -> dict[str, Any] | None:
    """Map een ORI Membership naar polder-mandaat.

    Skipt rollen die geen polder-classificatie hebben (zie
    ``ROLE_TO_CLASSIFICATION``).
    """
    role = (raw_membership.get("role") or "").strip()
    classification = ROLE_TO_CLASSIFICATION.get(role)
    if classification is None:
        return None
    today_str = today or _today()
    bare = _normalize_gemeente_slug(gemeente_slug)
    org_id = f"org:gemeente-{bare}"
    post_id = f"post:{classification}-gemeente-{bare}"
    membership_id = str(raw_membership.get("@id") or raw_membership.get("id") or "")
    role_label = f"{role} gemeente {bare.replace('-', ' ').title()}"
    source_url = (
        _ori_url(membership_id) if membership_id else f"{ORI_ELASTIC_BASE}/ori_{bare}/_search"
    )
    return {
        "id": _ori_mandaat_id(membership_id, org_id, post_id),
        "organization_id": org_id,
        "post_id": post_id,
        "role": role_label,
        # ORI Membership heeft geen start_date in elastic. Gebruik vandaag als
        # ondergrens; downstream-fetchers (Allmanak, Kiesraad) kunnen dit
        # verbeteren via merge.
        "start_date": today_str,
        "end_date": None,
        "sources": [
            {
                "id": SOURCE_ID,
                "url": source_url,
                "retrieved": today_str,
            }
        ],
    }


def person_to_polder_record(
    person_raw: dict[str, Any],
    memberships_raw: list[dict[str, Any]],
    *,
    gemeente_slug: str,
    today: str | None = None,
) -> dict[str, Any] | None:
    """Combineer Person + Memberships tot een polder-record."""
    record = parse_person(person_raw)
    if record is None:
        return None
    today_str = today or _today()
    ori_id = str(person_raw.get("@id") or person_raw.get("id") or "")

    mandaten: list[dict[str, Any]] = []
    for ms in memberships_raw:
        mandaat = build_mandaat(raw_membership=ms, gemeente_slug=gemeente_slug, today=today_str)
        if mandaat is not None:
            mandaten.append(mandaat)
    if not mandaten:
        # Geen polder-relevant mandaat → laat persoon weg.
        return None
    record["mandaten"] = mandaten
    record["sources"] = [
        {
            "id": SOURCE_ID,
            "url": _ori_url(ori_id),
            "retrieved": today_str,
        }
    ]
    return record


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


def fetch_persons_for_gemeente(
    gemeente_slug: str,
    *,
    cache_dir: Path | None = None,
    today: str | None = None,
    client: httpx.Client | None = None,
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    """Haal Person + Membership records voor één gemeente op.

    Returnt een lijst van dicts met keys ``person`` en ``memberships`` (lijst).
    Caching: response wordt per gemeente per dag in
    ``_cache/ori/<gemeente>-<date>.json`` gezet.
    """
    today_str = today or _today()
    bare = _normalize_gemeente_slug(gemeente_slug)
    index = ori_index_for_gemeente(gemeente_slug)

    cache_path = _cache_path(bare, today_str, cache_dir) if cache_dir is not None else None
    cached = _load_cache(cache_path) if use_cache and cache_path else None
    if cached is not None:
        logger.info("Cache hit voor %s (%s)", bare, cache_path)
        return cached.get("results") or []

    persons = _scan_all(index, {"term": {"@type": "Person"}}, client=client)
    memberships = _scan_all(index, {"term": {"@type": "Membership"}}, client=client)

    # Index memberships op `member` (= person ORI-id).
    by_member: dict[str, list[dict[str, Any]]] = {}
    for hit in memberships:
        src = hit.get("_source") or {}
        member_id = str(src.get("member") or "")
        if not member_id:
            continue
        by_member.setdefault(member_id, []).append(src)

    results: list[dict[str, Any]] = []
    for hit in persons:
        src = hit.get("_source") or {}
        ori_id = str(src.get("@id") or hit.get("_id") or "")
        if not ori_id:
            continue
        if "@id" not in src:
            src = {**src, "@id": ori_id}
        results.append(
            {
                "person": src,
                "memberships": by_member.get(ori_id, []),
            }
        )

    if cache_path is not None:
        _save_cache(cache_path, {"gemeente": bare, "date": today_str, "results": results})

    return results


# ---------------------------------------------------------------------------
# Bootstrap posts
# ---------------------------------------------------------------------------


_POST_LABELS: dict[str, str] = {
    "raadslid": "Lid van de gemeenteraad",
    "wethouder": "Wethouder",
    "burgemeester": "Burgemeester",
    "gemeentesecretaris": "Gemeentesecretaris",
    "griffier": "Raadsgriffier",
    "griffiemedewerker": "Griffiemedewerker",
}


def ensure_org_and_posts(
    data_root: Path,
    gemeente_slug: str,
    *,
    today: str | None = None,
    dry_run: bool = False,
) -> list[Path]:
    """Schrijf post-records voor `<role>-gemeente-<slug>` als ze nog niet
    bestaan. De org-yaml zelf wordt aangemaakt door de ROO-fetcher; we doen
    hier alleen posts.
    """
    today_str = today or _today()
    bare = _normalize_gemeente_slug(gemeente_slug)
    org_id = f"org:gemeente-{bare}"
    posts_dir = data_root / "posten" / "gemeenten" / bare
    written: list[Path] = []

    for classification, label in _POST_LABELS.items():
        post_id = f"post:{classification}-gemeente-{bare}"
        path = posts_dir / f"{classification}.yaml"
        if path.exists():
            continue
        record = {
            "id": post_id,
            "organization_id": org_id,
            "label": f"{label} {bare.replace('-', ' ').title()}",
            "classification": classification,
            "seat_count": 1 if classification in _SINGLE_SEAT_CLASSIFICATIONS else None,
            "valid_from": "1900-01-01",
            "valid_until": None,
        }
        if dry_run:
            logger.info("DRY-RUN zou post schrijven: %s", path)
            written.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(record, fh, sort_keys=False, allow_unicode=True)
        written.append(path)
    # `today_str` wordt nu niet in posts geschreven (post.schema kent geen
    # sources), maar laat hem als parameter staan voor symmetrie met andere
    # ensure_*-functies.
    _ = today_str
    return written


# ---------------------------------------------------------------------------
# Write / merge persons
# ---------------------------------------------------------------------------


def _has_active_mandaat(record: dict[str, Any]) -> bool:
    for mandaat in record.get("mandaten") or []:
        if mandaat.get("end_date") is None:
            return True
    return False


def _target_path(out_dir: Path, record: dict[str, Any]) -> Path:
    slug = record["id"].split(":", 1)[1]
    return out_dir / f"{slug}.yaml"


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


def _mandaat_key(mandaat: dict[str, Any]) -> tuple[str, str]:
    """Dedup-sleutel `(post_id, start_date)`.

    Een nachtelijke rerun mag geen tweede open mandaat aanmaken voor
    dezelfde zetel. Dat werkt alleen als ``start_date`` stabiel is tussen
    runs — zie ``person_to_polder_record``, dat geen synthetische
    fetch-datum meer in ``start_date`` zet. Twee echte raadstermijnen op
    dezelfde post hebben verschillende (door downstream-bronnen ingevulde)
    start_dates en blijven zo gescheiden (het Bos-Coenraad-geval).
    """
    return (mandaat.get("post_id", ""), mandaat.get("start_date", ""))


def _earliest(*dates: str | None) -> str:
    present = [d for d in dates if d]
    return min(present) if present else ""


def _is_synthetic_ori_date(mandaat: dict[str, Any]) -> bool:
    """True als start_date een ORI-fetch-stempel is, geen echte datum.

    ``build_mandaat`` zet zowel ``start_date`` als de ORI-source
    ``retrieved`` op dezelfde fetch-dag. Een downstream-bron die een echte
    aanvangsdatum invult laat die gelijkheid los. Alleen synthetische
    mandaten mogen bij rerun naar een bestaand open mandaat snappen; twee
    echte raadstermijnen op dezelfde post (Bos-Coenraad) niet.
    """
    sd = mandaat.get("start_date")
    if not sd:
        return False
    for src in mandaat.get("sources") or []:
        if (src or {}).get("id") == SOURCE_ID and src.get("retrieved") == sd:
            return True
    return False


def _open_post_index(
    by_key: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, tuple[str, str]]:
    """post_id -> key, alleen voor posten met precies één open mandaat."""
    seen: dict[str, list[tuple[str, str]]] = {}
    for k, m in by_key.items():
        if m.get("end_date") is None:
            seen.setdefault(m.get("post_id", ""), []).append(k)
    return {post: keys[0] for post, keys in seen.items() if len(keys) == 1}


def _merge_mandaten(
    existing: list[dict[str, Any]] | None, new: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for mandaat in existing or []:
        if not isinstance(mandaat, dict):
            continue
        by_key[_mandaat_key(mandaat)] = dict(mandaat)
    for mandaat in new or []:
        key = _mandaat_key(mandaat)
        # ORI heeft geen echte start_date. Een nieuw open mandaat op een
        # post die al precies één open mandaat heeft is dezelfde zetel,
        # her-opgehaald: snap naar de bestaande key zodat de rerun
        # idempotent is i.p.v. een tweede open mandaat aan te maken.
        # Posten met al >1 open mandaat (echte meervoudige zetels) raken
        # we niet aan.
        if (
            key not in by_key
            and mandaat.get("end_date") is None
            and _is_synthetic_ori_date(mandaat)
        ):
            open_idx = _open_post_index(by_key)
            existing_key = open_idx.get(mandaat.get("post_id", ""))
            if existing_key is not None:
                key = existing_key
        if key in by_key:
            prev = by_key[key]
            merged = dict(prev)
            # Behoud bestaand id (uuid).
            kept_id = prev.get("id")
            merged.update(mandaat)
            if kept_id:
                merged["id"] = kept_id
            # Synthetische ORI-start_date mag niet vooruit kruipen bij rerun.
            merged["start_date"] = _earliest(prev.get("start_date"), mandaat.get("start_date"))
            merged["sources"] = _merge_sources(prev.get("sources"), mandaat.get("sources"))
            by_key[key] = merged
        else:
            by_key[key] = dict(mandaat)
    return sorted(by_key.values(), key=lambda m: m.get("start_date", ""))


def merge_person(existing: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Idempotente merge: bestaande velden behouden, ORI-velden aanvullen."""
    if not existing:
        return dict(new)
    merged: dict[str, Any] = dict(existing)
    for key, value in new.items():
        if key == "identifiers":
            ids = dict(merged.get("identifiers") or {})
            for k, v in (value or {}).items():
                if v is not None and v != "":
                    ids[k] = v
                elif k not in ids:
                    ids[k] = v
            merged["identifiers"] = ids
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
    for k in order:
        if k in record:
            out[k] = record[k]
    for k, v in record.items():
        if k not in out:
            out[k] = v
    return out


def _dedup_key(record: dict[str, Any], organization_id: str | None) -> tuple[str, str, str] | None:
    """Bouw dedup-sleutel `(family, given-lower, organization_id)`.

    Twee records met dezelfde sleutel zijn waarschijnlijk dezelfde persoon
    (zelfde familienaam + voornaam in dezelfde gemeente).

    Returnt None als één van de drie velden ontbreekt; dat record is niet
    deduplicate-baar.
    """
    name = record.get("name") or {}
    family = (name.get("family") or "").strip().lower()
    given = (name.get("given") or "").strip().lower()
    if not family or not given or not organization_id:
        return None
    return (family, given, organization_id)


def dedup_records_for_gemeente(
    records: list[dict[str, Any]],
    organization_id: str,
) -> list[dict[str, Any]]:
    """Merge records met dezelfde (family, given) binnen één gemeente.

    Voorkomt het Bos-Coenraad-patroon (3 ORI-IDs voor dezelfde persoon).
    Behoudt de slug van het record met de meeste mandaten / langste initialen
    (zie `_choose_winner`). Loser-IDs gaan in identifiers.aliases.
    """
    if not records:
        return []
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    untouched: list[dict[str, Any]] = []
    for rec in records:
        key = _dedup_key(rec, organization_id)
        if key is None:
            untouched.append(rec)
            continue
        groups.setdefault(key, []).append(rec)

    result: list[dict[str, Any]] = list(untouched)
    for group in groups.values():
        if len(group) == 1:
            result.append(group[0])
            continue
        group.sort(key=_record_score, reverse=True)
        winner = group[0]
        for loser in group[1:]:
            winner = merge_person(winner, loser)
        result.append(winner)
    return result


def _record_score(rec: dict[str, Any]) -> tuple[int, int, int, int]:
    """Hoger = beter. Voor dedup-winner-selectie."""
    name = rec.get("name") or {}
    return (
        1 if (rec.get("birth") or {}).get("year") else 0,
        len(str(name.get("initials") or "")),
        len(rec.get("mandaten") or []),
        len(rec.get("sources") or []),
    )


def write_person(
    record: dict[str, Any],
    out_dir: Path,
    *,
    dry_run: bool = False,
) -> Path:
    target = _target_path(out_dir, record)

    existing: dict[str, Any] = {}
    if target.exists():
        with target.open("r", encoding="utf-8") as fh:
            existing = yaml.safe_load(fh) or {}

    merged = merge_person(existing, record)
    merged = _ordered_for_dump(merged)
    # Strip lege `identifiers`.
    if not merged.get("identifiers"):
        merged.pop("identifiers", None)

    if dry_run:
        print(f"DRY-RUN zou schrijven: {target}", file=sys.stderr)
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(merged, fh, sort_keys=False, default_flow_style=False, allow_unicode=True)
    return target


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="polder-fetch-ori",
        description=(
            "Haal raadsleden, wethouders, burgemeesters en gemeentesecretarissen "
            "uit Open Raadsinformatie en schrijf polder-personenrecords."
        ),
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--gemeente",
        type=str,
        help="Gemeente-slug (`utrecht`, `gemeente-utrecht`, of `org:gemeente-utrecht`).",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Doe alle gemeenten in data/organisaties/gemeenten/ (350+ calls; traag).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max aantal personen per gemeente (testen).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data/personen"),
        help="Output-directory voor personen (default: data/personen).",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("data"),
        help="Root van data/ voor post-bootstrap (default: data).",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("_cache/ori"),
        help="Cache-directory (default: _cache/ori).",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Negeer en overschrijf cache.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Schrijf niets, log alleen.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
    )
    return parser


def _gemeente_slugs_from_data(data_root: Path) -> list[str]:
    """Actieve gemeenten uit data/.

    Opgeheven gemeenten (`valid_until` gezet of een `successor_id`) blijven
    als historisch record bestaan maar moeten geen ORI-fetch krijgen —
    anders worden huidige griffiers/secretarissen aan een niet-bestaande
    gemeente gekoppeld (bv. Geldrop i.p.v. Geldrop-Mierlo).
    """
    gem_dir = data_root / "organisaties" / "gemeenten"
    if not gem_dir.exists():
        return []
    active: list[str] = []
    for path in gem_dir.glob("*.yaml"):
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            logger.warning("kon gemeente-bestand niet lezen: %s", path)
            continue
        if doc.get("valid_until") or doc.get("successor_id"):
            continue
        active.append(path.stem)
    return sorted(active)


def _process_gemeente(
    gemeente_slug: str,
    *,
    out_dir: Path,
    data_root: Path,
    cache_dir: Path,
    use_cache: bool,
    dry_run: bool,
    limit: int | None,
    today: str,
    client: httpx.Client | None,
) -> tuple[int, int]:
    bare = _normalize_gemeente_slug(gemeente_slug)
    logger.info("ORI-fetch voor gemeente %s", bare)
    ensure_org_and_posts(data_root, bare, today=today, dry_run=dry_run)

    raw = fetch_persons_for_gemeente(
        bare,
        cache_dir=cache_dir,
        today=today,
        client=client,
        use_cache=use_cache,
    )
    if limit is not None:
        raw = raw[:limit]

    organization_id = f"org:gemeente-{bare}" if not bare.startswith("gemeente-") else f"org:{bare}"
    records: list[dict[str, Any]] = []
    for entry in raw:
        record = person_to_polder_record(
            entry["person"], entry.get("memberships") or [], gemeente_slug=bare, today=today
        )
        if record is not None:
            records.append(record)

    # Dedup: meerdere ORI-IDs voor dezelfde persoon (zelfde family+given+org)
    # mergen tot één record. Voorkomt het Bos-Coenraad-patroon waar één
    # politicus 3 aparte records kreeg.
    before = len(records)
    records = dedup_records_for_gemeente(records, organization_id)
    if before != len(records):
        logger.info(
            "ORI dedup voor %s: %d -> %d records (%d duplicates gemerged)",
            bare,
            before,
            len(records),
            before - len(records),
        )

    n_current = 0
    n_historisch = 0
    for record in records:
        write_person(record, out_dir, dry_run=dry_run)
        if _has_active_mandaat(record):
            n_current += 1
        else:
            n_historisch += 1
    return n_current, n_historisch


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    today = _today()

    if args.all:
        gemeenten = _gemeente_slugs_from_data(args.data_root)
        if not gemeenten:
            print("Geen gemeenten gevonden in data/organisaties/gemeenten/", file=sys.stderr)
            return 1
    else:
        gemeenten = [args.gemeente]

    total_c = total_h = 0
    with httpx.Client(timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
        for slug in gemeenten:
            try:
                c, h = _process_gemeente(
                    slug,
                    out_dir=args.out,
                    data_root=args.data_root,
                    cache_dir=args.cache_dir,
                    use_cache=not args.no_cache,
                    dry_run=args.dry_run,
                    limit=args.limit,
                    today=today,
                    client=client,
                )
            except httpx.HTTPError as exc:
                logger.error("ORI-fetch faalde voor %s: %s", slug, exc)
                continue
            total_c += c
            total_h += h
            time.sleep(RATE_LIMIT_DELAY)

    print(
        f"Wrote {total_c} current + {total_h} historisch persoon-records to {args.out}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
