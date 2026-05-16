"""Tests voor de Pydantic-pass vóór schrijven in execute_apply (#44).

apply-staging valideerde de data-tree pas *ná* `execute_apply` op disk had
geschreven. Een upstream parse-skill die `start_date: 2020-03` (zonder dag)
leverde landde dan op disk en crashte vervolgens `polder resolve`. De fix:
elk record gaat door `_validate_record` vóór het wordt weggeschreven.
"""

from __future__ import annotations

from pathlib import Path

from polder.apply import ApplyAction, _validate_record, execute_apply


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
