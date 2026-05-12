"""Tests voor `resolve_proposal`: complete proposal-naar-resolved-mapping."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def polder_index():
    from polder.resolve.matcher import PolderIndex

    return PolderIndex.load(Path("data"))


def test_resolve_proposal_full_match(polder_index) -> None:
    """Een proposal met bestaande org + post + persoon krijgt alle resolved-velden."""
    from polder.resolve.proposal import resolve_proposal

    proposal = {
        "person_name": "Mark Rutte",
        "organization_id": "org:min-az",
        "post_id": "post:minister-president-min-az",
        "role": "minister-president",
        "start_date": "2010-10-14",
        "end_date": "2024-07-02",
        "event_type": "benoeming",
        "abd_nieuws_url": "https://example.org/x",
        "confidence": 0.95,
    }
    result = resolve_proposal(proposal, polder_index)

    assert result["resolved_organization_id"] == "org:min-az"
    assert result["resolved_post_id"] == "post:minister-president-min-az"
    assert result["resolved_person_id"] == "person:rutte-m-1967"
    assert result["resolution_confidence"]["organization"] >= 0.95
    assert result["resolution_confidence"]["post"] >= 0.85
    assert result["resolution_confidence"]["person"] >= 0.85
    assert result["merge_recommendation"] == "auto-merge"


def test_resolve_proposal_unknown_org(polder_index) -> None:
    """Onbekende org krijgt None + lage confidence."""
    from polder.resolve.proposal import resolve_proposal

    proposal = {
        "person_name": "Mark Rutte",
        "organization_id": "org:bestaat-niet",
        "post_id": "post:minister-president-min-az",
    }
    result = resolve_proposal(proposal, polder_index)
    assert result["resolved_organization_id"] is None
    assert result["resolution_confidence"]["organization"] == 0.0
    assert result["merge_recommendation"] == "needs-review"


def test_resolve_proposal_org_from_chain(polder_index) -> None:
    """Als `organization_id` onbekend is maar chain[-1].slug_proposal bekend, gebruik die."""
    from polder.resolve.proposal import resolve_proposal

    proposal = {
        "person_name": "Mark Rutte",
        "organization_id": "org:fictieve-onderdeel",
        "organization_chain": [
            {"level": "ministerie", "slug_proposal": "org:min-az"},
        ],
        "post_id": "post:minister-president-min-az",
    }
    result = resolve_proposal(proposal, polder_index)
    assert result["resolved_organization_id"] == "org:min-az"
    assert result["resolved_organization_level"] == "ministerie"


def test_resolve_proposal_no_event(polder_index) -> None:
    """`event_type: geen_benoeming` levert skip-recommendation op."""
    from polder.resolve.proposal import resolve_proposal

    proposal = {
        "event_type": "geen_benoeming",
        "person_name": None,
    }
    result = resolve_proposal(proposal, polder_index)
    assert result["merge_recommendation"] == "skip"
    assert result["resolved_person_id"] is None


def test_resolve_proposal_ambiguous_person(polder_index) -> None:
    """Ambigue persoon-match → needs-review, met candidates in notes."""
    from polder.resolve.proposal import resolve_proposal

    proposal = {
        "person_name": "drs. H.W.M. Schoof",
        "organization_id": "org:min-az",
        "post_id": "post:minister-president-min-az",
    }
    result = resolve_proposal(proposal, polder_index)
    assert result["resolved_person_id"] is None
    assert result["merge_recommendation"] == "needs-review"
    assert "ambiguous" in (result.get("resolution_notes") or "")


def test_resolve_proposal_preserves_original_fields(polder_index) -> None:
    """De oorspronkelijke proposal-velden blijven behouden in de output."""
    from polder.resolve.proposal import resolve_proposal

    proposal = {
        "person_name": "Mark Rutte",
        "organization_id": "org:min-az",
        "post_id": "post:minister-president-min-az",
        "role": "minister-president",
        "start_date": "2010-10-14",
        "event_type": "benoeming",
        "evidence_snippet": "Rutte wordt minister-president.",
        "confidence": 0.95,
        "source_identifier": "test-source",
    }
    result = resolve_proposal(proposal, polder_index)
    # Oorspronkelijke velden zijn behouden
    assert result["role"] == "minister-president"
    assert result["start_date"] == "2010-10-14"
    assert result["evidence_snippet"] == "Rutte wordt minister-president."
    assert result["source_identifier"] == "test-source"


def test_resolve_proposal_org_via_name_alias(polder_index) -> None:
    """Een verbose org-naam `org:ministerie-van-financien` matched op `org:min-fin`."""
    from polder.resolve.proposal import resolve_proposal

    proposal = {
        "person_name": "Mark Rutte",
        "organization_id": "org:ministerie-van-financien",
        "post_id": "post:minister-min-fin",
    }
    result = resolve_proposal(proposal, polder_index)
    assert result["resolved_organization_id"] == "org:min-fin"
    assert "alias" in (result.get("resolution_notes") or "")


def test_resolve_proposal_org_abbr_alias(polder_index) -> None:
    """De afkortings-vorm `org:bzk` matched op `org:min-bzk`."""
    from polder.resolve.proposal import resolve_proposal

    proposal = {
        "person_name": "Mark Rutte",
        "organization_id": "org:bzk",
    }
    result = resolve_proposal(proposal, polder_index)
    assert result["resolved_organization_id"] == "org:min-bzk"


def test_resolve_proposal_post_not_in_data(polder_index) -> None:
    """Onbekende post-id zonder bruikbare role: resolved=None, propose_post_creation=True."""
    from polder.resolve.proposal import resolve_proposal

    proposal = {
        "person_name": "Mark Rutte",
        "organization_id": "org:min-az",
        "post_id": "post:bestaat-niet",
        "role": "iets onduidelijks zonder ABD-keyword",
    }
    result = resolve_proposal(proposal, polder_index)
    assert result["resolved_post_id"] is None
    assert result["propose_post_creation"] is True


def test_resolve_proposal_post_creatable_from_role(polder_index) -> None:
    """Onbekende post-id mét classifiable role: post-confidence 0.85, auto-merge mogelijk."""
    from polder.resolve.proposal import resolve_proposal

    proposal = {
        "person_name": "Mark Rutte",
        "organization_id": "org:min-az",
        "post_id": "post:nieuwe-directeur-bij-az",
        "role": "directeur Bedrijfsvoering",
        "confidence": 0.95,
    }
    result = resolve_proposal(proposal, polder_index)
    assert result["resolution_confidence"]["post"] == 0.85
    assert result["resolved_post_id"] == "post:nieuwe-directeur-bij-az"
    # Pas als persoon ook hoog scoort wordt het auto-merge; Rutte is matchbaar.
    assert "creatable_from_role" in (result.get("resolution_notes") or "")
