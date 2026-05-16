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
    assert ori_index_for_gemeente("alphen-chaam") == ("ori_alphen-chaam*,ori_alphen_chaam*")


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


def test_build_mandaat_raadsgriffier_is_single_seat_griffier():
    """`Raadsgriffier` markeert de echte gemeentegriffier → single-seat post."""
    mandaat = build_mandaat(
        raw_membership=_membership_raw(role="Raadsgriffier"),
        gemeente_slug="utrecht",
        today="2026-05-09",
    )
    assert mandaat is not None
    assert mandaat["post_id"] == "post:griffier-gemeente-utrecht"


def test_build_mandaat_griffier_is_griffiemedewerker():
    """ORI labelt de hele griffie als `Griffier`; dat is geen single-seat
    gemeentegriffier maar een griffiemedewerker (multi-seat post)."""
    mandaat = build_mandaat(
        raw_membership=_membership_raw(role="Griffier"),
        gemeente_slug="utrecht",
        today="2026-05-09",
    )
    assert mandaat is not None
    assert mandaat["post_id"] == "post:griffiemedewerker-gemeente-utrecht"


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


def test_build_mandaat_id_stabiel_over_runs():
    """Issue #64: zelfde Membership → zelfde mandaat-id, ongeacht run of dag.

    De id wordt afgeleid van de stabiele ORI Membership-`@id`, niet uit
    een per-run uuid4. Twee aanroepen op verschillende dagen voor dezelfde
    bezetting leveren dezelfde id, zodat reruns idempotent zijn.
    """
    a = build_mandaat(
        raw_membership=_membership_raw(ori_id="6329499", role="Wethouder"),
        gemeente_slug="utrecht",
        today="2026-05-09",
    )
    b = build_mandaat(
        raw_membership=_membership_raw(ori_id="6329499", role="Wethouder"),
        gemeente_slug="utrecht",
        today="2026-05-13",
    )
    assert a is not None and b is not None
    assert a["id"] == b["id"]
    assert a["id"].startswith("mandate-ori-")


def test_build_mandaat_id_uniek_per_bezetter_zelfde_post():
    """Meervoudige bezetting: twee raadsleden op dezelfde post moeten
    verschillende mandaat-id's krijgen.

    Een gemeenteraad heeft tientallen gelijktijdige raadsleden op
    ``post:raadslid-gemeente-X``. De id mag dus niet puur uit
    ``(org, post)`` komen (dat liet ze allemaal botsen), maar uit de
    per-persoon-stabiele Membership-`@id`.
    """
    lid_a = build_mandaat(
        raw_membership=_membership_raw(ori_id="111", role="Raadslid"),
        gemeente_slug="druten",
    )
    lid_b = build_mandaat(
        raw_membership=_membership_raw(ori_id="222", role="Raadslid"),
        gemeente_slug="druten",
    )
    assert lid_a is not None and lid_b is not None
    assert lid_a["post_id"] == lid_b["post_id"]
    assert lid_a["id"] != lid_b["id"]


def test_build_mandaat_id_fallback_zonder_membership_id():
    """Zonder ORI Membership-id valt de id terug op (org, post) en blijft
    deterministisch (geen uuid4)."""
    m = build_mandaat(
        raw_membership={"@type": "Membership", "role": "Burgemeester"},
        gemeente_slug="utrecht",
    )
    assert m is not None
    assert m["id"].startswith("mandate-ori-")


def test_role_to_classification_dekt_alle_polder_rollen():
    expected = {"raadslid", "wethouder", "burgemeester", "gemeentesecretaris"}
    assert expected.issubset(set(ROLE_TO_CLASSIFICATION.values()))


def test_role_to_classification_splitst_griffier_van_raadsgriffier():
    """`Raadsgriffier` → single-seat `griffier`; `Griffier` → multi-seat
    `griffiemedewerker`. Zie issue #54: ORI labelt de hele griffie als
    `Griffier` zonder onderscheidend veld."""
    assert ROLE_TO_CLASSIFICATION["Raadsgriffier"] == "griffier"
    assert ROLE_TO_CLASSIFICATION["Griffier"] == "griffiemedewerker"


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
        "utrecht",
        cache_dir=tmp_path,
        today="2026-05-09",
        client=client,  # type: ignore[arg-type]
    )
    n_calls_first = len(client.calls)
    assert n_calls_first >= 2  # persons + memberships query
    # Tweede call: cache hit, geen extra HTTP-calls.
    fetch_persons_for_gemeente(
        "utrecht",
        cache_dir=tmp_path,
        today="2026-05-09",
        client=client,  # type: ignore[arg-type]
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


def test_write_person_actief(tmp_path: Path):
    rec = _record(active=True)
    target = write_person(rec, tmp_path)
    assert target == tmp_path / "schilderman-s-6329497.yaml"
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
            {
                "id": "wikidata",
                "url": "https://www.wikidata.org/wiki/Q999",
                "retrieved": "2025-01-01",
            }
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


def test_ori_rerun_geen_dubbel_mandaat_bij_slug_drift(tmp_path: Path):
    """Issue #64: drie ORI-runs mogen geen dubbele open mandaten geven,
    ook niet wanneer de persoon-slug tussen runs verandert.

    Het bug-pad: ORI levert in run 2 net andere initialen → andere
    person-slug → ander YAML-bestand → ``merge_person`` ziet geen
    bestaand record en de snap-naar-open-mandaat-logica in
    ``_merge_mandaten`` wordt nooit bereikt. Vóór de fix kreeg elk
    bestand een vers uuid4-mandaat (490 dubbels in productie). Met een id
    afgeleid van de stabiele ORI Membership-`@id` is het mandaat over
    alle runs byte-identiek qua id, zodat downstream-dedup het als één
    bezetting ziet.
    """
    runs = [
        ("2026-05-09", "Schilderman, Susanne", "S."),
        ("2026-05-13", "Schilderman, Susanne", "S.M."),  # ORI-drift in initialen
        ("2026-05-16", "Schilderman, Susanne", "S."),
    ]
    mandaat_ids: set[str] = set()
    written: list[Path] = []
    for day, name, _initials in runs:
        rec = person_to_polder_record(
            person_raw=_person_raw(name=name),
            memberships_raw=[_membership_raw(role="Wethouder")],
            gemeente_slug="utrecht",
            today=day,
        )
        assert rec is not None
        # Forceer het slug-drift-pad: schrijf naar een per-run pad zodat
        # er geen bestaand record is om tegen te mergen (het bug-pad).
        target = write_person(rec, tmp_path / day)
        written.append(target)
        for m in rec["mandaten"]:
            mandaat_ids.add(m["id"])

    # Drie runs, één unieke mandaat-id: idempotent ondanks slug-drift.
    assert len(mandaat_ids) == 1
    # En elk geschreven record heeft precies één open mandaat (geen
    # interne dubbeling).
    for path in written:
        data = yaml.safe_load(path.read_text())
        open_mandaten = [m for m in data["mandaten"] if m["end_date"] is None]
        assert len(open_mandaten) == 1


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


# ---------------------------------------------------------------------------
# Dedup-tests (Bos-Coenraad-patroon: zelfde persoon, meerdere ORI-IDs)
# ---------------------------------------------------------------------------


def test_dedup_merges_same_family_given_in_same_org():
    """Twee records voor 'Joep Bos-Coenraad' in dezelfde org -> 1 record."""
    from polder.fetchers.open_raadsinformatie import dedup_records_for_gemeente

    record_a = {
        "id": "person:bos-coenraad-j-5482024",
        "name": {"family": "Bos-Coenraad", "given": "Joep", "initials": "J."},
        "mandaten": [
            {
                "organization_id": "org:gemeente-utrecht",
                "post_id": "post:raadslid-utrecht",
                "role": "raadslid",
                "start_date": "2014-03-19",
            }
        ],
        "sources": [{"id": "ori", "url": "https://id.openraadsinformatie.nl/5482024"}],
    }
    record_b = {
        "id": "person:bos-coenraad-j-7770655",
        "name": {"family": "Bos-Coenraad", "given": "Joep", "initials": "J."},
        "mandaten": [
            {
                "organization_id": "org:gemeente-utrecht",
                "post_id": "post:raadslid-utrecht",
                "role": "raadslid",
                "start_date": "2022-03-30",
            }
        ],
        "sources": [{"id": "ori", "url": "https://id.openraadsinformatie.nl/7770655"}],
    }

    deduped = dedup_records_for_gemeente([record_a, record_b], "org:gemeente-utrecht")
    assert len(deduped) == 1
    winner = deduped[0]
    # Twee mandaten samengevoegd
    assert len(winner["mandaten"]) == 2


def test_dedup_keeps_different_persons_separate():
    """Zelfde family, andere given-name -> blijven gescheiden."""
    from polder.fetchers.open_raadsinformatie import dedup_records_for_gemeente

    record_a = {
        "id": "person:doedens-b-2866445",
        "name": {"family": "Doedens", "given": "Berend", "initials": "B."},
        "sources": [{"id": "ori", "url": "https://x"}],
    }
    record_b = {
        "id": "person:doedens-c-9999",
        "name": {"family": "Doedens", "given": "Christine", "initials": "C."},
        "sources": [{"id": "ori", "url": "https://y"}],
    }
    deduped = dedup_records_for_gemeente([record_a, record_b], "org:gemeente-utrecht")
    assert len(deduped) == 2


def test_dedup_skipt_records_zonder_given():
    """Records zonder given-name worden niet gededupt (key ontbreekt)."""
    from polder.fetchers.open_raadsinformatie import dedup_records_for_gemeente

    record = {
        "id": "person:onbekend-9999",
        "name": {"family": "Onbekend", "initials": "X."},
        "sources": [{"id": "ori", "url": "https://x"}],
    }
    deduped = dedup_records_for_gemeente([record], "org:gemeente-utrecht")
    assert len(deduped) == 1


def test_parse_person_extracts_given_from_email_when_missing():
    """ORI levert soms alleen family. Email-local-part vult voornaam aan."""
    from polder.fetchers.open_raadsinformatie import parse_person

    raw = {
        "@id": "4580272",
        "name": "Haas",
        "family_name": "Haas",
        "email": "guus.haas@gemeenteraadkerkrade.nl",
    }
    rec = parse_person(raw)
    assert rec is not None
    assert rec["name"]["family"] == "Haas"
    assert rec["name"].get("given") == "Guus"
    assert rec["name"].get("initials") == "G."


def test_parse_person_email_role_prefix_stripped():
    """Email met `raadslid.<voornaam>.<family>` patroon wordt correct geparsed."""
    from polder.fetchers.open_raadsinformatie import parse_person

    raw = {
        "@id": "9999",
        "name": "Vlampijp",
        "family_name": "Vlampijp",
        "email": "raadslid.gerrion.vlampijp@example.nl",
    }
    rec = parse_person(raw)
    assert rec is not None
    assert rec["name"].get("given") == "Gerrion"


def test_parse_person_skips_email_if_family_mismatch():
    """Als de family in de email niet matched aan onze family, gebruik die niet."""
    from polder.fetchers.open_raadsinformatie import parse_person

    raw = {
        "@id": "9999",
        "name": "Bakker",
        "family_name": "Bakker",
        "email": "raadslid.henk.smit@example.nl",  # email zegt 'smit', niet 'bakker'
    }
    rec = parse_person(raw)
    assert rec is not None
    assert rec["name"].get("given") is None


def test_split_name_keeps_parens_for_normalize():
    """`_split_name` raakt parens niet aan; dat doet `_normalize_given`."""
    from polder.fetchers.open_raadsinformatie import _split_name

    family, given = _split_name("Smeulders, P. (Paul)")
    assert family == "Smeulders"
    assert given == "P. (Paul)"


def test_split_name_keeps_given_when_no_parens():
    """Normaal patroon ongemoeid laten."""
    from polder.fetchers.open_raadsinformatie import _split_name

    family, given = _split_name("Schilderman, Susanne")
    assert family == "Schilderman"
    assert given == "Susanne"


def test_split_name_handles_initials_only():
    """Geen roepnaam tussen haakjes → laat de initialen-vorm staan."""
    from polder.fetchers.open_raadsinformatie import _split_name

    family, given = _split_name("Smeulders, P.")
    assert family == "Smeulders"
    assert given == "P."


def test_normalize_given_returns_nickname_initials_and_tussenvoegsel():
    """`L.S. (Larissa)` → ('Larissa', 'L.S.', None)."""
    from polder.fetchers.open_raadsinformatie import _normalize_given

    given, initials, tussen = _normalize_given("L.S. (Larissa)")
    assert given == "Larissa"
    assert initials == "L.S."
    assert tussen is None


def test_normalize_given_extracts_tussenvoegsel():
    """`A.M. (Alies) van` → ('Alies', 'A.M.', 'van')."""
    from polder.fetchers.open_raadsinformatie import _normalize_given

    given, initials, tussen = _normalize_given("A.M. (Alies) van")
    assert given == "Alies"
    assert initials == "A.M."
    assert tussen == "van"


def test_normalize_given_without_parens():
    """Plain voornaam blijft ongemoeid."""
    from polder.fetchers.open_raadsinformatie import _normalize_given

    given, initials, tussen = _normalize_given("Susanne")
    assert given == "Susanne"
    assert initials is None
    assert tussen is None


def test_parse_person_preserves_initials_from_parens():
    """Volledige integratie: ORI `name='Vlieger, L.S. (Larissa)'` → record met
    given='Larissa' EN initials='L.S.' (NIET 'L.')."""
    from polder.fetchers.open_raadsinformatie import parse_person

    raw = {
        "@id": "6065963",
        "name": "Vlieger, L.S. (Larissa)",
        "family_name": "Vlieger",
    }
    rec = parse_person(raw)
    assert rec is not None
    assert rec["name"]["family"] == "Vlieger"
    assert rec["name"]["given"] == "Larissa"
    assert rec["name"]["initials"] == "L.S."  # NIET 'L.'
    assert rec["name"]["full"] == "Larissa Vlieger"


def test_parse_person_preserves_tussenvoegsel():
    """ORI `name='Weperen, A.M. (Alies) van'` → tussenvoegsel='van', given='Alies'."""
    from polder.fetchers.open_raadsinformatie import parse_person

    raw = {
        "@id": "7775658",
        "name": "Weperen, A.M. (Alies) van",
        "family_name": "Weperen",
    }
    rec = parse_person(raw)
    assert rec is not None
    assert rec["name"]["family"] == "Weperen"
    assert rec["name"]["given"] == "Alies"
    assert rec["name"]["tussenvoegsel"] == "van"
    assert rec["name"]["initials"] == "A.M."
    assert rec["name"]["full"] == "Alies van Weperen"


def test_parse_person_tussenvoegsel_from_name_diff():
    """ORI `name='Henk van der Linden'` + `family_name='Linden'`: tussenvoegsel='van der'."""
    from polder.fetchers.open_raadsinformatie import parse_person

    raw = {
        "@id": "9999991",
        "name": "Henk van der Linden",
        "family_name": "Linden",
    }
    rec = parse_person(raw)
    assert rec is not None
    assert rec["name"]["family"] == "Linden"
    assert rec["name"]["tussenvoegsel"] == "van der"
    assert rec["name"]["given"] == "Henk"


def test_parse_person_no_tussenvoegsel_when_none():
    """`Schilderman, Susanne` heeft geen tussenvoegsel."""
    from polder.fetchers.open_raadsinformatie import parse_person

    raw = {"@id": "1", "name": "Schilderman, Susanne", "family_name": "Schilderman"}
    rec = parse_person(raw)
    assert rec is not None
    assert "tussenvoegsel" not in rec["name"]


def test_extract_tussenvoegsel_handles_apostroph_prefix():
    """`'t` en `'s` zijn ook tussenvoegsels (in 't, 's)."""
    from polder.fetchers.open_raadsinformatie import _extract_tussenvoegsel

    assert _extract_tussenvoegsel("Anna in 't Veld", "Veld") == "in 't"
    assert _extract_tussenvoegsel("Piet de Vries", "Vries") == "de"
    assert _extract_tussenvoegsel("Anna van der Burg", "Burg") == "van der"
    assert _extract_tussenvoegsel("Susanne Schilderman", "Schilderman") is None


# ---------------------------------------------------------------------------
# Tussenvoegsel-extractie: uitgebreide matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,family,expected_tussenvoegsel",
    [
        # Klassieke patronen
        ("Anna de Vries", "Vries", "de"),
        ("Anna van der Berg", "Berg", "van der"),
        ("Anna van den Bosch", "Bosch", "van den"),
        ("Anna op de Beek", "Beek", "op de"),
        ("Anne in 't Veld", "Veld", "in 't"),
        ("Hugo van 't Hoff", "Hoff", "van 't"),
        ("Anna ter Beek", "Beek", "ter"),
        ("Anna ten Hoeve", "Hoeve", "ten"),
        # Geen tussenvoegsel
        ("Anna Vries", "Vries", None),
        ("Susanne Schilderman", "Schilderman", None),
        # Tussenvoegsel in family (niet apart extracten)
        ("Anna de Vries", "de Vries", None),
        ("Anne in 't Veld", "in 't Veld", None),
        # Hyphen-family met tussenvoegsel ervoor
        ("Anna van der Berg-Smit", "Berg-Smit", "van der"),
        ("Pieter de Vries-Jansen", "Vries-Jansen", "de"),
        # Hyphen-family zonder tussenvoegsel (oude huwelijksconventie)
        ("Anna Mulder-Roelofs", "Mulder-Roelofs", None),
        # 2024+ gecombineerde achternaam (zonder hyphen)
        ("Anna Mulder de Vries", "Mulder de Vries", None),
        ("Anna de Vries Mulder", "de Vries Mulder", None),
        ("Anna van der Berg Mulder", "van der Berg Mulder", None),
        # 2024+ gecombineerd MET expliciet tussenvoegsel in name
        ("Anna van der Berg de Vries", "Berg de Vries", "van der"),
        # Edge cases
        ("", "Vries", None),
        ("Anna Vries", "", None),
        ("Anna", "Anna", None),
    ],
)
def test_extract_tussenvoegsel_matrix(
    name: str, family: str, expected_tussenvoegsel: str | None
) -> None:
    from polder.fetchers.open_raadsinformatie import _extract_tussenvoegsel

    assert _extract_tussenvoegsel(name, family) == expected_tussenvoegsel


@pytest.mark.parametrize(
    "raw_name,family_name,expected_given,expected_tussen,expected_family",
    [
        # ORI-patroon: comma + tussenvoegsel achteraan
        ("Weperen, A.M. (Alies) van", "Weperen", "Alies", "van", "Weperen"),
        ("Berg, J.P. (Jan) van der", "Berg", "Jan", "van der", "Berg"),
        ("Veld, A. (Anne) in 't", "Veld", "Anne", "in 't", "Veld"),
        # ORI-patroon zonder comma (full display-form)
        ("Henk van der Linden", "Linden", "Henk", "van der", "Linden"),
        ("Anna de Vries", "Vries", "Anna", "de", "Vries"),
        # ORI-patroon: comma, geen tussenvoegsel
        ("Schilderman, Susanne", "Schilderman", "Susanne", None, "Schilderman"),
        # 2024+ gecombineerde family-name
        ("Anna Mulder de Vries", "Mulder de Vries", "Anna", None, "Mulder de Vries"),
        # Comma + nickname + tussenvoegsel
        ("Hofweegen, M.M.J. (Marjon) van", "Hofweegen", "Marjon", "van", "Hofweegen"),
    ],
)
def test_parse_person_name_matrix(
    raw_name: str,
    family_name: str,
    expected_given: str,
    expected_tussen: str | None,
    expected_family: str,
) -> None:
    """Volledige integratie: ORI-input → parse_person → name-record."""
    from polder.fetchers.open_raadsinformatie import parse_person

    raw = {"@id": "test-1", "name": raw_name, "family_name": family_name}
    rec = parse_person(raw)
    assert rec is not None
    name = rec["name"]
    assert name["family"] == expected_family
    assert name.get("given") == expected_given
    assert name.get("tussenvoegsel") == expected_tussen


def test_parse_person_full_includes_tussenvoegsel() -> None:
    """`full` is `<given> <tussenvoegsel> <family>` in display-volgorde."""
    from polder.fetchers.open_raadsinformatie import parse_person

    raw = {"@id": "1", "name": "Henk van der Linden", "family_name": "Linden"}
    rec = parse_person(raw)
    assert rec is not None
    assert rec["name"]["full"] == "Henk van der Linden"


def test_parse_person_2024_compound_name_no_tussenvoegsel() -> None:
    """2024+ kind met gecombineerde achternaam zonder hyphen: tussenvoegsel
    blijft None want de hele samenstelling is family."""
    from polder.fetchers.open_raadsinformatie import parse_person

    raw = {
        "@id": "1",
        "name": "Anna Mulder de Vries",
        "family_name": "Mulder de Vries",
    }
    rec = parse_person(raw)
    assert rec is not None
    assert rec["name"]["family"] == "Mulder de Vries"
    assert rec["name"]["given"] == "Anna"
    assert "tussenvoegsel" not in rec["name"]
    assert rec["name"]["full"] == "Anna Mulder de Vries"
