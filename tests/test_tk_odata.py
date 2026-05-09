"""Tests voor de TK OData fetcher.

Geen netwerk: alle tkapi-objecten worden gemockt via lichte stand-ins die de
properties opleveren waar de mapper op leunt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

from polder.fetchers.tk_odata import (
    ORG_ID_TWEEDE_KAMER,
    POST_ID_KAMERLID,
    SOURCE_ID,
    build_mandaat,
    ensure_org_and_post,
    merge_person,
    person_to_polder_record,
    slugify_person,
    write_person,
)

# ---------------------------------------------------------------------------
# Stand-ins voor tkapi-objecten
# ---------------------------------------------------------------------------


@dataclass
class FakeFractie:
    naam: str = ""
    afkorting: str = ""


@dataclass
class FakeFZP:
    """Stand-in voor tkapi.fractie.FractieZetelPersoon."""

    id: str = "fzp-1"
    van: date | None = date(2017, 3, 23)
    tot_en_met: date | None = None


@dataclass
class FakePersoon:
    """Stand-in voor tkapi.persoon.Persoon."""

    id: str = "p-rutte"
    achternaam: str = "Rutte"
    voornamen: str = "Mark"
    roepnaam: str = "Mark"
    initialen: str = "M.P."
    tussenvoegsel: str = ""
    titels: str = ""
    geslacht: str = "man"
    geboortedatum: date | None = date(1967, 2, 14)
    woonplaats: str = "Den Haag"
    contact_informaties: list[Any] = field(default_factory=list)


# ---------------------------------------------------------------------------
# slugify_person
# ---------------------------------------------------------------------------


def test_slugify_person_basis():
    assert slugify_person("Rutte", "M.P.", 1967) == "rutte-mp-1967"


def test_slugify_person_meerdere_initialen():
    assert slugify_person("Kaag", "S.A.M.", 1961) == "kaag-sam-1961"


def test_slugify_person_tussenvoegsel_stripped():
    # Familie "van der Linden" → tussenvoegsels uit slug, jaar erbij.
    assert slugify_person("van der Linden", "P.J.", 1980) == "linden-pj-1980"


def test_slugify_person_accenten():
    assert slugify_person("Özütok", "N.", 1969) == "ozutok-n-1969"


def test_slugify_person_zonder_initialen():
    # Edge case: lege initialen → familie + jaar.
    assert slugify_person("Jansen", "", 1975) == "jansen-1975"


def test_slugify_person_familie_met_streepje():
    assert slugify_person("Klein-Holland", "A.", 1970) == "klein-holland-a-1970"


# ---------------------------------------------------------------------------
# slugify_person fallback paden (D+C: Wikidata wint, anders UUID-suffix)
# ---------------------------------------------------------------------------


def test_slugify_person_birth_year_wint_van_fallback():
    """Beide gegeven: birth_year wint, fallback wordt genegeerd."""
    slug = slugify_person(
        "Rutte", "M.P.", 1967, fallback_uuid="0192a3f4-5d6e-7008-b9ab-cdef01234567"
    )
    assert slug == "rutte-mp-1967"


def test_slugify_person_uuid_fallback_zonder_jaar():
    """Geen birth_year + UUIDv7-fallback → eerste 8 hex als suffix."""
    slug = slugify_person(
        "Kewal", "S.", None, fallback_uuid="0192a3f4-5d6e-7008-b9ab-cdef01234567"
    )
    assert slug == "kewal-s-0192a3f4"


def test_slugify_person_uuid_fallback_zonder_initialen():
    slug = slugify_person(
        "Kewal", "", None, fallback_uuid="0192a3f4-5d6e-7008-b9ab-cdef01234567"
    )
    assert slug == "kewal-0192a3f4"


def test_slugify_person_geen_jaar_geen_uuid_raised():
    with pytest.raises(ValueError, match="geboortejaar of fallback_uuid"):
        slugify_person("Rutte", "M.P.", None)


def test_slugify_person_uuid_fallback_lowercased_en_dashstrip():
    """Hex met hoofdletters en streepjes wordt genormaliseerd."""
    slug = slugify_person(
        "Test", "T.", None, fallback_uuid="ABCDEF12-3456-7890-ABCD-EF1234567890"
    )
    assert slug == "test-t-abcdef12"


def test_slugify_person_uuid_fallback_invalid_hex_raised():
    """Suffix is niet hex (bijv. een letter buiten a-f)."""
    with pytest.raises(ValueError, match="hex"):
        slugify_person("Test", "T.", None, fallback_uuid="zzzzzzzz")


def test_slugify_person_uuid_fallback_te_kort_raised():
    """UUID-string moet >=8 hex-tekens leveren (na dash-strip)."""
    with pytest.raises(ValueError, match="hex"):
        slugify_person("Test", "T.", None, fallback_uuid="abc")


def test_slugify_person_fallback_slug_voldoet_aan_schema_pattern():
    """De UUID-fallback-slug moet matchen op het Persoon.id-patroon."""
    import re

    pattern = re.compile(
        r"^person:([a-z][a-z0-9-]*-)?([0-9]{4}|[0-9]{7,}|[0-9a-f]{8})$"
    )
    slug = slugify_person(
        "Kewal", "S.", None, fallback_uuid="0192a3f4-5d6e-7008-b9ab-cdef01234567"
    )
    assert pattern.match(f"person:{slug}")


# ---------------------------------------------------------------------------
# person_to_polder_record
# ---------------------------------------------------------------------------


def test_person_record_basis_velden():
    persoon = FakePersoon()
    rec = person_to_polder_record(persoon, mandaten=[], today="2026-05-09")
    assert rec is not None
    assert rec["id"] == "person:rutte-mp-1967"
    assert rec["identifiers"] == {"tk_persoon_id": "p-rutte"}
    assert rec["name"]["full"] == "Mark Rutte"
    assert rec["name"]["family"] == "Rutte"
    assert rec["name"]["initials"] == "M.P."
    assert rec["gender"] == "m"


def test_person_record_birth_alleen_jaar():
    persoon = FakePersoon()
    rec = person_to_polder_record(persoon, mandaten=[], today="2026-05-09")
    assert rec is not None
    assert rec["birth"] == {"year": 1967}
    # Geen maand/dag gelekt.
    assert "month" not in rec["birth"]
    assert "day" not in rec["birth"]


def test_person_record_initials_normalisatie():
    persoon = FakePersoon(initialen="MP")  # zonder punten
    rec = person_to_polder_record(persoon, mandaten=[], today="2026-05-09")
    assert rec is not None
    assert rec["name"]["initials"] == "M.P."


def test_person_record_gender_vrouw():
    persoon = FakePersoon(achternaam="Kaag", initialen="S.A.M.", geslacht="vrouw")
    rec = person_to_polder_record(persoon, mandaten=[], today="2026-05-09")
    assert rec is not None
    assert rec["gender"] == "f"


def test_person_record_zonder_geboortedatum_wordt_geskipt():
    persoon = FakePersoon(geboortedatum=None)
    rec = person_to_polder_record(persoon, mandaten=[], today="2026-05-09")
    assert rec is None


def test_person_record_zonder_achternaam_wordt_geskipt():
    persoon = FakePersoon(achternaam="")
    rec = person_to_polder_record(persoon, mandaten=[], today="2026-05-09")
    assert rec is None


def test_person_record_tussenvoegsel_in_family_niet_in_slug():
    persoon = FakePersoon(
        id="p-x",
        achternaam="Linden",
        tussenvoegsel="van der",
        roepnaam="Pieter",
        voornamen="Pieter Jan",
        initialen="P.J.",
        geboortedatum=date(1980, 6, 1),
    )
    rec = person_to_polder_record(persoon, mandaten=[], today="2026-05-09")
    assert rec is not None
    assert rec["id"] == "person:linden-pj-1980"
    assert rec["name"]["family"] == "van der Linden"
    assert rec["name"]["full"] == "Pieter van der Linden"


def test_person_record_source_url_naar_persoon():
    persoon = FakePersoon()
    rec = person_to_polder_record(persoon, mandaten=[], today="2026-05-09")
    assert rec is not None
    sources = rec["sources"]
    assert len(sources) == 1
    assert sources[0]["id"] == SOURCE_ID
    assert "Persoon(p-rutte)" in sources[0]["url"]
    assert sources[0]["retrieved"] == "2026-05-09"


# ---------------------------------------------------------------------------
# build_mandaat
# ---------------------------------------------------------------------------


def test_build_mandaat_actief():
    fzp = FakeFZP(id="z1", van=date(2017, 3, 23), tot_en_met=None)
    mandaat = build_mandaat(
        fzp=fzp,
        fractie_naam="Volkspartij voor Vrijheid en Democratie",
        fractie_afkorting="VVD",
        today="2026-05-09",
    )
    assert mandaat["organization_id"] == ORG_ID_TWEEDE_KAMER
    assert mandaat["post_id"] == POST_ID_KAMERLID
    assert mandaat["role"] == "Kamerlid voor VVD"
    assert mandaat["start_date"] == "2017-03-23"
    assert mandaat["end_date"] is None
    assert mandaat["sources"][0]["id"] == SOURCE_ID
    assert "FractieZetelPersoon(z1)" in mandaat["sources"][0]["url"]


def test_build_mandaat_afgesloten():
    fzp = FakeFZP(id="z2", van=date(2010, 6, 17), tot_en_met=date(2012, 9, 20))
    mandaat = build_mandaat(
        fzp=fzp,
        fractie_naam="GroenLinks",
        fractie_afkorting="GL",
        today="2026-05-09",
    )
    assert mandaat["start_date"] == "2010-06-17"
    assert mandaat["end_date"] == "2012-09-20"


def test_build_mandaat_uniek_id():
    fzp = FakeFZP()
    a = build_mandaat(fzp=fzp, fractie_naam="X", fractie_afkorting="X")
    b = build_mandaat(fzp=fzp, fractie_naam="X", fractie_afkorting="X")
    assert a["id"] != b["id"]


# ---------------------------------------------------------------------------
# write_person + current/historisch routing
# ---------------------------------------------------------------------------


def _record_with_mandaat(active: bool, slug: str = "rutte-mp-1967") -> dict[str, Any]:
    return {
        "id": f"person:{slug}",
        "identifiers": {"tk_persoon_id": "p-rutte"},
        "name": {"full": "Mark Rutte", "family": "Rutte", "initials": "M.P."},
        "birth": {"year": 1967},
        "gender": "m",
        "mandaten": [
            {
                "id": "m1",
                "organization_id": ORG_ID_TWEEDE_KAMER,
                "post_id": POST_ID_KAMERLID,
                "role": "Kamerlid voor VVD",
                "start_date": "2002-07-22",
                "end_date": None if active else "2010-10-14",
                "sources": [
                    {
                        "id": SOURCE_ID,
                        "url": "https://gegevensmagazijn.tweedekamer.nl/OData/v4/2.0/FractieZetelPersoon(x)",
                        "retrieved": "2026-05-09",
                    }
                ],
            }
        ],
        "sources": [
            {
                "id": SOURCE_ID,
                "url": "https://gegevensmagazijn.tweedekamer.nl/OData/v4/2.0/Persoon(p-rutte)",
                "retrieved": "2026-05-09",
            }
        ],
    }


def test_write_person_actief_naar_current(tmp_path: Path):
    rec = _record_with_mandaat(active=True)
    target = write_person(rec, tmp_path)
    assert target == tmp_path / "current" / "rutte-mp-1967.yaml"
    assert target.exists()


def test_write_person_afgesloten_naar_historisch(tmp_path: Path):
    rec = _record_with_mandaat(active=False)
    target = write_person(rec, tmp_path)
    assert target == tmp_path / "historisch" / "rutte-mp-1967.yaml"
    assert target.exists()


def test_write_person_verplaatst_naar_historisch(tmp_path: Path):
    """Als persoon eerst actief was en nu afgesloten: oude file moet weg."""
    active = _record_with_mandaat(active=True)
    write_person(active, tmp_path)
    assert (tmp_path / "current" / "rutte-mp-1967.yaml").exists()

    closed = _record_with_mandaat(active=False)
    target = write_person(closed, tmp_path)
    assert target == tmp_path / "historisch" / "rutte-mp-1967.yaml"
    assert not (tmp_path / "current" / "rutte-mp-1967.yaml").exists()
    assert (tmp_path / "historisch" / "rutte-mp-1967.yaml").exists()


def test_write_person_yaml_inhoud_volgorde(tmp_path: Path):
    rec = _record_with_mandaat(active=True)
    target = write_person(rec, tmp_path)
    content = target.read_text(encoding="utf-8")
    # `id` komt voor `name`.
    assert content.index("id:") < content.index("name:")
    parsed = yaml.safe_load(content)
    assert parsed["id"] == "person:rutte-mp-1967"
    assert parsed["birth"] == {"year": 1967}


# ---------------------------------------------------------------------------
# merge_person
# ---------------------------------------------------------------------------


def test_merge_person_lokaal_blijft_staan():
    existing = {
        "id": "person:rutte-mp-1967",
        "identifiers": {
            "tk_persoon_id": "p-rutte",
            "wikidata": "Q57792",
        },
        "name": {"full": "Mark Rutte", "family": "Rutte"},
        "birth": {"year": 1967},
        "sources": [
            {
                "id": "wikidata",
                "url": "https://www.wikidata.org/wiki/Q57792",
                "retrieved": "2025-01-01",
            },
        ],
    }
    new = {
        "id": "person:rutte-mp-1967",
        "identifiers": {"tk_persoon_id": "p-rutte"},
        "name": {"full": "Mark Rutte", "family": "Rutte", "initials": "M.P."},
        "birth": {"year": 1967},
        "sources": [
            {"id": SOURCE_ID, "url": "https://example/x", "retrieved": "2026-05-09"},
        ],
    }
    merged = merge_person(existing, new)
    # Wikidata-Q blijft, TK-id staat er ook.
    assert merged["identifiers"]["wikidata"] == "Q57792"
    assert merged["identifiers"]["tk_persoon_id"] == "p-rutte"
    # Beide bronnen aanwezig.
    src_ids = {s["id"] for s in merged["sources"]}
    assert src_ids == {"wikidata", SOURCE_ID}
    # Nieuwe initials in name.
    assert merged["name"]["initials"] == "M.P."


def test_merge_person_mandaat_id_blijft_stabiel():
    existing = {
        "id": "person:rutte-mp-1967",
        "name": {"full": "Mark Rutte", "family": "Rutte"},
        "mandaten": [
            {
                "id": "stable-uuid-1",
                "organization_id": ORG_ID_TWEEDE_KAMER,
                "post_id": POST_ID_KAMERLID,
                "role": "Kamerlid voor VVD",
                "start_date": "2002-07-22",
                "end_date": "2006-11-30",
                "sources": [
                    {"id": SOURCE_ID, "url": "https://x/old", "retrieved": "2025-01-01"},
                ],
            }
        ],
        "sources": [
            {"id": SOURCE_ID, "url": "https://x", "retrieved": "2025-01-01"},
        ],
    }
    new = {
        "id": "person:rutte-mp-1967",
        "name": {"full": "Mark Rutte", "family": "Rutte"},
        "mandaten": [
            {
                "id": "fresh-uuid-2",
                "organization_id": ORG_ID_TWEEDE_KAMER,
                "post_id": POST_ID_KAMERLID,
                "role": "Kamerlid voor VVD",
                "start_date": "2002-07-22",
                "end_date": "2006-11-30",
                "sources": [
                    {"id": SOURCE_ID, "url": "https://x/new", "retrieved": "2026-05-09"},
                ],
            }
        ],
        "sources": [
            {"id": SOURCE_ID, "url": "https://x", "retrieved": "2026-05-09"},
        ],
    }
    merged = merge_person(existing, new)
    assert len(merged["mandaten"]) == 1
    assert merged["mandaten"][0]["id"] == "stable-uuid-1"


# ---------------------------------------------------------------------------
# ensure_org_and_post
# ---------------------------------------------------------------------------


def test_ensure_org_and_post_creates_files(tmp_path: Path):
    org_path, post_path = ensure_org_and_post(tmp_path, today="2026-05-09")
    assert org_path.exists()
    assert post_path.exists()

    org_data = yaml.safe_load(org_path.read_text())
    assert org_data["id"] == ORG_ID_TWEEDE_KAMER
    assert org_data["type"] == "hoge-college"
    assert org_data["sources"][0]["id"] == SOURCE_ID

    post_data = yaml.safe_load(post_path.read_text())
    assert post_data["id"] == POST_ID_KAMERLID
    assert post_data["organization_id"] == ORG_ID_TWEEDE_KAMER
    assert post_data["classification"] == "kamerlid"
    assert post_data["seat_count"] == 150


def test_ensure_org_and_post_idempotent(tmp_path: Path):
    org_path, post_path = ensure_org_and_post(tmp_path, today="2026-05-09")
    first_org = org_path.read_text()
    first_post = post_path.read_text()
    # Tweede keer laat ze met rust.
    ensure_org_and_post(tmp_path, today="2099-01-01")
    assert org_path.read_text() == first_org
    assert post_path.read_text() == first_post


# ---------------------------------------------------------------------------
# CLI smoke (zonder network: --dry-run met monkeypatched fetch)
# ---------------------------------------------------------------------------


def test_cli_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    # tkapi.TKApi mocken zodat er geen netwerkverkeer is.
    import polder.fetchers.tk_odata as mod

    class FakeApi:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    def fake_fetch(api: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return [_record_with_mandaat(active=True)]

    # Monkeypatch het submodule TKApi en de fetch-functie.
    import tkapi

    monkeypatch.setattr(tkapi, "TKApi", FakeApi)
    monkeypatch.setattr(mod, "fetch_persons_with_fractiezetels", fake_fetch)

    out = tmp_path / "personen"
    rc = mod.main(
        [
            "--limit",
            "1",
            "--dry-run",
            "--out",
            str(out),
            "--data-root",
            str(tmp_path),
        ]
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "Wrote" in captured.err
