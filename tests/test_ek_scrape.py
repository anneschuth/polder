"""Tests voor de Eerste-Kamer scrape fetcher.

Mockt httpx via fixture-HTML in ``tests/fixtures/ek/``. Geen netwerk.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from polder.fetchers.ek_scrape import (
    EK_BASE,
    ORG_ID_EERSTE_KAMER,
    POST_ID_SENATOR,
    SOURCE_ID,
    EkLidIndexEntry,
    _parse_dutch_date,
    _split_initials_and_family,
    _strip_titles,
    build_record,
    ensure_org_and_post,
    extract_index_entries,
    fetch_lid_pagina,
    parse_lid_pagina,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ek"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# String helpers
# ---------------------------------------------------------------------------


def test_strip_titles_pre_and_post():
    assert _strip_titles("Dr. M.L. Vos (GroenLinks-PvdA)") == "M.L. Vos"
    assert _strip_titles("R. van Aelst-den Uijl MA (SP)") == "R. van Aelst-den Uijl"
    assert _strip_titles("prof. dr. E.B. van Apeldoorn") == "E.B. van Apeldoorn"
    assert _strip_titles("A.J.M. van Kesteren  (PVV)") == "A.J.M. van Kesteren"


def test_split_initials_and_family():
    assert _split_initials_and_family("M.L. Vos") == ("M.L.", "Vos")
    assert _split_initials_and_family("R. van Aelst-den Uijl") == ("R.", "van Aelst-den Uijl")
    assert _split_initials_and_family("A.J.M. van Kesteren") == ("A.J.M.", "van Kesteren")


def test_parse_dutch_date_basis():
    assert _parse_dutch_date("13 juni 2023") == date(2023, 6, 13)
    assert _parse_dutch_date("1 december 1988") == date(1988, 12, 1)
    assert _parse_dutch_date("31 maart 1970") == date(1970, 3, 31)


def test_parse_dutch_date_invalid():
    assert _parse_dutch_date("foo bar baz") is None
    assert _parse_dutch_date("32 januari 2024") is None
    assert _parse_dutch_date("13 invalidmonth 2024") is None


# ---------------------------------------------------------------------------
# Index parsing
# ---------------------------------------------------------------------------


def test_extract_index_entries_returns_many():
    entries = extract_index_entries(_load("alle_leden.html"))
    assert len(entries) > 50  # de live snapshot heeft 74 leden
    # Iedere entry heeft minimaal slug + display + party.
    for e in entries:
        assert e.slug
        assert e.display_name
        assert e.party


def test_extract_index_entry_specific_member():
    entries = extract_index_entries(_load("alle_leden.html"))
    by_slug = {e.slug: e for e in entries}
    aelst = by_slug["r_van_aelst_den_uijl_ma_sp"]
    assert aelst.display_name == "R. van Aelst-den Uijl MA"
    assert aelst.party == "SP"
    assert aelst.birth_date == date(1988, 12, 1)


def test_extract_index_birthdate_optional():
    """Niet alle leden hebben een geboortedatum in de index."""
    entries = extract_index_entries(_load("alle_leden.html"))
    # Sommige leden mogen ontbrekende geboortedatum hebben (vacante woonplaats etc.).
    none_count = sum(1 for e in entries if e.birth_date is None)
    # Maar de overgrote meerderheid moet er een hebben.
    assert none_count < len(entries) // 2


# ---------------------------------------------------------------------------
# Lid-pagina parsing
# ---------------------------------------------------------------------------


def test_parse_lid_pagina_aelst():
    parsed = parse_lid_pagina(_load("aelst.html"))
    assert parsed["display_name"].startswith("R. van Aelst-den Uijl MA")
    assert parsed["party"] == "SP"
    assert parsed["birth_date"] == date(1988, 12, 1)
    assert parsed["mandaat_start"] == date(2024, 2, 13)
    assert parsed["gender"] == "f"
    assert "Lies van Aelst-den Uijl" in parsed["intro_full_name"]


def test_parse_lid_pagina_vos():
    parsed = parse_lid_pagina(_load("vos.html"))
    assert "Vos" in parsed["display_name"]
    assert parsed["party"] == "GroenLinks-PvdA"
    assert parsed["birth_date"] == date(1970, 3, 31)
    assert parsed["mandaat_start"] == date(2023, 6, 13)
    assert parsed["gender"] == "f"
    assert parsed["intro_full_name"] == "Mei Li Vos"


def test_parse_lid_pagina_kesteren():
    """Kesteren heeft een afwijkende intro; Loopbaan-fallback moet pakken."""
    parsed = parse_lid_pagina(_load("kesteren.html"))
    assert parsed["party"] == "PVV"
    assert parsed["birth_date"] == date(1954, 8, 26)
    assert parsed["mandaat_start"] == date(2023, 12, 12)
    assert parsed["gender"] == "m"


# ---------------------------------------------------------------------------
# build_record
# ---------------------------------------------------------------------------


def test_build_record_volledig_aelst():
    entry = EkLidIndexEntry(
        slug="r_van_aelst_den_uijl_ma_sp",
        display_name="R. van Aelst-den Uijl MA",
        party="SP",
        birth_date=date(1988, 12, 1),
    )
    parsed = parse_lid_pagina(_load("aelst.html"))
    record = build_record(entry, parsed, today="2026-05-09")
    assert record is not None
    assert record["id"] == "person:aelst-den-uijl-r-1988"
    assert record["name"]["family"] == "van Aelst-den Uijl"
    assert record["name"]["initials"] == "R."
    assert record["name"]["full"] == "Lies van Aelst-den Uijl"
    assert record["name"].get("honorifics_post") == ["MA"]
    assert record["birth"] == {"year": 1988}
    assert record["gender"] == "f"
    mandaten = record["mandaten"]
    assert len(mandaten) == 1
    m = mandaten[0]
    assert m["organization_id"] == ORG_ID_EERSTE_KAMER
    assert m["post_id"] == POST_ID_SENATOR
    assert m["role"] == "Senator voor SP"
    assert m["start_date"] == "2024-02-13"
    assert m["end_date"] is None
    assert m["sources"][0]["id"] == SOURCE_ID
    assert m["sources"][0]["url"] == f"{EK_BASE}/persoon/r_van_aelst_den_uijl_ma_sp"


def test_build_record_birth_alleen_jaar():
    entry = EkLidIndexEntry(
        slug="dr_m_l_vos_groenlinks_pvda",
        display_name="Dr. M.L. Vos",
        party="GroenLinks-PvdA",
        birth_date=date(1970, 3, 31),
    )
    parsed = parse_lid_pagina(_load("vos.html"))
    record = build_record(entry, parsed, today="2026-05-09")
    assert record is not None
    assert record["birth"] == {"year": 1970}
    assert "month" not in record["birth"]
    assert "day" not in record["birth"]
    assert record["name"].get("honorifics_pre") == ["Dr."]


def test_build_record_geen_geboortedatum_returnt_none():
    entry = EkLidIndexEntry(
        slug="x_y_z",
        display_name="X. Y.",
        party="VVD",
        birth_date=None,
    )
    record = build_record(entry, {}, today="2026-05-09")
    assert record is None


def test_build_record_geen_mandaat_start_returnt_none():
    entry = EkLidIndexEntry(
        slug="x_y_z",
        display_name="X. Y.",
        party="VVD",
        birth_date=date(1970, 1, 1),
    )
    # parsed zonder mandaat_start
    record = build_record(entry, {"display_name": "X. Y."}, today="2026-05-09")
    assert record is None


def test_build_record_source_url():
    entry = EkLidIndexEntry(
        slug="dr_m_l_vos_groenlinks_pvda",
        display_name="Dr. M.L. Vos",
        party="GroenLinks-PvdA",
        birth_date=date(1970, 3, 31),
    )
    parsed = parse_lid_pagina(_load("vos.html"))
    record = build_record(entry, parsed, today="2026-05-09")
    assert record is not None
    sources = record["sources"]
    assert len(sources) == 1
    assert sources[0]["id"] == SOURCE_ID
    assert sources[0]["url"] == f"{EK_BASE}/persoon/dr_m_l_vos_groenlinks_pvda"
    assert sources[0]["retrieved"] == "2026-05-09"


# ---------------------------------------------------------------------------
# ensure_org_and_post
# ---------------------------------------------------------------------------


def test_ensure_org_and_post_creates_files(tmp_path: Path):
    org_path, post_path = ensure_org_and_post(tmp_path, today="2026-05-09")
    assert org_path.exists()
    assert post_path.exists()

    org_data = yaml.safe_load(org_path.read_text())
    assert org_data["id"] == ORG_ID_EERSTE_KAMER
    assert org_data["type"] == "hoge-college"
    assert org_data["sources"][0]["id"] == SOURCE_ID

    post_data = yaml.safe_load(post_path.read_text())
    assert post_data["id"] == POST_ID_SENATOR
    assert post_data["organization_id"] == ORG_ID_EERSTE_KAMER
    assert post_data["classification"] == "lid-hcs"
    assert post_data["seat_count"] == 75


def test_ensure_org_and_post_idempotent(tmp_path: Path):
    org_path, post_path = ensure_org_and_post(tmp_path, today="2026-05-09")
    first_org = org_path.read_text()
    first_post = post_path.read_text()
    ensure_org_and_post(tmp_path, today="2099-01-01")
    assert org_path.read_text() == first_org
    assert post_path.read_text() == first_post


# ---------------------------------------------------------------------------
# Cache + httpx-mock smoke
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"status {self.status_code}", request=None, response=None  # type: ignore[arg-type]
            )


class _FakeClient:
    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.calls: list[str] = []

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append(url)
        return _FakeResponse(self.payload)


def test_fetch_lid_pagina_uses_cache(tmp_path: Path):
    client = _FakeClient(_load("aelst.html"))
    html = fetch_lid_pagina(
        "r_van_aelst_den_uijl_ma_sp",
        cache_root=tmp_path,
        today="2026-05-09",
        client=client,  # type: ignore[arg-type]
    )
    assert "van Aelst" in html
    assert len(client.calls) == 1
    # Tweede call serveert vanuit cache, geen extra HTTP.
    html2 = fetch_lid_pagina(
        "r_van_aelst_den_uijl_ma_sp",
        cache_root=tmp_path,
        today="2026-05-09",
        client=client,  # type: ignore[arg-type]
    )
    assert html2 == html
    assert len(client.calls) == 1


# ---------------------------------------------------------------------------
# CLI smoke (geen netwerk, mock fetchers)
# ---------------------------------------------------------------------------


def test_cli_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    import polder.fetchers.ek_scrape as mod

    def fake_index(**kwargs: Any) -> str:
        return _load("alle_leden.html")

    def fake_lid(slug: str, **kwargs: Any) -> str:
        # Geef altijd Aelst — getest dat parser veerkrachtig is.
        return _load("aelst.html")

    monkeypatch.setattr(mod, "fetch_leden_index", fake_index)
    monkeypatch.setattr(mod, "fetch_lid_pagina", fake_lid)

    out = tmp_path / "personen"
    rc = mod.main(
        [
            "--limit",
            "2",
            "--dry-run",
            "--out",
            str(out),
            "--data-root",
            str(tmp_path),
            "--cache-root",
            str(tmp_path / "_cache"),
        ]
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "Wrote" in captured.err
