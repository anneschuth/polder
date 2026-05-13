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
            "sources": [{"id": "roo", "url": "https://example.org/roo", "retrieved": "2026-05-09"}],
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
            "names": [{"value": "Directie Digitale Samenleving", "valid_from": "2020-01-01"}],
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
        "resolution_confidence": {"organization": 0.85, "post": 0.85, "person": 0.85},
        "resolution_notes": "Afdeling niet gevonden, geklommen.",
        "propose_post_creation": True,
        "merge_recommendation": "auto-merge",
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


def test_create_person_even_with_familyname_collision(mini_polder: Path) -> None:
    """Familienaam-collision blokkeert geen create-person meer.

    _person_slug bevat een hash-suffix uit de full-name, dus twee personen
    met dezelfde familienaam krijgen verschillende slugs. Alleen exact-
    slug-collision blokkeert. Dit is een bewuste loosening voor ABD-records
    waar veel namen niet op Wikidata staan maar wel via een gezaghebbend
    KB benoemd worden.
    """
    _write_yaml(
        mini_polder / "data" / "personen" / "kewal-x.yaml",
        {
            "id": "person:kewal-x-1900",
            "name": {"full": "Andere Kewal", "family": "Kewal"},
            "sources": [{"id": "test", "url": "https://example.org/x", "retrieved": "2026-05-09"}],
        },
    )
    p = _kewal_proposal()
    p.pop("birth_year", None)
    actions, _ = plan_apply([p], mini_polder / "data")
    types = [a.type for a in actions]
    assert "create-person" in types


def test_only_high_confidence_filter(mini_polder: Path) -> None:
    p = _kewal_proposal()
    p["confidence"] = 0.90
    actions, skipped = plan_apply([p], mini_polder / "data", only_high_confidence=True)
    assert actions == []
    assert skipped


def test_skip_persons_flag(mini_polder: Path) -> None:
    actions, _skipped = plan_apply([_kewal_proposal()], mini_polder / "data", skip_persons=True)
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


def test_dedup_exact_mandaat_skipped(mini_polder: Path) -> None:
    """Re-applying an identical proposal must not append a duplicate mandaat."""
    _write_yaml(
        mini_polder / "data" / "personen" / "kewal-s-1975.yaml",
        {
            "id": "person:kewal-s-1975",
            "name": {"full": "Suzie Kewal", "family": "Kewal", "given": "Suzie"},
            "birth": {"year": 1975},
            "mandaten": [
                {
                    "id": "existing-mandate",
                    "organization_id": "org:onderdeel-directie-digitale-samenleving",
                    "post_id": "post:p1",
                    "role": "afdelingshoofd test",
                    "start_date": "2020-01-01",
                    "end_date": "2024-01-01",
                    "sources": [
                        {
                            "id": "abd_nieuws",
                            "url": "https://example.org/abd",
                            "retrieved": "2026-05-09",
                        }
                    ],
                }
            ],
            "sources": [
                {
                    "id": "abd_nieuws",
                    "url": "https://example.org/abd",
                    "retrieved": "2026-05-09",
                }
            ],
        },
    )
    p = _kewal_proposal()
    p["resolved_person_id"] = "person:kewal-s-1975"
    p["post_id"] = "post:p1"
    p["organization_id"] = "org:onderdeel-directie-digitale-samenleving"
    p["resolved_organization_id"] = "org:onderdeel-directie-digitale-samenleving"
    p["organization_chain"] = []
    p["start_date"] = "2020-01-01"
    p["end_date"] = "2024-01-01"
    p["role"] = "afdelingshoofd test"

    actions, skipped = plan_apply([p], mini_polder / "data")
    assert all(a.type != "append-mandaat" for a in actions)
    assert any("idempotent" in r for s in skipped for r in s.reasons), skipped


def test_skip_invalid_date_order_existing_person(mini_polder: Path) -> None:
    """Append-target met start_date > end_date wordt geweigerd."""
    _write_yaml(
        mini_polder / "data" / "personen" / "kewal-s-1975.yaml",
        {
            "id": "person:kewal-s-1975",
            "name": {"full": "Suzie Kewal", "family": "Kewal", "given": "Suzie"},
            "birth": {"year": 1975},
            "mandaten": [],
            "sources": [
                {
                    "id": "abd_nieuws",
                    "url": "https://example.org/abd",
                    "retrieved": "2026-05-09",
                }
            ],
        },
    )
    p = _kewal_proposal()
    p["resolved_person_id"] = "person:kewal-s-1975"
    p["start_date"] = "2026-01-01"
    p["end_date"] = "2024-01-01"

    actions, skipped = plan_apply([p], mini_polder / "data")
    assert all(a.type != "append-mandaat" for a in actions)
    assert any("ongeldige datum-volgorde" in r for s in skipped for r in s.reasons), skipped


def test_skip_invalid_date_order_new_person(mini_polder: Path) -> None:
    """Nieuwe persoon met start > end: skip in plaats van persoon-zonder-mandaat."""
    p = _kewal_proposal()
    p["start_date"] = "2030-01-01"
    p["end_date"] = "2025-01-01"

    actions, skipped = plan_apply([p], mini_polder / "data")
    assert all(a.type != "create-person" for a in actions)
    assert any("ongeldige datum-volgorde" in r for s in skipped for r in s.reasons), skipped


def test_skip_implausible_date(mini_polder: Path) -> None:
    """Datums >5 jaar in de toekomst of <1798 worden geweigerd."""
    p = _kewal_proposal()
    p["start_date"] = "1750-01-01"
    p["end_date"] = None
    actions, skipped = plan_apply([p], mini_polder / "data")
    assert all(a.type != "create-person" for a in actions)
    assert any("redelijk bereik" in r for s in skipped for r in s.reasons), skipped


def test_fuzzy_duplicate_writes_with_warning(mini_polder: Path) -> None:
    """Datums binnen 7 dagen van bestaand mandaat: append met warning, niet skip."""
    _write_yaml(
        mini_polder / "data" / "personen" / "kewal-s-1975.yaml",
        {
            "id": "person:kewal-s-1975",
            "name": {"full": "Suzie Kewal", "family": "Kewal", "given": "Suzie"},
            "birth": {"year": 1975},
            "mandaten": [
                {
                    "id": "existing-mandate",
                    "organization_id": "org:onderdeel-directie-digitale-samenleving",
                    "post_id": "post:p1",
                    "role": "afdelingshoofd test",
                    "start_date": "2020-01-01",
                    "end_date": "2024-01-01",
                    "sources": [
                        {
                            "id": "abd_nieuws",
                            "url": "https://example.org/abd",
                            "retrieved": "2026-05-09",
                        }
                    ],
                }
            ],
            "sources": [
                {
                    "id": "abd_nieuws",
                    "url": "https://example.org/abd",
                    "retrieved": "2026-05-09",
                }
            ],
        },
    )
    p = _kewal_proposal()
    p["resolved_person_id"] = "person:kewal-s-1975"
    p["post_id"] = "post:p1"
    p["organization_id"] = "org:onderdeel-directie-digitale-samenleving"
    p["resolved_organization_id"] = "org:onderdeel-directie-digitale-samenleving"
    p["organization_chain"] = []
    p["start_date"] = "2020-01-05"
    p["end_date"] = "2024-01-05"
    p["role"] = "afdelingshoofd test variant"

    actions, _skipped = plan_apply([p], mini_polder / "data")
    appends = [a for a in actions if a.type == "append-mandaat"]
    assert len(appends) == 1
    assert any("fuzzy-duplicaat" in r for r in appends[0].reasons), appends[0].reasons


def test_competing_proposals_keep_highest_confidence(mini_polder: Path) -> None:
    """Twee proposals voor dezelfde (post_id, person, start_date): houd hoogste."""
    p_low = _kewal_proposal()
    p_low["confidence"] = 0.86

    p_high = _kewal_proposal()
    p_high["confidence"] = 0.95

    actions, skipped = plan_apply([p_low, p_high], mini_polder / "data")
    create_persons = [a for a in actions if a.type == "create-person"]
    assert len(create_persons) == 1
    assert create_persons[0].confidence == 0.95
    assert any("concurrerende proposal" in r for s in skipped for r in s.reasons), skipped


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


# ---------------------------------------------------------------------------
# Regression: contract met resolver
# ---------------------------------------------------------------------------


def test_skip_when_merge_recommendation_needs_review(mini_polder: Path) -> None:
    """`needs-review` of `skip` mag nooit door apply heen, ook bij high confidence."""
    p = _kewal_proposal()
    p["merge_recommendation"] = "needs-review"
    p["confidence"] = 0.99  # high proposal-confidence mag dit niet overrulen
    actions, skipped = plan_apply([p], mini_polder / "data")
    assert actions == []
    assert any("needs-review" in r for s in skipped for r in s.reasons)


def test_skip_when_merge_recommendation_skip(mini_polder: Path) -> None:
    p = _kewal_proposal()
    p["merge_recommendation"] = "skip"
    actions, skipped = plan_apply([p], mini_polder / "data")
    assert actions == []
    assert any("skip" in r for s in skipped for r in s.reasons)


def test_combined_function_two_mandaten_one_person(mini_polder: Path) -> None:
    """Twee proposals voor dezelfde nieuwe persoon (gecombineerde functie) → 1 record met 2 mandaten."""
    # Eerste proposal: directeur bij BZK
    p1 = _kewal_proposal()
    p1["person_name"] = "Esther Wijker"
    p1["organization_id"] = "org:onderdeel-directie-digitale-samenleving"
    p1["organization_chain"] = []
    p1["post_id"] = "post:directeur-x-min-bzk"
    p1["role"] = "directeur Bestuursadvisering bij BZK"
    p1["birth_year"] = 1957

    # Tweede proposal: zelfde persoon, ander ministerie + andere post
    p2 = dict(p1)
    p2["post_id"] = "post:directeur-x-min-vro"
    p2["role"] = "directeur Bestuursadvisering bij VRO"

    actions, _ = plan_apply([p1, p2], mini_polder / "data")
    create_persons = [a for a in actions if a.type == "create-person"]
    assert len(create_persons) == 1, "verwacht één create-person, niet twee"
    mandaten = create_persons[0].record["mandaten"]
    assert len(mandaten) == 2, mandaten
    post_ids = {m["post_id"] for m in mandaten}
    assert post_ids == {"post:directeur-x-min-bzk", "post:directeur-x-min-vro"}


def test_classification_word_boundary() -> None:
    """`ministerie` mag NIET als `minister` worden geclassificeerd."""
    from polder.apply import _classification_from_role

    # CISO bij een ministerie is geen bewindspersoon
    assert (
        _classification_from_role(
            "Chief Information Security Officer (CISO) Rijk, ministerie van BZK"
        )
        is None
    )
    # minister-president blijft bewindspersoon
    assert _classification_from_role("minister-president") == "bewindspersoon"
    # directeur-generaal blijft abd-tmg
    assert _classification_from_role("directeur-generaal Mobiliteit") == "abd-tmg"
    # afdelingshoofd binnen een ministerie blijft afdelingshoofd
    assert (
        _classification_from_role("afdelingshoofd Bedrijfsvoering, ministerie X")
        == "abd-afdelingshoofd"
    )


def test_chain_unknown_ministerie_skipped(mini_polder: Path) -> None:
    """Een chain met een onbekende ministerie-entry mag NIET tot create-org leiden.

    Twee checks kunnen 'm vangen: de chain[-1]/organization_id-mismatch (als
    de proposal een ander org_id heeft) of de explicit onbekend-ministerie-
    check. Beide zijn acceptabel.
    """
    p = _kewal_proposal()
    p["organization_id"] = "org:onderdeel-directie-x"
    p["organization_chain"] = [
        {
            "level": "ministerie",
            "name": "Ministerie van Bestaat-Niet",
            "slug_proposal": "org:min-bestaat-niet",
        },
        {
            "level": "directie",
            "name": "Directie X",
            "slug_proposal": "org:onderdeel-directie-x",
        },
    ]
    actions, skipped = plan_apply([p], mini_polder / "data")
    assert actions == []
    reasons = [r for s in skipped for r in s.reasons]
    assert any("ministerie" in r and "niet bekend" in r for r in reasons) or any(
        "mismatcht" in r for r in reasons
    )


def test_chain_name_implies_wrong_parent_skipped(mini_polder: Path) -> None:
    """Een chain die `Belastingdienst` onder BZK plaatst — terwijl die onder Financiën valt — wordt geweigerd."""
    _write_yaml(
        mini_polder / "data" / "organisaties" / "ministeries" / "fin.yaml",
        {
            "id": "org:min-fin",
            "type": "ministerie",
            "classification": "ministerie",
            "parent_id": None,
            "names": [{"value": "Financiën", "valid_from": "2010-10-14"}],
            "valid_from": "2010-10-14",
            "sources": [{"id": "roo", "url": "https://example.org/roo", "retrieved": "2026-05-09"}],
        },
    )
    _write_yaml(
        mini_polder / "data" / "organisaties" / "organisatieonderdelen" / "belastingdienst.yaml",
        {
            "id": "org:belastingdienst",
            "type": "organisatieonderdeel",
            "classification": "organisatieonderdeel",
            "parent_id": "org:min-fin",
            "names": [{"value": "Belastingdienst", "valid_from": "2010-01-01"}],
            "valid_from": "2010-01-01",
            "sources": [{"id": "roo", "url": "https://example.org/roo", "retrieved": "2026-05-09"}],
        },
    )
    p = _kewal_proposal()
    p["organization_id"] = "org:onderdeel-afdeling-x-min-bzk"
    p["organization_chain"] = [
        {
            "level": "ministerie",
            "name": "Ministerie van Binnenlandse Zaken",
            "slug_proposal": "org:min-bzk",
        },
        {
            "level": "organisatieonderdeel",
            "name": "Belastingdienst",
            "slug_proposal": "org:belastingdienst-min-bzk",
        },
        {
            "level": "afdeling",
            "name": "Afdeling X",
            "slug_proposal": "org:onderdeel-afdeling-x-min-bzk",
        },
    ]
    actions, skipped = plan_apply([p], mini_polder / "data")
    assert actions == []
    reasons = [r for s in skipped for r in s.reasons]
    assert any(
        "Belastingdienst" in r and "parent" in r and "verschilt" in r for r in reasons
    ), reasons


def test_chain_inconsistent_with_organization_id_skipped(mini_polder: Path) -> None:
    """Een chain die naar een ander ministerie wijst dan `organization_id` impliceert."""
    p = _kewal_proposal()
    # organization_id zegt BZK, maar de chain plaatst de unit onder een
    # ander ministerie — dat is de Esmeralda-NVWA-pattern in het echt.
    p["organization_id"] = "org:nvwa-min-lvvn"
    p["organization_chain"] = [
        {
            "level": "ministerie",
            "name": "Ministerie van Binnenlandse Zaken",
            "slug_proposal": "org:min-bzk",
        },
        {
            "level": "afdeling",
            "name": "Directie X",
            "slug_proposal": "org:onderdeel-directie-x",
        },
    ]
    actions, skipped = plan_apply([p], mini_polder / "data")
    assert actions == []
    assert any("mismatcht" in r for s in skipped for r in s.reasons)


def test_chain_alias_resolves_ministerie_via_names(mini_polder: Path) -> None:
    """Een ministerie-alias slug (`org:bzk`) hoort niet tot een create-org te leiden."""
    p = _kewal_proposal()
    # Een chain met de verbose alias `org:ministerie-van-binnenlandse-zaken-en-koninkrijksrelaties`
    # moet resolven naar `org:min-bzk` (al in mini-polder).
    p["organization_chain"] = [
        {
            "level": "ministerie",
            "name": "Ministerie van Binnenlandse Zaken en Koninkrijksrelaties",
            "slug_proposal": "org:ministerie-van-binnenlandse-zaken-en-koninkrijksrelaties",
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
    ]
    actions, _ = plan_apply([p], mini_polder / "data")
    # Geen create-org voor het ministerie zelf — die staat al in data/.
    create_orgs = [a for a in actions if a.type == "create-org"]
    for a in create_orgs:
        assert "min-bzk" not in str(a.target_path)
        assert "binnenlandse-zaken" not in a.target_path.name


def test_filename_strips_org_prefix(mini_polder: Path) -> None:
    """Een chain-entry `slug_proposal=org:foo` mag NIET tot bestand `org:foo.yaml` leiden."""
    p = _kewal_proposal()
    actions, _ = plan_apply([p], mini_polder / "data")
    create_orgs = [a for a in actions if a.type == "create-org"]
    assert create_orgs, "verwacht een create-org actie"
    for a in create_orgs:
        assert (
            ":" not in a.target_path.name
        ), f"filename mag geen ':' bevatten, kreeg: {a.target_path.name}"


def test_skip_when_role_empty(mini_polder: Path) -> None:
    p = _kewal_proposal()
    p["role"] = ""
    actions, skipped = plan_apply([p], mini_polder / "data")
    assert actions == []
    assert any("role" in r for s in skipped for r in s.reasons)


def test_skip_when_no_public_source_url(mini_polder: Path) -> None:
    p = _kewal_proposal()
    p["abd_nieuws_url"] = "_cache/local/path.pdf"
    p.pop("staatscourant_url", None)
    p.pop("organogram_pdf", None)
    actions, skipped = plan_apply([p], mini_polder / "data")
    assert actions == []
    assert any("publieke bron-URL" in r for s in skipped for r in s.reasons)


def test_skip_when_no_start_date(mini_polder: Path) -> None:
    p = _kewal_proposal()
    p["start_date"] = None
    actions, skipped = plan_apply([p], mini_polder / "data")
    assert actions == []
    assert any("start_date" in r for s in skipped for r in s.reasons)


def test_datetime_start_date_normalised(mini_polder: Path) -> None:
    """`start_date: '2022-01-01T00:00:00'` moet `2022-01-01` worden in mandaat-id en velden."""
    _write_yaml(
        mini_polder / "data" / "personen" / "kewal-s-1975.yaml",
        {
            "id": "person:kewal-s-1975",
            "name": {"full": "Suzie Kewal", "family": "Kewal", "given": "Suzie"},
            "birth": {"year": 1975},
            "mandaten": [],
            "sources": [{"id": "abd", "url": "https://example.org/abd", "retrieved": "2026-05-09"}],
        },
    )
    p = _kewal_proposal()
    p["resolved_person_id"] = "person:kewal-s-1975"
    p["start_date"] = "2022-01-01T00:00:00"
    actions, _ = plan_apply([p], mini_polder / "data")
    appends = [a for a in actions if a.type == "append-mandaat"]
    assert appends, "verwacht een append-mandaat actie"
    mandate = appends[0].record["mandaten"][-1]
    assert mandate["start_date"] == "2022-01-01"
    assert "T" not in mandate["id"]
