"""Tests voor polder.audit (data-audit beyond schema-validatie)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from polder.audit import CATEGORIES, run_audit, summary


def _categories_in(report) -> set[str]:
    return {f.category for f in report.findings}


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


def _good_mandate(start: str = "2010-01-01", end: str | None = "2014-01-01") -> dict:
    return {
        "id": "m1",
        "organization_id": "org:min-test",
        "post_id": "post:minister-min-test",
        "role": "Minister",
        "start_date": start,
        "end_date": end,
        "confidence": 0.95,
        "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
    }


def test_clean_data_has_no_findings(fake_data: Path) -> None:
    _write_person(
        fake_data,
        "klop-jp-1970",
        {
            "id": "person:klop-jp-1970",
            "name": {"family": "Klop", "initials": "J.P."},
            "birth": {"year": 1970},
            "mandaten": [_good_mandate()],
            "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
        },
    )

    report = run_audit(fake_data, today="2026-05-11")
    assert summary(report) == (0, 0)


def test_detects_start_after_end(fake_data: Path) -> None:
    m = _good_mandate(start="2020-01-01", end="2018-01-01")
    _write_person(
        fake_data,
        "fout-i-1980",
        {
            "id": "person:fout-i-1980",
            "name": {"family": "Fout", "initials": "I."},
            "birth": {"year": 1980},
            "mandaten": [m],
            "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
        },
    )

    report = run_audit(fake_data, today="2026-05-11")
    assert "start_after_end" in _categories_in(report)


def test_detects_mandaat_before_age_18(fake_data: Path) -> None:
    """Wikidata-sentinel 1945-01-01 voor iemand geboren 1995."""
    m = _good_mandate(start="1945-01-01", end=None)
    _write_person(
        fake_data,
        "kind-i-1995",
        {
            "id": "person:kind-i-1995",
            "name": {"family": "Kind", "initials": "I."},
            "birth": {"year": 1995},
            "mandaten": [m],
            "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
        },
    )

    report = run_audit(fake_data, today="2026-05-11")
    assert "mandaat_before_age_18" in _categories_in(report)


def test_detects_start_in_future(fake_data: Path) -> None:
    m = _good_mandate(start="2027-01-01", end=None)
    _write_person(
        fake_data,
        "toekomst-i-1980",
        {
            "id": "person:toekomst-i-1980",
            "name": {"family": "Toekomst", "initials": "I."},
            "birth": {"year": 1980},
            "mandaten": [m],
            "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
        },
    )

    report = run_audit(fake_data, today="2026-05-11")
    assert "start_in_future" in _categories_in(report)


def test_detects_orphan_org_ref(fake_data: Path) -> None:
    m = _good_mandate()
    m["organization_id"] = "org:bestaat-niet"
    _write_person(
        fake_data,
        "orphan-i-1980",
        {
            "id": "person:orphan-i-1980",
            "name": {"family": "Orphan", "initials": "I."},
            "birth": {"year": 1980},
            "mandaten": [m],
            "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
        },
    )

    report = run_audit(fake_data, today="2026-05-11")
    assert "orphan_org_ref" in _categories_in(report)


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

    report = run_audit(fake_data, today="2026-05-11")
    assert "implausible_birth_year" in _categories_in(report)


def test_detects_quasi_duplicate_family_birth(fake_data: Path) -> None:
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

    report = run_audit(fake_data, today="2026-05-11")
    assert "quasi_dup_family_birth" in _categories_in(report)


def test_detects_quasi_duplicate_family_no_birth(fake_data: Path) -> None:
    """De Wopke Hoekstra-case: twee personen, één zonder birth-year."""
    _write_person(
        fake_data,
        "hoekstra-w-9999999",
        {
            "id": "person:hoekstra-w-9999999",
            "name": {"family": "Hoekstra", "initials": "W."},
            # geen birth.year
            "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
        },
    )
    _write_person(
        fake_data,
        "hoekstra-wb-1975",
        {
            "id": "person:hoekstra-wb-1975",
            "name": {"family": "Hoekstra", "initials": "W.B."},
            "birth": {"year": 1975},
            "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
        },
    )

    report = run_audit(fake_data, today="2026-05-11")
    cats = _categories_in(report)
    assert "quasi_dup_family_no_birth" in cats
    # Initialen W. is prefix van W.B. -> ook flag
    assert "quasi_dup_initials_prefix" in cats


def test_detects_quasi_duplicate_initials_prefix(fake_data: Path) -> None:
    _write_person(
        fake_data,
        "rutten-m-1970",
        {
            "id": "person:rutten-m-1970",
            "name": {"family": "Rutten", "initials": "M."},
            "birth": {"year": 1970},
            "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
        },
    )
    _write_person(
        fake_data,
        "rutten-mh-1970",
        {
            "id": "person:rutten-mh-1970",
            "name": {"family": "Rutten", "initials": "M.H."},
            "birth": {"year": 1970},
            "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
        },
    )

    report = run_audit(fake_data, today="2026-05-11")
    assert "quasi_dup_initials_prefix" in _categories_in(report)


def test_quasi_dup_initials_prefix_not_triggered_for_same_initials(fake_data: Path) -> None:
    """Twee mensen met identieke initialen+family+year: family_birth, niet prefix."""
    _write_person(
        fake_data,
        "rutten-m-1970",
        {
            "id": "person:rutten-m-1970",
            "name": {"family": "Rutten", "initials": "M."},
            "birth": {"year": 1970},
            "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
        },
    )
    _write_person(
        fake_data,
        "rutten-m2-1970",
        {
            "id": "person:rutten-m2-1970",
            "name": {"family": "Rutten", "initials": "M."},
            "birth": {"year": 1970},
            "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
        },
    )

    report = run_audit(fake_data, today="2026-05-11")
    cats = _categories_in(report)
    assert "quasi_dup_family_birth" in cats
    assert "quasi_dup_initials_prefix" not in cats


def test_detects_confidence_out_of_range(fake_data: Path) -> None:
    m = _good_mandate()
    m["confidence"] = 1.5
    _write_person(
        fake_data,
        "conf-i-1980",
        {
            "id": "person:conf-i-1980",
            "name": {"family": "Conf", "initials": "I."},
            "birth": {"year": 1980},
            "mandaten": [m],
            "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
        },
    )

    report = run_audit(fake_data, today="2026-05-11")
    assert "confidence_out_of_range" in _categories_in(report)


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

    report = run_audit(fake_data, today="2026-05-11")
    assert "no_sources_persons" in _categories_in(report)


def test_detects_bsn_in_text(fake_data: Path) -> None:
    """9-cijferige reeks in een name-veld lijkt op BSN."""
    _write_person(
        fake_data,
        "bsn-i-1980",
        {
            "id": "person:bsn-i-1980",
            "name": {"family": "BSN", "full": "Iemand 123456789", "initials": "I."},
            "birth": {"year": 1980},
            "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
        },
    )

    report = run_audit(fake_data, today="2026-05-11")
    assert "bsn_in_text" in _categories_in(report)


def test_bsn_check_skipt_identifiers(fake_data: Path) -> None:
    """OIN's en andere identifiers met 9+ cijfers triggeren GEEN BSN-warning."""
    _write_person(
        fake_data,
        "oin-i-1980",
        {
            "id": "person:oin-i-1980",
            "name": {"family": "OIN", "initials": "I."},
            "birth": {"year": 1980},
            "identifiers": {"tk_persoon_id": "123456789-abc", "oin": "123456789"},
            "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
        },
    )

    report = run_audit(fake_data, today="2026-05-11")
    assert "bsn_in_text" not in _categories_in(report)


def test_detects_cyclic_parent_org(fake_data: Path) -> None:
    org_a = {
        "id": "org:a",
        "type": "agentschap",
        "parent_id": "org:b",
        "names": [{"value": "A"}],
        "sources": [{"id": "test", "url": "https://t", "retrieved": "2026-01-01"}],
    }
    org_b = {
        "id": "org:b",
        "type": "agentschap",
        "parent_id": "org:a",
        "names": [{"value": "B"}],
        "sources": [{"id": "test", "url": "https://t", "retrieved": "2026-01-01"}],
    }
    (fake_data / "organisaties" / "agentschappen").mkdir()
    (fake_data / "organisaties" / "agentschappen" / "a.yaml").write_text(
        yaml.safe_dump(org_a, sort_keys=False), encoding="utf-8"
    )
    (fake_data / "organisaties" / "agentschappen" / "b.yaml").write_text(
        yaml.safe_dump(org_b, sort_keys=False), encoding="utf-8"
    )

    report = run_audit(fake_data, today="2026-05-11")
    assert "cyclic_parent_org" in _categories_in(report)


def test_detects_successor_predecessor_mismatch(fake_data: Path) -> None:
    org_old = {
        "id": "org:min-old",
        "type": "ministerie",
        "successor_id": "org:min-new",  # zegt: min-new is opvolger
        "names": [{"value": "Oud"}],
        "sources": [{"id": "test", "url": "https://t", "retrieved": "2026-01-01"}],
    }
    org_new = {
        "id": "org:min-new",
        "type": "ministerie",
        # Mist predecessor_id! Inconsistent.
        "names": [{"value": "Nieuw"}],
        "sources": [{"id": "test", "url": "https://t", "retrieved": "2026-01-01"}],
    }
    (fake_data / "organisaties" / "ministeries" / "min-old.yaml").write_text(
        yaml.safe_dump(org_old, sort_keys=False), encoding="utf-8"
    )
    (fake_data / "organisaties" / "ministeries" / "min-new.yaml").write_text(
        yaml.safe_dump(org_new, sort_keys=False), encoding="utf-8"
    )

    report = run_audit(fake_data, today="2026-05-11")
    assert "successor_predecessor_mismatch" in _categories_in(report)


def test_detects_dead_person_active_mandate(fake_data: Path) -> None:
    m = _good_mandate(start="2010-01-01", end=None)
    _write_person(
        fake_data,
        "dood-i-1900",
        {
            "id": "person:dood-i-1900",
            "name": {"family": "Dood", "initials": "I."},
            "birth": {"year": 1900},
            "death": {"year": 1990},
            "mandaten": [m],
            "sources": [{"id": "test", "url": "https://t", "retrieved": "2026-01-01"}],
        },
    )

    report = run_audit(fake_data, today="2026-05-11")
    assert "dead_person_active_mandate" in _categories_in(report)


def test_detects_mandate_longer_than_30y(fake_data: Path) -> None:
    m = _good_mandate(start="1960-01-01", end="2010-01-01")
    _write_person(
        fake_data,
        "lang-i-1930",
        {
            "id": "person:lang-i-1930",
            "name": {"family": "Lang", "initials": "I."},
            "birth": {"year": 1930},
            "mandaten": [m],
            "sources": [{"id": "test", "url": "https://t", "retrieved": "2026-01-01"}],
        },
    )

    report = run_audit(fake_data, today="2026-05-11")
    assert "mandaat_longer_than_30y" in _categories_in(report)


def test_detects_mandaat_no_sources(fake_data: Path) -> None:
    m = _good_mandate()
    m["sources"] = []
    _write_person(
        fake_data,
        "geen-srcs-i-1980",
        {
            "id": "person:geen-srcs-i-1980",
            "name": {"family": "Geen", "initials": "I."},
            "birth": {"year": 1980},
            "mandaten": [m],
            "sources": [{"id": "test", "url": "https://t", "retrieved": "2026-01-01"}],
        },
    )

    report = run_audit(fake_data, today="2026-05-11")
    assert "mandaat_no_sources" in _categories_in(report)


def test_detects_mandaat_evidence_missing(fake_data: Path) -> None:
    """Apply-staging mandaat zonder appointment + zonder valid URL = evidence-loos."""
    m = _good_mandate()
    m["sources"] = [
        {
            "id": "staatscourant",
            "url": "https://example.invalid",  # geen echte bron
            "retrieved": "2026-01-01",
            "fields": ["applied_via:apply-staging"],
        }
    ]
    _write_person(
        fake_data,
        "ev-i-1980",
        {
            "id": "person:ev-i-1980",
            "name": {"family": "Ev", "initials": "I."},
            "birth": {"year": 1980},
            "mandaten": [m],
            "sources": [{"id": "test", "url": "https://t", "retrieved": "2026-01-01"}],
        },
    )

    report = run_audit(fake_data, today="2026-05-11")
    assert "mandaat_evidence_missing" in _categories_in(report)


def test_evidence_check_accepts_valid_url(fake_data: Path) -> None:
    """Apply-staging mandaat MET een valid URL is OK, ook zonder appointment."""
    m = _good_mandate()
    m["sources"] = [
        {
            "id": "abd_nieuws",
            "url": "https://www.algemenebestuursdienst.nl/actueel/nieuws/2024/01/X",
            "retrieved": "2026-01-01",
            "fields": ["applied_via:apply-staging"],
        }
    ]
    _write_person(
        fake_data,
        "ok-i-1980",
        {
            "id": "person:ok-i-1980",
            "name": {"family": "Ok", "initials": "I."},
            "birth": {"year": 1980},
            "mandaten": [m],
            "sources": [{"id": "test", "url": "https://t", "retrieved": "2026-01-01"}],
        },
    )

    report = run_audit(fake_data, today="2026-05-11")
    assert "mandaat_evidence_missing" not in _categories_in(report)


def test_verified_findings_are_filtered(fake_data: Path, tmp_path: Path) -> None:
    """data/_audit/verified.yaml haalt findings uit de output."""
    for slug, initials in [("dijk-a-1980", "A."), ("dijk-b-1980", "B.")]:
        _write_person(
            fake_data,
            slug,
            {
                "id": f"person:{slug}",
                "name": {"family": "Dijk", "initials": initials},
                "birth": {"year": 1980},
                "sources": [{"id": "test", "url": "https://t", "retrieved": "2026-01-01"}],
            },
        )

    # Run zonder whitelist: vinding aanwezig
    report = run_audit(fake_data, today="2026-05-11", apply_whitelist=False)
    cats = _categories_in(report)
    assert "quasi_dup_family_birth" in cats

    # Schrijf een verified entry
    audit_dir = fake_data / "_audit"
    audit_dir.mkdir()
    (audit_dir / "verified.yaml").write_text(
        yaml.safe_dump(
            {
                "verified": [
                    {
                        "category": "quasi_dup_family_birth",
                        "key": "dijk|1980",
                        "note": "Twee echte verschillende personen",
                        "verified_at": "2026-05-11",
                        "verified_by": "anneschuth",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    # Run met whitelist: vinding eruit, skipped > 0
    report = run_audit(fake_data, today="2026-05-11", apply_whitelist=True)
    assert "quasi_dup_family_birth" not in _categories_in(report)
    assert report.verified_skipped >= 1


def test_summary_counts_correctly(fake_data: Path) -> None:
    m = _good_mandate(start="2030-01-01", end="2025-01-01")  # future + after-end
    _write_person(
        fake_data,
        "a-i-1980",
        {
            "id": "person:a-i-1980",
            "name": {"family": "A", "initials": "I."},
            "birth": {"year": 1980},
            "mandaten": [m],
            "sources": [{"id": "test", "url": "https://t", "retrieved": "2026-01-01"}],
        },
    )

    report = run_audit(fake_data, today="2026-05-11")
    n_cats, n_findings = summary(report)
    assert n_cats >= 2
    assert n_findings >= 2


def test_categories_dict_covers_known_categories() -> None:
    """Elke categorie die `run_audit` retourneert heeft een Category-entry."""
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
        "no_sources_orgs",
        "confidence_out_of_range",
        "mandaat_org_post_mismatch",
        "quasi_dup_family_birth",
        "quasi_dup_family_no_birth",
        "quasi_dup_initials_prefix",
        "mandaat_no_sources",
        "bsn_in_text",
        "mandaat_evidence_missing",
        "cyclic_parent_org",
        "successor_predecessor_mismatch",
        "dead_person_active_mandate",
        "mandaat_longer_than_30y",
    }
    for cat in expected:
        assert cat in CATEGORIES, f"Missing Category entry voor {cat}"
        assert CATEGORIES[cat].severity in ("error", "review")
        assert CATEGORIES[cat].help
