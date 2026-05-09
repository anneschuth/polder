"""Tests voor `polder.fetchers.logius_cor`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from polder.fetchers import logius_cor as lc

# ---------------------------------------------------------------------------
# Fixture: kleine OIN-CSV in dezelfde shape als de pubproxy oplevert
# ---------------------------------------------------------------------------

# Velden komen 1-op-1 overeen met `_normalise_record(...)` zodat we de fetch-
# stap kunnen overslaan en direct de matching kunnen testen.
SAMPLE_ROWS: list[dict[str, Any]] = [
    {
        "oin": "00000001003214345000",
        "name": "ministerie van Binnenlandse Zaken en Koninkrijksrelaties",
        "kvk": "50200097",
        "tooi": "https://identifier.overheid.nl/tooi/id/ministerie/mnre1034",
        "organisation_type": "MNRE",
        "status": "Actief",
        "modification_date": "2026-02-12T18:43:21",
    },
    {
        "oin": "00000001003214394000",
        "name": "ministerie van Financiën",
        "kvk": "27365323",
        "tooi": "https://identifier.overheid.nl/tooi/id/ministerie/mnre1090",
        "organisation_type": "MNRE",
        "status": "Actief",
        "modification_date": "2025-11-01T00:00:00",
    },
    {
        "oin": "00000001002220647000",
        "name": "gemeente Utrecht",
        "kvk": "30280353",
        "tooi": "https://identifier.overheid.nl/tooi/id/gemeente/gm0344",
        "organisation_type": "GM",
        "status": "Actief",
        "modification_date": "2025-09-09T00:00:00",
    },
    {
        # Sub-OIN zonder tooi/type → mag NIET in de naam-index landen,
        # anders zou "Ministerie van BZK" hier per ongeluk op matchen.
        "oin": "00000004153838536000",
        "name": "ministerie van Binnenlandse Zaken en Koninkrijksrelaties - Volkshuisvesting en Ruimtelijke Ordening",
        "kvk": None,
        "tooi": None,
        "organisation_type": None,
        "status": "Actief",
        "modification_date": "2025-12-01T00:00:00",
    },
]


# ---------------------------------------------------------------------------
# normalize_org_name
# ---------------------------------------------------------------------------


def test_normalize_strips_prefixes_and_accents() -> None:
    assert (
        lc.normalize_org_name("Ministerie van Binnenlandse Zaken en Koninkrijksrelaties")
        == "binnenlandse zaken en koninkrijksrelaties"
    )
    assert lc.normalize_org_name("Gemeente Utrecht") == "utrecht"
    assert lc.normalize_org_name("Ministerie van Financiën") == "financien"
    assert lc.normalize_org_name(None) == ""
    assert lc.normalize_org_name("") == ""


# ---------------------------------------------------------------------------
# build_oin_index — primaire records vs. sub-OINs
# ---------------------------------------------------------------------------


def test_build_index_primary_only_in_name_index() -> None:
    index = lc.build_oin_index(SAMPLE_ROWS)
    assert set(index["by_tooi"].keys()) == {
        "https://identifier.overheid.nl/tooi/id/ministerie/mnre1034",
        "https://identifier.overheid.nl/tooi/id/ministerie/mnre1090",
        "https://identifier.overheid.nl/tooi/id/gemeente/gm0344",
    }
    assert set(index["by_kvk"].keys()) == {"50200097", "27365323", "30280353"}
    # De sub-OIN "...Volkshuisvesting en Ruimtelijke Ordening" mag NIET
    # in de name-index zitten (geen tooi, geen typecode).
    assert "binnenlandse zaken en koninkrijksrelaties - volkshuisvesting en ruimtelijke ordening" not in index["by_name"]
    # Maar het primaire BZK-record wel.
    assert (
        index["by_name"]["binnenlandse zaken en koninkrijksrelaties"]["oin"]
        == "00000001003214345000"
    )


def test_build_index_skips_inactive() -> None:
    rows = [
        dict(SAMPLE_ROWS[0]),
        {**SAMPLE_ROWS[1], "status": "Inactief"},
    ]
    index = lc.build_oin_index(rows)
    assert "27365323" not in index["by_kvk"]
    assert "financien" not in index["by_name"]


# ---------------------------------------------------------------------------
# match_to_records — match-volgorde tooi > kvk > name
# ---------------------------------------------------------------------------


def test_match_by_tooi_takes_precedence() -> None:
    index = lc.build_oin_index(SAMPLE_ROWS)
    record = {
        "id": "org:min-bzk",
        "type": "ministerie",
        "identifiers": {
            "tooi": "https://identifier.overheid.nl/tooi/id/ministerie/mnre1034",
            # KvK wijst expres naar Financiën om volgorde te bewijzen.
            "kvk": "27365323",
        },
        "names": [{"value": "Ministerie van BZK"}],
    }
    matches = lc.match_to_records([record], index)
    assert len(matches) == 1
    _, row, method = matches[0]
    assert method == "tooi"
    assert row["oin"] == "00000001003214345000"


def test_match_by_kvk_when_tooi_absent() -> None:
    index = lc.build_oin_index(SAMPLE_ROWS)
    record = {
        "id": "org:min-fin",
        "type": "ministerie",
        "identifiers": {"kvk": "27365323"},
        "names": [{"value": "Min Fin"}],  # naam zou niet matchen
    }
    matches = lc.match_to_records([record], index)
    assert len(matches) == 1
    _, row, method = matches[0]
    assert method == "kvk"
    assert row["oin"] == "00000001003214394000"


def test_match_by_name_when_no_identifiers() -> None:
    index = lc.build_oin_index(SAMPLE_ROWS)
    record = {
        "id": "org:gemeente-utrecht",
        "type": "gemeente",
        "names": [{"value": "Gemeente Utrecht"}],
    }
    matches = lc.match_to_records([record], index)
    assert len(matches) == 1
    _, row, method = matches[0]
    assert method == "name"
    assert row["oin"] == "00000001002220647000"


def test_match_skips_records_with_existing_oin() -> None:
    index = lc.build_oin_index(SAMPLE_ROWS)
    record = {
        "id": "org:min-bzk",
        "identifiers": {"oin": "00000099999999999999"},
        "names": [{"value": "Ministerie van Binnenlandse Zaken en Koninkrijksrelaties"}],
    }
    matches = lc.match_to_records([record], index)
    assert matches == []


def test_match_no_false_match_on_unrelated_name() -> None:
    index = lc.build_oin_index(SAMPLE_ROWS)
    record = {
        "id": "org:bla",
        "names": [{"value": "Iets wat niet bestaat"}],
    }
    matches = lc.match_to_records([record], index)
    assert matches == []


# ---------------------------------------------------------------------------
# merge_oin_into_record
# ---------------------------------------------------------------------------


def test_merge_adds_oin_kvk_tooi_and_source() -> None:
    record = {
        "id": "org:min-bzk",
        "type": "ministerie",
        "names": [{"value": "Ministerie van BZK"}],
        "sources": [
            {
                "id": "roo",
                "url": "https://organisaties.overheid.nl/9632/",
                "retrieved": "2026-01-01",
            }
        ],
    }
    merged = lc.merge_oin_into_record(record, SAMPLE_ROWS[0], today="2026-05-09")
    assert merged["identifiers"]["oin"] == "00000001003214345000"
    assert merged["identifiers"]["kvk"] == "50200097"
    assert (
        merged["identifiers"]["tooi"]
        == "https://identifier.overheid.nl/tooi/id/ministerie/mnre1034"
    )
    src_ids = {s["id"] for s in merged["sources"]}
    assert src_ids == {"roo", "logius_cor"}
    cor_src = next(s for s in merged["sources"] if s["id"] == "logius_cor")
    assert cor_src["retrieved"] == "2026-05-09"
    assert cor_src["fields"] == ["oin"]
    assert cor_src["url"] == lc.OIN_PUBPROXY_URL


def test_merge_does_not_overwrite_existing_kvk_or_tooi() -> None:
    record = {
        "id": "org:min-bzk",
        "identifiers": {
            "kvk": "11111111",
            "tooi": "https://identifier.overheid.nl/tooi/id/ministerie/mnre0000",
        },
        "names": [{"value": "Ministerie van BZK"}],
    }
    merged = lc.merge_oin_into_record(record, SAMPLE_ROWS[0], today="2026-05-09")
    assert merged["identifiers"]["oin"] == "00000001003214345000"
    assert merged["identifiers"]["kvk"] == "11111111"
    assert (
        merged["identifiers"]["tooi"]
        == "https://identifier.overheid.nl/tooi/id/ministerie/mnre0000"
    )


# ---------------------------------------------------------------------------
# CSV cache roundtrip
# ---------------------------------------------------------------------------


def test_csv_roundtrip(tmp_path: Path) -> None:
    target = tmp_path / "logius" / "oinregister-2026-05-09.csv"
    lc.rows_to_csv(SAMPLE_ROWS, target)
    assert target.exists()
    loaded = lc.rows_from_csv(target)
    assert len(loaded) == len(SAMPLE_ROWS)
    assert loaded[0]["oin"] == "00000001003214345000"
    assert loaded[0]["tooi"] == "https://identifier.overheid.nl/tooi/id/ministerie/mnre1034"
    # Lege velden komen als None terug, niet als lege string.
    assert loaded[3]["kvk"] is None
    assert loaded[3]["tooi"] is None


# ---------------------------------------------------------------------------
# fetch_oin_register — gepagineerd via een StubClient
# ---------------------------------------------------------------------------


class _StubResponse:
    def __init__(self, payload: list[dict[str, Any]], total: int) -> None:
        self._payload = payload
        self.headers = {"x-total-count": str(total)}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> list[dict[str, Any]]:
        return self._payload


class _PaginatingClient:
    """Mock van httpx.Client die een lijst opdeelt in pagina's van pageSize."""

    def __init__(self, all_records: list[dict[str, Any]]) -> None:
        self.all_records = all_records
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, params: dict[str, Any], headers: dict[str, str]) -> _StubResponse:
        self.calls.append(dict(params))
        page = int(params.get("page", 1))
        size = int(params.get("pageSize", 100))
        start = (page - 1) * size
        chunk = self.all_records[start : start + size]
        return _StubResponse(chunk, total=len(self.all_records))


def test_fetch_oin_register_paginates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lc, "MIN_REQUEST_INTERVAL", 0.0)
    raw = [
        {
            "oin": f"0000000100000000{i:04d}",
            "name": f"Org {i}",
            "kvkNumber": f"{10000000 + i}",
            "rooId": None,
            "organisationType": "GM" if i % 2 == 0 else None,
            "status": "Actief",
            "modificationDate": "2026-01-01T00:00:00",
        }
        for i in range(7)
    ]
    client = _PaginatingClient(raw)
    rows = lc.fetch_oin_register(client=client, page_size=3)
    assert len(rows) == 7
    # 7 records / pageSize 3 → 3 pagina's (3, 3, 1).
    assert [c["page"] for c in client.calls] == [1, 2, 3]
    # Velden zijn genormaliseerd (kvkNumber → kvk, rooId → tooi).
    assert rows[0]["kvk"] == "10000000"
    assert rows[0]["tooi"] is None


# ---------------------------------------------------------------------------
# enrich_organisations end-to-end
# ---------------------------------------------------------------------------


def _write_org(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(payload, fh, sort_keys=False, allow_unicode=True)


def test_enrich_organisations_writes_oin(tmp_path: Path) -> None:
    org_root = tmp_path / "organisaties"
    bzk_path = org_root / "ministeries" / "bzk.yaml"
    _write_org(
        bzk_path,
        {
            "id": "org:min-bzk",
            "type": "ministerie",
            "classification": "ministerie",
            "names": [{"value": "Ministerie van Binnenlandse Zaken en Koninkrijksrelaties"}],
            "valid_from": "2010-10-14",
            "sources": [
                {
                    "id": "roo",
                    "url": "https://organisaties.overheid.nl/9632/",
                    "retrieved": "2026-01-01",
                }
            ],
        },
    )
    utrecht_path = org_root / "gemeenten" / "utrecht.yaml"
    _write_org(
        utrecht_path,
        {
            "id": "org:gemeente-utrecht",
            "type": "gemeente",
            "names": [{"value": "Gemeente Utrecht"}],
            "valid_from": "1900-01-01",
            "sources": [
                {
                    "id": "roo",
                    "url": "https://organisaties.overheid.nl/0344/",
                    "retrieved": "2026-01-01",
                }
            ],
        },
    )
    # Already-set OIN: must not be touched.
    fin_path = org_root / "ministeries" / "fin.yaml"
    _write_org(
        fin_path,
        {
            "id": "org:min-fin",
            "type": "ministerie",
            "identifiers": {"oin": "00000099999999999999"},
            "names": [{"value": "Ministerie van Financiën"}],
            "valid_from": "1798-03-12",
            "sources": [
                {
                    "id": "roo",
                    "url": "https://organisaties.overheid.nl/x/",
                    "retrieved": "2026-01-01",
                }
            ],
        },
    )

    stats = lc.enrich_organisations(
        org_root,
        cache_dir=tmp_path / "cache",
        rows=SAMPLE_ROWS,
        today="2026-05-09",
    )

    bzk_loaded = yaml.safe_load(bzk_path.read_text(encoding="utf-8"))
    assert bzk_loaded["identifiers"]["oin"] == "00000001003214345000"
    assert bzk_loaded["identifiers"]["kvk"] == "50200097"
    src_ids = {s["id"] for s in bzk_loaded["sources"]}
    assert src_ids == {"roo", "logius_cor"}

    utrecht_loaded = yaml.safe_load(utrecht_path.read_text(encoding="utf-8"))
    assert utrecht_loaded["identifiers"]["oin"] == "00000001002220647000"

    fin_loaded = yaml.safe_load(fin_path.read_text(encoding="utf-8"))
    assert fin_loaded["identifiers"]["oin"] == "00000099999999999999"

    assert stats["candidates"] == 3
    assert stats["written"] == 2
    assert stats["matched_name"] == 2
    assert stats["matched_tooi"] == 0


def test_enrich_organisations_dry_run_does_not_write(tmp_path: Path) -> None:
    org_root = tmp_path / "organisaties"
    bzk_path = org_root / "ministeries" / "bzk.yaml"
    _write_org(
        bzk_path,
        {
            "id": "org:min-bzk",
            "type": "ministerie",
            "names": [{"value": "Ministerie van Binnenlandse Zaken en Koninkrijksrelaties"}],
            "valid_from": "2010-10-14",
            "sources": [
                {
                    "id": "roo",
                    "url": "https://organisaties.overheid.nl/9632/",
                    "retrieved": "2026-01-01",
                }
            ],
        },
    )
    before = bzk_path.read_text(encoding="utf-8")

    stats = lc.enrich_organisations(
        org_root,
        cache_dir=tmp_path / "cache",
        rows=SAMPLE_ROWS,
        dry_run=True,
        today="2026-05-09",
    )
    assert stats["written"] == 1
    after = bzk_path.read_text(encoding="utf-8")
    assert after == before


def test_enrich_idempotent(tmp_path: Path) -> None:
    """Tweede run wijzigt niets meer (alle OIN's al gezet)."""
    org_root = tmp_path / "organisaties"
    bzk_path = org_root / "ministeries" / "bzk.yaml"
    _write_org(
        bzk_path,
        {
            "id": "org:min-bzk",
            "type": "ministerie",
            "names": [{"value": "Ministerie van Binnenlandse Zaken en Koninkrijksrelaties"}],
            "valid_from": "2010-10-14",
            "sources": [
                {
                    "id": "roo",
                    "url": "https://organisaties.overheid.nl/9632/",
                    "retrieved": "2026-01-01",
                }
            ],
        },
    )

    stats1 = lc.enrich_organisations(
        org_root, cache_dir=tmp_path / "cache", rows=SAMPLE_ROWS, today="2026-05-09"
    )
    after_first = bzk_path.read_text(encoding="utf-8")

    stats2 = lc.enrich_organisations(
        org_root, cache_dir=tmp_path / "cache", rows=SAMPLE_ROWS, today="2026-05-10"
    )
    after_second = bzk_path.read_text(encoding="utf-8")

    assert stats1["written"] == 1
    assert stats2["written"] == 0
    assert after_first == after_second


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_main_dry_run_with_cached_csv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Voorkom netwerk: schrijf de cache vooraf, --dry-run doet de rest."""
    cache_dir = tmp_path / "cache"
    cache_path = cache_dir / f"oinregister-{lc._today()}.csv"
    lc.rows_to_csv(SAMPLE_ROWS, cache_path)

    org_root = tmp_path / "data" / "organisaties"
    _write_org(
        org_root / "ministeries" / "bzk.yaml",
        {
            "id": "org:min-bzk",
            "type": "ministerie",
            "names": [{"value": "Ministerie van Binnenlandse Zaken en Koninkrijksrelaties"}],
            "valid_from": "2010-10-14",
            "sources": [
                {
                    "id": "roo",
                    "url": "https://organisaties.overheid.nl/9632/",
                    "retrieved": "2026-01-01",
                }
            ],
        },
    )

    rc = lc.main(
        [
            "--data-root",
            str(tmp_path / "data"),
            "--cache",
            str(cache_dir),
            "--dry-run",
        ]
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "OIN-register" in captured.err
