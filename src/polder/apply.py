"""Apply staging-proposals (na resolver) automatisch op `data/`.

Pure functies die een lijst resolved-proposals omzetten in concrete schrijf-acties.
Geen CLI-koppeling, geen LLM-calls. Aanroepers zijn `polder apply-staging` en de
tests onder `tests/test_apply_staging.py`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml

ActionType = Literal["create-org", "create-post", "create-person", "append-mandaat"]

# AVG-grenzen: rollen die niet in `data/` thuishoren conform docs/avg-grenzen.md
RED_AVG_KEYWORDS = (
    "beleidsmedewerker",
    "secretaresse",
    "stagiair",
    "junior medewerker",
    "ondersteuner",
)

# Mapping van rolwoord -> post-classification (schema-enum).
# Volgorde is belangrijk: meer specifieke termen eerst.
ROLE_TO_CLASSIFICATION: list[tuple[str, str]] = [
    ("plaatsvervangend secretaris-generaal", "abd-tmg"),
    ("secretaris-generaal", "abd-tmg"),
    ("directeur-generaal", "abd-tmg"),
    ("inspecteur-generaal", "abd-tmg"),
    ("directeur", "abd-directeur"),
    ("afdelingshoofd", "abd-afdelingshoofd"),
    ("projectleider", "abd-projectleider"),
    ("minister", "bewindspersoon"),
    ("staatssecretaris", "bewindspersoon"),
]

# Source-id mapping uit input-bestandsnaam.
SOURCE_ID_BY_PREFIX: list[tuple[str, str]] = [
    ("abd-nieuws", "abd_nieuws"),
    ("staatscourant", "staatscourant"),
    ("organogram", "organogram"),
]


@dataclass
class ApplyAction:
    """Een concrete schrijf-actie tegen `data/`."""

    type: ActionType
    target_path: Path
    record: dict[str, Any]
    source_proposal: dict[str, Any]
    confidence: float
    reasons: list[str] = field(default_factory=list)


@dataclass
class SkippedProposal:
    """Een proposal die niet auto-mergeable is, met reden."""

    proposal: dict[str, Any]
    reasons: list[str]


# ---------------------------------------------------------------------------
# Helpers: slug, source-id, classification, AVG-check
# ---------------------------------------------------------------------------


def _slugify(value: str) -> str:
    """Maak een slug volgens polder-conventie: lowercase, ascii, koppeltekens."""
    nfkd = unicodedata.normalize("NFKD", value)
    ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
    lowered = ascii_only.lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", lowered)
    return cleaned.strip("-")


def _normalize_id(raw_id: str | None, prefix: str) -> str | None:
    """Normaliseer een `org:`/`post:`/`person:` id naar lowercase ascii. Strip
    ongeldige characters. Faal-veilig: returns None als raw leeg is.
    """
    if not raw_id:
        return None
    s = raw_id.strip()
    if not s:
        return None
    if s.startswith(f"{prefix}:"):
        body = s[len(prefix) + 1 :]
    else:
        body = s
    body = _slugify(body)
    if not body:
        return None
    return f"{prefix}:{body}"


def _today_iso() -> str:
    return date.today().isoformat()


def _detect_source_id(proposal: dict[str, Any], fallback: str = "abd_nieuws") -> str:
    """Leid een polder source-id af uit URL-velden of staging-filename hint."""
    if proposal.get("abd_nieuws_url"):
        return "abd_nieuws"
    if proposal.get("staatscourant_url"):
        return "staatscourant"
    if proposal.get("organogram_pdf"):
        return "organogram"
    hint = proposal.get("_source_filename", "")
    for prefix, sid in SOURCE_ID_BY_PREFIX:
        if hint.startswith(prefix):
            return sid
    return fallback


def _detect_source_url(proposal: dict[str, Any]) -> str | None:
    return (
        proposal.get("abd_nieuws_url")
        or proposal.get("staatscourant_url")
        or proposal.get("organogram_pdf")
    )


def _classification_from_role(role: str) -> str | None:
    """Map een role-string naar een post-classification, of None bij geen match."""
    role_l = role.lower()
    for keyword, classification in ROLE_TO_CLASSIFICATION:
        if keyword in role_l:
            return classification
    return None


# ---------------------------------------------------------------------------
# Mandaat-deduplicatie en datum-validatie
# ---------------------------------------------------------------------------


# Reasonable bounds: oldest plausible Dutch civil-service date is ~1798
# (Bataafse Republiek). Future dates beyond five years from today are likely
# parsing errors rather than legitimate appointments.
_MIN_PLAUSIBLE_YEAR = 1798
_MAX_FUTURE_YEARS = 5


def _mandaat_key(m: dict[str, Any]) -> tuple[str, str, str, str, str]:
    """Canonical key voor mandaat-deduplicatie.

    Identical (post_id, organization_id, start_date, end_date, role) is treated
    as the same mandate regardless of source, decision_reference, or confidence.
    """
    return (
        str(m.get("post_id") or ""),
        str(m.get("organization_id") or ""),
        str(m.get("start_date") or ""),
        str(m.get("end_date") or ""),
        str(m.get("role") or ""),
    )


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    s = str(value)
    if "T" in s:
        s = s.split("T", 1)[0]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _normalize_date_string(value: str | None) -> str | None:
    """Strip een eventuele datetime-tail (T00:00:00) van een ISO-datum-string."""
    if not value:
        return value
    s = str(value)
    if "T" in s:
        return s.split("T", 1)[0]
    return s


def _dates_valid(start: str | None, end: str | None) -> bool:
    """True als start_date <= end_date wanneer beide gezet zijn.

    Falt-veilig bij parse-errors: returns True (validatie is geen schema-check).
    """
    sd = _parse_iso_date(start)
    ed = _parse_iso_date(end)
    if sd is None or ed is None:
        return True
    return sd <= ed


def _date_in_plausible_range(value: str | None) -> bool:
    """True als de datum binnen [1798, today+5y] valt of niet geparsed kan worden."""
    d = _parse_iso_date(value)
    if d is None:
        return True
    if d.year < _MIN_PLAUSIBLE_YEAR:
        return False
    today = date.today()
    max_year = today.year + _MAX_FUTURE_YEARS
    return d.year <= max_year


def _validate_mandaat_dates(
    start: str | None, end: str | None
) -> str | None:
    """Retourneer een Nederlandse skip-reden of None als datums OK zijn."""
    if not _dates_valid(start, end):
        return f"ongeldige datum-volgorde: start_date {start} na end_date {end}"
    for label, value in (("start_date", start), ("end_date", end)):
        if not _date_in_plausible_range(value):
            return f"datum buiten redelijk bereik: {label}={value}"
    return None


def _within_days(a: str | None, b: str | None, days: int) -> bool:
    """True als beide datums binnen `days` dagen liggen, of beide None."""
    if not a and not b:
        return True
    da = _parse_iso_date(a)
    db = _parse_iso_date(b)
    if da is None or db is None:
        return False
    return abs((da - db).days) <= days


def _fuzzy_duplicate_mandaat(
    existing: list[dict[str, Any]],
    *,
    post_id: str,
    organization_id: str,
    start_date: str | None,
    end_date: str | None,
) -> dict[str, Any] | None:
    """Vind een bestaand mandaat met zelfde post+org en datums binnen 7 dagen."""
    for m in existing:
        if m.get("post_id") != post_id:
            continue
        if m.get("organization_id") != organization_id:
            continue
        if not _within_days(m.get("start_date"), start_date, 7):
            continue
        if not _within_days(m.get("end_date"), end_date, 7):
            continue
        return m
    return None


def _is_red_avg(role: str) -> bool:
    role_l = role.lower()
    return any(k in role_l for k in RED_AVG_KEYWORDS)


def _person_slug(name_full: str, birth_year: int | None) -> str:
    """Genereer een persoon-slug volgens conventie `<family>-<initials>-<birthyear>`.

    Als geboortejaar ontbreekt, vervalt het achtervoegsel. Initialen op basis
    van eerste letters van given-names voor de familienaam.
    """
    import secrets

    # Strip parenthese-bijnaam ('A. (Abdeluheb) Choho' -> 'A. Choho').
    cleaned = re.sub(r"\([^)]*\)", "", name_full).strip()
    parts = [p for p in re.split(r"\s+", cleaned) if p]
    if not parts:
        return ""
    family = parts[-1]
    given_parts = parts[:-1]
    # Strip honorifics zoals 'drs.', 'dr.', 'mr.' uit de given-parts.
    given_parts = [p for p in given_parts if not p.lower().endswith(".")]
    # Bouw initialen uit eerste letter van elke given-part, maar alleen
    # alfabetische karakters meenemen (haakjes en cijfers wegfilteren).
    initials_chars = []
    for p in given_parts:
        if p and p[0].isalpha():
            initials_chars.append(p[0].lower())
    initials = "".join(initials_chars)
    family_slug = _slugify(family)
    if not family_slug:
        return ""
    pieces = [family_slug]
    if initials:
        pieces.append(initials)
    if birth_year is not None:
        pieces.append(str(birth_year))
    else:
        # Schema-eis: slug eindigt op 4-cijferig jaar, 7+-cijferig extern ID,
        # of 8-hex UUID-fallback. Zonder geboortejaar gebruiken we 8 random hex.
        pieces.append(secrets.token_hex(4))
    return "-".join(p for p in pieces if p)


def _name_record(name_full: str) -> dict[str, Any]:
    """Bouw een persoon.name dict uit een volledige naam-string."""
    cleaned = name_full.strip()
    # Knip leading honorifics (drs., dr., mr., ir., prof., etc.).
    cleaned = re.sub(r"^((drs?|mr|ir|prof|dr)\.\s*)+", "", cleaned, flags=re.I)
    # Knip parenthese-bijnaam tussen initialen ('N. (Niels) Kastelein').
    cleaned = re.sub(r"\([^)]+\)\s*", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    parts = [p for p in cleaned.split(" ") if p]
    if not parts:
        return {"full": name_full.strip(), "family": name_full.strip()}
    family = parts[-1]
    given = " ".join(parts[:-1])
    record: dict[str, Any] = {"full": name_full.strip(), "family": family}
    if given:
        record["given"] = given
    # Initialen alleen meegeven als ze schoon matchen op A. of A.B. patroon.
    initial_letters = [p[0].upper() for p in parts[:-1] if p and p[0].isalpha()]
    if initial_letters:
        candidate = "".join(f"{c}." for c in initial_letters)
        if re.fullmatch(r"([A-Z]\.)+", candidate):
            record["initials"] = candidate
    return record


# ---------------------------------------------------------------------------
# Existing-data lookup (geen Polder-lib import om circulars te vermijden)
# ---------------------------------------------------------------------------


def _iter_yaml(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(directory.rglob("*.yaml"))


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def _existing_org_ids(data_dir: Path) -> set[str]:
    ids: set[str] = set()
    for path in _iter_yaml(data_dir / "organisaties"):
        rec = _load_yaml(path)
        oid = rec.get("id")
        if isinstance(oid, str):
            ids.add(oid)
    return ids


def _existing_post_ids(data_dir: Path) -> set[str]:
    ids: set[str] = set()
    for path in _iter_yaml(data_dir / "posten"):
        rec = _load_yaml(path)
        pid = rec.get("id")
        if isinstance(pid, str):
            ids.add(pid)
    return ids


def _existing_personen(data_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    out: list[tuple[Path, dict[str, Any]]] = []
    for path in _iter_yaml(data_dir / "personen"):
        rec = _load_yaml(path)
        if rec.get("id"):
            out.append((path, rec))
    return out


def _person_family_match(person: dict[str, Any], family: str) -> bool:
    name = person.get("name", {})
    if not isinstance(name, dict):
        return False
    return _slugify(str(name.get("family", ""))) == _slugify(family)


# ---------------------------------------------------------------------------
# Plan-builder
# ---------------------------------------------------------------------------


def _dedupe_competing_proposals(
    proposals: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[SkippedProposal]]:
    """Houd per (post_id, person_name, start_date) alleen de hoogste-confidence.

    Multiple proposals naming the same persoon for the same post with the same
    start_date are treated as competing duplicates. Lower-confidence ones are
    skipped with a clear reason.
    """
    grouped: dict[tuple[str, str, str], list[tuple[int, dict[str, Any]]]] = {}
    for idx, p in enumerate(proposals):
        post_id = str(p.get("post_id") or "")
        name = str(p.get("person_name") or "").strip().lower()
        start = str(p.get("start_date") or "")
        if not post_id or not name:
            grouped.setdefault(("", str(idx), ""), []).append((idx, p))
            continue
        grouped.setdefault((post_id, name, start), []).append((idx, p))

    keep_indices: set[int] = set()
    skipped: list[SkippedProposal] = []
    for key, items in grouped.items():
        if len(items) == 1:
            keep_indices.add(items[0][0])
            continue
        # Multiple competing proposals: keep highest confidence.
        items_sorted = sorted(
            items, key=lambda t: float(t[1].get("confidence") or 0.0), reverse=True
        )
        winner_idx, winner = items_sorted[0]
        keep_indices.add(winner_idx)
        for _, loser in items_sorted[1:]:
            skipped.append(
                SkippedProposal(
                    proposal=loser,
                    reasons=[
                        "concurrerende proposal: hogere confidence "
                        f"{float(winner.get('confidence') or 0.0):.2f} wint voor "
                        f"post_id={key[0]} start_date={key[2]}"
                    ],
                )
            )
    kept = [p for i, p in enumerate(proposals) if i in keep_indices]
    return kept, skipped


def plan_apply(
    resolved_proposals: list[dict[str, Any]],
    data_dir: Path,
    *,
    only_high_confidence: bool = False,
    skip_persons: bool = False,
) -> tuple[list[ApplyAction], list[SkippedProposal]]:
    """Bouw een plan op basis van resolved proposals.

    Idempotent: bestaande org/post/person-IDs leiden niet tot duplicate-actions.
    Auto-merge regels conform de specificatie in de skill-beschrijving.
    """
    actions: list[ApplyAction] = []
    skipped: list[SkippedProposal] = []

    # Snapshots van bestaande data voor lookups.
    org_ids = _existing_org_ids(data_dir)
    post_ids = _existing_post_ids(data_dir)
    personen = _existing_personen(data_dir)

    # Pending-IDs binnen deze run zodat opvolgende proposals weten dat we al
    # iets zullen aanmaken.
    pending_org_ids: set[str] = set()
    pending_post_ids: set[str] = set()
    pending_person_ids: set[str] = set()

    confidence_floor = 0.95 if only_high_confidence else 0.85

    # Pre-pass: collapse proposals that target the same (post_id, person_name,
    # start_date) into the single highest-confidence one. This protects single-
    # seat posts from being filled by multiple competing proposals in one run.
    resolved_proposals, conflict_skips = _dedupe_competing_proposals(
        resolved_proposals
    )
    skipped.extend(conflict_skips)

    for raw in resolved_proposals:
        proposal = dict(raw)
        reasons: list[str] = []
        confidence = float(proposal.get("confidence", 0.0))

        if confidence < confidence_floor:
            skipped.append(
                SkippedProposal(
                    proposal=proposal,
                    reasons=[
                        f"confidence {confidence:.2f} < drempel {confidence_floor:.2f}"
                    ],
                )
            )
            continue

        # Honoreer `merge_recommendation` van de resolver als die gezet is.
        # De resolver heeft daarin al de inhoudelijke confidence-check gedaan
        # (org/post/person ieder ≥ 0.85, geen ambiguïteit); apply hoeft die
        # logica dus niet te dupliceren.
        rec = proposal.get("merge_recommendation")
        if rec is not None and rec != "auto-merge":
            skipped.append(
                SkippedProposal(
                    proposal=proposal,
                    reasons=[f"merge_recommendation={rec!r} (geen auto-merge)"],
                )
            )
            continue

        role = str(proposal.get("role", "")).strip()
        if not role:
            skipped.append(
                SkippedProposal(
                    proposal=proposal,
                    reasons=["geen role in proposal (verplicht voor mandaat)"],
                )
            )
            continue
        if _is_red_avg(role):
            skipped.append(
                SkippedProposal(
                    proposal=proposal,
                    reasons=["rood-AVG niveau (geen merge in data/)"],
                )
            )
            continue

        # Een fatsoenlijke bron-URL is verplicht; placeholders en lokale
        # cache-paden mogen nooit in een mandaat-source-url terechtkomen.
        source_url = _detect_source_url(proposal)
        if not source_url or not str(source_url).startswith(("http://", "https://")):
            skipped.append(
                SkippedProposal(
                    proposal=proposal,
                    reasons=["geen publieke bron-URL (http/https) in proposal"],
                )
            )
            continue

        # start_date verplicht: een mandaat zonder start-datum vervalt anders
        # naar `today`, wat een onzin-datum is voor een retro-actief mandaat.
        if not proposal.get("start_date"):
            skipped.append(
                SkippedProposal(
                    proposal=proposal,
                    reasons=["geen start_date in proposal"],
                )
            )
            continue

        # --- Stap 1: organisatie-chain doorlopen, missing afdelingen aanmaken ---
        chain = proposal.get("organization_chain") or proposal.get(
            "organization_chain_inferred", []
        )
        target_org_id = _normalize_id(
            proposal.get("organization_id") or proposal.get("resolved_organization_id"),
            "org",
        )

        chain_actions, chain_skip_reasons = _plan_chain(
            chain=chain,
            data_dir=data_dir,
            existing=org_ids | pending_org_ids,
            proposal=proposal,
            confidence=confidence,
        )
        if chain_skip_reasons:
            skipped.append(
                SkippedProposal(proposal=proposal, reasons=chain_skip_reasons)
            )
            continue
        for act in chain_actions:
            actions.append(act)
            pending_org_ids.add(act.record["id"])
            reasons.append(f"chain-org {act.record['id']}")

        # Verifieer dat target-org bestaat of zal worden aangemaakt.
        if target_org_id and target_org_id not in (
            org_ids | pending_org_ids
        ):
            skipped.append(
                SkippedProposal(
                    proposal=proposal,
                    reasons=[
                        f"organization_id {target_org_id} niet in data/ en niet via chain aangemaakt"
                    ],
                )
            )
            continue

        # --- Stap 2: post aanmaken indien nodig ---
        post_id = _normalize_id(proposal.get("post_id"), "post")
        if not post_id:
            skipped.append(
                SkippedProposal(proposal=proposal, reasons=["geen post_id in proposal"])
            )
            continue

        if post_id not in (post_ids | pending_post_ids):
            classification = _classification_from_role(role)
            if classification is None:
                skipped.append(
                    SkippedProposal(
                        proposal=proposal,
                        reasons=[
                            f"post-classification niet afleidbaar uit role '{role[:60]}...'"
                        ],
                    )
                )
                continue
            post_record = _build_post_record(
                post_id=post_id,
                organization_id=target_org_id or "",
                role=role,
                classification=classification,
                start_date=proposal.get("start_date"),
            )
            post_path = data_dir / "posten" / f"{post_id.split(':', 1)[1]}.yaml"
            actions.append(
                ApplyAction(
                    type="create-post",
                    target_path=post_path,
                    record=post_record,
                    source_proposal=proposal,
                    confidence=confidence,
                    reasons=[f"post {post_id} ontbreekt in data/posten/"],
                )
            )
            pending_post_ids.add(post_id)
            reasons.append(f"post {post_id}")

        # --- Stap 3: persoon + mandaat ---
        if skip_persons:
            reasons.append("persons skipped (--skip-persons)")
            continue

        person_action_or_skip = _plan_person(
            proposal=proposal,
            data_dir=data_dir,
            personen=personen,
            target_org_id=target_org_id or "",
            post_id=post_id,
            confidence=confidence,
            pending_person_ids=pending_person_ids,
        )
        if isinstance(person_action_or_skip, SkippedProposal):
            skipped.append(person_action_or_skip)
            continue
        actions.append(person_action_or_skip)
        if person_action_or_skip.type == "create-person":
            pending_person_ids.add(person_action_or_skip.record["id"])

    return actions, skipped


def _plan_chain(
    *,
    chain: list[dict[str, Any]],
    data_dir: Path,
    existing: set[str],
    proposal: dict[str, Any],
    confidence: float,
) -> tuple[list[ApplyAction], list[str]]:
    """Bouw create-org acties voor elke chain-entry die nog niet bestaat.

    Retourneert (actions, skip_reasons). Bij skip_reasons is de proposal
    niet auto-mergeable.
    """
    actions: list[ApplyAction] = []
    if not chain:
        return actions, []

    # Volgorde top-down (ministerie -> directie -> afdeling). Voor elke nieuwe
    # entry moet de parent (vorige) bestaan of in dezelfde batch zijn.
    available = set(existing)
    parent_id: str | None = None
    for entry in chain:
        slug = _normalize_id(entry.get("slug_proposal"), "org")
        if not slug:
            return [], [f"chain-entry zonder slug_proposal: {entry}"]
        # Sync de slug terug zodat _build_org_record en parent-tracking
        # consistent zijn.
        entry["slug_proposal"] = slug
        if slug in available:
            parent_id = slug
            continue
        # Nieuwe org: parent moet bekend zijn.
        if parent_id is None and entry.get("level") != "ministerie":
            return [], [f"chain {slug}: parent ontbreekt"]
        record = _build_org_record(entry=entry, parent_id=parent_id, proposal=proposal)
        slug_body = slug.removeprefix("org:onderdeel-").removeprefix("org:")
        path = (
            data_dir
            / "organisaties"
            / "organisatieonderdelen"
            / f"{slug_body}.yaml"
        )
        actions.append(
            ApplyAction(
                type="create-org",
                target_path=path,
                record=record,
                source_proposal=proposal,
                confidence=confidence,
                reasons=[
                    f"chain-entry {entry.get('level')} {entry.get('name')}"
                ],
            )
        )
        available.add(slug)
        parent_id = slug
    return actions, []


def _build_org_record(
    *,
    entry: dict[str, Any],
    parent_id: str | None,
    proposal: dict[str, Any],
) -> dict[str, Any]:
    name = str(entry.get("name", "")).strip()
    today = _today_iso()
    source_url = _detect_source_url(proposal) or "https://example.invalid"
    source_id = _detect_source_id(proposal)
    record: dict[str, Any] = {
        "id": entry["slug_proposal"],
        "type": "organisatieonderdeel",
        "classification": "organisatieonderdeel",
        "parent_id": parent_id,
        "names": [{"value": name, "valid_from": today}],
        "valid_from": today,
        "valid_until": None,
        "sources": [
            {
                "id": source_id,
                "url": source_url,
                "retrieved": today,
                "fields": ["applied_via:apply-staging"],
            }
        ],
    }
    return record


def _build_post_record(
    *,
    post_id: str,
    organization_id: str,
    role: str,
    classification: str,
    start_date: str | None,
) -> dict[str, Any]:
    valid_from = _normalize_date_string(start_date) or _today_iso()
    record = {
        "id": post_id,
        "organization_id": organization_id,
        "label": role,
        "classification": classification,
        "valid_from": valid_from,
        "valid_until": None,
    }
    return record


def _plan_person(
    *,
    proposal: dict[str, Any],
    data_dir: Path,
    personen: list[tuple[Path, dict[str, Any]]],
    target_org_id: str,
    post_id: str,
    confidence: float,
    pending_person_ids: set[str],
) -> ApplyAction | SkippedProposal:
    resolved_id = proposal.get("resolved_person_id")
    name_full = str(proposal.get("person_name", "")).strip()
    if not name_full:
        return SkippedProposal(proposal=proposal, reasons=["geen person_name"])

    # Validate the mandate dates before doing any persoon-resolution. Invalid
    # dates poison the whole action: skip the proposal completely so we never
    # create a persoon with a broken mandate or mutate an existing persoon.
    date_skip = _validate_mandaat_dates(
        proposal.get("start_date"), proposal.get("end_date")
    )
    if date_skip is not None:
        return SkippedProposal(proposal=proposal, reasons=[date_skip])

    birth_year = _extract_birth_year(proposal)

    # Idempotency: als de slug die we zouden aanmaken al bestaat in data/personen/,
    # behandel dat als de bestaande persoon.
    if resolved_id is None:
        provisional_slug = _person_slug(name_full, birth_year)
        if provisional_slug:
            provisional_id = f"person:{provisional_slug}"
            if any(p[1].get("id") == provisional_id for p in personen):
                resolved_id = provisional_id

    # Bestaande persoon: append mandaat.
    if resolved_id:
        match = next((p for p in personen if p[1].get("id") == resolved_id), None)
        if match is None:
            return SkippedProposal(
                proposal=proposal,
                reasons=[f"resolved_person_id {resolved_id} niet gevonden"],
            )
        path, record = match
        new_record, fuzzy_warnings = _append_mandaat(
            record=record,
            organization_id=target_org_id,
            post_id=post_id,
            proposal=proposal,
        )
        if new_record is None:
            return SkippedProposal(
                proposal=proposal,
                reasons=["mandaat al aanwezig (idempotent)"],
            )
        reasons = [f"append mandaat aan {resolved_id}"]
        for w in fuzzy_warnings:
            reasons.append(f"waarschuwing: {w}")
        return ApplyAction(
            type="append-mandaat",
            target_path=path,
            record=new_record,
            source_proposal=proposal,
            confidence=confidence,
            reasons=reasons,
        )

    # Nieuwe persoon. Eis: confidence >= 0.85 (al gechecked) EN
    # geen kandidaat-conflict.
    family = _name_record(name_full).get("family", "")
    candidates = [p for _, p in personen if _person_family_match(p, family)]

    if birth_year is None and len(candidates) > 0:
        return SkippedProposal(
            proposal=proposal,
            reasons=[
                f"geen geboortejaar bekend en {len(candidates)} familienaam-kandidaat(en) in data/personen/"
            ],
        )

    slug_body = _person_slug(name_full, birth_year)
    if not slug_body:
        return SkippedProposal(
            proposal=proposal, reasons=["kan geen persoon-slug afleiden"]
        )
    person_id = f"person:{slug_body}"
    if person_id in pending_person_ids:
        return SkippedProposal(
            proposal=proposal,
            reasons=[f"person:{slug_body} al in deze run aangemaakt"],
        )

    record = _build_person_record(
        person_id=person_id,
        name_full=name_full,
        birth_year=birth_year,
        organization_id=target_org_id,
        post_id=post_id,
        proposal=proposal,
    )
    path = data_dir / "personen" / f"{slug_body}.yaml"
    return ApplyAction(
        type="create-person",
        target_path=path,
        record=record,
        source_proposal=proposal,
        confidence=confidence,
        reasons=[f"nieuwe persoon {person_id} met inline mandaat"],
    )


def _extract_birth_year(proposal: dict[str, Any]) -> int | None:
    birth = proposal.get("birth")
    if isinstance(birth, dict) and isinstance(birth.get("year"), int):
        return int(birth["year"])
    if isinstance(proposal.get("birth_year"), int):
        return int(proposal["birth_year"])
    return None


def _append_mandaat(
    *,
    record: dict[str, Any],
    organization_id: str,
    post_id: str,
    proposal: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Append een nieuw mandaat aan een persoon-record.

    Retourneert (new_record, warnings).
      * new_record is None bij idempotent skip (exact duplicate).
      * warnings bevat fuzzy-duplicate notes maar het mandaat wordt wel toegevoegd.
    """
    warnings: list[str] = []
    mandaten = list(record.get("mandaten") or [])
    candidate = _build_mandaat(
        organization_id=organization_id, post_id=post_id, proposal=proposal
    )
    new_key = _mandaat_key(candidate)
    for m in mandaten:
        if _mandaat_key(m) == new_key:
            return None, warnings
    fuzzy = _fuzzy_duplicate_mandaat(
        mandaten,
        post_id=post_id,
        organization_id=organization_id,
        start_date=candidate.get("start_date"),
        end_date=candidate.get("end_date"),
    )
    if fuzzy is not None:
        warnings.append(
            "fuzzy-duplicaat: bestaand mandaat "
            f"({fuzzy.get('start_date')}..{fuzzy.get('end_date')}) "
            f"binnen 7 dagen van nieuw ({candidate.get('start_date')}..{candidate.get('end_date')})"
        )
    new_record = dict(record)
    new_record["mandaten"] = [*mandaten, candidate]
    return new_record, warnings


def _build_mandaat(
    *, organization_id: str, post_id: str, proposal: dict[str, Any]
) -> dict[str, Any]:
    today = _today_iso()
    source_id = _detect_source_id(proposal)
    source_url = _detect_source_url(proposal) or "https://example.invalid"
    start_date = _normalize_date_string(proposal.get("start_date")) or today
    end_date = _normalize_date_string(proposal.get("end_date"))
    mandaat = {
        "id": f"mandate-{_slugify(post_id)}-{start_date}",
        "organization_id": organization_id,
        "post_id": post_id,
        "role": proposal.get("role", ""),
        "start_date": start_date,
        "end_date": end_date,
        "sources": [
            {
                "id": source_id,
                "url": source_url,
                "retrieved": today,
                "fields": ["applied_via:apply-staging"],
            }
        ],
    }
    if proposal.get("decision_reference") or proposal.get("staatscourant_url"):
        appointment: dict[str, Any] = {}
        if proposal.get("decision_reference"):
            appointment["decision"] = proposal["decision_reference"]
        if proposal.get("staatscourant_url"):
            appointment["staatscourant_url"] = proposal["staatscourant_url"]
        mandaat["appointment"] = appointment
    if proposal.get("confidence") is not None:
        mandaat["confidence"] = float(proposal["confidence"])
    return mandaat


def _build_person_record(
    *,
    person_id: str,
    name_full: str,
    birth_year: int | None,
    organization_id: str,
    post_id: str,
    proposal: dict[str, Any],
) -> dict[str, Any]:
    today = _today_iso()
    source_id = _detect_source_id(proposal)
    source_url = _detect_source_url(proposal) or "https://example.invalid"
    record: dict[str, Any] = {
        "id": person_id,
        "name": _name_record(name_full),
    }
    if birth_year is not None:
        record["birth"] = {"year": birth_year}
    record["mandaten"] = [
        _build_mandaat(
            organization_id=organization_id, post_id=post_id, proposal=proposal
        )
    ]
    record["sources"] = [
        {
            "id": source_id,
            "url": source_url,
            "retrieved": today,
            "fields": ["applied_via:apply-staging"],
        }
    ]
    return record


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def execute_apply(actions: list[ApplyAction], data_dir: Path) -> int:
    """Schrijf alle acties weg. Retourneert het aantal aangepaste files."""
    written = 0
    for action in actions:
        action.target_path.parent.mkdir(parents=True, exist_ok=True)
        if action.type == "append-mandaat":
            # `record` is de volledige nieuwe persoon-yaml (oud + extra mandaat).
            payload = action.record
        else:
            payload = action.record
        with action.target_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(payload, f, sort_keys=False, allow_unicode=True)
        written += 1
    return written


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_resolved_input(input_path: Path) -> list[dict[str, Any]]:
    """Lees een `.resolved.json` file of map met meerdere `.resolved.json` files.

    Per record wordt `_source_filename` toegevoegd zodat downstream-detectie
    de bron kan afleiden uit het pad.
    """
    import json

    items: list[dict[str, Any]] = []
    paths: list[Path]
    if input_path.is_dir():
        paths = sorted(input_path.glob("*.resolved.json"))
    else:
        paths = [input_path]
    import logging

    log = logging.getLogger("polder.apply")
    for p in paths:
        try:
            if p.stat().st_size == 0:
                log.warning("skip lege resolved-file: %s", p)
                continue
            with p.open(encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as exc:
            log.warning("skip corrupte JSON %s: %s", p, exc)
            continue
        except OSError as exc:
            log.warning("kan %s niet lezen: %s", p, exc)
            continue
        if not isinstance(data, list):
            continue
        for entry in data:
            if not isinstance(entry, dict):
                continue
            entry.setdefault("_source_filename", p.name)
            items.append(entry)
    return items
