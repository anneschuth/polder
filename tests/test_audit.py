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
        "type": "ministerie",
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
        "classification": "bewindspersoon",
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
        "overlapping_open_mandates_different_orgs",
        "single_seat_both_open",
    }
    for cat in expected:
        assert cat in CATEGORIES, f"Missing Category entry voor {cat}"
        assert CATEGORIES[cat].severity in ("error", "review")
        assert CATEGORIES[cat].help


def test_detects_overlapping_open_mandates_different_orgs(fake_data: Path) -> None:
    """Persoon met twee open mandaten bij niet-verwante ministeries."""
    # Extra ministerie + post zodat we cross-org mandaten kunnen maken.
    other_org = {
        "id": "org:min-other",
        "type": "ministerie",
        "names": [{"value": "Other", "lang": "nl"}],
        "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
    }
    (fake_data / "organisaties" / "ministeries" / "min-other.yaml").write_text(
        yaml.safe_dump(other_org, sort_keys=False), encoding="utf-8"
    )
    other_post = {
        "id": "post:minister-min-other",
        "organization_id": "org:min-other",
        "label": "Minister van Other",
        "classification": "bewindspersoon",
        "seat_count": 1,
        "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
    }
    (fake_data / "posten" / "minister-min-other.yaml").write_text(
        yaml.safe_dump(other_post, sort_keys=False), encoding="utf-8"
    )
    m1 = _good_mandate(start="2020-01-01", end=None)
    m2 = {
        **_good_mandate(start="2023-01-01", end=None),
        "id": "m2",
        "organization_id": "org:min-other",
        "post_id": "post:minister-min-other",
    }
    _write_person(
        fake_data,
        "vergeten-i-1970",
        {
            "id": "person:vergeten-i-1970",
            "name": {"family": "Vergeten", "initials": "I."},
            "birth": {"year": 1970},
            "mandaten": [m1, m2],
            "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
        },
    )

    report = run_audit(fake_data, today="2026-05-11")
    assert "overlapping_open_mandates_different_orgs" in _categories_in(report)


def test_detects_single_seat_both_open(fake_data: Path) -> None:
    """Twee personen met beide open mandaat op dezelfde single-seat post."""
    # De minister-post in fake_data is al single-seat (classification:
    # bewindspersoon). Voeg twee personen toe die beide minister zijn.
    # Voor de check moet de post seat_count=1 hebben.
    post_path = fake_data / "posten" / "minister-min-test.yaml"
    post_data = yaml.safe_load(post_path.read_text(encoding="utf-8"))
    post_data["seat_count"] = 1
    post_path.write_text(yaml.safe_dump(post_data, sort_keys=False), encoding="utf-8")

    m = _good_mandate(start="2020-01-01", end=None)
    _write_person(
        fake_data,
        "a-i-1970",
        {
            "id": "person:a-i-1970",
            "name": {"family": "A", "initials": "I."},
            "birth": {"year": 1970},
            "mandaten": [m],
            "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
        },
    )
    _write_person(
        fake_data,
        "b-i-1970",
        {
            "id": "person:b-i-1970",
            "name": {"family": "B", "initials": "I."},
            "birth": {"year": 1970},
            "mandaten": [{**m, "id": "m2", "start_date": "2022-01-01"}],
            "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
        },
    )

    report = run_audit(fake_data, today="2026-05-11")
    assert "single_seat_both_open" in _categories_in(report)


def test_single_seat_check_skips_multi_seat_posts(fake_data: Path) -> None:
    """Multi-seat posts (seat_count=null) worden niet geflagd ook al hebben twee
    personen daarvoor een open mandaat."""
    post_path = fake_data / "posten" / "minister-min-test.yaml"
    post_data = yaml.safe_load(post_path.read_text(encoding="utf-8"))
    post_data["seat_count"] = None
    post_path.write_text(yaml.safe_dump(post_data, sort_keys=False), encoding="utf-8")

    m = _good_mandate(start="2020-01-01", end=None)
    _write_person(
        fake_data,
        "a-i-1970",
        {
            "id": "person:a-i-1970",
            "name": {"family": "A", "initials": "I."},
            "birth": {"year": 1970},
            "mandaten": [m],
            "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
        },
    )
    _write_person(
        fake_data,
        "b-i-1970",
        {
            "id": "person:b-i-1970",
            "name": {"family": "B", "initials": "I."},
            "birth": {"year": 1970},
            "mandaten": [{**m, "id": "m2"}],
            "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
        },
    )

    report = run_audit(fake_data, today="2026-05-11")
    assert "single_seat_both_open" not in _categories_in(report)


# ---------------------------------------------------------------------------
# Phase 5: ROO superset audit-checks
# ---------------------------------------------------------------------------


_MINI_ROO_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<overheidsorganisaties xmlns:p="https://organisaties.overheid.nl/static/schema/oo/export/2.6.9">
<organisaties>
  <organisatie p:systeemId="9999">
    <naam>Ministerie van Test</naam>
    <types><type>Ministerie</type></types>
    <datumMutatie>2026-05-15</datumMutatie>
  </organisatie>
  <organisatie p:systeemId="8888">
    <naam>Wij Bestaan Niet In Polder</naam>
    <types><type>Ministerie</type></types>
  </organisatie>
</organisaties>
</overheidsorganisaties>
"""


def test_roo_missing_org_detects_org_not_in_data(fake_data: Path) -> None:
    """ROO heeft een org die polder niet heeft → roo_missing_org."""
    cache = fake_data.parent / "_cache"
    cache.mkdir()
    (cache / "roo-export-2026-05-15.xml").write_bytes(_MINI_ROO_XML)
    report = run_audit(fake_data, today="2026-05-15")
    cats = _categories_in(report)
    assert "roo_missing_org" in cats
    # Polder kent roo_id 9999 niet (de fake_data org heeft geen roo_id), dus
    # beide ROO-records (9999 én 8888) zijn missend. Specifiek 8888 met de
    # niet-bestaande naam moet erin zitten.
    msgs = [f.message for f in report.findings if f.category == "roo_missing_org"]
    assert any("Wij Bestaan Niet In Polder" in m for m in msgs)


def test_roo_field_drift_detects_outdated_last_mutation(fake_data: Path, tmp_path: Path) -> None:
    """ROO datumMutatie nieuwer dan polder last_mutation → roo_field_drift."""
    # Voeg roo_id + last_mutation aan de fake org toe, een dag ouder dan ROO.
    org_path = fake_data / "organisaties" / "ministeries" / "min-test.yaml"
    org_data = yaml.safe_load(org_path.read_text(encoding="utf-8"))
    org_data["identifiers"] = {"roo_id": "9999"}
    org_data["last_mutation"] = "2026-05-14"
    org_path.write_text(yaml.safe_dump(org_data, sort_keys=False), encoding="utf-8")

    cache = fake_data.parent / "_cache"
    cache.mkdir()
    (cache / "roo-export-2026-05-15.xml").write_bytes(_MINI_ROO_XML)

    report = run_audit(fake_data, today="2026-05-15")
    drift = [f for f in report.findings if f.category == "roo_field_drift"]
    assert drift, "Verwachtte roo_field_drift voor min-test.yaml"
    assert "2026-05-14" in drift[0].message
    assert "2026-05-15" in drift[0].message


def test_roo_audit_checks_skipped_without_cache(fake_data: Path) -> None:
    """Geen `_cache/roo-export-*.xml` → ROO-checks worden stilletjes overgeslagen."""
    report = run_audit(fake_data, today="2026-05-15")
    cats = _categories_in(report)
    assert "roo_missing_org" not in cats
    assert "roo_field_drift" not in cats


def test_roo_audit_categories_registered() -> None:
    """De drie ROO-categorieën staan in CATEGORIES, dus `polder audit --explain`
    kan ze tonen."""
    assert "roo_missing_org" in CATEGORIES
    assert "roo_field_drift" in CATEGORIES
    assert "roo_stale_appointment" in CATEGORIES
    assert CATEGORIES["roo_missing_org"].severity == "error"
    assert CATEGORIES["roo_field_drift"].severity == "review"


def test_roo_stale_appointment_fires_when_polder_has_end_date(fake_data: Path) -> None:
    """ROO listt nog actieve medewerker, polder heeft end_date → roo_stale_appointment."""
    import json as _json

    # Voeg een gesloten mandaat toe.
    person_path = fake_data / "personen" / "fired-i-1970.yaml"
    person_path.write_text(
        yaml.safe_dump(
            {
                "id": "person:fired-i-1970",
                "name": {"family": "Fired", "initials": "I."},
                "birth": {"year": 1970},
                "mandaten": [
                    {
                        "id": "m1",
                        "organization_id": "org:min-test",
                        "post_id": "post:minister-min-test",
                        "role": "Minister",
                        "start_date": "2020-01-01",
                        "end_date": "2024-01-01",
                        "confidence": 0.95,
                        "sources": [
                            {"id": "test", "url": "https://test", "retrieved": "2026-01-01"}
                        ],
                    }
                ],
                "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    # Schrijf een resolved staging-file die deze persoon nog actief noemt.
    staging = fake_data / "_staging"
    staging.mkdir()
    (staging / "roo-functies-2026-05-15.resolved.json").write_text(
        _json.dumps(
            {
                "proposals": [
                    {
                        "roo_functie_naam": "Minister",
                        "resolved_post_id": "post:minister-min-test",
                        "medewerkers": [
                            {
                                "naam": "dhr. I. Fired",
                                "resolved_person_id": "person:fired-i-1970",
                                # Geen end_date in ROO → ROO denkt nog actief.
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = run_audit(fake_data, today="2026-05-15")
    stale = [f for f in report.findings if f.category == "roo_stale_appointment"]
    assert stale, "Verwachtte roo_stale_appointment"
    assert "person:fired-i-1970" in stale[0].key
    assert "post:minister-min-test" in stale[0].key


def test_roo_stale_appointment_skipped_when_roo_also_ended(fake_data: Path) -> None:
    """ROO heeft eindDatum die niet later is dan polder → géén drift."""
    import json as _json

    person_path = fake_data / "personen" / "ok-i-1970.yaml"
    person_path.write_text(
        yaml.safe_dump(
            {
                "id": "person:ok-i-1970",
                "name": {"family": "OK", "initials": "I."},
                "birth": {"year": 1970},
                "mandaten": [
                    {
                        "id": "m1",
                        "organization_id": "org:min-test",
                        "post_id": "post:minister-min-test",
                        "role": "Minister",
                        "start_date": "2020-01-01",
                        "end_date": "2024-01-01",
                        "confidence": 0.95,
                        "sources": [
                            {"id": "test", "url": "https://test", "retrieved": "2026-01-01"}
                        ],
                    }
                ],
                "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    staging = fake_data / "_staging"
    staging.mkdir()
    (staging / "roo-functies-2026-05-15.resolved.json").write_text(
        _json.dumps(
            {
                "proposals": [
                    {
                        "resolved_post_id": "post:minister-min-test",
                        "medewerkers": [
                            {
                                "resolved_person_id": "person:ok-i-1970",
                                "end_date": "2023-12-31",  # eerder dan polder
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    report = run_audit(fake_data, today="2026-05-15")
    assert "roo_stale_appointment" not in _categories_in(report)


# ---------------------------------------------------------------------------
# dup_org_identifier / dup_post_role_org / dup_mandate
# ---------------------------------------------------------------------------


def _write_org(data: Path, subdir: str, slug: str, doc: dict) -> None:
    d = data / "organisaties" / subdir
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{slug}.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def test_dup_org_identifier_flags_shared_tooi(fake_data: Path) -> None:
    """Twee org-records met dezelfde tooi-code maar verschillende id zijn
    hetzelfde organisatie-record onder twee slugs."""
    base = {
        "type": "hoge-college",
        "names": [{"value": "Eerste Kamer", "lang": "nl"}],
        "sources": [{"id": "t", "url": "https://t", "retrieved": "2026-01-01"}],
    }
    _write_org(
        fake_data,
        "hoge-colleges",
        "ek-a",
        {
            **base,
            "id": "org:ek-a",
            "identifiers": {"tooi": "https://identifier.overheid.nl/tooi/id/oorg/oorg10001"},
        },
    )
    _write_org(
        fake_data,
        "hoge-colleges",
        "ek-b",
        {
            **base,
            "id": "org:ek-b",
            "identifiers": {"tooi": "https://identifier.overheid.nl/tooi/id/oorg/oorg10001"},
        },
    )
    report = run_audit(fake_data)
    assert "dup_org_identifier" in _categories_in(report)


def test_dup_org_identifier_ignores_shared_oin(fake_data: Path) -> None:
    """Een gedeelde OIN is rechtspersoon-niveau (adviescollege onder een
    ministerie); dat mag GEEN duplicaat-finding opleveren."""
    _write_org(
        fake_data,
        "adviescolleges",
        "awti",
        {
            "id": "org:adviescollege-awti",
            "type": "adviescollege",
            "names": [{"value": "AWTI", "lang": "nl"}],
            "identifiers": {
                "oin": "00000001003214400000",
                "tooi": "https://identifier.overheid.nl/tooi/id/oorg/oorg10253",
            },
            "sources": [{"id": "t", "url": "https://t", "retrieved": "2026-01-01"}],
        },
    )
    _write_org(
        fake_data,
        "ministeries",
        "ocw",
        {
            "id": "org:min-ocw",
            "type": "ministerie",
            "names": [{"value": "OCW", "lang": "nl"}],
            "identifiers": {
                "oin": "00000001003214400000",
                "tooi": "https://identifier.overheid.nl/tooi/id/ministerie/mnre1109",
            },
            "sources": [{"id": "t", "url": "https://t", "retrieved": "2026-01-01"}],
        },
    )
    report = run_audit(fake_data)
    assert "dup_org_identifier" not in _categories_in(report)


def test_dup_post_role_org_flags_same_role_same_org(fake_data: Path) -> None:
    """Twee posts, zelfde org, genormaliseerd dezelfde rol."""
    # Echte casus: labels verschillen alleen in casing/interpunctie
    # ("ministerie" vs "Ministerie"). _normalize_label collapst dat wel,
    # maar "en integratie" vs "integratie" bewust niet (conservatief).
    for slug, label in (
        ("directeur-si-a", "directeur Samenleving en Integratie, ministerie van SZW"),
        ("directeur-si-b", "Directeur Samenleving en Integratie, Ministerie van SZW"),
    ):
        (fake_data / "posten" / f"{slug}.yaml").write_text(
            yaml.safe_dump(
                {
                    "id": f"post:{slug}",
                    "organization_id": "org:min-test",
                    "label": label,
                    "classification": "ambtenaar",
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
    report = run_audit(fake_data)
    assert "dup_post_role_org" in _categories_in(report)


def test_dup_mandate_flags_double_entry(fake_data: Path) -> None:
    """Persoon met twee mandaten identiek op org+periode onder twee posten."""
    _write_person(
        fake_data,
        "jansen-j-1970",
        {
            "id": "person:jansen-j-1970",
            "name": {"family": "Jansen"},
            "birth": {"year": 1970},
            "sources": [{"id": "t", "url": "https://t", "retrieved": "2026-01-01"}],
            # NL-sleutel `mandaten` (data gebruikt deze; `mandates` bestaat
            # nergens). Identieke periode onder twee post-ids.
            "mandaten": [
                {
                    "id": "a",
                    "organization_id": "org:min-test",
                    "post_id": "post:minister-min-test",
                    "role": "Minister",
                    "start_date": "2022-01-10",
                    "end_date": "2024-07-02",
                    "sources": [{"id": "t", "url": "https://t", "retrieved": "2026-01-01"}],
                },
                {
                    "id": "b",
                    "organization_id": "org:min-test",
                    "post_id": "post:minister-zp-test",
                    "role": "Minister",
                    "start_date": "2022-01-10",
                    "end_date": "2024-07-02",
                    "sources": [{"id": "t", "url": "https://t", "retrieved": "2026-01-01"}],
                },
            ],
        },
    )
    report = run_audit(fake_data)
    assert "dup_mandate" in _categories_in(report)


def _write_post(data: Path, slug: str, doc: dict) -> None:
    (data / "posten" / f"{slug}.yaml").write_text(
        yaml.safe_dump(doc, sort_keys=False), encoding="utf-8"
    )


def test_dup_mandate_same_start_catches_minister_president_pattern(
    fake_data: Path,
) -> None:
    """Het Minister-President-bug: één benoeming onder twee post-ids met
    verschillende rol-labels, zelfde org + zelfde start_date. `dup_mandate`
    groepeert op post OF rol en mist dit; `dup_mandate_same_start` moet
    het wél vangen."""
    _write_post(
        fake_data,
        "minister-president-min-test-roo",
        {
            "id": "post:minister-president-min-test-roo",
            "organization_id": "org:min-test",
            "label": "Minister-President, Minister van Test",
            "classification": "bewindspersoon",
        },
    )
    _write_person(
        fake_data,
        "jetten-r-1987",
        {
            "id": "person:jetten-r-1987",
            "name": {"family": "Jetten"},
            "birth": {"year": 1987},
            "sources": [{"id": "t", "url": "https://t", "retrieved": "2026-01-01"}],
            "mandaten": [
                {
                    "id": "wd-1",
                    "organization_id": "org:min-test",
                    "post_id": "post:minister-min-test",
                    "role": "Minister-president van Nederland",
                    "start_date": "2026-02-23",
                    "end_date": None,
                    "sources": [{"id": "wd", "url": "https://wd", "retrieved": "2026-01-01"}],
                },
                {
                    "id": "roo-1",
                    "organization_id": "org:min-test",
                    "post_id": "post:minister-president-min-test-roo",
                    "role": "Minister-President en Minister belast met de leiding",
                    "start_date": "2026-02-23",
                    "end_date": None,
                    "sources": [{"id": "roo", "url": "https://roo", "retrieved": "2026-01-01"}],
                },
            ],
        },
    )
    report = run_audit(fake_data)
    cats = _categories_in(report)
    assert "dup_mandate_same_start" in cats
    assert "dup_post_same_office" in cats
    # `dup_mandate` mist dit juist (verschillende post én rol): regressie-anker.
    assert "dup_mandate" not in cats


def test_dup_post_same_office_ignores_distinct_offices(fake_data: Path) -> None:
    """Twee posten bij dezelfde org zonder enige persoon die ze via
    overlappende mandaten linkt is geen finding."""
    _write_post(
        fake_data,
        "staatssecretaris-min-test",
        {
            "id": "post:staatssecretaris-min-test",
            "organization_id": "org:min-test",
            "label": "Staatssecretaris van Test",
            "classification": "bewindspersoon",
        },
    )
    _write_person(
        fake_data,
        "vries-p-1960",
        {
            "id": "person:vries-p-1960",
            "name": {"family": "Vries"},
            "birth": {"year": 1960},
            "sources": [{"id": "t", "url": "https://t", "retrieved": "2026-01-01"}],
            "mandaten": [
                {
                    "id": "m1",
                    "organization_id": "org:min-test",
                    "post_id": "post:minister-min-test",
                    "role": "Minister",
                    "start_date": "2010-01-01",
                    "end_date": "2014-01-01",
                    "sources": [{"id": "t", "url": "https://t", "retrieved": "2026-01-01"}],
                }
            ],
        },
    )
    report = run_audit(fake_data)
    assert "dup_post_same_office" not in _categories_in(report)


def test_dup_mandate_flags_near_duplicate_start(fake_data: Path) -> None:
    """Zelfde org+rol, start_date een paar dagen uit elkaar (ORI-fetcher
    die per run een nieuw mandaat met de run-datum aanmaakt)."""
    _write_person(
        fake_data,
        "spin-p-1970",
        {
            "id": "person:spin-p-1970",
            "name": {"family": "Spin"},
            "birth": {"year": 1970},
            "sources": [{"id": "t", "url": "https://t", "retrieved": "2026-01-01"}],
            "mandaten": [
                {
                    "id": "a",
                    "organization_id": "org:gemeente-test",
                    "post_id": "post:raadslid-gemeente-test",
                    "role": "Raadslid gemeente Test",
                    "start_date": "2026-05-09",
                    "end_date": None,
                    "sources": [{"id": "ori", "url": "https://t", "retrieved": "2026-05-09"}],
                },
                {
                    "id": "b",
                    "organization_id": "org:gemeente-test",
                    "post_id": "post:raadslid-gemeente-test",
                    "role": "Raadslid gemeente Test",
                    "start_date": "2026-05-13",
                    "end_date": None,
                    "sources": [{"id": "ori", "url": "https://t", "retrieved": "2026-05-13"}],
                },
            ],
        },
    )
    report = run_audit(fake_data)
    assert "dup_mandate" in _categories_in(report)


def test_dup_mandate_ignores_consecutive_terms(fake_data: Path) -> None:
    """Opeenvolgende termijnen (A eindigt op de dag dat B begint) zijn
    geen duplicaat."""
    _write_person(
        fake_data,
        "ephraim-o-1965",
        {
            "id": "person:ephraim-o-1965",
            "name": {"family": "Ephraim"},
            "birth": {"year": 1965},
            "sources": [{"id": "t", "url": "https://t", "retrieved": "2026-01-01"}],
            "mandaten": [
                {
                    "id": "a",
                    "organization_id": "org:tweede-kamer",
                    "post_id": "post:kamerlid",
                    "role": "Kamerlid",
                    "start_date": "2021-05-13",
                    "end_date": "2023-08-02",
                    "sources": [{"id": "t", "url": "https://t", "retrieved": "2026-01-01"}],
                },
                {
                    "id": "b",
                    "organization_id": "org:tweede-kamer",
                    "post_id": "post:kamerlid",
                    "role": "Kamerlid",
                    "start_date": "2023-08-02",
                    "end_date": "2023-12-05",
                    "sources": [{"id": "t", "url": "https://t", "retrieved": "2026-01-01"}],
                },
            ],
        },
    )
    report = run_audit(fake_data)
    assert "dup_mandate" not in _categories_in(report)
