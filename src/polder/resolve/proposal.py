"""End-to-end resolve: één staging-proposal → resolved-proposal.

Combineert persoon-match (via `polder.resolve.matcher`) met organization- en
post-resolutie. Output-format is identiek aan wat de oude
`resolve-staging-proposals`-skill produceerde, zodat `polder apply-staging`
ongewijzigd werkt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from polder.resolve.matcher import PersonMatch, PolderIndex, match_person


# Een enricher krijgt een (proposal_name, existing_birth_hint) en retourneert
# een birth_year als hij er een kan vinden, of None. Default is geen enricher.
PersonEnricher = Callable[[str, int | None], int | None]


@dataclass(frozen=True)
class OrgMatch:
    organization_id: str | None
    confidence: float
    method: str
    level: str | None = None


@dataclass(frozen=True)
class PostMatch:
    post_id: str | None
    confidence: float
    method: str


def _lookup_org(idx: PolderIndex, slug: str | None) -> str | None:
    """Resolve een ruwe org-slug: eerst exact, daarna via name-alias."""
    if not slug:
        return None
    if slug in idx.org_ids:
        return slug
    return idx.org_by_alias.get(slug)


def _resolve_organization(proposal: dict, idx: PolderIndex) -> OrgMatch:
    """Probeer organization-id te resolven uit een proposal.

    Volgorde:
    1. proposal.organization_id exists (direct of via alias) — 1.0 / 0.92
    2. organization_chain[last].slug_proposal — 0.95 / 0.88
    3. organization_chain[*].slug_proposal — 0.85 / 0.80
    4. None
    """
    raw_org = proposal.get("organization_id")
    if raw_org:
        if raw_org in idx.org_ids:
            level = _level_for_org(proposal, raw_org)
            return OrgMatch(raw_org, 1.0, "proposal_id_exact", level)
        alias_hit = idx.org_by_alias.get(raw_org)
        if alias_hit:
            level = _level_for_org(proposal, alias_hit) or _level_for_org(proposal, raw_org)
            return OrgMatch(alias_hit, 0.92, "proposal_id_via_alias", level)

    chain = proposal.get("organization_chain") or []
    # Eerst: laatste (meest-specifieke) chain-entry
    if chain and isinstance(chain[-1], dict):
        slug = chain[-1].get("slug_proposal")
        resolved = _lookup_org(idx, slug)
        if resolved:
            method = "chain_last_exact" if resolved == slug else "chain_last_via_alias"
            conf = 0.95 if resolved == slug else 0.88
            return OrgMatch(resolved, conf, method, chain[-1].get("level"))
    # Anders: eerste hit in de chain (via alias indien nodig)
    for entry in chain:
        if not isinstance(entry, dict):
            continue
        slug = entry.get("slug_proposal")
        resolved = _lookup_org(idx, slug)
        if resolved:
            method = "chain_partial_exact" if resolved == slug else "chain_partial_via_alias"
            conf = 0.85 if resolved == slug else 0.80
            return OrgMatch(resolved, conf, method, entry.get("level"))

    return OrgMatch(None, 0.0, "no_match", None)


def _level_for_org(proposal: dict, org_id: str) -> str | None:
    """Pak de level uit organization_chain die bij `org_id` past."""
    for entry in proposal.get("organization_chain") or []:
        if isinstance(entry, dict) and entry.get("slug_proposal") == org_id:
            return entry.get("level")
    return None


# Volgorde belangrijk: meest-specifieke keyword eerst. Anders slokt "minister"
# de "vice-minister-president"-rol op.
_ROLE_KEYWORDS: list[tuple[str, str]] = [
    ("vice-minister-president", "vice-minister-president"),
    ("minister-president", "minister-president"),
    ("staatssecretaris", "staatssecretaris"),
    ("plaatsvervangend secretaris-generaal", "psg"),
    ("secretaris-generaal", "sg"),
    ("directeur-generaal", "dg"),
    ("inspecteur-generaal", "ig"),
    ("afdelingshoofd", "afdelingshoofd"),
    ("directeur", "directeur"),
    ("minister", "minister"),
]


def _post_keyword_for_role(role: str) -> str | None:
    """Pak de canonical post-id-keyword die bij een role-string hoort.

    Voorbeelden:
      "Minister van Defensie"              -> "minister"
      "Staatssecretaris van Financiën"     -> "staatssecretaris"
      "Vice-Minister-President, Minister"  -> "vice-minister-president"
    """
    rl = role.lower()
    for needle, keyword in _ROLE_KEYWORDS:
        if needle in rl:
            return keyword
    return None


def _resolve_post(
    proposal: dict, idx: PolderIndex, org_id: str | None
) -> PostMatch:
    """Match post-id in vier oplopende strategieën:

    1. Exact: ``post:X`` zit letterlijk in ``data/posten/``.
    2. Org + classification: als de skill een verzonnen slug levert maar er
       is precies één post met dezelfde classification onder de geresolveerde
       organisatie. Voor bewindspersoon-posts werkt dit altijd (één
       ``post:minister-min-X`` per ministerie), voor andere classifications
       alleen als er geen ambiguïteit is.
    3. Creatable: ``role`` mapt op een schema-classification, dus apply kan
       de post zelf aanmaken in deze run.
    4. Geen match.
    """
    from polder.apply import _classification_from_role

    pid = proposal.get("post_id")
    role = str(proposal.get("role") or "").strip()

    # Strategie 1: exact slug-match.
    if pid and pid in idx.post_ids:
        post_org = idx.post_to_org.get(pid)
        if org_id and post_org and post_org != org_id:
            return PostMatch(pid, 0.70, "exact_but_org_mismatch")
        return PostMatch(pid, 0.95, "exact")

    classification = _classification_from_role(role) if role else None

    # Strategie 2: fuzzy via (org, classification) met role-keyword als sub-
    # discriminator. Bewindspersoon-posts bestaan in twee smaken onder elk
    # ministerie (minister + staatssecretaris); classification alleen is dan
    # ambigu. We filteren de kandidaten op de role-keyword die in de canonical
    # post-id terugkomt, bv. "minister" matcht "post:minister-min-def" maar
    # niet "post:staatssecretaris-min-def".
    if org_id and classification:
        candidates = idx.posts_by_org_class.get((org_id, classification), [])
        if role:
            keyword = _post_keyword_for_role(role)
            if keyword:
                refined = [
                    p for p in candidates if f":{keyword}-" in p or p.endswith(f":{keyword}")
                ]
                if len(refined) == 1:
                    return PostMatch(refined[0], 0.90, "matched_by_org_classification")
                if len(refined) > 1:
                    # Meerdere keyword-matches: prefer de slug die expliciet de
                    # ministerie-suffix bevat (canonical conventie:
                    # `post:<rol>-min-<afk>`). Anders een verzonnen variant
                    # zoals `post:minister-president-az` zou hier blijven
                    # hangen en apply zou m onbedoeld aanmaken.
                    min_suffix = org_id.removeprefix("org:")
                    if min_suffix.startswith("min-"):
                        with_suffix = [p for p in refined if p.endswith(f"-{min_suffix}")]
                        if len(with_suffix) == 1:
                            return PostMatch(
                                with_suffix[0], 0.90, "matched_by_org_classification"
                            )
        if len(candidates) == 1:
            return PostMatch(candidates[0], 0.90, "matched_by_org_classification")

    # Strategie 3: creatable. Apply maakt de post zelf aan.
    if pid and classification:
        return PostMatch(pid, 0.85, "creatable_from_role")
    if not pid:
        return PostMatch(None, 0.0, "no_post_id_in_proposal")
    return PostMatch(None, 0.0, "not_in_data")


def _extract_birth_hint(proposal: dict) -> int | None:
    """Extract `birth.year` of `birth_year` uit een proposal."""
    birth = proposal.get("birth")
    if isinstance(birth, dict):
        y = birth.get("year")
        if isinstance(y, int):
            return y
    y = proposal.get("birth_year")
    if isinstance(y, int):
        return y
    return None


def resolve_proposal(
    proposal: dict,
    idx: PolderIndex,
    *,
    enricher: PersonEnricher | None = None,
) -> dict:
    """Resolve één proposal. Retourneer enriched dict in apply-staging-format.

    Adds:
    - resolved_organization_id, resolved_organization_level
    - resolved_post_id, resolved_person_id
    - resolution_confidence (per veld)
    - resolution_notes
    - propose_post_creation
    - merge_recommendation: 'auto-merge' | 'needs-review' | 'skip'
    - birth (uit enricher, indien gevonden)

    Argumenten:
    - `enricher`: optionele callable die (name, existing_birth_hint) krijgt
      en een birth_year retourneert. Gebruikt bij `no_match` om alsnog een
      `creatable_new_person`-pad te openen.
    """
    out: dict[str, Any] = dict(proposal)

    # Skip-paden
    event = proposal.get("event_type")
    if event == "geen_benoeming" or (event is None and not proposal.get("person_name")):
        out.update(
            resolved_organization_id=None,
            resolved_organization_level=None,
            resolved_post_id=None,
            resolved_person_id=None,
            resolution_confidence={"organization": 0.0, "post": 0.0, "person": 0.0},
            resolution_notes="Geen benoemings-event; niets te resolven.",
            propose_post_creation=False,
            merge_recommendation="skip",
        )
        return out

    org = _resolve_organization(proposal, idx)
    post = _resolve_post(proposal, idx, org.organization_id)

    name = proposal.get("person_name") or ""
    birth_hint = _extract_birth_hint(proposal)

    person = match_person(name, idx=idx, birth_year=birth_hint)

    # Als geen match én een enricher beschikbaar is, probeer birth_year op te
    # halen en re-match. Wikidata levert vaak een geboortejaar dat onze
    # parser-skill mist; daarmee gaat een no_match naar creatable_new_person
    # of zelfs een echte family_initials_year-match als de family wél in
    # data/personen/ blijkt te staan onder een andere geboortedatum-variant.
    if (
        enricher is not None
        and person.confidence < 0.85
        and name
        and birth_hint is None
    ):
        enriched_year = enricher(name, birth_hint)
        if isinstance(enriched_year, int):
            person = match_person(name, idx=idx, birth_year=enriched_year)
            birth_hint = enriched_year
            out["birth"] = {"year": enriched_year}

    notes = _format_notes(org, post, person)

    propose_post_creation = (
        post.post_id is None and bool(proposal.get("post_id"))
    )

    rec = _recommend_merge(org, post, person, proposal)

    out.update(
        resolved_organization_id=org.organization_id,
        resolved_organization_level=org.level,
        resolved_post_id=post.post_id,
        resolved_person_id=person.person_id,
        resolution_confidence={
            "organization": round(org.confidence, 2),
            "post": round(post.confidence, 2),
            "person": round(person.confidence, 2),
        },
        resolution_notes=notes,
        propose_post_creation=propose_post_creation,
        merge_recommendation=rec,
    )
    return out


def _format_notes(org: OrgMatch, post: PostMatch, person: PersonMatch) -> str:
    parts: list[str] = [
        f"org: {org.method}",
        f"post: {post.method}",
        f"person: {person.method}",
    ]
    if person.candidates:
        parts.append(f"person-candidates: {','.join(person.candidates[:3])}")
    return "; ".join(parts)


def _recommend_merge(
    org: OrgMatch, post: PostMatch, person: PersonMatch, proposal: dict
) -> str:
    """Bepaal `merge_recommendation`.

    Auto-merge: alle drie velden ≥ 0.85 én proposal-confidence ≥ 0.85 (als
    aanwezig) én geen ambiguïteit.
    """
    proposal_conf = proposal.get("confidence")
    if isinstance(proposal_conf, (int, float)) and proposal_conf < 0.85:
        return "needs-review"

    if "ambiguous" in person.method:
        return "needs-review"

    # Alle drie velden moeten ≥ 0.85 zijn
    if org.confidence < 0.85 or post.confidence < 0.85 or person.confidence < 0.85:
        return "needs-review"

    return "auto-merge"
