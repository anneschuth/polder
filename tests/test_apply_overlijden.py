"""Tests voor de overlijden-sweep in apply (#32).

Een ABD-overlijdensbericht sluit van rechtswege ALLE lopende mandaten van
de persoon op de overlijdensdatum. Geen post/org-mutatie, idempotent.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from polder.apply import (
    ApplyAction,
    SkippedProposal,
    _plan_close_all_mandates,
    plan_apply,
)

SCHEMAS_DIR = Path(__file__).parent.parent / "schemas"


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


def test_skips_without_public_source_url() -> None:
    path, record = _person_with_mandaten([_mandaat("dg-jenv", end_date=None)])
    proposal = _overlijden_proposal("person:docters-van-leeuwen-a-1945", "2020-08-17")
    del proposal["abd_nieuws_url"]  # geen publieke bron-URL meer

    result = _plan_close_all_mandates(proposal=proposal, personen=[(path, record)], confidence=0.9)

    assert isinstance(result, SkippedProposal)
    assert "geen publieke bron-URL" in result.reasons[0]


# ---------------------------------------------------------------------------
# End-to-end via plan_apply. Dit is de test die de eerste-versie-bug had
# moeten vangen: de overlijden-tak stond ná de `geen role`-guard, dus elk
# overlijden werd geskipt vóór het _plan_close_all_mandates bereikte.
# ---------------------------------------------------------------------------


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)


@pytest.fixture
def mini_polder(tmp_path: Path) -> Path:
    root = tmp_path
    (root / "data" / "posten").mkdir(parents=True)
    schemas_target = root / "schemas"
    schemas_target.mkdir()
    for s in SCHEMAS_DIR.glob("*.schema.json"):
        shutil.copy(s, schemas_target / s.name)
    _write_yaml(
        root / "data" / "organisaties" / "ministeries" / "jenv.yaml",
        {
            "id": "org:min-jenv",
            "type": "ministerie",
            "classification": "ministerie",
            "parent_id": None,
            "names": [{"value": "Justitie en Veiligheid", "valid_from": "2010-10-14"}],
            "valid_from": "2010-10-14",
            "sources": [{"id": "roo", "url": "https://example.org/roo", "retrieved": "2026-05-09"}],
        },
    )
    _write_yaml(
        root / "data" / "personen" / "docters-van-leeuwen-a-1945.yaml",
        {
            "id": "person:docters-van-leeuwen-a-1945",
            "name": {"full": "Arthur Docters van Leeuwen", "family": "Docters van Leeuwen"},
            "birth": {"year": 1945},
            "mandaten": [
                {
                    "id": "mandate-dg-jenv-2010-01-01",
                    "organization_id": "org:min-jenv",
                    "post_id": "post:dg-min-jenv",
                    "role": "directeur-generaal",
                    "start_date": "2010-01-01",
                    "sources": [
                        {"id": "roo", "url": "https://example.org/roo", "retrieved": "2020-01-01"}
                    ],
                }
            ],
            "sources": [{"id": "roo", "url": "https://example.org/roo", "retrieved": "2020-01-01"}],
        },
    )
    return root


def test_plan_apply_routes_overlijden_to_close_sweep(mini_polder: Path) -> None:
    """De integratietest: een overlijden-proposal zonder role/post_id mag
    NIET op de `geen role`-guard sneuvelen, maar moet een close-mandaat
    action opleveren."""
    data = mini_polder / "data"
    proposal = {
        "person_name": "Arthur Docters van Leeuwen",
        "event_type": "overlijden",
        "end_date": "2020-08-17",
        "post_id": None,
        "organization_id": None,
        "resolved_person_id": "person:docters-van-leeuwen-a-1945",
        "abd_nieuws_url": "https://example.org/in-memoriam",
        "confidence": 0.9,
        "merge_recommendation": "auto-merge",
        "merge_reason": "overlijden_person_strong",
    }

    actions, skipped = plan_apply([proposal], data)

    assert len(actions) == 1, f"verwacht 1 close-actie, kreeg skips: {[s.reasons for s in skipped]}"
    assert actions[0].type == "close-mandaat"
    assert actions[0].record["mandaten"][0]["end_date"] == "2020-08-17"
    assert skipped == []
