"""Tests voor polder.validate."""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest
import yaml

from polder.validate import (
    SCHEMA_MAP,
    build_index,
    check_birth_year_only,
    check_bsn_patterns,
    check_future_valid_until,
    check_inline_mandaat_sources,
    check_overlapping_mandaten,
    check_referential_integrity,
    count_files,
    exit_code,
    load_records,
    load_schemas,
    main,
    run_all_checks,
    validate_file,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = REPO_ROOT / "schemas"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def schemas_dir(tmp_path: Path) -> Path:
    """Copy real schemas into a tmp directory so tests are hermetic."""
    target = tmp_path / "schemas"
    target.mkdir()
    for filename in set(SCHEMA_MAP.values()):
        shutil.copy(SCHEMAS_DIR / filename, target / filename)
    return target


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    for cat in SCHEMA_MAP:
        (root / cat).mkdir(parents=True)
    return root


def write_yaml(path: Path, content: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(content, fh, sort_keys=False, allow_unicode=True)


def make_org(org_id: str = "org:test", **overrides) -> dict:
    base = {
        "id": org_id,
        "type": "ministerie",
        "names": [{"value": "Test Ministerie", "valid_from": "2020-01-01"}],
        "valid_from": "2020-01-01",
        "sources": [
            {
                "id": "roo",
                "url": "https://organisaties.overheid.nl/test",
                "retrieved": "2026-05-01",
            }
        ],
    }
    base.update(overrides)
    return base


def make_post(post_id: str = "post:test", org_id: str = "org:test", **overrides) -> dict:
    base = {
        "id": post_id,
        "organization_id": org_id,
        "label": "Testpost",
        "classification": "bewindspersoon",
        "seat_count": 1,
        "valid_from": "2020-01-01",
    }
    base.update(overrides)
    return base


def make_person(
    person_id: str = "person:test",
    *,
    mandaten: list | None = None,
    **overrides,
) -> dict:
    base: dict = {
        "id": person_id,
        "name": {"full": "Test Persoon", "family": "Persoon"},
        "sources": [
            {"id": "roo", "url": "https://example.org/p", "retrieved": "2026-05-01"}
        ],
    }
    if mandaten is not None:
        base["mandaten"] = mandaten
    base.update(overrides)
    return base


def make_inline_mandaat(
    *,
    mandaat_id: str = "01900000-0000-0000-0000-000000000001",
    org_id: str = "org:test",
    post_id: str = "post:test",
    start: str = "2024-01-01",
    end: str | None = None,
    sources: list | None = None,
) -> dict:
    base: dict = {
        "id": mandaat_id,
        "organization_id": org_id,
        "post_id": post_id,
        "role": "minister",
        "start_date": start,
    }
    if end is not None:
        base["end_date"] = end
    if sources is None:
        sources = [
            {"id": "roo", "url": "https://example.org/m", "retrieved": "2026-05-01"}
        ]
    base["sources"] = sources
    return base


# ---------------------------------------------------------------------------
# Empty data dir => clean run
# ---------------------------------------------------------------------------


def test_empty_data_dir_no_issues(data_dir: Path, schemas_dir: Path) -> None:
    issues = run_all_checks(data_dir, schemas_dir)
    assert issues == []
    assert count_files(data_dir) == 0
    assert exit_code(issues, strict=False) == 0
    assert exit_code(issues, strict=True) == 0


def test_main_on_empty_data_dir(
    data_dir: Path, schemas_dir: Path, capsys: pytest.CaptureFixture
) -> None:
    rc = main(["--data", str(data_dir), "--schemas", str(schemas_dir)])
    assert rc == 0
    err = capsys.readouterr().err
    assert "0 errors" in err
    assert "0 warnings" in err


# ---------------------------------------------------------------------------
# Schema-validatie: valide vs invalide
# ---------------------------------------------------------------------------


def test_valid_org_passes_schema(data_dir: Path, schemas_dir: Path) -> None:
    write_yaml(data_dir / "organisaties" / "test.yaml", make_org())
    issues = run_all_checks(data_dir, schemas_dir)
    assert all(i.severity != "error" for i in issues), [
        (i.path.name, i.field, i.message) for i in issues
    ]


def test_invalid_org_missing_required_field(data_dir: Path, schemas_dir: Path) -> None:
    org = make_org()
    del org["valid_from"]
    write_yaml(data_dir / "organisaties" / "broken.yaml", org)
    issues = run_all_checks(data_dir, schemas_dir)
    errors = [i for i in issues if i.severity == "error"]
    assert errors, "Expected schema error voor ontbrekend valid_from"
    assert any("valid_from" in (i.message or "") for i in errors)


def test_additional_properties_rejected(data_dir: Path, schemas_dir: Path) -> None:
    org = make_org()
    org["foo_bar"] = "stiekem"
    write_yaml(data_dir / "organisaties" / "x.yaml", org)
    issues = run_all_checks(data_dir, schemas_dir)
    assert any("foo_bar" in i.message for i in issues if i.severity == "error")


def test_validate_file_returns_sorted_errors(schemas_dir: Path) -> None:
    schemas = load_schemas(schemas_dir)
    bad = {"id": "wrong-pattern", "type": "ministerie"}  # missing many required
    issues = validate_file(Path("dummy.yaml"), bad, schemas["organisatie.schema.json"])
    assert all(i.severity == "error" for i in issues)
    assert len(issues) >= 2


# ---------------------------------------------------------------------------
# Referentiele integriteit
# ---------------------------------------------------------------------------


def test_unknown_org_in_post(data_dir: Path, schemas_dir: Path) -> None:
    write_yaml(data_dir / "organisaties" / "a.yaml", make_org("org:bestaand"))
    write_yaml(
        data_dir / "posten" / "p.yaml",
        make_post("post:p1", org_id="org:nietbestaand"),
    )
    issues = run_all_checks(data_dir, schemas_dir)
    refs = [i for i in issues if "Onbekende" in i.message]
    assert any("organization_id" == i.field for i in refs)


def test_unknown_org_in_inline_mandaat(data_dir: Path, schemas_dir: Path) -> None:
    write_yaml(data_dir / "organisaties" / "a.yaml", make_org("org:bestaand"))
    write_yaml(data_dir / "posten" / "p.yaml", make_post("post:p1", org_id="org:bestaand"))
    person = make_person(
        "person:test",
        mandaten=[
            make_inline_mandaat(
                org_id="org:nietbestaand",
                post_id="post:p1",
            )
        ],
    )
    write_yaml(data_dir / "personen" / "x.yaml", person)
    issues = run_all_checks(data_dir, schemas_dir)
    err_msgs = [i for i in issues if "Onbekende organisatie" in i.message]
    assert err_msgs, "Verwachtte ref-error voor inline mandaat org_id"


def test_all_refs_resolved_no_error(data_dir: Path, schemas_dir: Path) -> None:
    write_yaml(data_dir / "organisaties" / "a.yaml", make_org("org:bestaand"))
    write_yaml(data_dir / "posten" / "p.yaml", make_post("post:p1", org_id="org:bestaand"))
    person = make_person(
        "person:test",
        mandaten=[
            make_inline_mandaat(
                org_id="org:bestaand",
                post_id="post:p1",
            )
        ],
    )
    write_yaml(data_dir / "personen" / "x.yaml", person)
    issues = run_all_checks(data_dir, schemas_dir)
    assert all(i.severity != "error" for i in issues), [
        (i.field, i.message) for i in issues
    ]


def test_check_referential_integrity_unit() -> None:
    records, _ = load_records(Path("/nonexistent"))
    assert records == []


# ---------------------------------------------------------------------------
# BSN-pattern
# ---------------------------------------------------------------------------


def test_bsn_pattern_caught(data_dir: Path, schemas_dir: Path) -> None:
    org = make_org()
    org["names"][0]["value"] = "Ministerie BSN: 123456789 hier"
    write_yaml(data_dir / "organisaties" / "leak.yaml", org)
    issues = run_all_checks(data_dir, schemas_dir)
    bsn_errors = [i for i in issues if "BSN-achtig" in i.message]
    assert bsn_errors, "Verwachtte BSN-pattern error"
    assert bsn_errors[0].severity == "error"


def test_bsn_pattern_does_not_fire_on_postcode_or_kvk(
    data_dir: Path, schemas_dir: Path
) -> None:
    # 8-cijferig KvK is geen 9-cijferige hit.
    org = make_org()
    org["identifiers"] = {"kvk": "12345678"}
    write_yaml(data_dir / "organisaties" / "ok.yaml", org)
    issues = run_all_checks(data_dir, schemas_dir)
    assert not any("BSN-achtig" in i.message for i in issues)


def test_check_bsn_patterns_unit(tmp_path: Path) -> None:
    from polder.validate import Record

    rec = Record(
        path=tmp_path / "x.yaml",
        category="organisaties",
        data={"id": "org:x", "note": "iets met 987654321 erin"},
        schema_name="organisatie.schema.json",
    )
    issues = check_bsn_patterns([rec])
    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert issues[0].field == "note"


# ---------------------------------------------------------------------------
# Overlappende mandaten
# ---------------------------------------------------------------------------


def test_overlapping_single_seat_warning(data_dir: Path, schemas_dir: Path) -> None:
    write_yaml(data_dir / "organisaties" / "a.yaml", make_org("org:bestaand"))
    write_yaml(
        data_dir / "posten" / "p.yaml",
        make_post("post:p1", org_id="org:bestaand", seat_count=1),
    )
    p1 = make_person(
        "person:alpha",
        mandaten=[
            make_inline_mandaat(
                mandaat_id="01900000-0000-0000-0000-000000000001",
                org_id="org:bestaand",
                post_id="post:p1",
                start="2024-01-01",
                end="2024-12-31",
            )
        ],
    )
    p2 = make_person(
        "person:beta",
        mandaten=[
            make_inline_mandaat(
                mandaat_id="01900000-0000-0000-0000-000000000002",
                org_id="org:bestaand",
                post_id="post:p1",
                start="2024-06-01",
                end="2025-06-01",
            )
        ],
    )
    write_yaml(data_dir / "personen" / "alpha.yaml", p1)
    write_yaml(data_dir / "personen" / "beta.yaml", p2)
    issues = run_all_checks(data_dir, schemas_dir)
    warnings = [i for i in issues if i.severity == "warning"]
    assert any("Overlap" in i.message for i in warnings), [i.message for i in issues]


def test_no_warning_when_seat_count_gt_one(data_dir: Path, schemas_dir: Path) -> None:
    write_yaml(data_dir / "organisaties" / "a.yaml", make_org("org:bestaand"))
    write_yaml(
        data_dir / "posten" / "p.yaml",
        make_post("post:p1", org_id="org:bestaand", seat_count=5),
    )
    p1 = make_person(
        "person:alpha",
        mandaten=[
            make_inline_mandaat(
                org_id="org:bestaand",
                post_id="post:p1",
                start="2024-01-01",
                end="2024-12-31",
            )
        ],
    )
    p2 = make_person(
        "person:beta",
        mandaten=[
            make_inline_mandaat(
                mandaat_id="01900000-0000-0000-0000-000000000002",
                org_id="org:bestaand",
                post_id="post:p1",
                start="2024-06-01",
                end="2025-06-01",
            )
        ],
    )
    write_yaml(data_dir / "personen" / "alpha.yaml", p1)
    write_yaml(data_dir / "personen" / "beta.yaml", p2)
    issues = run_all_checks(data_dir, schemas_dir)
    assert not any("Overlap" in i.message for i in issues)


# ---------------------------------------------------------------------------
# Future valid_until
# ---------------------------------------------------------------------------


def test_future_valid_until_warning(data_dir: Path, schemas_dir: Path) -> None:
    org = make_org()
    org["valid_until"] = "2099-12-31"
    write_yaml(data_dir / "organisaties" / "future.yaml", org)
    issues = run_all_checks(data_dir, schemas_dir, today=date(2026, 5, 9))
    assert any("toekomst" in i.message for i in issues if i.severity == "warning")


def test_future_valid_until_with_planned_source_no_warning(
    data_dir: Path, schemas_dir: Path
) -> None:
    org = make_org()
    org["valid_until"] = "2099-12-31"
    org["sources"].append(
        {
            "id": "planned",
            "url": "https://example.org/plan",
            "retrieved": "2026-05-01",
        }
    )
    write_yaml(data_dir / "organisaties" / "ok.yaml", org)
    issues = run_all_checks(data_dir, schemas_dir, today=date(2026, 5, 9))
    assert not any("toekomst" in i.message for i in issues)


# ---------------------------------------------------------------------------
# Inline mandaat sources
# ---------------------------------------------------------------------------


def test_inline_mandaat_zonder_sources_zou_via_schema_error_geven(
    data_dir: Path, schemas_dir: Path
) -> None:
    person = make_person(
        "person:test",
        mandaten=[
            {
                "id": "01900000-0000-0000-0000-000000000099",
                "organization_id": "org:bestaand",
                "post_id": "post:p1",
                "role": "minister",
                "start_date": "2024-01-01",
                # geen sources -> schema error verwacht (sources is required)
            }
        ],
    )
    write_yaml(data_dir / "personen" / "x.yaml", person)
    issues = run_all_checks(data_dir, schemas_dir)
    assert any(i.severity == "error" for i in issues)


def test_check_inline_mandaat_sources_unit(tmp_path: Path) -> None:
    from polder.validate import Record

    person = make_person(
        "person:test",
        mandaten=[make_inline_mandaat(sources=[])],
    )
    rec = Record(
        path=tmp_path / "p.yaml",
        category="personen",
        data=person,
        schema_name="persoon.schema.json",
    )
    issues = check_inline_mandaat_sources([rec])
    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert "sources" in (issues[0].field or "")


# ---------------------------------------------------------------------------
# Birth-year-only
# ---------------------------------------------------------------------------


def test_birth_year_only_unit(tmp_path: Path) -> None:
    from polder.validate import Record

    person = {
        "id": "person:x",
        "name": {"full": "X", "family": "X"},
        "birth": {"year": 1970, "month": 6},
        "sources": [{"id": "x", "url": "https://x", "retrieved": "2026-01-01"}],
    }
    rec = Record(
        path=tmp_path / "x.yaml",
        category="personen",
        data=person,
        schema_name="persoon.schema.json",
    )
    issues = check_birth_year_only([rec])
    assert len(issues) == 1
    assert issues[0].severity == "error"


# ---------------------------------------------------------------------------
# Skip _staging en .gitkeep
# ---------------------------------------------------------------------------


def test_staging_files_skipped(data_dir: Path, schemas_dir: Path) -> None:
    staging = data_dir / "organisaties" / "_staging" / "draft.yaml"
    staging.parent.mkdir(parents=True)
    bad = make_org()
    del bad["valid_from"]
    write_yaml(staging, bad)
    issues = run_all_checks(data_dir, schemas_dir)
    assert all(staging.name not in str(i.path) for i in issues)


def test_gitkeep_skipped(data_dir: Path, schemas_dir: Path) -> None:
    (data_dir / "organisaties" / ".gitkeep").write_text("")
    issues = run_all_checks(data_dir, schemas_dir)
    assert issues == []


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------


def test_exit_code_clean() -> None:
    assert exit_code([], strict=False) == 0
    assert exit_code([], strict=True) == 0


def test_exit_code_with_warnings_only(tmp_path: Path) -> None:
    from polder.validate import ValidationIssue

    only_warn = [
        ValidationIssue(severity="warning", path=tmp_path / "x", field=None, message="m")
    ]
    assert exit_code(only_warn, strict=True) == 2
    # Spec: warnings-only is exit 2, ook zonder strict.
    assert exit_code(only_warn, strict=False) == 2


def test_exit_code_with_errors(tmp_path: Path) -> None:
    from polder.validate import ValidationIssue

    errs = [ValidationIssue(severity="error", path=tmp_path / "x", field=None, message="m")]
    assert exit_code(errs, strict=False) == 1
    assert exit_code(errs, strict=True) == 1


# ---------------------------------------------------------------------------
# build_index sanity
# ---------------------------------------------------------------------------


def test_build_index_collects_ids(data_dir: Path, schemas_dir: Path) -> None:
    write_yaml(data_dir / "organisaties" / "a.yaml", make_org("org:a"))
    write_yaml(data_dir / "posten" / "p.yaml", make_post("post:p", org_id="org:a"))
    write_yaml(
        data_dir / "personen" / "x.yaml",
        make_person("person:x"),
    )
    records, _ = load_records(data_dir)
    idx = build_index(records)
    assert "org:a" in idx.org_ids
    assert "post:p" in idx.post_ids
    assert "person:x" in idx.person_ids


# ---------------------------------------------------------------------------
# Full main with errors -> exit 1
# ---------------------------------------------------------------------------


def test_main_returns_1_on_errors(
    data_dir: Path, schemas_dir: Path, capsys: pytest.CaptureFixture
) -> None:
    org = make_org()
    del org["valid_from"]
    write_yaml(data_dir / "organisaties" / "broken.yaml", org)
    rc = main(["--data", str(data_dir), "--schemas", str(schemas_dir)])
    assert rc == 1


def test_check_overlapping_unit_no_data() -> None:
    from polder.validate import Index

    assert check_overlapping_mandaten(Index()) == []


def test_check_referential_integrity_no_records() -> None:
    from polder.validate import Index

    assert check_referential_integrity([], Index()) == []


def test_check_future_valid_until_no_records() -> None:
    assert check_future_valid_until([], today=date(2026, 5, 9)) == []
