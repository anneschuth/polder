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
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("polder.resolve.llm_enrich")

_SKILL_NAME = "lookup-person"
_MIN_PERSON_CONF_FOR_AUTOMERGE = 0.95
_RUNNER_TIMEOUT_S = 180.0


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

    recomputed: int = 0
    """Al-ge-enrichte proposals waarvan merge_recommendation bij een rerun
    veranderde door een gewijzigde _recompute_merge-policy (geen skill-call)."""

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
    # Verzamel ALLE segmenten die persoon-context bevatten. De resolver
    # schrijft o.a. "person: ambiguous_family; person-candidates: person:X,person:Y"
    # — die staan in aparte segmenten, dus we joinen ze terug bij elkaar.
    person_segments = [s.strip() for s in notes.split(";") if "person" in s]
    person_note = "; ".join(person_segments)

    candidate_ids = _extract_candidate_ids(person_note)
    resolved_pid = proposal.get("resolved_person_id")

    if "no_match" in person_note or "no_family" in person_note:
        return _BucketChoice(mode="no_match")
    if "ambiguous_family" in person_note or "person-candidates" in person_note:
        cands = [{"id": cid} for cid in candidate_ids]
        return _BucketChoice(mode="ambiguous_family", candidates=cands)
    if (
        "family_initials_no_year" in person_note
        or "family_unique" in person_note
        or "family_given" in person_note
    ):
        # Een 'year_fill' is een sterke partial-match. resolved_person_id wijst
        # naar de kandidaat die het code-pad koos; geef die expliciet mee zodat
        # de skill weet welk record hij moet aanvullen of verwerpen.
        cands: list[dict[str, Any]] = []
        if isinstance(resolved_pid, str) and resolved_pid:
            cands.append({"id": resolved_pid})
        return _BucketChoice(mode="year_fill", candidates=cands)
    return None


_CANDIDATE_RX = re.compile(r"person:[a-z][a-z0-9-]*-(?:[0-9]{4}|[0-9]{7,}|[0-9a-f]{8})")


def _extract_candidate_ids(person_note: str) -> list[str]:
    """Trek 'person:slug-...'-IDs uit een resolution_notes-segment."""
    if not person_note:
        return []
    seen: list[str] = []
    for hit in _CANDIDATE_RX.findall(person_note):
        if hit not in seen:
            seen.append(hit)
    return seen


def _enrich_candidate(candidate_id: str, *, data_dir: Path) -> dict[str, Any]:
    """Laad een bestaand persoon-record voor de skill om uit te kiezen."""
    from polder.lib.quick_lookup import load_by_id

    record = load_by_id(data_dir, candidate_id)
    if record is None:
        return {"id": candidate_id, "not_found": True}

    raw = record.model_dump(mode="json", exclude_none=True)
    # Trim: alleen velden die de skill nodig heeft om identiteit te beoordelen.
    return {
        "id": raw.get("id"),
        "name": raw.get("name"),
        "birth": raw.get("birth"),
        "gender": raw.get("gender"),
        "identifiers": raw.get("identifiers"),
        "mandaten": [
            {
                "post_id": m.get("post_id"),
                "organization_id": m.get("organization_id"),
                "role": m.get("role"),
                "start_date": m.get("start_date"),
                "end_date": m.get("end_date"),
            }
            for m in (raw.get("mandaten") or [])
        ][:20],
    }


def _wikidata_candidates(name: str, role: str | None, org: str | None) -> list[dict[str, Any]]:
    """Eén Wikidata-lookup voor de no_match-bucket. Best-effort; lege lijst
    als de SPARQL- of reconciliation-call faalt of niets oplevert."""
    try:
        from polder.fetchers.wikidata_sparql import lookup_person_by_name
        from polder.resolve.names import parse_person_name
    except ImportError:
        return []

    parsed = parse_person_name(name)
    if not parsed.family:
        return []
    try:
        candidates = lookup_person_by_name(
            parsed.family,
            initials=parsed.initials,
            given=parsed.given,
        )
    except Exception as exc:
        logger.debug("Wikidata-lookup faalde voor %s: %s", name, exc)
        return []

    # Top-5 met meeste signal: liever met birth_year dan zonder.
    candidates.sort(key=lambda c: (c.get("birth_year") is None, c.get("label") or ""))
    return candidates[:5]


def _build_payload(
    proposal: dict[str, Any],
    bucket: _BucketChoice,
    *,
    data_dir: Path,
) -> str:
    """Bouw de JSON-payload die naar de skill gaat. Single-turn: alle context
    die de skill nodig heeft staat in de payload, geen tool-calls."""
    enriched_candidates: list[dict[str, Any]] = []
    for c in bucket.candidates:
        cid = c.get("id") if isinstance(c, dict) else None
        if cid:
            enriched_candidates.append(_enrich_candidate(cid, data_dir=data_dir))

    wikidata: list[dict[str, Any]] = []
    if bucket.mode == "no_match":
        wikidata = _wikidata_candidates(
            proposal.get("person_name") or "",
            proposal.get("role"),
            proposal.get("resolved_organization_id") or proposal.get("organization_id"),
        )

    # Organogram-proposals (type: person_post) gebruiken `evidence` ipv
    # `evidence_snippet`, en hebben geen `role` of `start_date`. Map die
    # zodat de skill toch context heeft om mee te werken.
    proposal_type = proposal.get("type") or ""
    is_organogram = proposal_type == "person_post"

    payload = {
        "mode": bucket.mode,
        "proposal_type": proposal_type or "staatscourant_or_abd",
        "proposal": {
            "person_name": proposal.get("person_name"),
            "role": proposal.get("role")
            or proposal.get("classification")
            or (proposal.get("post_id") or "").replace("post:", "").replace("-", " "),
            "organization_id": proposal.get("resolved_organization_id")
            or proposal.get("organization_id"),
            "organization_chain": proposal.get("organization_chain"),
            "post_id": proposal.get("post_id"),
            "classification": proposal.get("classification"),
            "start_date": proposal.get("start_date"),
            "abd_nieuws_url": proposal.get("abd_nieuws_url"),
            "staatscourant_url": proposal.get("staatscourant_url"),
            "bron_url": proposal.get("bron_url"),
            "evidence_snippet": proposal.get("evidence_snippet") or proposal.get("evidence"),
        },
        "candidates": enriched_candidates,
        "wikidata_candidates": wikidata,
    }
    if is_organogram:
        payload["note_to_skill"] = (
            "Dit is een ORGANOGRAM-proposal: extract uit een ABD-organogram-PDF. "
            "Er is geen Staatscourant/ABD-tekst, alleen een rubriek-extract. "
            "Met alleen naam + post_id + classification kun je in de meeste gevallen "
            "alleen `no_match` of `matched_existing` produceren als Wikidata of "
            "candidates uitsluitsel geven. Geen create_new zonder geverifieerde "
            "birth_year."
        )
    return json.dumps(payload, ensure_ascii=False)


_JSON_FENCE_RX = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _parse_skill_output(text: str) -> dict[str, Any] | None:
    """Extract de JSON-output uit skill-text. Tolerant voor:

    - bare JSON-object
    - ```json …``` of ``` …``` fences (laatste wint als er meerdere zijn)
    - system-reminders, leading prose, of bash-blokken voor de JSON
    """
    text = text.strip()
    candidates: list[str] = []

    # Verzamel alle JSON-fence blokken; vaak prefix de skill een uitleg en
    # sluit hij af met de echte output. Laatste fence wint.
    candidates.extend(_JSON_FENCE_RX.findall(text))

    # Fallback: heuristisch zoeken naar de eerste `{` na de laatste fence
    # of in de hele text.
    if not candidates:
        start = text.find("{")
        if start >= 0:
            candidates.append(text[start:])

    for raw in reversed(candidates):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
        logger.warning("Skill-output is geen JSON-object: %r", type(parsed).__name__)

    logger.warning(
        "Skill-output bevat geen valide JSON-object (text-prefix: %r)",
        text[:120],
    )
    return None


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
                out["merge_recommendation"], out["merge_reason"] = _recompute_merge(out)
            stats.matched_existing += 1
            return out

    if outcome == "create_new":
        new = skill_result.get("new_person") or {}
        birth_year = new.get("birth_year")
        # Een create_new zonder birth_year is geen valide create — onze
        # slug-conventie person:<family>-<initials>-<year> vereist een
        # jaar. Downgrade naar no_match zodat de operator handmatig de
        # call kan maken (UUID-suffix of nader onderzoek).
        if not isinstance(birth_year, int):
            out["resolution_notes"] = _append_note(
                out.get("resolution_notes", ""),
                f"llm-enrich: create_new_without_birth_year_downgraded ({conf:.2f})",
            )
            stats.no_match += 1
            return out
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
            out["merge_recommendation"], out["merge_reason"] = _recompute_merge(out)
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


def _chain_supports_org_creation(proposal: dict[str, Any]) -> bool:
    """True als de organization_chain veilig genoeg is om er via apply
    nieuwe organisatieonderdelen onder te laten aanmaken.

    Vangnet aan de resolver-kant, bovenop de top-down hardening in
    `apply._build_chain_create_actions`. Eist een chain van ≥ 2 niveaus
    waarvan het bovenste niveau een ministerie is. Dat sluit losse,
    contextloze entries uit zoals `[{level: directie, name: Eurowerkgroep}]`
    (een EU-gremium, geen NL-overheidseenheid) die anders een parentloos
    org-record zouden worden.
    """
    chain = proposal.get("organization_chain") or proposal.get("organization_chain_inferred")
    if not isinstance(chain, list) or len(chain) < 2:
        return False
    top = chain[0]
    if not isinstance(top, dict):
        return False
    return str(top.get("level", "")).strip().lower() == "ministerie"


# Org-resolutie-statussen waarbij de org-zijde veilig genoeg is voor
# auto-merge, ook als de numerieke org-confidence < 0.95 is. De
# apply-laag valideert de chain top-down (ministerie moet bestaan,
# parent-tracking, hiërarchie-check), dus deze statussen + een geldige
# chain zijn betrouwbaarder dan een vlakke confidence-drempel.
_SAFE_ORG_METHODS = {
    "proposal_id_exact",
    "proposal_id_via_alias",
    "chain_partial_exact",
    "chain_partial_via_alias",
}


def _org_side_ok_for_automerge(proposal: dict[str, Any]) -> bool:
    """True als de org-zijde auto-merge mag, gegeven de resolver-status.

    - org-confidence ≥ 0.95: altijd ok.
    - status in _SAFE_ORG_METHODS: ok (apply valideert de chain).
    - org: no_match: alleen ok mét een chain die `_chain_supports_org_creation`
      passeert (≥ 2 niveaus, ministerie-top). Dubbel slot: hier én in
      apply._build_chain_create_actions.
    """
    rc = proposal.get("resolution_confidence") or {}
    if float(rc.get("organization") or 0) >= 0.95:
        return True
    notes = str(proposal.get("resolution_notes") or "")
    for token in notes.split(";"):
        token = token.strip()
        if not token.startswith("org:"):
            continue
        status = token[4:].strip()
        if status in _SAFE_ORG_METHODS:
            return True
        if status == "no_match":
            return _chain_supports_org_creation(proposal)
    return False


def _recompute_merge(proposal: dict[str, Any]) -> tuple[str, str]:
    """Hercompute `(merge_recommendation, merge_reason)` na LLM-enrich.

    Policy:
    - `person` confidence is niet-onderhandelbaar: < 0.95 → needs-review.
      Dit is de kern van de two-source-rule (identiteit moet zeker zijn).
    - org-zijde mag auto-merge via `_org_side_ok_for_automerge`: ofwel
      org-confidence ≥ 0.95, ofwel een resolver-status die de apply-laag
      veilig kan valideren (chain top-down, ministerie-hardening).
    - post-zijde: post-confidence ≥ 0.95 OF `propose_post_creation` met
      een door de resolver voorgestelde slug. De create-post-actie in
      apply heeft een eigen ABD-rol-vs-bewindspersoon-guard als vangnet.

    `merge_reason` is altijd niet-leeg, zodat een handmatige reviewer ziet
    welke check een needs-review tegenhield (#33).
    """
    rc = proposal.get("resolution_confidence") or {}
    person_conf = float(rc.get("person") or 0)
    if person_conf < 0.95:
        return "needs-review", (
            f"low_person_confidence: person {person_conf:.2f} < 0.95 "
            "(two-source-rule, identiteit niet zeker)"
        )
    if not _org_side_ok_for_automerge(proposal):
        org_conf = float(rc.get("organization") or 0)
        return "needs-review", (
            f"org_side_not_automergeable: org {org_conf:.2f} < 0.95 en geen "
            "veilig valideerbare resolver-status"
        )
    post_conf = float(rc.get("post") or 0)
    if post_conf < 0.95 and not proposal.get("propose_post_creation"):
        return "needs-review", (
            f"low_post_confidence: post {post_conf:.2f} < 0.95 en geen propose_post_creation"
        )
    return "auto-merge", "post_enrich_strong: person ≥ 0.95, org/post auto-mergeable"


def enrich_resolved(
    resolved: list[dict[str, Any]],
    *,
    max_cost_usd: float = 1.00,
    runner: Any | None = None,
    skill_name: str = _SKILL_NAME,
    quote_or_die_check: Any | None = None,
    data_dir: Path | None = None,
) -> tuple[list[dict[str, Any]], EnrichStats]:
    """Verrijk een resolved-lijst met LLM-output voor person-fallback.

    `runner` is een callable met de signatuur van `polder.llm.runner.run_skill`;
    default haalt deze functie hem zelf op. Argument bestaat voor testbaarheid.

    `quote_or_die_check(snippet, source_url) -> bool`: caller-side hook om de
    evidence_snippet te verifiëren. Default = `None` (skill-output wordt op
    waarde geaccepteerd, omdat de skill zelf al de quote-or-die-rule honoreert
    en re-fetchen per proposal traag is). Tests gebruiken een echte hook.

    Het is veilig om dit op een al-LLM-ge-enriched lijst te draaien: de dure
    skill- en Wikidata-calls worden overgeslagen via de `llm_enrich` key.
    De goedkope `merge_recommendation` wordt wél opnieuw afgeleid met de
    huidige `_recompute_merge`-policy, zodat een policy-wijziging propageert
    bij een rerun zonder de hele enrich-machinerie opnieuw te draaien.
    """
    if runner is None:
        from polder.llm.runner import run_skill

        runner = run_skill

    if data_dir is None:
        data_dir = Path("data")

    stats = EnrichStats()
    enriched: list[dict[str, Any]] = []
    budget_exhausted = False

    for idx_proposal, proposal in enumerate(resolved):
        if not isinstance(proposal, dict):
            enriched.append(proposal)
            continue
        if "llm_enrich" in proposal:
            # Dure delen overslaan, maar de merge_recommendation opnieuw
            # afleiden met de actuele policy. Alleen voor proposals waar
            # de eerdere enrich een zekere persoon-match opleverde
            # (person-confidence ≥ _MIN_PERSON_CONF_FOR_AUTOMERGE) — dat
            # is exact het contract waaronder _recompute_merge in de
            # normale flow draait (zie de call-sites in _apply_*). Een
            # eerdere no_match (person 0.0) blijft onaangeroerd.
            rc = proposal.get("resolution_confidence") or {}
            if float(rc.get("person") or 0) >= _MIN_PERSON_CONF_FOR_AUTOMERGE:
                recomputed, recomputed_reason = _recompute_merge(proposal)
                if recomputed != proposal.get("merge_recommendation"):
                    proposal = dict(proposal)
                    proposal["merge_recommendation"] = recomputed
                    proposal["merge_reason"] = recomputed_reason
                    stats.recomputed += 1
            enriched.append(proposal)
            continue

        bucket = _classify_proposal(proposal)
        if bucket is None:
            enriched.append(proposal)
            continue

        stats.candidates += 1
        name = proposal.get("person_name") or "?"

        if budget_exhausted:
            stats.skipped_budget += 1
            enriched.append(proposal)
            continue

        payload = _build_payload(proposal, bucket, data_dir=data_dir)

        # SESSION PER CALL voor lookup-person.
        # 1. De stream-json sessie houdt user-messages cumulatief in context.
        #    Als de skill bij case N van slag raakt zien alle volgende cases
        #    die corruptie ook. Verse subprocess per call voorkomt dat.
        # 2. De runner krijgt een harde timeout. Bij een Anthropic-outage moet
        #    `SkillSession.call` zelf het hangende subprocess sluiten — anders
        #    lekken `claude -p` processen weg in de background.
        #
        # Beide eisen worden afgedwongen door `reuse_session=False` (context
        # manager sluit subprocess deterministisch) plus `timeout_s` (interne
        # select-based deadline op stdout-read; geen daemon-thread truc).
        attempts = 0
        result = None
        while attempts < 2:
            attempts += 1
            try:
                result = runner(
                    skill_name,
                    payload,
                    reuse_session=False,
                    timeout_s=_RUNNER_TIMEOUT_S,
                )
            except TypeError:
                # Test-mocks accepteren mogelijk geen kwargs.
                try:
                    result = runner(skill_name, payload)
                except Exception as exc:
                    logger.warning(
                        "[%d/%d] %s (%s): runner-exception (try %d) %s",
                        idx_proposal + 1,
                        len(resolved),
                        name,
                        bucket.mode,
                        attempts,
                        exc,
                    )
                    if attempts >= 2:
                        stats.skill_errors += 1
                        enriched.append(proposal)
                        result = None
                        break
                    continue
            except Exception as exc:
                logger.warning(
                    "[%d/%d] %s (%s): runner-exception (try %d) %s",
                    idx_proposal + 1,
                    len(resolved),
                    name,
                    bucket.mode,
                    attempts,
                    exc,
                )
                if attempts >= 2:
                    stats.skill_errors += 1
                    enriched.append(proposal)
                    result = None
                    break
                continue

            if not getattr(result, "is_error", False) and not getattr(
                result, "rate_limited", False
            ):
                break

            # Error of rate-limit: retry met fresh subprocess (volgende loop-
            # iteratie maakt sowieso een nieuwe via reuse_session=False).
            err_msg = getattr(result, "error_message", None) or ""
            logger.warning(
                "[%d/%d] %s (%s): try %d failed (is_error=%s rate_limited=%s) msg=%s",
                idx_proposal + 1,
                len(resolved),
                name,
                bucket.mode,
                attempts,
                result.is_error,
                result.rate_limited,
                (err_msg or "<none>")[:160],
            )

        if result is None:
            continue  # exception-pad

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

        # Cost = 0 bij disk-cache-hit (runner overschrijft) zodat het
        # budget-cap niet getript wordt door uitgaven uit een eerdere run.
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
            logger.warning(
                "[%d/%d] %s (%s): unparseable skill-output",
                idx_proposal + 1,
                len(resolved),
                name,
                bucket.mode,
            )
            enriched.append(proposal)
            continue

        if quote_or_die_check is not None:
            snippet = parsed.get("evidence_snippet")
            url = parsed.get("evidence_source_url")
            if snippet and url and not quote_or_die_check(snippet, url):
                logger.warning(
                    "[%d/%d] %s (%s): quote-or-die rejected",
                    idx_proposal + 1,
                    len(resolved),
                    name,
                    bucket.mode,
                )
                stats.quote_or_die_rejected += 1
                enriched.append(proposal)
                continue

        outcome = parsed.get("outcome") or "?"
        conf = parsed.get("confidence", 0)
        logger.info(
            "[%d/%d] %s (%s) -> %s conf=%.2f cost=$%.4f",
            idx_proposal + 1,
            len(resolved),
            name,
            bucket.mode,
            outcome,
            float(conf),
            float(cost),
        )
        enriched.append(_apply_skill_result(proposal, parsed, stats=stats))

    return enriched, stats


__all__ = ["EnrichStats", "enrich_resolved"]
