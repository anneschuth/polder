"""Tests voor `polder.resolve.llm_enrich`."""

from __future__ import annotations

import json
from dataclasses import dataclass

from polder.resolve.llm_enrich import enrich_resolved


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
    out, stats = enrich_resolved([proposal], runner=runner)
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
