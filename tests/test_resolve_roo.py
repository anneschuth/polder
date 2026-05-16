"""Tests voor `polder.resolve_roo`."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from polder.resolve_roo import (
    build_index,
    confirm_mandaat,
    enrich_post,
    find_open_mandate,
    find_person,
    find_post_for_functie,
    parse_roo_name,
    resolve,
)

# ---------------------------------------------------------------------------
# Name parsing
# ---------------------------------------------------------------------------


def test_parse_roo_name_extracts_family_and_full_initials():
    assert parse_roo_name("dhr. H.J. (Henkjan) Derks MGM") == ("derks", "hj")
    assert parse_roo_name("Dhr. B.C.M. Vostermans") == ("vostermans", "bcm")
    assert parse_roo_name("mw. drs. M. (Mirjam) van Leeuwen") == ("van leeuwen", "m")


def test_parse_roo_name_without_initials():
    # `IGK` aan het einde wordt als postnominaal gestript (all-caps 2-5 chars).
    # Dat is geen kwaad: matching gaat op family + initials, en deze entiteit
    # is geen persoon maar een organisatie-mailbox. Hier checken we alleen
    # dat we niet crashen en geen initials produceren.
    family, init = parse_roo_name("Algemeen IGK")
    assert init == ""
    assert "algemeen" in family
    # Geen titel, geen initials, alleen naam.
    assert parse_roo_name("Foo Bar") == ("foo bar", "")


def test_parse_roo_name_strips_postnominals():
    """`MGM`, `MA`, `MSc` aan het einde knippen we eraf."""
    family, _init = parse_roo_name("dhr. H.J. Derks MGM")
    assert family == "derks"


# ---------------------------------------------------------------------------
# Index + person matching
# ---------------------------------------------------------------------------


def _setup_data(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    (data / "personen").mkdir(parents=True)
    (data / "posten").mkdir()
    (data / "organisaties" / "ministeries").mkdir(parents=True)
    return data


def _write_person(data: Path, slug: str, family: str, initials: str, given: str = "", **kw):
    rec = {
        "id": f"person:{slug}",
        "name": {"family": family, "initials": initials, "given": given},
        "birth": {"year": 1970},
        "sources": [{"id": "test", "url": "https://test", "retrieved": "2026-01-01"}],
        "mandaten": kw.get("mandaten", []),
    }
    (data / "personen" / f"{slug}.yaml").write_text(
        yaml.safe_dump(rec, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


def _write_post(data: Path, slug: str, org: str, label: str):
    rec = {
        "id": f"post:{slug}",
        "organization_id": org,
        "label": label,
        "classification": "overig",
        "valid_from": "2020-01-01",
    }
    (data / "posten" / f"{slug}.yaml").write_text(
        yaml.safe_dump(rec, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


def test_find_person_full_init_match(tmp_path: Path):
    data = _setup_data(tmp_path)
    _write_person(data, "derks-hj-1970", "Derks", "H.J.")
    idx = build_index(data)
    pid, kind = find_person(idx, "derks", "hj")
    assert pid == "person:derks-hj-1970"
    assert kind == "family+full-init"


def test_find_person_first_init_fallback(tmp_path: Path):
    """Polder heeft alleen `H.`, ROO geeft `H.J.` — match op eerste letter."""
    data = _setup_data(tmp_path)
    _write_person(data, "derks-h-1970", "Derks", "H.")
    idx = build_index(data)
    pid, kind = find_person(idx, "derks", "hj")
    assert pid == "person:derks-h-1970"
    assert kind == "family+first-init"


def test_find_person_ambiguous_returns_none(tmp_path: Path):
    """Twee personen met dezelfde family+full-init → ambiguous, geen match."""
    data = _setup_data(tmp_path)
    _write_person(data, "derks-hj-1970", "Derks", "H.J.")
    _write_person(data, "derks-hj-1980", "Derks", "H.J.")
    idx = build_index(data)
    pid, kind = find_person(idx, "derks", "hj")
    assert pid is None
    assert kind == "ambiguous-init"


def test_find_person_uses_given_when_initials_short(tmp_path: Path):
    """Polder slaat `given='H.J.'` op maar `initials='H.'` — match werkt nog."""
    data = _setup_data(tmp_path)
    _write_person(data, "derks-h-1970", "Derks", "H.", given="H.J.")
    idx = build_index(data)
    pid, kind = find_person(idx, "derks", "hj")
    assert pid == "person:derks-h-1970"
    assert kind == "family+full-init"


# ---------------------------------------------------------------------------
# Post matching
# ---------------------------------------------------------------------------


def test_find_post_via_suggested_post_id(tmp_path: Path):
    data = _setup_data(tmp_path)
    _write_post(data, "minister-min-fin", "org:min-fin", "Minister van Financiën")
    idx = build_index(data)
    pid = find_post_for_functie(
        idx, "org:min-fin", "Minister van Financiën", "post:minister-min-fin"
    )
    assert pid == "post:minister-min-fin"


def test_find_post_via_label_slug_match(tmp_path: Path):
    """Suggested-id matcht niet, maar label-slug matcht binnen org."""
    data = _setup_data(tmp_path)
    _write_post(data, "min-fin-staatssecretaris", "org:min-fin", "Staatssecretaris van Financien")
    idx = build_index(data)
    pid = find_post_for_functie(
        idx, "org:min-fin", "Staatssecretaris van Financien", "post:does-not-exist"
    )
    assert pid == "post:min-fin-staatssecretaris"


def test_find_post_returns_none_when_no_match(tmp_path: Path):
    data = _setup_data(tmp_path)
    idx = build_index(data)
    assert (
        find_post_for_functie(idx, "org:min-fin", "Iets nieuws", "post:iets-nieuws-min-fin") is None
    )


# ---------------------------------------------------------------------------
# Lane 1: post enrichment
# ---------------------------------------------------------------------------


def test_enrich_post_adds_roo_fields(tmp_path: Path):
    post = {
        "id": "post:x",
        "organization_id": "org:y",
        "label": "X",
        "classification": "overig",
        "valid_from": "2020-01-01",
    }
    proposal = {
        "roo_functie_id": "12345",
        "roo_functie_naam": "X-functie",
        "parent_roo_id": "999",
    }
    changed = enrich_post(Path("/tmp/x.yaml"), post, proposal, today="2026-05-15")
    assert changed is True
    assert post["roo_functie_id"] == "12345"
    assert post["roo_naam"] == "X-functie"
    assert post["sources"][0]["id"] == "roo"
    # ROO eist non-empty path-segment ná de roo_id; we hangen de polder-org-slug eraan.
    assert post["sources"][0]["url"] == "https://organisaties.overheid.nl/999/y"


def test_enrich_post_idempotent(tmp_path: Path):
    """Tweede run met dezelfde proposal moet niets meer veranderen."""
    post = {
        "id": "post:x",
        "organization_id": "org:y",
        "label": "X",
        "classification": "overig",
        "valid_from": "2020-01-01",
        "roo_functie_id": "12345",
        "roo_naam": "X-functie",
        "sources": [{"id": "roo", "url": "https://x", "retrieved": "2026-05-14"}],
    }
    proposal = {"roo_functie_id": "12345", "roo_functie_naam": "X-functie"}
    assert enrich_post(Path("/tmp/x.yaml"), post, proposal, today="2026-05-15") is False


# ---------------------------------------------------------------------------
# Lane 2: mandaat bevestiging
# ---------------------------------------------------------------------------


def test_find_open_mandate_returns_open():
    person = {
        "mandaten": [
            {"post_id": "post:a", "end_date": "2024-01-01"},
            {"post_id": "post:a", "end_date": None},
            {"post_id": "post:b", "end_date": None},
        ]
    }
    m = find_open_mandate(person, "post:a")
    assert m is not None and m["end_date"] is None


def test_confirm_mandaat_appends_roo_source():
    mandaat = {
        "post_id": "post:a",
        "sources": [{"id": "staatscourant", "url": "https://stc"}],
    }
    proposal = {"parent_roo_id": "999", "parent_org_id": "org:y"}
    med = {"roo_medewerker_id": "12345"}
    changed = confirm_mandaat(mandaat, proposal, med, today="2026-05-15")
    assert changed is True
    # URL wijst naar de organisatie (medewerker-URLs bestaan niet in ROO);
    # link tussen bron en specifieke medewerker zit in `roo_medewerker_id=...` field.
    roo_source = next(s for s in mandaat["sources"] if s["id"] == "roo")
    assert roo_source["url"] == "https://organisaties.overheid.nl/999/y"
    assert "roo_medewerker_id=12345" in roo_source["fields"]
    # Idempotent.
    assert confirm_mandaat(mandaat, proposal, med, today="2026-05-15") is False


# Lane 3 (auto-create mandaat uit ROO-only) is verwijderd wegens
# two-source-rule-schending. De vervangende staging-proposal-route wordt
# gedekt door `test_resolve_new_mandaat_goes_to_staging` hieronder.


# ---------------------------------------------------------------------------
# End-to-end resolve
# ---------------------------------------------------------------------------


def test_resolve_end_to_end_two_lanes_plus_staging(tmp_path: Path):
    data = _setup_data(tmp_path)
    _write_post(data, "minister-min-fin", "org:min-fin", "Minister van Financiën")
    # Persoon met bestaand open mandaat → lane 2 (bevestiging).
    _write_person(
        data,
        "klop-jp-1970",
        "Klop",
        "J.P.",
        mandaten=[
            {
                "id": "m1",
                "organization_id": "org:min-fin",
                "post_id": "post:minister-min-fin",
                "role": "Minister",
                "start_date": "2020-01-01",
                "end_date": None,
                "confidence": 0.95,
                "sources": [
                    {"id": "staatscourant", "url": "https://stc", "retrieved": "2020-01-02"}
                ],
            }
        ],
    )
    # Persoon ZONDER mandaat op die post → mag GEEN auto-mandaat krijgen
    # (two-source rule); moet als staging-proposal landen.
    _write_person(data, "nieuw-ab-1980", "Nieuw", "A.B.")

    proposals_payload = {
        "proposals": [
            {
                "roo_functie_id": "999",
                "roo_functie_naam": "Minister van Financiën",
                "parent_org_id": "org:min-fin",
                "parent_roo_id": "12345",
                "suggested_post_id": "post:minister-min-fin",
                "medewerkers": [
                    {"roo_medewerker_id": "111", "naam": "dhr. J.P. Klop"},
                    {
                        "roo_medewerker_id": "222",
                        "naam": "dhr. A.B. Nieuw",
                        "start_date": "2024-01-01",
                    },
                ],
            },
            # Functie zonder bestaande post → naar staging.
            {
                "roo_functie_id": "888",
                "roo_functie_naam": "Onbekende functie",
                "parent_org_id": "org:min-fin",
                "parent_roo_id": "12345",
                "suggested_post_id": "post:onbekende-functie-min-fin",
                "medewerkers": [],
            },
        ]
    }
    proposals_file = tmp_path / "props.json"
    proposals_file.write_text(json.dumps(proposals_payload), encoding="utf-8")

    stats, _staging = resolve(proposals_file, data, today="2026-05-15")

    assert stats.posts_enriched == 1  # lane 1
    assert stats.mandaten_confirmed == 1  # lane 2 (Klop)
    assert stats.post_not_found == 1  # Onbekende functie → staging
    # Nieuw + Onbekende functie → minstens 2 staging-entries.
    assert stats.proposals_to_staging >= 2

    # Post heeft roo-fields (lane 1).
    post = yaml.safe_load((data / "posten" / "minister-min-fin.yaml").read_text())
    assert post["roo_functie_id"] == "999"
    assert post["sources"][0]["id"] == "roo"

    # Klop's mandaat heeft een roo-source extra (lane 2).
    klop = yaml.safe_load((data / "personen" / "klop-jp-1970.yaml").read_text())
    assert any(s["id"] == "roo" for s in klop["mandaten"][0]["sources"])

    # Nieuw heeft GEEN auto-mandaat gekregen (two-source rule).
    nieuw = yaml.safe_load((data / "personen" / "nieuw-ab-1980.yaml").read_text())
    assert not (nieuw.get("mandaten") or [])

    # Staging-file bevat de nieuwe-benoeming-proposal met reasoning.
    staging_files = list((data / "_staging").glob("roo-functies-*.unresolved.json"))
    assert staging_files
    payload = json.loads(staging_files[0].read_text(encoding="utf-8"))
    assert payload["n_unresolved"] == stats.proposals_to_staging
    new_mandaat_props = [
        p for p in payload["proposals"] if p.get("_resolution") == "new_mandaat_needs_second_source"
    ]
    assert len(new_mandaat_props) == 1
    p = new_mandaat_props[0]
    assert p["resolved_person_id"] == "person:nieuw-ab-1980"
    assert p["confidence"] == 0.7
    assert "two-source rule" in p["confidence_reasoning"]


def test_resolve_dry_run_does_not_write(tmp_path: Path):
    data = _setup_data(tmp_path)
    _write_post(data, "minister-min-fin", "org:min-fin", "Minister van Financiën")

    proposals_payload = {
        "proposals": [
            {
                "roo_functie_id": "999",
                "roo_functie_naam": "Minister van Financiën",
                "parent_org_id": "org:min-fin",
                "parent_roo_id": "12345",
                "suggested_post_id": "post:minister-min-fin",
                "medewerkers": [],
            }
        ]
    }
    proposals_file = tmp_path / "props.json"
    proposals_file.write_text(json.dumps(proposals_payload), encoding="utf-8")
    stats, _staging = resolve(proposals_file, data, dry_run=True, today="2026-05-15")
    assert stats.posts_enriched == 1
    # Disk niet aangeraakt.
    post = yaml.safe_load((data / "posten" / "minister-min-fin.yaml").read_text())
    assert "roo_functie_id" not in post


# ---------------------------------------------------------------------------
# Helpers (test-gaten dichtmaken)
# ---------------------------------------------------------------------------


def test_compact_initials_strips_punctuation():
    from polder.resolve_roo import compact_initials

    assert compact_initials("H.J.") == "hj"
    assert compact_initials("M.F.W.") == "mfw"
    assert compact_initials("J. P.") == "jp"
    assert compact_initials("") == ""
    assert compact_initials(None) == ""


def test_slugify_label_basic():
    from polder.resolve_roo import _slugify_label

    assert _slugify_label("Minister van Financiën") == "minister-van-financien"
    assert _slugify_label("Foo  Bar") == "foo-bar"
    assert _slugify_label("") == ""
    assert _slugify_label("???") == ""


def test_roo_org_url_via_resolve_roo_helper():
    """`_roo_org_url` delegeert naar de centrale `roo_org_url`. Verifieer dat
    org_id-slug correct wordt geëxtraheerd."""
    from polder.resolve_roo import _roo_org_url

    assert _roo_org_url("21849", "org:gemeente-helmond") == (
        "https://organisaties.overheid.nl/21849/gemeente-helmond"
    )
    # Geen org_id → fallback `x` (uit roo_org_url).
    assert _roo_org_url("21849", None) == "https://organisaties.overheid.nl/21849/x"
    # Geen roo_id → PRIMARY_URL.
    assert "exportOO.xml" in _roo_org_url(None, "org:foo")


def test_atomic_write_yaml_skips_no_op(tmp_path: Path):
    from polder.resolve_roo import _atomic_write_yaml

    p = tmp_path / "x.yaml"
    data = {"id": "org:foo", "type": "ministerie"}
    _atomic_write_yaml(p, data)
    mtime1 = p.stat().st_mtime_ns
    # Tweede call met identieke inhoud → geen rewrite.
    import time

    time.sleep(0.01)
    _atomic_write_yaml(p, data)
    mtime2 = p.stat().st_mtime_ns
    assert mtime1 == mtime2, "no-op write zou file niet moeten aanraken"


def test_atomic_write_yaml_replaces_on_change(tmp_path: Path):
    from polder.resolve_roo import _atomic_write_yaml

    p = tmp_path / "x.yaml"
    _atomic_write_yaml(p, {"id": "org:foo"})
    _atomic_write_yaml(p, {"id": "org:bar"})
    assert "bar" in p.read_text(encoding="utf-8")


def test_resolve_new_mandaat_goes_to_staging_not_canonical(tmp_path: Path):
    """Een ROO-only nieuwe benoeming mag NOOIT direct in data/personen
    belanden (two-source rule). Het hoort als staging-proposal met
    confidence 0.7 + reasoning."""
    data = _setup_data(tmp_path)
    _write_post(data, "burgemeester-gemeente-x", "org:gemeente-x", "Burgemeester")
    _write_person(data, "foo-ab-1980", "Foo", "A.B.")

    proposals_payload = {
        "proposals": [
            {
                "roo_functie_id": "1",
                "roo_functie_naam": "Burgemeester",
                "parent_org_id": "org:gemeente-x",
                "parent_roo_id": "5",
                "suggested_post_id": "post:burgemeester-gemeente-x",
                "medewerkers": [
                    {
                        "roo_medewerker_id": "100",
                        "naam": "dhr. A.B. Foo",
                        "start_date": "2024-01-01",
                    }
                ],
            }
        ]
    }
    pf = tmp_path / "props.json"
    pf.write_text(json.dumps(proposals_payload), encoding="utf-8")
    stats, _ = resolve(pf, data, today="2026-05-15")

    # Geen canoniek mandaat aangemaakt.
    foo = yaml.safe_load((data / "personen" / "foo-ab-1980.yaml").read_text())
    assert not (foo.get("mandaten") or [])
    # Wel een staging-proposal.
    assert stats.proposals_to_staging == 1
    sf = next((data / "_staging").glob("roo-functies-*.unresolved.json"))
    payload = json.loads(sf.read_text(encoding="utf-8"))
    prop = payload["proposals"][0]
    assert prop["_resolution"] == "new_mandaat_needs_second_source"
    assert prop["confidence"] == 0.7
    assert "confidence_reasoning" in prop
