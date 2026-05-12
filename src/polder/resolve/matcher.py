"""Deterministische persoon-matcher voor staging-proposals.

Bouwt een in-memory index over `data/personen/` en zoekt per proposal-name
de unieke persoon-record-id. Geen LLM. Geen netwerk.

Strategieen, in volgorde van strikt → laks. Stopt bij eerste unieke match.
Faalt naar geen-match als 0 of meerdere kandidaten op het strikste niveau.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from polder.lib.initials import compact_initials
from polder.resolve.names import ParsedName, parse_person_name

logger = logging.getLogger("polder.resolve.matcher")


_ORG_NAME_DROP = (
    "ministerie van",
    "ministerie-van",
    "ministerie",
)


def _slugify_org(raw: str | None) -> str | None:
    """Slugify een org-naam naar `org:<slug>` zonder prefix-stripping."""
    if not raw:
        return None
    s = unicodedata.normalize("NFKD", str(raw)).encode("ascii", "ignore").decode("ascii")
    s = s.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if not slug:
        return None
    return f"org:{slug}"


def _org_alias_slug(raw: str | None) -> str | None:
    """Slugify een org-naam, met `ministerie van` als prefix gestript.

    Voor alias-lookup: zowel "Financiën" als "Ministerie van Financiën"
    landen op `org:financien`.
    """
    if not raw:
        return None
    s = str(raw).strip().lower()
    for prefix in _ORG_NAME_DROP:
        if s.startswith(prefix + " "):
            s = s[len(prefix) + 1 :].strip()
            break
    return _slugify_org(s)


def _norm_family(value: str | None) -> str:
    """Lowercase ASCII zonder tussenvoegsels.

    Tussenvoegsels detecteren we via de heuristiek "begint met kleine letter
    of apostrof". `'van Marum'` → `'marum'`. `'op de Beek'` → `'beek'`.
    Geen vaste lijst, robuust voor alle Nederlandse tussenvoegsel-vormen.
    """
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    tokens = [t for t in ascii_only.split() if t]
    # Strip kleine-letter-tokens vooraan (tussenvoegsels)
    while tokens and (tokens[0][0].islower() or tokens[0][0] in ("'", "`")):
        tokens.pop(0)
    if not tokens:
        return ""
    return " ".join(tokens).lower()


def _norm_given(value: str | None) -> str:
    return _norm_family(value)


@dataclass
class PolderIndex:
    """In-memory index voor matching: personen + organisaties + posten.

    Niet hetzelfde als `Polder.local()` — die laadt door Pydantic (3s, slow).
    Deze pakt yaml.safe_load + minimaal-veld-extract = ~200ms voor alle drie
    entity-types samen.
    """

    # Alle persoon-records, key = id
    persons_by_id: dict[str, dict] = field(default_factory=dict)
    # (family, initials_compact, birth_year) -> [person_id, ...]
    by_family_initials_year: dict[tuple[str, str, int], list[str]] = field(default_factory=dict)
    # (family, given_compact) -> [person_id, ...] (compact zonder accenten)
    by_family_given: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    # family -> [person_id, ...]
    by_family: dict[str, list[str]] = field(default_factory=dict)

    # Org/Post IDs voor existence-checks (sets, want we hoeven de records
    # zelf niet vast te houden voor resolve-doel).
    org_ids: set[str] = field(default_factory=set)
    post_ids: set[str] = field(default_factory=set)
    # post_id -> organization_id (om post/org-consistency te checken)
    post_to_org: dict[str, str] = field(default_factory=dict)
    # alias-slug -> canonical org_id. Vult vanuit names[*].value en abbr,
    # zodat bv. `org:ministerie-van-financien` matched op `org:min-fin`.
    org_by_alias: dict[str, str] = field(default_factory=dict)
    # org_id -> parent_id, voor hiërarchie-validatie tijdens chain-resolve.
    org_parent: dict[str, str | None] = field(default_factory=dict)

    @classmethod
    def load(cls, data_dir: Path) -> "PolderIndex":
        idx = cls()
        persons_dir = data_dir / "personen"
        orgs_dir = data_dir / "organisaties"
        posts_dir = data_dir / "posten"

        if persons_dir.exists():
            for path in persons_dir.glob("*.yaml"):
                try:
                    d = yaml.safe_load(path.read_text(encoding="utf-8"))
                except yaml.YAMLError:
                    continue
                if not isinstance(d, dict) or not d.get("id"):
                    continue
                pid = d["id"]
                idx.persons_by_id[pid] = d
                name = d.get("name") or {}
                family = _norm_family(name.get("family"))
                given = _norm_given(name.get("given"))
                initials = compact_initials(name.get("initials"))
                year = (d.get("birth") or {}).get("year")
                if family:
                    idx.by_family.setdefault(family, []).append(pid)
                    if initials and isinstance(year, int):
                        idx.by_family_initials_year.setdefault(
                            (family, initials, year), []
                        ).append(pid)
                    if given:
                        idx.by_family_given.setdefault((family, given), []).append(pid)

        if orgs_dir.exists():
            for path in orgs_dir.rglob("*.yaml"):
                try:
                    d = yaml.safe_load(path.read_text(encoding="utf-8"))
                except yaml.YAMLError:
                    continue
                if not isinstance(d, dict) or not d.get("id"):
                    continue
                oid = d["id"]
                idx.org_ids.add(oid)
                idx.org_parent[oid] = d.get("parent_id")
                # Bouw alias-keys uit names[*].value en names[*].abbr. Voor
                # ministeries voegen we óók de prefix-vorm toe zodat
                # `org:ministerie-van-financien` -> `org:min-fin`.
                is_ministerie = d.get("type") == "ministerie"
                for n in d.get("names") or []:
                    if not isinstance(n, dict):
                        continue
                    for raw in (n.get("value"), n.get("abbr")):
                        if not raw:
                            continue
                        # Twee vormen registreren: de korte ("financien")
                        # en, bij ministeries, ook de verbose ("ministerie-van-financien"
                        # en "min-financien"). Daardoor matchen we beide
                        # spelling-varianten op de canonical id.
                        for candidate in (
                            _org_alias_slug(raw),
                            (
                                _slugify_org(f"ministerie van {raw}")
                                if is_ministerie
                                else None
                            ),
                            (
                                _slugify_org(f"min {raw}")
                                if is_ministerie
                                else None
                            ),
                        ):
                            if candidate and candidate != oid:
                                idx.org_by_alias.setdefault(candidate, oid)

        if posts_dir.exists():
            for path in posts_dir.rglob("*.yaml"):
                try:
                    d = yaml.safe_load(path.read_text(encoding="utf-8"))
                except yaml.YAMLError:
                    continue
                if isinstance(d, dict) and d.get("id"):
                    idx.post_ids.add(d["id"])
                    if d.get("organization_id"):
                        idx.post_to_org[d["id"]] = d["organization_id"]

        return idx


@dataclass(frozen=True)
class PersonMatch:
    """Resultaat van `match_person`."""

    person_id: str | None
    confidence: float
    method: str
    # IDs van alle kandidaten als er ambiguïteit is. Leeg bij unieke match.
    candidates: tuple[str, ...] = ()


def match_person(
    proposal_name: str,
    *,
    idx: PolderIndex,
    birth_year: int | None = None,
) -> PersonMatch:
    """Match een proposal-name aan een persoon-record-id.

    Volgorde van strategieen:
    1. (family, initials, birth_year) — confidence 0.98
    2. (family, given) — 0.92, met initials-disambiguation
    3. (family, initials) zonder year, alleen als uniek — 0.88
    4. family unique — 0.70
    """
    parsed = parse_person_name(proposal_name)
    if not parsed.family:
        return PersonMatch(None, 0.0, "no_family")

    family = parsed.family  # al lowercase ASCII via parse_person_name

    # 1. family + initials + birth_year
    if parsed.initials and birth_year is not None:
        key = (family, parsed.initials, birth_year)
        ids = idx.by_family_initials_year.get(key, [])
        if len(ids) == 1:
            return PersonMatch(ids[0], 0.98, "family_initials_year")
        if len(ids) > 1:
            return PersonMatch(None, 0.0, "ambiguous_family_initials_year", tuple(ids))

    # 2. family + given (exact compact-match)
    if parsed.given:
        # `parsed.given` kan multi-word zijn ("erik jan"), zoek elke vorm.
        candidate_givens = [parsed.given]
        # Sommige proposals leveren "erik jan" terwijl polder "erik" heeft.
        # Probeer ook elk individueel given-token.
        for tok in parsed.given.split():
            if tok != parsed.given and tok not in candidate_givens:
                candidate_givens.append(tok)

        all_hits: list[str] = []
        for g in candidate_givens:
            hits = idx.by_family_given.get((family, g), [])
            for pid in hits:
                if pid not in all_hits:
                    all_hits.append(pid)
        if len(all_hits) == 1:
            return PersonMatch(all_hits[0], 0.92, "family_given")
        if len(all_hits) > 1 and parsed.initials:
            # Disambigueren via initials-prefix-match
            refined = []
            for pid in all_hits:
                rec_initials = compact_initials(
                    (idx.persons_by_id.get(pid, {}).get("name") or {}).get("initials")
                )
                if rec_initials and (
                    rec_initials.startswith(parsed.initials)
                    or parsed.initials.startswith(rec_initials)
                ):
                    refined.append(pid)
            if len(refined) == 1:
                return PersonMatch(refined[0], 0.93, "family_given_initials_refined")
        if len(all_hits) > 1:
            return PersonMatch(None, 0.0, "ambiguous_family_given", tuple(all_hits))

    # 3. family + initials, geen year, alleen als uniek
    if parsed.initials:
        all_ids = idx.by_family.get(family, [])
        matching = []
        for pid in all_ids:
            rec_initials = compact_initials(
                (idx.persons_by_id.get(pid, {}).get("name") or {}).get("initials")
            )
            if rec_initials == parsed.initials:
                matching.append(pid)
        if len(matching) == 1:
            return PersonMatch(matching[0], 0.88, "family_initials_no_year")
        if len(matching) > 1:
            return PersonMatch(None, 0.0, "ambiguous_family_initials", tuple(matching))

    # 4. Family alleen, alleen als uniek
    family_ids = idx.by_family.get(family, [])
    if len(family_ids) == 1:
        return PersonMatch(family_ids[0], 0.70, "family_unique")
    if len(family_ids) > 1:
        return PersonMatch(None, 0.0, "ambiguous_family", tuple(family_ids))

    # 5. Family niet in data: nieuwe persoon aanmaakbaar, mits birth_year
    # bekend is. Zonder birth_year kan apply geen deterministische slug
    # vormen volgens de polder-conventie `<family>-<initials>-<jaartal>`.
    # De resolver-laag verrijkt zo nodig vooraf via Wikidata.
    if birth_year is not None:
        return PersonMatch(None, 0.85, "creatable_new_person")

    return PersonMatch(None, 0.0, "no_match")
