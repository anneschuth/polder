"""End-to-end resolve: één staging-proposal → resolved-proposal.

Combineert persoon-match (via `polder.resolve.matcher`) met organization- en
post-resolutie. Output-format is identiek aan wat de oude
`resolve-staging-proposals`-skill produceerde, zodat `polder apply-staging`
ongewijzigd werkt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from polder.resolve.matcher import PersonMatch, PolderIndex, match_person


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


def _resolve_organization(proposal: dict, idx: PolderIndex) -> OrgMatch:
    """Probeer organization-id te resolven uit een proposal.

    Volgorde:
    1. proposal.organization_id exists in data — 1.0
    2. organization_chain[last].slug_proposal exists — 0.95 (chain-fallback)
    3. organization_chain[*].slug_proposal exists, eerste hit — 0.85
    4. None
    """
    org_id = proposal.get("organization_id")
    if org_id and org_id in idx.org_ids:
        # Bepaal level uit chain, indien aanwezig
        level = _level_for_org(proposal, org_id)
        return OrgMatch(org_id, 1.0, "proposal_id_exact", level)

    chain = proposal.get("organization_chain") or []
    # Eerst: laatste (meest-specifieke) chain-entry
    if chain and isinstance(chain[-1], dict):
        slug = chain[-1].get("slug_proposal")
        if slug and slug in idx.org_ids:
            return OrgMatch(slug, 0.95, "chain_last_exact", chain[-1].get("level"))
    # Anders: eerste exact-match in de chain
    for entry in chain:
        if not isinstance(entry, dict):
            continue
        slug = entry.get("slug_proposal")
        if slug and slug in idx.org_ids:
            return OrgMatch(slug, 0.85, "chain_partial_exact", entry.get("level"))

    return OrgMatch(None, 0.0, "no_match", None)


def _level_for_org(proposal: dict, org_id: str) -> str | None:
    """Pak de level uit organization_chain die bij `org_id` past."""
    for entry in proposal.get("organization_chain") or []:
        if isinstance(entry, dict) and entry.get("slug_proposal") == org_id:
            return entry.get("level")
    return None


def _resolve_post(proposal: dict, idx: PolderIndex, org_id: str | None) -> PostMatch:
    """Match post-id."""
    pid = proposal.get("post_id")
    if not pid:
        return PostMatch(None, 0.0, "no_post_id_in_proposal")
    if pid not in idx.post_ids:
        return PostMatch(None, 0.0, "not_in_data")
    # Optionele check: hoort post bij de gevonden org?
    post_org = idx.post_to_org.get(pid)
    if org_id and post_org and post_org != org_id:
        return PostMatch(pid, 0.70, "exact_but_org_mismatch")
    return PostMatch(pid, 0.95, "exact")


def resolve_proposal(proposal: dict, idx: PolderIndex) -> dict:
    """Resolve één proposal. Retourneer enriched dict in apply-staging-format.

    Adds:
    - resolved_organization_id, resolved_organization_level
    - resolved_post_id, resolved_person_id
    - resolution_confidence (per veld)
    - resolution_notes
    - propose_post_creation
    - merge_recommendation: 'auto-merge' | 'needs-review' | 'skip'
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

    birth_hint = None
    birth = proposal.get("birth")
    if isinstance(birth, dict):
        y = birth.get("year")
        if isinstance(y, int):
            birth_hint = y

    person = match_person(
        proposal.get("person_name") or "",
        idx=idx,
        birth_year=birth_hint,
    )

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
    parts: list[str] = []
    if org.method != "no_match":
        parts.append(f"org: {org.method}")
    if post.method not in ("no_match", "no_post_id_in_proposal"):
        parts.append(f"post: {post.method}")
    if person.method not in ("no_match", "no_family"):
        parts.append(f"person: {person.method}")
    if person.candidates:
        parts.append(f"person-candidates: {','.join(person.candidates[:3])}")
    return "; ".join(parts) or "no matches"


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
