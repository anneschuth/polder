"""Tests voor polder.audit (data-audit beyond schema-validatie)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from polder.audit import CATEGORY_HELP, run_audit, summary


@pytest.fixture
def fake_data(tmp_path: Path) -> Path:
    """Bouw een minimale data/ met bekende issues."""
    data = tmp_path / "data"
    (data / "organisaties" / "ministeries").mkdir(parents=True)
    (data / "posten").mkdir()
    (data / "personen").mkdir()

    org = {
        "id": "org:min-test",
        "type": "ministeries",
        "names": [{"value": "Test", "lang": "nl"}],
        "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
    }
    (data / "organisaties" / "ministeries" / "min-test.yaml").write_text(
        yaml.safe_dump(org, sort_keys=False), encoding="utf-8"
    )

    post = {
        "id": "post:minister-min-test",
        "organization_id": "org:min-test",
        "label": "Minister van Test",
        "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
    }
    (data / "posten" / "minister-min-test.yaml").write_text(
        yaml.safe_dump(post, sort_keys=False), encoding="utf-8"
    )

    return data


def _write_person(data: Path, slug: str, doc: dict) -> None:
    (data / "personen" / f"{slug}.yaml").write_text(
        yaml.safe_dump(doc, sort_keys=False), encoding="utf-8"
    )


def test_clean_data_has_no_findings(fake_data: Path) -> None:
    _write_person(
        fake_data,
        "klop-jp-1970",
        {
            "id": "person:klop-jp-1970",
            "name": {"family": "Klop", "initials": "J.P."},
            "birth": {"year": 1970},
            "mandaten": [
                {
                    "id": "m1",
                    "organization_id": "org:min-test",
                    "post_id": "post:minister-min-test",
                    "role": "Minister",
                    "start_date": "2010-01-01",
                    "end_date": "2014-01-01",
                    "confidence": 0.95,
                }
            ],
            "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
        },
    )

    issues = run_audit(fake_data, today="2026-05-11")
    assert summary(issues) == (0, 0)


def test_detects_start_after_end(fake_data: Path) -> None:
    _write_person(
        fake_data,
        "fout-i-1980",
        {
            "id": "person:fout-i-1980",
            "name": {"family": "Fout", "initials": "I."},
            "birth": {"year": 1980},
            "mandaten": [
                {
                    "id": "bad",
                    "organization_id": "org:min-test",
                    "post_id": "post:minister-min-test",
                    "role": "Minister",
                    "start_date": "2020-01-01",
                    "end_date": "2018-01-01",
                }
            ],
            "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
        },
    )

    issues = run_audit(fake_data, today="2026-05-11")
    assert "start_after_end" in issues
    assert len(issues["start_after_end"]) == 1


def test_detects_mandaat_before_age_18(fake_data: Path) -> None:
    """Wikidata-sentinel 1945-01-01 voor iemand geboren 1995."""
    _write_person(
        fake_data,
        "kind-i-1995",
        {
            "id": "person:kind-i-1995",
            "name": {"family": "Kind", "initials": "I."},
            "birth": {"year": 1995},
            "mandaten": [
                {
                    "id": "m",
                    "organization_id": "org:min-test",
                    "post_id": "post:minister-min-test",
                    "role": "Minister",
                    "start_date": "1945-01-01",
                }
            ],
            "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
        },
    )

    issues = run_audit(fake_data, today="2026-05-11")
    assert "mandaat_before_age_18" in issues
    assert any("1995" in s for s in issues["mandaat_before_age_18"])


def test_detects_start_in_future(fake_data: Path) -> None:
    _write_person(
        fake_data,
        "toekomst-i-1980",
        {
            "id": "person:toekomst-i-1980",
            "name": {"family": "Toekomst", "initials": "I."},
            "birth": {"year": 1980},
            "mandaten": [
                {
                    "id": "m",
                    "organization_id": "org:min-test",
                    "post_id": "post:minister-min-test",
                    "role": "Minister",
                    "start_date": "2027-01-01",
                }
            ],
            "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
        },
    )

    issues = run_audit(fake_data, today="2026-05-11")
    assert "start_in_future" in issues
    assert len(issues["start_in_future"]) == 1


def test_detects_orphan_org_ref(fake_data: Path) -> None:
    _write_person(
        fake_data,
        "orphan-i-1980",
        {
            "id": "person:orphan-i-1980",
            "name": {"family": "Orphan", "initials": "I."},
            "birth": {"year": 1980},
            "mandaten": [
                {
                    "id": "m",
                    "organization_id": "org:bestaat-niet",
                    "post_id": "post:minister-min-test",
                    "role": "Minister",
                    "start_date": "2010-01-01",
                }
            ],
            "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
        },
    )

    issues = run_audit(fake_data, today="2026-05-11")
    assert "orphan_org_ref" in issues


def test_detects_implausible_birth_year(fake_data: Path) -> None:
    _write_person(
        fake_data,
        "oud-i-1500",
        {
            "id": "person:oud-i-1500",
            "name": {"family": "Oud", "initials": "I."},
            "birth": {"year": 1500},
            "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
        },
    )

    issues = run_audit(fake_data, today="2026-05-11")
    assert "implausible_birth_year" in issues


def test_detects_quasi_duplicate_persons(fake_data: Path) -> None:
    for slug, initials in [("dijk-a-1980", "A."), ("dijk-b-1980", "B.")]:
        _write_person(
            fake_data,
            slug,
            {
                "id": f"person:{slug}",
                "name": {"family": "Dijk", "initials": initials},
                "birth": {"year": 1980},
                "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
            },
        )

    issues = run_audit(fake_data, today="2026-05-11")
    assert "quasi_dup_family_birth" in issues


def test_detects_confidence_out_of_range(fake_data: Path) -> None:
    _write_person(
        fake_data,
        "conf-i-1980",
        {
            "id": "person:conf-i-1980",
            "name": {"family": "Conf", "initials": "I."},
            "birth": {"year": 1980},
            "mandaten": [
                {
                    "id": "m",
                    "organization_id": "org:min-test",
                    "post_id": "post:minister-min-test",
                    "role": "Minister",
                    "start_date": "2010-01-01",
                    "confidence": 1.5,
                }
            ],
            "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
        },
    )

    issues = run_audit(fake_data, today="2026-05-11")
    assert "confidence_out_of_range" in issues


def test_detects_no_sources_on_person(fake_data: Path) -> None:
    _write_person(
        fake_data,
        "nosource-i-1980",
        {
            "id": "person:nosource-i-1980",
            "name": {"family": "Nosource", "initials": "I."},
            "birth": {"year": 1980},
        },
    )

    issues = run_audit(fake_data, today="2026-05-11")
    assert "no_sources_persons" in issues


def test_summary_counts_correctly(fake_data: Path) -> None:
    _write_person(
        fake_data,
        "a-i-1980",
        {
            "id": "person:a-i-1980",
            "name": {"family": "A", "initials": "I."},
            "birth": {"year": 1980},
            "mandaten": [
                {
                    "id": "m",
                    "organization_id": "org:min-test",
                    "post_id": "post:minister-min-test",
                    "role": "Minister",
                    "start_date": "2030-01-01",  # future
                    "end_date": "2025-01-01",  # before start
                }
            ],
            "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
        },
    )

    issues = run_audit(fake_data, today="2026-05-11")
    n_cats, n_findings = summary(issues)
    assert n_cats >= 2
    assert n_findings >= 2


def test_category_help_covers_known_categories() -> None:
    """Elke categorie die `run_audit` kan retourneren, heeft een help-string."""
    expected = {
        "start_after_end",
        "mandaat_before_age_18",
        "mandaat_after_age_100",
        "start_in_future",
        "orphan_org_ref",
        "orphan_post_ref",
        "post_orphan_org",
        "implausible_birth_year",
        "birth_year_not_int",
        "no_sources_persons",
        "no_sources_posts",
        "no_sources_orgs",
        "confidence_out_of_range",
        "mandaat_org_post_mismatch",
        "quasi_dup_family_birth",
    }
    for cat in expected:
        assert cat in CATEGORY_HELP, f"Missing help for {cat}"
