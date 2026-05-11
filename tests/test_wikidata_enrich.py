"""Tests voor polder.fetchers.wikidata_enrich."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from polder.fetchers.wikidata_enrich import (
    EnrichStats,
    enrich_ori_records,
    enrich_record,
)


@pytest.fixture
def ori_record() -> dict:
    return {
        "id": "person:smit-w-1234567",
        "name": {"family": "Smit", "given": "Willem", "initials": "W."},
        "sources": [
            {"id": "open_raadsinformatie", "url": "https://x", "retrieved": "2026-01-01"}
        ],
    }


def test_enrich_record_skips_without_given():
    rec = {"id": "x", "name": {"family": "Smit"}, "sources": []}
    ok, reason = enrich_record(rec)
    assert not ok
    assert reason == "no_given"


def test_enrich_record_skips_already_has_birth():
    rec = {
        "id": "x",
        "name": {"family": "Smit", "given": "Willem"},
        "birth": {"year": 1980},
    }
    ok, reason = enrich_record(rec)
    assert not ok
    assert reason == "already_has_birth"


def test_enrich_record_skips_already_has_wikidata():
    rec = {
        "id": "x",
        "name": {"family": "Smit", "given": "Willem"},
        "identifiers": {"wikidata": "Q123"},
    }
    ok, reason = enrich_record(rec)
    assert not ok
    assert reason == "already_has_wikidata"


def test_enrich_record_no_matches(ori_record: dict):
    with patch(
        "polder.fetchers.wikidata_enrich.lookup_person_by_name",
        return_value=[],
    ):
        ok, reason = enrich_record(ori_record)
    assert not ok
    assert reason == "no_matches"


def test_enrich_record_no_birth_year(ori_record: dict):
    """Kandidaten zonder birth-year tellen niet."""
    with patch(
        "polder.fetchers.wikidata_enrich.lookup_person_by_name",
        return_value=[{"qid": "Q123", "label": "Willem Smit", "birth_year": None}],
    ):
        ok, reason = enrich_record(ori_record)
    assert not ok
    assert reason == "no_birth_year"


def test_enrich_record_ambiguous(ori_record: dict):
    """Twee kandidaten met birth-year = skip (zou verkeerde kunnen koppelen)."""
    with patch(
        "polder.fetchers.wikidata_enrich.lookup_person_by_name",
        return_value=[
            {"qid": "Q123", "label": "Willem Smit", "birth_year": 1970},
            {"qid": "Q456", "label": "Willem Smit", "birth_year": 1985},
        ],
    ):
        ok, reason = enrich_record(ori_record)
    assert not ok
    assert reason == "ambiguous"


def test_enrich_record_success(ori_record: dict):
    with patch(
        "polder.fetchers.wikidata_enrich.lookup_person_by_name",
        return_value=[{"qid": "Q123", "label": "Willem Smit", "birth_year": 1970}],
    ):
        ok, reason = enrich_record(ori_record, today="2026-05-11")
    assert ok
    assert reason == "enriched"
    assert ori_record["birth"] == {"year": 1970}
    assert ori_record["identifiers"]["wikidata"] == "Q123"
    # Wikidata-source toegevoegd
    src_ids = {s["id"] for s in ori_record["sources"]}
    assert "wikidata" in src_ids


def test_enrich_ori_records_loops_and_counts(tmp_path: Path):
    data = tmp_path / "data"
    (data / "personen").mkdir(parents=True)

    rec = {
        "id": "person:smit-w-9999",
        "name": {"family": "Smit", "given": "Willem"},
        "sources": [
            {"id": "open_raadsinformatie", "url": "https://x", "retrieved": "2026-01-01"}
        ],
    }
    (data / "personen" / "smit-w-9999.yaml").write_text(
        yaml.safe_dump(rec, sort_keys=False), encoding="utf-8"
    )

    with patch(
        "polder.fetchers.wikidata_enrich.lookup_person_by_name",
        return_value=[{"qid": "Q123", "label": "W. Smit", "birth_year": 1970}],
    ):
        stats = enrich_ori_records(data)

    assert stats.candidates == 1
    assert stats.enriched == 1

    # Verifieer dat het bestand is bijgewerkt
    written = yaml.safe_load((data / "personen" / "smit-w-9999.yaml").read_text())
    assert written["birth"]["year"] == 1970
    assert written["identifiers"]["wikidata"] == "Q123"


def test_enrich_ori_records_skips_records_without_ori(tmp_path: Path):
    data = tmp_path / "data"
    (data / "personen").mkdir(parents=True)
    rec = {
        "id": "person:x",
        "name": {"family": "Smit", "given": "Willem"},
        "sources": [{"id": "tk_odata", "url": "x", "retrieved": "2026-01-01"}],
    }
    (data / "personen" / "x.yaml").write_text(yaml.safe_dump(rec), encoding="utf-8")
    stats = enrich_ori_records(data)
    assert stats.candidates == 0
