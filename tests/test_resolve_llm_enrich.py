"""Tests voor `polder.resolve.llm_enrich`."""

from __future__ import annotations

import json
from dataclasses import dataclass

from polder.resolve.llm_enrich import (
    _chain_supports_org_creation,
    _org_side_ok_for_automerge,
    _recompute_merge,
    enrich_resolved,
)


def _conf(person=0.96, org=0.96, post=0.96):
    return {"person": person, "organization": org, "post": post}


def _assert_reason(reason: str, must_contain: str) -> None:
    """Elke recommendation moet een niet-lege, informatieve reden hebben (#33)."""
    assert isinstance(reason, str)
    assert reason.strip()
    assert must_contain in reason


def test_recompute_merge_person_below_threshold_blocks() -> None:
    # person < 0.95 is niet-onderhandelbaar (kern two-source-rule).
    p = {"resolution_confidence": _conf(person=0.94), "resolution_notes": "org: proposal_id_exact"}
    rec, reason = _recompute_merge(p)
    assert rec == "needs-review"
    _assert_reason(reason, "low_person_confidence")


def test_recompute_merge_all_high_is_automerge() -> None:
    p = {"resolution_confidence": _conf(), "resolution_notes": "org: proposal_id_exact"}
    rec, reason = _recompute_merge(p)
    assert rec == "auto-merge"
    _assert_reason(reason, "post_enrich_strong")


def test_recompute_merge_chain_partial_org_allowed() -> None:
    # org-confidence 0.85 maar status chain_partial_exact: apply valideert
    # de chain top-down, dus auto-merge mag.
    p = {
        "resolution_confidence": _conf(org=0.85),
        "resolution_notes": "org: chain_partial_exact; post: exact",
    }
    rec, reason = _recompute_merge(p)
    assert rec == "auto-merge"
    _assert_reason(reason, "post_enrich_strong")


def test_recompute_merge_post_creation_allowed_when_org_safe() -> None:
    # propose_post_creation mag auto-merge mits person hoog en org veilig.
    p = {
        "resolution_confidence": _conf(post=0.0),
        "resolution_notes": "org: proposal_id_exact; post: creatable_from_role",
        "propose_post_creation": True,
    }
    rec, reason = _recompute_merge(p)
    assert rec == "auto-merge"
    _assert_reason(reason, "post_enrich_strong")


def test_recompute_merge_org_no_match_needs_ministerie_chain() -> None:
    # org: no_match met losse 1-niveau chain (EU-gremium) blijft needs-review.
    p = {
        "resolution_confidence": _conf(org=0.0),
        "resolution_notes": "org: no_match; post: not_in_data",
        "organization_chain": [{"level": "directie", "name": "Eurowerkgroep"}],
    }
    rec, reason = _recompute_merge(p)
    assert rec == "needs-review"
    _assert_reason(reason, "org_side_not_automergeable")


def test_recompute_merge_org_no_match_with_ministerie_chain_ok() -> None:
    # org: no_match maar chain ≥2 niveaus met ministerie-top: apply kan
    # de onderdelen veilig top-down aanmaken.
    p = {
        "resolution_confidence": _conf(org=0.0),
        "resolution_notes": "org: no_match; post: exact",
        "organization_chain": [
            {"level": "ministerie", "name": "ministerie van Financiën"},
            {"level": "directie", "name": "directie Begrotingszaken"},
        ],
    }
    rec, reason = _recompute_merge(p)
    assert rec == "auto-merge"
    _assert_reason(reason, "post_enrich_strong")


def test_chain_supports_org_creation_requires_two_levels_ministerie_top() -> None:
    assert not _chain_supports_org_creation({"organization_chain": []})
    assert not _chain_supports_org_creation(
        {"organization_chain": [{"level": "ministerie", "name": "X"}]}
    )
    assert not _chain_supports_org_creation(
        {"organization_chain": [{"level": "directie", "name": "X"}, {"level": "afdeling"}]}
    )
    assert _chain_supports_org_creation(
        {"organization_chain": [{"level": "ministerie"}, {"level": "directie"}]}
    )


def test_org_side_high_confidence_always_ok() -> None:
    assert _org_side_ok_for_automerge(
        {"resolution_confidence": {"organization": 0.96}, "resolution_notes": ""}
    )


def test_org_side_no_match_without_chain_blocked() -> None:
    assert not _org_side_ok_for_automerge(
        {"resolution_confidence": {"organization": 0.0}, "resolution_notes": "org: no_match"}
    )


@dataclass
class _FakeResult:
    """Mock voor `SkillResult` met alleen de velden die enrich gebruikt."""

    text: str
    cost_usd: float = 0.02
    cache_hit: bool = False
    rate_limited: bool = False
    is_error: bool = False


def _make_runner(canned: dict[str, _FakeResult] | _FakeResult):
    """Bouw een runner die per-input een vast result teruggeeft."""
    calls: list[tuple[str, str]] = []

    def runner(skill_name: str, payload: str, **_kwargs) -> _FakeResult:
        calls.append((skill_name, payload))
        if isinstance(canned, dict):
            data = json.loads(payload)
            key = data["proposal"]["person_name"]
            return canned.get(key, _FakeResult(text='{"outcome":"no_match","confidence":0.1}'))
        return canned

    return runner, calls


def _proposal_no_match(name: str = "Esther van Deursen") -> dict:
    return {
        "person_name": name,
        "role": "directeur",
        "resolved_organization_id": "org:min-ocw",
        "evidence_snippet": "Esther Deursen wordt directeur Toezicht mbo",
        "abd_nieuws_url": "https://example.com/abd/1",
        "resolution_confidence": {"organization": 0.95, "post": 0.95, "person": 0.0},
        "resolution_notes": "org: chain_exact; post: id_exact; person: no_match",
        "merge_recommendation": "needs-review",
    }


def _proposal_ambiguous(name: str = "Esther Bakker") -> dict:
    return {
        "person_name": name,
        "role": "afdelingshoofd",
        "resolved_organization_id": "org:min-fin",
        "evidence_snippet": "Esther Bakker als afdelingshoofd",
        "resolution_confidence": {"organization": 0.95, "post": 0.85, "person": 0.0},
        "resolution_notes": "org: chain_exact; post: not_in_data; person: ambiguous_family",
        "merge_recommendation": "needs-review",
    }


def _proposal_year_fill(name: str = "Ab Warffemius") -> dict:
    return {
        "person_name": name,
        "role": "directeur SK&B",
        "resolved_organization_id": "org:min-ienw",
        "evidence_snippet": "Ab Warffemius wordt directeur",
        "resolution_confidence": {"organization": 0.95, "post": 0.85, "person": 0.92},
        "resolution_notes": "org: id_exact; post: id_exact; person: family_given",
        "merge_recommendation": "needs-review",
    }


def test_skip_proposals_above_threshold() -> None:
    above = {
        "person_name": "Mark Rutte",
        "resolution_confidence": {"organization": 1.0, "post": 1.0, "person": 0.98},
        "resolution_notes": "person: family_initials_year_exact",
        "merge_recommendation": "auto-merge",
    }
    runner, calls = _make_runner(_FakeResult(text="{}"))
    out, stats = enrich_resolved([above], runner=runner)
    assert calls == []
    assert stats.candidates == 0
    assert out == [above]


def test_no_match_create_new_with_birth_year() -> None:
    proposal = _proposal_no_match()
    skill = _FakeResult(
        text=json.dumps(
            {
                "outcome": "create_new",
                "chosen_person_id": None,
                "new_person": {
                    "name": {"family": "Deursen", "given": "Esther", "tussenvoegsel": "van"},
                    "birth_year": 1972,
                    "wikidata_qid": "Q102345678",
                },
                "confidence": 0.96,
                "confidence_reasoning": "Wikidata + rol-match",
                "evidence_snippet": "Esther van Deursen (geboren 1972)",
                "evidence_source_url": "https://www.wikidata.org/wiki/Q102345678",
            }
        )
    )
    runner, _calls = _make_runner(skill)
    out, stats = enrich_resolved([proposal], runner=runner)
    assert stats.candidates == 1
    assert stats.created_new == 1
    assert out[0]["birth"] == {"year": 1972}
    assert out[0]["identifiers"]["wikidata"] == "Q102345678"
    assert out[0]["resolution_confidence"]["person"] == 0.96
    assert out[0]["merge_recommendation"] == "auto-merge"


def test_ambiguous_matched_existing_overrides_resolved_person_id() -> None:
    proposal = _proposal_ambiguous()
    skill = _FakeResult(
        text=json.dumps(
            {
                "outcome": "matched_existing",
                "chosen_person_id": "person:bakker-m-7766715",
                "confidence": 0.97,
                "confidence_reasoning": "Bestaand mandaat op zuster-org",
                "evidence_snippet": "...",
                "evidence_source_url": "https://www.wikidata.org/wiki/Q1",
            }
        )
    )
    runner, _ = _make_runner(skill)
    out, stats = enrich_resolved([proposal], runner=runner)
    assert stats.matched_existing == 1
    assert out[0]["resolved_person_id"] == "person:bakker-m-7766715"
    assert out[0]["resolution_confidence"]["person"] == 0.97


def test_quote_or_die_rejection_keeps_proposal_untouched() -> None:
    proposal = _proposal_no_match()
    skill = _FakeResult(
        text=json.dumps(
            {
                "outcome": "create_new",
                "new_person": {
                    "name": {"family": "Deursen"},
                    "birth_year": 1972,
                },
                "confidence": 0.96,
                "evidence_snippet": "verzonnen tekst",
                "evidence_source_url": "https://www.wikidata.org/wiki/Q1",
            }
        )
    )
    runner, _ = _make_runner(skill)
    out, stats = enrich_resolved(
        [proposal],
        runner=runner,
        quote_or_die_check=lambda snippet, url: False,
    )
    assert stats.quote_or_die_rejected == 1
    assert stats.created_new == 0
    assert "birth" not in out[0]
    assert out[0].get("merge_recommendation") == "needs-review"


def test_quote_or_die_accept_when_check_returns_true() -> None:
    proposal = _proposal_no_match()
    skill = _FakeResult(
        text=json.dumps(
            {
                "outcome": "create_new",
                "new_person": {"name": {"family": "Deursen"}, "birth_year": 1972},
                "confidence": 0.96,
                "evidence_snippet": "Esther van Deursen geboren 1972",
                "evidence_source_url": "https://www.wikidata.org/wiki/Q1",
            }
        )
    )
    runner, _ = _make_runner(skill)
    out, stats = enrich_resolved(
        [proposal],
        runner=runner,
        quote_or_die_check=lambda snippet, url: True,
    )
    assert stats.quote_or_die_rejected == 0
    assert stats.created_new == 1
    assert out[0]["birth"] == {"year": 1972}


def test_budget_cap_stops_after_first_call() -> None:
    proposals = [_proposal_no_match(f"Esther {i}") for i in range(5)]
    skill = _FakeResult(
        text=json.dumps(
            {
                "outcome": "no_match",
                "chosen_person_id": None,
                "confidence": 0.1,
            }
        ),
        cost_usd=0.50,
    )
    runner, calls = _make_runner(skill)
    out, stats = enrich_resolved(proposals, runner=runner, max_cost_usd=0.40)
    # Eén call gemaakt, kostte $0.50 → budget exhausted, rest skipped.
    assert stats.skill_calls == 1
    assert stats.skipped_budget == 4
    assert len(calls) == 1
    assert len(out) == 5


def test_idempotent_on_already_enriched_records() -> None:
    proposal = _proposal_no_match()
    proposal["llm_enrich"] = {"outcome": "no_match", "confidence": 0.1}
    runner, calls = _make_runner(_FakeResult(text="{}"))
    out, stats = enrich_resolved([proposal], runner=runner)
    assert calls == []
    assert stats.candidates == 0
    assert out == [proposal]
    assert stats.recomputed == 0


def test_already_enriched_high_conf_recomputes_merge_without_runner() -> None:
    # Al-ge-enrichte proposal: dure delen overslaan, maar merge_recommendation
    # opnieuw afleiden met de actuele policy. person 0.96 + org veilig +
    # post-creatie => onder de nieuwe policy auto-merge, geen runner-call.
    proposal = {
        "person_name": "Emine Özyenici",
        "llm_enrich": {"outcome": "matched_existing", "confidence": 0.96},
        "resolution_confidence": _conf(person=0.96, org=0.85, post=0.0),
        "resolution_notes": "org: chain_partial_exact; post: not_in_data; person: family_given",
        "propose_post_creation": True,
        "merge_recommendation": "needs-review",
    }
    runner, calls = _make_runner(_FakeResult(text="{}"))
    out, stats = enrich_resolved([proposal], runner=runner)
    assert calls == []  # geen skill-call
    assert stats.recomputed == 1
    assert out[0]["merge_recommendation"] == "auto-merge"
    # Origineel niet gemuteerd (defensieve kopie).
    assert proposal["merge_recommendation"] == "needs-review"


def test_already_enriched_no_match_not_recomputed() -> None:
    # Eerdere no_match (person 0.0) blijft onaangeroerd: geen recompute,
    # geen flip, geen runner-call.
    proposal = {
        "person_name": "Mark Vermeer",
        "llm_enrich": {"outcome": "no_match", "confidence": 0.0},
        "resolution_confidence": _conf(person=0.0, org=0.85, post=0.0),
        "resolution_notes": "org: chain_partial_exact; person: no_match",
        "merge_recommendation": "needs-review",
    }
    runner, calls = _make_runner(_FakeResult(text="{}"))
    out, stats = enrich_resolved([proposal], runner=runner)
    assert calls == []
    assert stats.recomputed == 0
    assert out[0]["merge_recommendation"] == "needs-review"


def test_malformed_skill_output_counts_as_error() -> None:
    proposal = _proposal_no_match()
    skill = _FakeResult(text="not json at all")
    runner, _ = _make_runner(skill)
    out, stats = enrich_resolved([proposal], runner=runner)
    assert stats.skill_errors == 1
    assert "birth" not in out[0]


def test_skill_output_with_markdown_fence_parses() -> None:
    proposal = _proposal_no_match()
    skill = _FakeResult(
        text='```json\n{"outcome":"no_match","confidence":0.2}\n```',
    )
    runner, _ = _make_runner(skill)
    _out, stats = enrich_resolved([proposal], runner=runner)
    assert stats.no_match == 1
    assert stats.skill_errors == 0


def test_year_fill_bucket_is_picked_up() -> None:
    proposal = _proposal_year_fill()
    skill = _FakeResult(
        text=json.dumps(
            {
                "outcome": "matched_existing",
                "chosen_person_id": "person:warffemius-a-1971",
                "confidence": 0.96,
                "evidence_snippet": "...",
                "evidence_source_url": "https://www.wikidata.org/wiki/Q1",
            }
        )
    )
    runner, calls = _make_runner(skill)
    out, stats = enrich_resolved([proposal], runner=runner)
    assert len(calls) == 1, "year_fill bucket should call the skill"
    assert stats.matched_existing == 1
    assert out[0]["resolved_person_id"] == "person:warffemius-a-1971"


def test_rate_limited_call_does_not_apply_changes() -> None:
    proposal = _proposal_no_match()
    skill = _FakeResult(text="{}", rate_limited=True)
    runner, _ = _make_runner(skill)
    out, stats = enrich_resolved([proposal], runner=runner)
    assert stats.rate_limited == 1
    assert stats.created_new == 0
    assert "birth" not in out[0]


def test_runner_exception_is_caught_and_counted() -> None:
    proposal = _proposal_no_match()

    def boom(*_args, **_kwargs):
        raise RuntimeError("network down")

    out, stats = enrich_resolved([proposal], runner=boom)
    assert stats.skill_errors == 1
    assert out[0] == proposal
