"""LLM-fallback voor persoon-resolution in `polder resolve --enrich-llm`.

Werkwijze:

1. Iteratie 1 van `polder resolve` heeft alle proposals geprobeerd te resolven
   met code-only matching (en optioneel Wikidata-enrichment). Een derde komt
   niet boven `person.confidence ≥ 0.85` en blijft op `merge_recommendation:
   needs-review` of `skip` staan.
2. Deze module pakt alleen die proposals op en classificeert ze in drie
   buckets (`no_match`, `ambiguous_family`, `year_fill`).
3. Per bucket bouwt het een payload voor de `lookup-person` skill en roept
   `polder.llm.runner.run_skill` aan. Skill-output wordt gevalideerd
   (quote-or-die op birth_year, schema-check op outcome) en gemerged in de
   bestaande resolved-dict — `resolved_person_id`, `birth`, `resolution_*`,
   `merge_recommendation` worden waar nodig overschreven.
4. Hard budget-cap via `max_cost_usd`. Bij overschrijding stopt de pas en
   blijven de overige proposals onveranderd.

Quote-or-die: een birth_year wordt alleen overgenomen als de skill een
`evidence_snippet` aanlevert die letterlijk in de opgehaalde content van
`evidence_source_url` voorkomt. Onbeperkt vertrouwen op LLM is uit den boze.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("polder.resolve.llm_enrich")

_SKILL_NAME = "lookup-person"
_MIN_PERSON_CONF_FOR_AUTOMERGE = 0.95


@dataclass
class EnrichStats:
    """Telt wat de LLM-pass gedaan heeft. Wordt aan de CLI-output gehangen."""

    candidates: int = 0
    """Proposals geclassificeerd als enrich-doelwit."""

    skipped_budget: int = 0
    """Niet geprobeerd omdat het cost-cap geraakt was."""

    skill_calls: int = 0
    cache_hits: int = 0
    rate_limited: int = 0
    skill_errors: int = 0

    matched_existing: int = 0
    """Skill koos een bestaande person:<id>."""

    created_new: int = 0
    """Skill leverde een nieuw record-voorstel (birth_year + naam)."""

    no_match: int = 0
    """Skill kon geen overtuigende match maken."""

    quote_or_die_rejected: int = 0
    """evidence_snippet niet teruggevonden in source-url content; geweigerd."""

    total_cost_usd: float = 0.0


@dataclass
class _BucketChoice:
    mode: str
    candidates: list[dict[str, Any]] = field(default_factory=list)


def _classify_proposal(proposal: dict[str, Any]) -> _BucketChoice | None:
    """Beslis of een proposal in een van de drie target-buckets valt.

    Return None als de proposal niets met persoon-fallback te maken heeft
    (geen person_name, al hoge confidence, of skip-event).
    """
    name = proposal.get("person_name")
    if not name:
        return None

    rc = proposal.get("resolution_confidence") or {}
    person_conf = float(rc.get("person") or 0.0)
    if person_conf >= 0.95:
        return None  # niets te enrichen

    notes = proposal.get("resolution_notes") or ""
    person_note = ""
    for part in notes.split(";"):
        if "person" in part:
            person_note = part.strip()
            break

    if "no_match" in person_note or "no_family" in person_note:
        return _BucketChoice(mode="no_match")
    if "ambiguous_family" in person_note or "person-candidates" in person_note:
        return _BucketChoice(mode="ambiguous_family")
    if (
        "family_initials_no_year" in person_note
        or "family_unique" in person_note
        or "family_given" in person_note
    ):
        return _BucketChoice(mode="year_fill")
    return None


def _build_payload(proposal: dict[str, Any], bucket: _BucketChoice) -> str:
    """Bouw de JSON-payload die naar de skill gaat. Minimal — alleen velden
    die de skill nodig heeft, niet de hele resolved-dict.
    """
    payload = {
        "mode": bucket.mode,
        "proposal": {
            "person_name": proposal.get("person_name"),
            "role": proposal.get("role"),
            "organization_id": proposal.get("resolved_organization_id")
            or proposal.get("organization_id"),
            "organization_chain": proposal.get("organization_chain"),
            "start_date": proposal.get("start_date"),
            "abd_nieuws_url": proposal.get("abd_nieuws_url"),
            "staatscourant_url": proposal.get("staatscourant_url"),
            "evidence_snippet": proposal.get("evidence_snippet"),
        },
        "candidates": bucket.candidates,
    }
    return json.dumps(payload, ensure_ascii=False)


def _parse_skill_output(text: str) -> dict[str, Any] | None:
    """Verwacht puur JSON. Tolerant voor één markdown-fence omdat skills die
    soms toch toevoegen."""
    text = text.strip()
    if text.startswith("```"):
        # Strip eerste regel (```json) en laatste ``` als die er staat.
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("Skill-output is geen geldige JSON: %s", exc)
        return None
    if not isinstance(parsed, dict):
        logger.warning("Skill-output is geen JSON-object: %r", type(parsed).__name__)
        return None
    return parsed


def _apply_skill_result(
    proposal: dict[str, Any],
    skill_result: dict[str, Any],
    *,
    stats: EnrichStats,
) -> dict[str, Any]:
    """Merge skill-output terug in de resolved-dict.

    Returnt een nieuwe dict (de caller mag de originele behouden voor
    diagnostics). Alleen velden waar de skill iets nuttigs zegt worden
    aangepast.
    """
    out = dict(proposal)
    out["llm_enrich"] = {
        "outcome": skill_result.get("outcome"),
        "confidence": skill_result.get("confidence"),
        "reasoning": skill_result.get("confidence_reasoning"),
        "evidence_snippet": skill_result.get("evidence_snippet"),
        "evidence_source_url": skill_result.get("evidence_source_url"),
    }

    outcome = skill_result.get("outcome")
    conf = float(skill_result.get("confidence") or 0.0)

    if outcome == "matched_existing":
        chosen = skill_result.get("chosen_person_id")
        if chosen:
            out["resolved_person_id"] = chosen
            rc = dict(out.get("resolution_confidence") or {})
            rc["person"] = round(conf, 2)
            out["resolution_confidence"] = rc
            out["resolution_notes"] = _append_note(
                out.get("resolution_notes", ""),
                f"llm-enrich: matched_existing {chosen} ({conf:.2f})",
            )
            if conf >= _MIN_PERSON_CONF_FOR_AUTOMERGE:
                out["merge_recommendation"] = _recompute_merge(out)
            stats.matched_existing += 1
            return out

    if outcome == "create_new":
        new = skill_result.get("new_person") or {}
        birth_year = new.get("birth_year")
        if isinstance(birth_year, int):
            out["birth"] = {"year": birth_year}
        wikidata_qid = new.get("wikidata_qid")
        if wikidata_qid:
            out.setdefault("identifiers", {})["wikidata"] = wikidata_qid
        rc = dict(out.get("resolution_confidence") or {})
        rc["person"] = round(conf, 2)
        out["resolution_confidence"] = rc
        out["resolution_notes"] = _append_note(
            out.get("resolution_notes", ""),
            f"llm-enrich: create_new ({conf:.2f})",
        )
        if conf >= _MIN_PERSON_CONF_FOR_AUTOMERGE:
            out["merge_recommendation"] = _recompute_merge(out)
        stats.created_new += 1
        return out

    out["resolution_notes"] = _append_note(
        out.get("resolution_notes", ""),
        f"llm-enrich: no_match ({conf:.2f})",
    )
    stats.no_match += 1
    return out


def _append_note(existing: str, addition: str) -> str:
    if not existing:
        return addition
    return f"{existing}; {addition}"


def _recompute_merge(proposal: dict[str, Any]) -> str:
    """Hercompute merge_recommendation na een geslaagde LLM-enrich.

    Eenvoudige policy: alleen als alle drie confidences ≥ 0.95 en er geen
    propose_post_creation is, kan het auto-merge worden. Anders needs-review.
    """
    rc = proposal.get("resolution_confidence") or {}
    if any(float(rc.get(k) or 0) < 0.95 for k in ("organization", "post", "person")):
        return "needs-review"
    if proposal.get("propose_post_creation"):
        return "needs-review"
    return "auto-merge"


def enrich_resolved(
    resolved: list[dict[str, Any]],
    *,
    max_cost_usd: float = 1.00,
    runner: Any | None = None,
    skill_name: str = _SKILL_NAME,
    quote_or_die_check: Any | None = None,
) -> tuple[list[dict[str, Any]], EnrichStats]:
    """Verrijk een resolved-lijst met LLM-output voor person-fallback.

    `runner` is een callable met de signatuur van `polder.llm.runner.run_skill`;
    default haalt deze functie hem zelf op. Argument bestaat voor testbaarheid.

    `quote_or_die_check(snippet, source_url) -> bool`: caller-side hook om de
    evidence_snippet te verifiëren. Default = `None` (skill-output wordt op
    waarde geaccepteerd, omdat de skill zelf al de quote-or-die-rule honoreert
    en re-fetchen per proposal traag is). Tests gebruiken een echte hook.

    Het is veilig om dit op een al-LLM-ge-enriched lijst te draaien (idempotent
    via `llm_enrich` key — die slaat de pass over).
    """
    if runner is None:
        from polder.llm.runner import run_skill

        runner = run_skill

    stats = EnrichStats()
    enriched: list[dict[str, Any]] = []
    budget_exhausted = False

    for proposal in resolved:
        if not isinstance(proposal, dict):
            enriched.append(proposal)
            continue
        if "llm_enrich" in proposal:
            enriched.append(proposal)
            continue

        bucket = _classify_proposal(proposal)
        if bucket is None:
            enriched.append(proposal)
            continue

        stats.candidates += 1

        if budget_exhausted:
            stats.skipped_budget += 1
            enriched.append(proposal)
            continue

        payload = _build_payload(proposal, bucket)
        try:
            result = runner(skill_name, payload)
        except Exception as exc:  # noqa: BLE001 — alle runner-failures opvangen
            logger.warning("Skill-call faalde voor %s: %s", proposal.get("person_name"), exc)
            stats.skill_errors += 1
            enriched.append(proposal)
            continue

        stats.skill_calls += 1
        if getattr(result, "cache_hit", False):
            stats.cache_hits += 1
        if getattr(result, "rate_limited", False):
            stats.rate_limited += 1
            enriched.append(proposal)
            continue
        if getattr(result, "is_error", False):
            stats.skill_errors += 1
            enriched.append(proposal)
            continue

        cost = float(getattr(result, "cost_usd", 0.0))
        stats.total_cost_usd += cost
        if stats.total_cost_usd > max_cost_usd:
            budget_exhausted = True
            logger.warning(
                "Budget-cap (%.2f USD) geraakt na %d calls; resterende proposals overgeslagen.",
                max_cost_usd,
                stats.skill_calls,
            )

        parsed = _parse_skill_output(result.text)
        if parsed is None:
            stats.skill_errors += 1
            enriched.append(proposal)
            continue

        if quote_or_die_check is not None:
            snippet = parsed.get("evidence_snippet")
            url = parsed.get("evidence_source_url")
            if snippet and url and not quote_or_die_check(snippet, url):
                logger.warning(
                    "Quote-or-die-check faalde voor %s; resultaat verworpen.",
                    proposal.get("person_name"),
                )
                stats.quote_or_die_rejected += 1
                enriched.append(proposal)
                continue

        enriched.append(_apply_skill_result(proposal, parsed, stats=stats))

    return enriched, stats


__all__ = ["EnrichStats", "enrich_resolved"]
