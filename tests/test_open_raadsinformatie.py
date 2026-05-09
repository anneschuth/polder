"""Tests voor de Open Raadsinformatie fetcher.

Geen netwerk: alle httpx-calls worden via monkeypatch gemockt op fixture-JSON.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from polder.fetchers.open_raadsinformatie import (
    ROLE_TO_CLASSIFICATION,
    SOURCE_ID,
    build_mandaat,
    ensure_org_and_posts,
    fetch_persons_for_gemeente,
    main,
    merge_person,
    ori_index_for_gemeente,
    parse_person,
    person_to_polder_record,
    slugify_person,
    write_person,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _person_raw(
    *,
    ori_id: str = "6329497",
    name: str = "Schilderman, Susanne",
    family_name: str | None = "Schilderman",
    email: str | None = None,
) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "@id": ori_id,
        "@type": "Person",
        "name": name,
    }
    if family_name is not None:
        raw["family_name"] = family_name
    if email is not None:
        raw["email"] = email
    return raw


def _membership_raw(
    *,
    ori_id: str = "6329499",
    member: str = "6329497",
    organization: str = "6329456",
    role: str = "Wethouder",
) -> dict[str, Any]:
    return {
        "@id": ori_id,
        "@type": "Membership",
        "member": member,
        "organization": organization,
        "role": role,
    }


# ---------------------------------------------------------------------------
# slugify_person
# ---------------------------------------------------------------------------


def test_slugify_person_basis():
    assert slugify_person("Schilderman", "S.", "6329497") == "schilderman-s-6329497"


def test_slugify_person_tussenvoegsel_stripped():
    # "van der Linden" → tussenvoegsels weg, ori_id achteraan.
    assert slugify_person("van der Linden", "P.J.", "12345") == "linden-pj-12345"


def test_slugify_person_accenten():
    assert slugify_person("Özütok", "N.", "999") == "ozutok-n-999"


def test_slugify_person_zonder_initialen():
    assert slugify_person("Jansen", "", "42") == "jansen-42"


# ---------------------------------------------------------------------------
# ori_index_for_gemeente
# ---------------------------------------------------------------------------


def test_ori_index_basis():
    assert ori_index_for_gemeente("utrecht") == "ori_utrecht*"


def test_ori_index_uit_polder_slug():
    assert ori_index_for_gemeente("gemeente-utrecht") == "ori_utrecht*"


def test_ori_index_uit_org_id():
    assert ori_index_for_gemeente("org:gemeente-utrecht") == "ori_utrecht*"


def test_ori_index_met_streepje():
    # ORI gebruikt zowel `ori_alphen-chaam_*` als `ori_alphen_chaam_*` in de
    # praktijk; we leveren beide als komma-separated lijst aan ES.
    assert ori_index_for_gemeente("alphen-chaam") == (
        "ori_alphen-chaam*,ori_alphen_chaam*"
    )


# ---------------------------------------------------------------------------
# parse_person
# ---------------------------------------------------------------------------


def test_parse_person_basis():
    rec = parse_person(_person_raw())
    assert rec is not None
    assert rec["id"] == "person:schilderman-s-6329497"
    assert rec["name"]["family"] == "Schilderman"
    assert rec["name"]["given"] == "Susanne"
    assert rec["name"]["initials"] == "S."
    assert rec["name"]["full"] == "Susanne Schilderman"


def test_parse_person_dubbele_voornaam():
    rec = parse_person(_person_raw(ori_id="111", name="Kroone, Marie-Antoinette", family_name=None))
    assert rec is not None
    assert rec["name"]["initials"] == "M.A."
    assert rec["name"]["family"] == "Kroone"


def test_parse_person_volgorde_voornaam_eerst():
    # Sommige indices leveren `Voornaam Achternaam`.
    raw = _person_raw(ori_id="222", name="Susanne Schilderman", family_name=None)
    rec = parse_person(raw)
    assert rec is not None
    assert rec["name"]["family"] == "Schilderman"
    assert rec["name"]["given"] == "Susanne"


def test_parse_person_zonder_id():
    raw = {"@type": "Person", "name": "Foo, Bar"}
    assert parse_person(raw) is None


def test_parse_person_zonder_naam():
    raw = {"@id": "999", "@type": "Person", "name": ""}
    assert parse_person(raw) is None


def test_parse_person_geen_geboortejaar():
    """ORI levert geen geboortejaar; record mag dus geen `birth` hebben."""
    rec = parse_person(_person_raw())
    assert rec is not None
    assert "birth" not in rec


def test_parse_person_geen_email_in_record():
    """Zelfs als ORI een (functioneel) email levert, schrijven we hem niet
    in identifiers — schema beperkt en privacy-conservatief."""
    rec = parse_person(_person_raw(email="s.schilderman@utrecht.nl"))
    assert rec is not None
    assert "email" not in rec
    assert rec.get("identifiers") == {}


# ---------------------------------------------------------------------------
# build_mandaat
# ---------------------------------------------------------------------------


def test_build_mandaat_wethouder():
    mandaat = build_mandaat(
        raw_membership=_membership_raw(role="Wethouder"),
        gemeente_slug="utrecht",
        today="2026-05-09",
    )
    assert mandaat is not None
    assert mandaat["organization_id"] == "org:gemeente-utrecht"
    assert mandaat["post_id"] == "post:wethouder-gemeente-utrecht"
    assert mandaat["role"].startswith("Wethouder")
    assert mandaat["start_date"] == "2026-05-09"
    assert mandaat["end_date"] is None
    assert mandaat["sources"][0]["id"] == SOURCE_ID
    assert "openraadsinformatie" in mandaat["sources"][0]["url"]


def test_build_mandaat_raadslid():
    mandaat = build_mandaat(
        raw_membership=_membership_raw(role="Raadslid"),
        gemeente_slug="utrecht",
        today="2026-05-09",
    )
    assert mandaat is not None
    assert mandaat["post_id"] == "post:raadslid-gemeente-utrecht"


def test_build_mandaat_burgemeester():
    mandaat = build_mandaat(
        raw_membership=_membership_raw(role="Burgemeester"),
        gemeente_slug="gemeente-amsterdam",
        today="2026-05-09",
    )
    assert mandaat is not None
    assert mandaat["organization_id"] == "org:gemeente-amsterdam"
    assert mandaat["post_id"] == "post:burgemeester-gemeente-amsterdam"


def test_build_mandaat_skip_member():
    """Generieke `Member` rol is fractie-membership, niet relevant als mandaat."""
    mandaat = build_mandaat(
        raw_membership=_membership_raw(role="Member"),
        gemeente_slug="utrecht",
    )
    assert mandaat is None


def test_build_mandaat_skip_gastspreker():
    mandaat = build_mandaat(
        raw_membership=_membership_raw(role="Gastspreker"),
        gemeente_slug="utrecht",
    )
    assert mandaat is None


def test_build_mandaat_uniek_id():
    a = build_mandaat(
        raw_membership=_membership_raw(role="Wethouder"), gemeente_slug="utrecht"
    )
    b = build_mandaat(
        raw_membership=_membership_raw(role="Wethouder"), gemeente_slug="utrecht"
    )
    assert a is not None and b is not None
    assert a["id"] != b["id"]


def test_role_to_classification_dekt_alle_polder_rollen():
    expected = {"raadslid", "wethouder", "burgemeester", "gemeentesecretaris"}
    assert expected.issubset(set(ROLE_TO_CLASSIFICATION.values()))


# ---------------------------------------------------------------------------
# person_to_polder_record
# ---------------------------------------------------------------------------


def test_person_to_polder_record_combineert_persoon_en_mandaat():
    rec = person_to_polder_record(
        _person_raw(),
        [_membership_raw(role="Wethouder")],
        gemeente_slug="utrecht",
        today="2026-05-09",
    )
    assert rec is not None
    assert rec["id"] == "person:schilderman-s-6329497"
    assert len(rec["mandaten"]) == 1
    assert rec["mandaten"][0]["post_id"] == "post:wethouder-gemeente-utrecht"
    assert rec["sources"][0]["id"] == SOURCE_ID
    assert rec["sources"][0]["url"].endswith("/6329497")


def test_person_to_polder_record_skipt_zonder_relevant_mandaat():
    """Geen `Wethouder/Raadslid/Burgemeester` mandaat → geen record (we
    verzamelen alleen polder-relevante personen)."""
    rec = person_to_polder_record(
        _person_raw(),
        [_membership_raw(role="Member"), _membership_raw(role="Gastspreker")],
        gemeente_slug="utrecht",
        today="2026-05-09",
    )
    assert rec is None


def test_person_to_polder_record_meerdere_mandaten():
    rec = person_to_polder_record(
        _person_raw(),
        [
            _membership_raw(ori_id="m1", role="Wethouder"),
            _membership_raw(ori_id="m2", role="Raadslid"),
            _membership_raw(ori_id="m3", role="Member"),  # geskipt
        ],
        gemeente_slug="utrecht",
        today="2026-05-09",
    )
    assert rec is not None
    classifications = {m["post_id"] for m in rec["mandaten"]}
    assert classifications == {
        "post:wethouder-gemeente-utrecht",
        "post:raadslid-gemeente-utrecht",
    }


# ---------------------------------------------------------------------------
# fetch_persons_for_gemeente (mock httpx)
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        pass


class _FakeClient:
    """Levert verschillende payloads per Elasticsearch-query type."""

    def __init__(self, persons: list[dict[str, Any]], memberships: list[dict[str, Any]]):
        self.persons = persons
        self.memberships = memberships
        self.calls: list[dict[str, Any]] = []

    def post(
        self, url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: float
    ) -> _FakeResponse:
        self.calls.append({"url": url, "body": json})
        query = json.get("query", {})
        term = query.get("term", {})
        from_ = json.get("from", 0)
        size = json.get("size", 10)
        if term.get("@type") == "Person":
            page = self.persons[from_ : from_ + size]
        elif term.get("@type") == "Membership":
            page = self.memberships[from_ : from_ + size]
        else:
            page = []
        return _FakeResponse({"hits": {"hits": page, "total": {"value": len(page)}}})


def test_fetch_persons_for_gemeente_combineert_via_member_field(tmp_path: Path):
    persons = [
        {"_id": "6329497", "_source": _person_raw(ori_id="6329497")},
        {"_id": "1111", "_source": _person_raw(ori_id="1111", name="Doe, Jane")},
    ]
    memberships = [
        {"_id": "m1", "_source": _membership_raw(ori_id="m1", member="6329497", role="Wethouder")},
        {"_id": "m2", "_source": _membership_raw(ori_id="m2", member="1111", role="Raadslid")},
        {
            "_id": "m3",
            "_source": _membership_raw(ori_id="m3", member="6329497", role="Member"),
        },
    ]
    client = _FakeClient(persons, memberships)
    results = fetch_persons_for_gemeente(
        "utrecht",
        cache_dir=tmp_path,
        today="2026-05-09",
        client=client,  # type: ignore[arg-type]
        use_cache=False,
    )
    assert len(results) == 2
    by_id = {r["person"]["@id"]: r for r in results}
    assert len(by_id["6329497"]["memberships"]) == 2
    assert len(by_id["1111"]["memberships"]) == 1


def test_fetch_persons_for_gemeente_cache_roundtrip(tmp_path: Path):
    persons = [{"_id": "1", "_source": _person_raw(ori_id="1")}]
    memberships: list[dict[str, Any]] = []
    client = _FakeClient(persons, memberships)
    # Eerste call vult de cache.
    fetch_persons_for_gemeente(
        "utrecht", cache_dir=tmp_path, today="2026-05-09", client=client  # type: ignore[arg-type]
    )
    n_calls_first = len(client.calls)
    assert n_calls_first >= 2  # persons + memberships query
    # Tweede call: cache hit, geen extra HTTP-calls.
    fetch_persons_for_gemeente(
        "utrecht", cache_dir=tmp_path, today="2026-05-09", client=client  # type: ignore[arg-type]
    )
    assert len(client.calls) == n_calls_first


# ---------------------------------------------------------------------------
# ensure_org_and_posts
# ---------------------------------------------------------------------------


def test_ensure_org_and_posts_creates_yaml(tmp_path: Path):
    written = ensure_org_and_posts(tmp_path, "utrecht", today="2026-05-09")
    assert len(written) >= 4  # raadslid, wethouder, burgemeester, gemeentesecretaris
    posts_dir = tmp_path / "posten" / "gemeenten" / "utrecht"
    raadslid = posts_dir / "raadslid.yaml"
    assert raadslid.exists()
    data = yaml.safe_load(raadslid.read_text())
    assert data["id"] == "post:raadslid-gemeente-utrecht"
    assert data["organization_id"] == "org:gemeente-utrecht"
    assert data["classification"] == "raadslid"
    assert data["valid_from"]


def test_ensure_org_and_posts_idempotent(tmp_path: Path):
    ensure_org_and_posts(tmp_path, "utrecht", today="2026-05-09")
    raadslid = tmp_path / "posten" / "gemeenten" / "utrecht" / "raadslid.yaml"
    first = raadslid.read_text()
    # Tweede call met andere `today` — moet ongewijzigd blijven.
    ensure_org_and_posts(tmp_path, "utrecht", today="2099-01-01")
    assert raadslid.read_text() == first


def test_ensure_org_and_posts_alle_classificaties(tmp_path: Path):
    ensure_org_and_posts(tmp_path, "amsterdam", today="2026-05-09")
    posts_dir = tmp_path / "posten" / "gemeenten" / "amsterdam"
    classifications = {yaml.safe_load(p.read_text())["classification"] for p in posts_dir.iterdir()}
    assert {"raadslid", "wethouder", "burgemeester", "gemeentesecretaris"}.issubset(classifications)


# ---------------------------------------------------------------------------
# write_person + merge
# ---------------------------------------------------------------------------


def _record(active: bool = True) -> dict[str, Any]:
    return {
        "id": "person:schilderman-s-6329497",
        "name": {
            "full": "Susanne Schilderman",
            "family": "Schilderman",
            "given": "Susanne",
            "initials": "S.",
        },
        "mandaten": [
            {
                "id": "uuid-1",
                "organization_id": "org:gemeente-utrecht",
                "post_id": "post:wethouder-gemeente-utrecht",
                "role": "Wethouder gemeente Utrecht",
                "start_date": "2026-05-09",
                "end_date": None if active else "2026-01-01",
                "sources": [
                    {
                        "id": SOURCE_ID,
                        "url": "https://id.openraadsinformatie.nl/6329499",
                        "retrieved": "2026-05-09",
                    }
                ],
            }
        ],
        "sources": [
            {
                "id": SOURCE_ID,
                "url": "https://id.openraadsinformatie.nl/6329497",
                "retrieved": "2026-05-09",
            }
        ],
    }


def test_write_person_actief_naar_current(tmp_path: Path):
    rec = _record(active=True)
    target = write_person(rec, tmp_path)
    assert target == tmp_path / "current" / "schilderman-s-6329497.yaml"
    assert target.exists()


def test_write_person_yaml_volgorde(tmp_path: Path):
    rec = _record(active=True)
    target = write_person(rec, tmp_path)
    content = target.read_text()
    assert content.index("id:") < content.index("name:")
    parsed = yaml.safe_load(content)
    assert parsed["id"] == "person:schilderman-s-6329497"


def test_merge_person_behoudt_bestaande_identifiers():
    existing = {
        "id": "person:schilderman-s-6329497",
        "identifiers": {"wikidata": "Q999"},
        "name": {"full": "Susanne Schilderman", "family": "Schilderman"},
        "sources": [
            {"id": "wikidata", "url": "https://www.wikidata.org/wiki/Q999", "retrieved": "2025-01-01"}
        ],
    }
    new = _record(active=True)
    merged = merge_person(existing, new)
    assert merged["identifiers"]["wikidata"] == "Q999"
    src_ids = {s["id"] for s in merged["sources"]}
    assert src_ids == {"wikidata", SOURCE_ID}


def test_merge_person_mandaat_id_blijft_stabiel():
    existing = {
        "id": "person:schilderman-s-6329497",
        "name": {"full": "Susanne Schilderman", "family": "Schilderman"},
        "mandaten": [
            {
                "id": "stable-uuid",
                "organization_id": "org:gemeente-utrecht",
                "post_id": "post:wethouder-gemeente-utrecht",
                "role": "Wethouder gemeente Utrecht",
                "start_date": "2026-05-09",
                "end_date": None,
                "sources": [
                    {
                        "id": SOURCE_ID,
                        "url": "https://id.openraadsinformatie.nl/old",
                        "retrieved": "2025-01-01",
                    }
                ],
            }
        ],
        "sources": [
            {
                "id": SOURCE_ID,
                "url": "https://id.openraadsinformatie.nl/6329497",
                "retrieved": "2025-01-01",
            }
        ],
    }
    new = _record(active=True)
    merged = merge_person(existing, new)
    assert len(merged["mandaten"]) == 1
    assert merged["mandaten"][0]["id"] == "stable-uuid"


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def test_cli_dry_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    import polder.fetchers.open_raadsinformatie as mod

    def fake_fetch(slug: str, **kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "person": _person_raw(),
                "memberships": [_membership_raw(role="Wethouder")],
            }
        ]

    monkeypatch.setattr(mod, "fetch_persons_for_gemeente", fake_fetch)

    rc = mod.main(
        [
            "--gemeente",
            "utrecht",
            "--limit",
            "1",
            "--dry-run",
            "--out",
            str(tmp_path / "personen"),
            "--data-root",
            str(tmp_path),
            "--cache-dir",
            str(tmp_path / "cache"),
        ]
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "Wrote" in captured.err


def test_cli_requires_gemeente_or_all(monkeypatch: pytest.MonkeyPatch):
    with pytest.raises(SystemExit):
        main([])
