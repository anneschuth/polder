"""Tests voor de overlijden-sweep in apply (#32).

Een ABD-overlijdensbericht sluit van rechtswege ALLE lopende mandaten van
de persoon op de overlijdensdatum. Geen post/org-mutatie, idempotent.
"""

from __future__ import annotations

from pathlib import Path

from polder.apply import (
    ApplyAction,
    SkippedProposal,
    _plan_close_all_mandates,
)


def _person_with_mandaten(mandaten: list[dict]) -> tuple[Path, dict]:
    record = {
        "id": "person:docters-van-leeuwen-a-1945",
        "name": {"full": "Arthur Docters van Leeuwen", "family": "Docters van Leeuwen"},
        "mandaten": mandaten,
        "sources": [
            {"id": "abd_nieuws", "url": "https://example.org/x", "retrieved": "2026-05-16"}
        ],
    }
    return Path("data/personen/docters-van-leeuwen-a-1945.yaml"), record


def _mandaat(post: str, *, end_date: str | None) -> dict:
    return {
        "id": f"mandate-{post}-2010-01-01",
        "organization_id": "org:min-jenv",
        "post_id": f"post:{post}",
        "role": post,
        "start_date": "2010-01-01",
        "end_date": end_date,
        "sources": [{"id": "roo", "url": "https://example.org/roo", "retrieved": "2020-01-01"}],
    }


def _overlijden_proposal(person_id: str | None, end_date: str | None) -> dict:
    return {
        "person_name": "Arthur Docters van Leeuwen",
        "event_type": "overlijden",
        "end_date": end_date,
        "resolved_person_id": person_id,
        "abd_nieuws_url": "https://example.org/in-memoriam",
        "confidence": 0.9,
    }


def test_closes_all_open_mandates_on_death_date() -> None:
    path, record = _person_with_mandaten(
        [
            _mandaat("dg-jenv", end_date=None),
            _mandaat("voorzitter-afm", end_date=None),
            _mandaat("sg-min-jenv", end_date="2005-06-30"),  # al gesloten
        ]
    )
    proposal = _overlijden_proposal("person:docters-van-leeuwen-a-1945", "2020-08-17")

    action = _plan_close_all_mandates(proposal=proposal, personen=[(path, record)], confidence=0.9)

    assert isinstance(action, ApplyAction)
    assert action.type == "close-mandaat"
    mandaten = action.record["mandaten"]
    assert mandaten[0]["end_date"] == "2020-08-17"
    assert mandaten[1]["end_date"] == "2020-08-17"
    # Reeds gesloten mandaat blijft ongemoeid.
    assert mandaten[2]["end_date"] == "2005-06-30"
    # Bron toegevoegd aan de nu-gesloten mandaten.
    assert any(
        s.get("fields") == ["end_date", "applied_via:apply-staging:overlijden"]
        for s in mandaten[0]["sources"]
    )


def test_idempotent_when_no_open_mandates() -> None:
    path, record = _person_with_mandaten([_mandaat("dg-jenv", end_date="2020-08-17")])
    proposal = _overlijden_proposal("person:docters-van-leeuwen-a-1945", "2020-08-17")

    result = _plan_close_all_mandates(proposal=proposal, personen=[(path, record)], confidence=0.9)

    assert isinstance(result, SkippedProposal)
    assert "geen lopend mandaat" in result.reasons[0]


def test_skips_without_resolved_person_id() -> None:
    path, record = _person_with_mandaten([_mandaat("dg-jenv", end_date=None)])
    proposal = _overlijden_proposal(None, "2020-08-17")

    result = _plan_close_all_mandates(proposal=proposal, personen=[(path, record)], confidence=0.9)

    assert isinstance(result, SkippedProposal)
    assert "geen resolved_person_id" in result.reasons[0]


def test_skips_without_end_date() -> None:
    path, record = _person_with_mandaten([_mandaat("dg-jenv", end_date=None)])
    proposal = _overlijden_proposal("person:docters-van-leeuwen-a-1945", None)

    result = _plan_close_all_mandates(proposal=proposal, personen=[(path, record)], confidence=0.9)

    assert isinstance(result, SkippedProposal)
    assert "geen end_date" in result.reasons[0]


def test_skips_when_person_not_found() -> None:
    path, record = _person_with_mandaten([_mandaat("dg-jenv", end_date=None)])
    proposal = _overlijden_proposal("person:bestaat-niet-1900", "2020-08-17")

    result = _plan_close_all_mandates(proposal=proposal, personen=[(path, record)], confidence=0.9)

    assert isinstance(result, SkippedProposal)
    assert "niet gevonden" in result.reasons[0]


def test_implausible_death_date_skipped() -> None:
    path, record = _person_with_mandaten([_mandaat("dg-jenv", end_date=None)])
    proposal = _overlijden_proposal("person:docters-van-leeuwen-a-1945", "1700-01-01")

    result = _plan_close_all_mandates(proposal=proposal, personen=[(path, record)], confidence=0.9)

    assert isinstance(result, SkippedProposal)
    assert "buiten redelijk bereik" in result.reasons[0]
