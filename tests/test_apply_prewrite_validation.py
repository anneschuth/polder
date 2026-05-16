"""Tests voor de Pydantic-pass vóór schrijven in execute_apply (#44).

apply-staging valideerde de data-tree pas *ná* `execute_apply` op disk had
geschreven. Een upstream parse-skill die `start_date: 2020-03` (zonder dag)
leverde landde dan op disk en crashte vervolgens `polder resolve`. De fix:
elk record gaat door `_validate_record` vóór het wordt weggeschreven.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from polder.apply import ApplyAction, _validate_record, execute_apply, plan_apply

SCHEMAS_DIR = Path(__file__).parent.parent / "schemas"


def _valid_persoon_record() -> dict:
    return {
        "id": "person:dungen-vd-6e0c6fe9",
        "name": {
            "full": "Bas van den Dungen",
            "family": "Dungen",
            "tussenvoegsel": "van den",
            "initials": "B.",
        },
        "mandaten": [
            {
                "id": "mandate-dungen-sg-fin-2020",
                "organization_id": "org:min-fin",
                "post_id": "post:sg-min-fin",
                "role": "secretaris-generaal",
                "start_date": "2020-03-01",
                "sources": [
                    {
                        "id": "src-1",
                        "url": "https://example.gov/besluit",
                        "retrieved": "2026-05-15",
                    }
                ],
            }
        ],
        "sources": [
            {
                "id": "src-1",
                "url": "https://example.gov/besluit",
                "retrieved": "2026-05-15",
            }
        ],
    }


def _action(record: dict, target: Path) -> ApplyAction:
    return ApplyAction(
        type="append-mandaat",
        target_path=target,
        record=record,
        source_proposal={},
        confidence=0.99,
    )


def test_validate_record_accepts_valid_persoon(tmp_path) -> None:
    action = _action(_valid_persoon_record(), tmp_path / "p.yaml")
    assert _validate_record(action) is None


def test_validate_record_rejects_yyyy_mm_start_date(tmp_path) -> None:
    rec = _valid_persoon_record()
    rec["mandaten"][0]["start_date"] = "2020-03"  # de #44-bug
    action = _action(rec, tmp_path / "p.yaml")
    err = _validate_record(action)
    assert err is not None
    assert "Persoon-validatie faalde" in err


def test_execute_apply_skips_invalid_record_does_not_write(tmp_path, caplog) -> None:
    good = tmp_path / "good.yaml"
    bad = tmp_path / "bad.yaml"

    bad_rec = _valid_persoon_record()
    bad_rec["id"] = "person:bad-6e0c6fe9"
    bad_rec["mandaten"][0]["start_date"] = "2020-03"

    actions = [
        _action(_valid_persoon_record(), good),
        _action(bad_rec, bad),
    ]

    written = execute_apply(actions, tmp_path)

    assert written == 1
    assert good.exists()
    assert not bad.exists()  # malformed record landt NIET op disk
    assert any("valideert niet" in r.message for r in caplog.records)


def test_execute_apply_writes_all_valid(tmp_path) -> None:
    a = tmp_path / "a.yaml"
    b = tmp_path / "b.yaml"
    rec_b = _valid_persoon_record()
    rec_b["id"] = "person:other-1971"
    actions = [
        _action(_valid_persoon_record(), a),
        _action(rec_b, b),
    ]
    assert execute_apply(actions, tmp_path) == 2
    assert a.exists() and b.exists()


def test_execute_apply_reports_skipped_via_out_list(tmp_path) -> None:
    """De skipped-out-lijst laat de caller hard falen i.p.v. stil minder
    schrijven (review-finding op #44-PR)."""
    bad = _valid_persoon_record()
    bad["id"] = "person:bad-6e0c6fe9"
    bad["mandaten"][0]["start_date"] = "2020-03"
    skipped: list[tuple[ApplyAction, str]] = []

    written = execute_apply([_action(bad, tmp_path / "bad.yaml")], tmp_path, skipped=skipped)

    assert written == 0
    assert len(skipped) == 1
    action, err = skipped[0]
    assert action.type == "append-mandaat"
    assert "Persoon-validatie faalde" in err


# ---------------------------------------------------------------------------
# End-to-end: een YYYY-MM proposal door de echte plan_apply -> execute_apply,
# zodat de guard tegen de werkelijke create-person action-shape getest is,
# niet alleen tegen een synthetische dict.
# ---------------------------------------------------------------------------


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)


@pytest.fixture
def mini_polder(tmp_path: Path) -> Path:
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
            "names": [{"value": "Binnenlandse Zaken", "valid_from": "2010-10-14"}],
            "valid_from": "2010-10-14",
            "sources": [{"id": "roo", "url": "https://example.org/roo", "retrieved": "2026-05-09"}],
        },
    )
    _write_yaml(
        root / "data" / "posten" / "sg-min-bzk.yaml",
        {
            "id": "post:sg-min-bzk",
            "organization_id": "org:min-bzk",
            "label": "secretaris-generaal BZK",
            "classification": "ambtelijke-top",
            "valid_from": "2010-10-14",
        },
    )
    return root


def _proposal_with_bad_start_date() -> dict[str, Any]:
    return {
        "person_name": "Test Persoon",
        "existing_person_id": None,
        "organization_id": "org:min-bzk",
        "post_id": "post:sg-min-bzk",
        "role": "secretaris-generaal",
        "start_date": "2020-03",  # de #44-bug: YYYY-MM zonder dag
        "end_date": None,
        "event_type": "benoeming",
        "abd_nieuws_url": "https://example.org/x",
        "confidence": 0.99,
        "evidence_snippet": "Test Persoon start in maart 2020.",
        "resolved_organization_id": "org:min-bzk",
        "resolved_organization_level": "ministerie",
        "resolved_post_id": "post:sg-min-bzk",
        "resolved_person_id": None,
        "resolution_confidence": {"organization": 0.99, "post": 0.99, "person": 0.0},
        "merge_recommendation": "auto-merge",
        "birth_year": 1970,
    }


def test_end_to_end_yyyy_mm_proposal_does_not_land_on_disk(mini_polder: Path) -> None:
    data = mini_polder / "data"
    actions, _ = plan_apply([_proposal_with_bad_start_date()], data)
    assert actions, "verwacht minstens een create-person action"

    skipped: list[tuple[ApplyAction, str]] = []
    written = execute_apply(actions, data, skipped=skipped)

    # Geen enkel persoon-record met de kapotte datum op disk.
    persoon_files = list((data / "personen").glob("*.yaml"))
    assert persoon_files == []
    assert written < len(actions)
    assert any("Persoon-validatie faalde" in err for _, err in skipped)
