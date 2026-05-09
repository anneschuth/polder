"""Tests voor `polder apply-staging` (CLI + apply.py)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from polder.apply import (
    execute_apply,
    load_resolved_input,
    plan_apply,
)
from polder.cli.main import app

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = REPO_ROOT / "schemas"


# ---------------------------------------------------------------------------
# Fixtures: mini-polder-tree zonder _staging
# ---------------------------------------------------------------------------


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


@pytest.fixture
def mini_polder(tmp_path: Path) -> Path:
    """Mini-polder met BZK + directie Digitale Samenleving al aanwezig."""
    root = tmp_path
    (root / "data" / "personen").mkdir(parents=True)
    (root / "data" / "posten").mkdir(parents=True)
    schemas_target = root / "schemas"
    schemas_target.mkdir()
    for s in SCHEMAS_DIR.glob("*.schema.json"):
        shutil.copy(s, schemas_target / s.name)

    _write_yaml(
        root / "data" / "organisaties" / "ministeries" / "bzk.yaml",
        {
            "id": "org:min-bzk",
            "type": "ministerie",
            "classification": "ministerie",
            "parent_id": None,
            "names": [
                {"value": "Binnenlandse Zaken en Koninkrijksrelaties", "valid_from": "2010-10-14"}
            ],
            "valid_from": "2010-10-14",
            "sources": [
                {"id": "roo", "url": "https://example.org/roo", "retrieved": "2026-05-09"}
            ],
        },
    )
    _write_yaml(
        root
        / "data"
        / "organisaties"
        / "organisatieonderdelen"
        / "directie-digitale-samenleving.yaml",
        {
            "id": "org:onderdeel-directie-digitale-samenleving",
            "type": "organisatieonderdeel",
            "classification": "organisatieonderdeel",
            "parent_id": "org:min-bzk",
            "names": [
                {"value": "Directie Digitale Samenleving", "valid_from": "2020-01-01"}
            ],
            "valid_from": "2020-01-01",
            "valid_until": None,
            "sources": [
                {
                    "id": "roo",
                    "url": "https://organisaties.overheid.nl/96964/",
                    "retrieved": "2026-05-09",
                }
            ],
        },
    )
    return root


def _kewal_proposal() -> dict[str, Any]:
    """Een resolver-output dict gebaseerd op de echte Kewal-fixture maar met
    confidence 0.86 zodat hij door de drempel komt."""
    return {
        "person_name": "Suzie Kewal",
        "existing_person_id": None,
        "organization_id": "org:onderdeel-afdeling-ai-algoritmen-data-digitale-inclusie-directie-digitale-samenleving",
        "organization_chain": [
            {
                "level": "ministerie",
                "name": "ministerie van Binnenlandse Zaken en Koninkrijksrelaties",
                "slug_proposal": "org:min-bzk",
            },
            {
                "level": "directie",
                "name": "directie Digitale Samenleving",
                "slug_proposal": "org:onderdeel-directie-digitale-samenleving",
            },
            {
                "level": "afdeling",
                "name": "afdeling AI, Algoritmen, Data en Digitale Inclusie",
                "slug_proposal": "org:onderdeel-afdeling-ai-algoritmen-data-digitale-inclusie-directie-digitale-samenleving",
            },
        ],
        "post_id": "post:afdelingshoofd-ai-algoritmen-data-digitale-inclusie-directie-digitale-samenleving",
        "role": "afdelingshoofd met aandachtsgebieden AI, Algoritmen, Data en Digitale Inclusie, directie Digitale Samenleving, ministerie van Binnenlandse Zaken en Koninkrijksrelaties",
        "start_date": "2024-01-01",
        "end_date": None,
        "decision_reference": "ABD-nieuwsbericht 2023-11-27",
        "staatscourant_url": None,
        "abd_nieuws_url": "https://www.algemenebestuursdienst.nl/actueel/nieuws/2023/11/27/suzie-kewal-afdelingshoofd-met-aandachtsgebieden-ai-algoritmen-data-en-digitale-inclusie-bij-bzk",
        "event_type": "benoeming",
        "confidence": 0.86,
        "evidence_snippet": "Suzie Kewal start op 1 januari 2024.",
        "resolved_organization_id": "org:onderdeel-directie-digitale-samenleving",
        "resolved_organization_level": "directie",
        "resolved_post_id": None,
        "resolved_person_id": None,
        "resolution_confidence": {"organization": 0.85, "post": 0.0, "person": 0.0},
        "resolution_notes": "Afdeling niet gevonden, geklommen.",
        "propose_post_creation": True,
        "merge_recommendation": "needs-review",
        "birth_year": 1975,
    }


# ---------------------------------------------------------------------------
# Plan-apply tests
# ---------------------------------------------------------------------------


def test_plan_dry_run_kewal(mini_polder: Path) -> None:
    actions, skipped = plan_apply([_kewal_proposal()], mini_polder / "data")
    types = [a.type for a in actions]
    assert types == ["create-org", "create-post", "create-person"], (types, skipped)
    assert skipped == []

    # De afdeling moet als parent de directie hebben.
    org_action = actions[0]
    assert org_action.record["parent_id"] == "org:onderdeel-directie-digitale-samenleving"
    assert (
        org_action.record["id"]
        == "org:onderdeel-afdeling-ai-algoritmen-data-digitale-inclusie-directie-digitale-samenleving"
    )

    # Post: classification afdelingshoofd.
    post_action = actions[1]
    assert post_action.record["classification"] == "abd-afdelingshoofd"
    assert post_action.record["organization_id"] == org_action.record["id"]

    # Persoon: 1 inline mandaat, source applied_via:apply-staging.
    person_action = actions[2]
    assert person_action.record["id"].startswith("person:kewal-s")
    assert len(person_action.record["mandaten"]) == 1
    fields = person_action.record["sources"][0]["fields"]
    assert "applied_via:apply-staging" in fields


def test_skip_low_confidence(mini_polder: Path) -> None:
    p = _kewal_proposal()
    p["confidence"] = 0.80
    _, skipped = plan_apply([p], mini_polder / "data")
    assert len(skipped) == 1
    assert any("drempel" in r for r in skipped[0].reasons)


def test_skip_red_avg(mini_polder: Path) -> None:
    p = _kewal_proposal()
    p["role"] = "beleidsmedewerker bij directie Digitale Samenleving"
    actions, skipped = plan_apply([p], mini_polder / "data")
    assert actions == []
    assert any("AVG" in r for r in skipped[0].reasons)


def test_skip_person_when_no_birthyear_and_conflict(mini_polder: Path) -> None:
    # Bestaande persoon met dezelfde familienaam toevoegen.
    _write_yaml(
        mini_polder / "data" / "personen" / "kewal-x.yaml",
        {
            "id": "person:kewal-x-1900",
            "name": {"full": "Andere Kewal", "family": "Kewal"},
            "sources": [
                {"id": "test", "url": "https://example.org/x", "retrieved": "2026-05-09"}
            ],
        },
    )
    p = _kewal_proposal()
    p.pop("birth_year", None)
    actions, skipped = plan_apply([p], mini_polder / "data")
    types = [a.type for a in actions]
    # Org + post moet door, persoon moet skip.
    assert "create-person" not in types
    assert any("kandidaat" in r or "geboortejaar" in r for s in skipped for r in s.reasons)


def test_only_high_confidence_filter(mini_polder: Path) -> None:
    p = _kewal_proposal()
    p["confidence"] = 0.90
    actions, skipped = plan_apply(
        [p], mini_polder / "data", only_high_confidence=True
    )
    assert actions == []
    assert skipped


def test_skip_persons_flag(mini_polder: Path) -> None:
    actions, _skipped = plan_apply(
        [_kewal_proposal()], mini_polder / "data", skip_persons=True
    )
    types = [a.type for a in actions]
    assert "create-person" not in types
    assert "create-org" in types
    assert "create-post" in types


def test_idempotent_second_run(mini_polder: Path) -> None:
    """Na een eerste run die alles aanmaakt, is een tweede run leeg."""
    actions, _ = plan_apply([_kewal_proposal()], mini_polder / "data")
    written = execute_apply(actions, mini_polder / "data")
    assert written == 3
    actions2, _skipped2 = plan_apply([_kewal_proposal()], mini_polder / "data")
    # Alle org/post bestaan nu, en de persoon ook (slug gelijk omdat birth_year fixed).
    # We verwachten alleen een 'append-mandaat' OF zelfs niets als mandaat al
    # bestaat. In onze fixture is de mandaat na execute identiek, dus 0.
    create_actions = [a for a in actions2 if a.type.startswith("create-")]
    assert create_actions == []


def test_append_mandaat_to_existing_resolved_person(mini_polder: Path) -> None:
    _write_yaml(
        mini_polder / "data" / "personen" / "kewal-s-1975.yaml",
        {
            "id": "person:kewal-s-1975",
            "name": {"full": "Suzie Kewal", "family": "Kewal", "given": "Suzie"},
            "birth": {"year": 1975},
            "mandaten": [],
            "sources": [
                {
                    "id": "abd",
                    "url": "https://example.org/abd",
                    "retrieved": "2026-05-09",
                }
            ],
        },
    )
    p = _kewal_proposal()
    p["resolved_person_id"] = "person:kewal-s-1975"
    actions, _skipped = plan_apply([p], mini_polder / "data")
    types = [a.type for a in actions]
    assert "append-mandaat" in types
    append = next(a for a in actions if a.type == "append-mandaat")
    assert len(append.record["mandaten"]) == 1
    assert append.record["mandaten"][0]["post_id"] == p["post_id"]


# ---------------------------------------------------------------------------
# Execute + validate
# ---------------------------------------------------------------------------


def test_execute_writes_yaml_validate_clean(mini_polder: Path) -> None:
    actions, _ = plan_apply([_kewal_proposal()], mini_polder / "data")
    written = execute_apply(actions, mini_polder / "data")
    assert written == 3

    # Verifieer dat alle target-paden bestaan en valid YAML zijn.
    for a in actions:
        assert a.target_path.exists(), a.target_path
        loaded = yaml.safe_load(a.target_path.read_text())
        assert isinstance(loaded, dict)
        assert loaded["id"]

    # Run validate op de data.
    from polder.validate import run_all_checks

    issues = run_all_checks(mini_polder / "data", mini_polder / "schemas")
    errors = [i for i in issues if i.severity == "error"]
    assert errors == [], errors


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


def _write_resolved(path: Path, proposals: list[dict]) -> None:
    path.write_text(json.dumps(proposals), encoding="utf-8")


def test_cli_dry_run(mini_polder: Path) -> None:
    staging = mini_polder / "data" / "_staging"
    staging.mkdir(parents=True, exist_ok=True)
    resolved = staging / "abd-nieuws-kewal.resolved.json"
    _write_resolved(resolved, [_kewal_proposal()])

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "apply-staging",
            str(resolved),
            "--data",
            str(mini_polder / "data"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Zou aanmaken" in result.output
    assert "create-org" in result.output
    assert "create-post" in result.output
    assert "create-person" in result.output
    assert "Run met --apply" in result.output


def test_cli_apply_writes_files(mini_polder: Path) -> None:
    staging = mini_polder / "data" / "_staging"
    staging.mkdir(parents=True, exist_ok=True)
    resolved = staging / "abd-nieuws-kewal.resolved.json"
    _write_resolved(resolved, [_kewal_proposal()])

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "apply-staging",
            str(resolved),
            "--apply",
            "--data",
            str(mini_polder / "data"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Geschreven: 3 files" in result.output


def test_load_resolved_input_directory(tmp_path: Path) -> None:
    a = tmp_path / "x.resolved.json"
    b = tmp_path / "y.resolved.json"
    a.write_text(json.dumps([{"person_name": "A"}]), encoding="utf-8")
    b.write_text(json.dumps([{"person_name": "B"}]), encoding="utf-8")
    items = load_resolved_input(tmp_path)
    names = sorted(i["person_name"] for i in items)
    assert names == ["A", "B"]
    # Source-filename meta moet aanwezig zijn.
    assert all("_source_filename" in i for i in items)
