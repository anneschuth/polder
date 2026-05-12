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


def _resolve_post(
    proposal: dict, idx: PolderIndex, org_id: str | None
) -> PostMatch:
    """Match post-id.

    Een ontbrekende post in `data/` is nog steeds auto-mergeable mits apply
    hem kan aanmaken: dat lukt als de `role` mapt op een schema-classification.
    In dat geval is `creatable` waar en geeft apply zelf de post-creation door.
    """
    from polder.apply import _classification_from_role

    pid = proposal.get("post_id")
    if not pid:
        return PostMatch(None, 0.0, "no_post_id_in_proposal")
    if pid in idx.post_ids:
        post_org = idx.post_to_org.get(pid)
        if org_id and post_org and post_org != org_id:
            return PostMatch(pid, 0.70, "exact_but_org_mismatch")
        return PostMatch(pid, 0.95, "exact")
    # Post niet in data. Acceptabel als de role een afleidbare classification
    # heeft — dan maakt apply hem in deze run aan.
    role = str(proposal.get("role") or "").strip()
    if role and _classification_from_role(role) is not None:
        return PostMatch(pid, 0.85, "creatable_from_role")
    return PostMatch(None, 0.0, "not_in_data")


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
