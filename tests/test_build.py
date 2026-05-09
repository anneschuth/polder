"""Tests voor polder.build."""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

import pytest
import yaml

from polder.build import build_csv, build_datapackage, build_sqlite


def _write_yaml(path: Path, data: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)


@pytest.fixture
def fixture_data(tmp_path: Path) -> Path:
    """Bouw fixture met 1 organisatie + 1 persoon + 1 post + 1 mandaat."""
    data_dir = tmp_path / "data"
    organisatie = {
        "id": "org:min-bzk",
        "type": "ministerie",
        "identifiers": {
            "oin": "00000001003214345000",
            "tooi": "https://identifier.overheid.nl/tooi/id/ministerie/mnre1034",
            "wikidata": "Q1727053",
            "roo_id": "9632",
        },
        "classification": "ministerie",
        "parent_id": None,
        "names": [
            {
                "value": "Ministerie van Binnenlandse Zaken en Koninkrijksrelaties",
                "abbr": "BZK",
                "valid_from": "2010-10-14",
            }
        ],
        "contact": {
            "website": "https://www.rijksoverheid.nl/ministeries/bzk",
            "bezoekadres": "Turfmarkt 147, 2511 DP Den Haag",
        },
        "valid_from": "1798-08-12",
        "valid_until": None,
        "sources": [
            {
                "id": "roo",
                "url": "https://organisaties.overheid.nl/9632/",
                "retrieved": "2026-05-08",
                "fields": ["names", "classification"],
            }
        ],
    }
    _write_yaml(data_dir / "organisaties" / "ministeries" / "min-bzk.yaml", organisatie)

    post = {
        "id": "post:sg-min-bzk",
        "organization_id": "org:min-bzk",
        "label": "Secretaris-Generaal",
        "classification": "abd-tmg",
        "seat_count": 1,
        "valid_from": "1962-01-01",
        "valid_until": None,
    }
    _write_yaml(data_dir / "posten" / "sg-min-bzk.yaml", post)

    persoon = {
        "id": "person:jansen-jp-1965",
        "identifiers": {
            "wikidata": "Q12345678",
            "tk_persoon_id": None,
        },
        "name": {
            "full": "Jan Pieter Jansen",
            "family": "Jansen",
            "given": "Jan Pieter",
            "initials": "J.P.",
        },
        "birth": {"year": 1965},
        "gender": "m",
        "mandaten": [
            {
                "id": "01HXY9ABCDEFGHJKMNPQRSTVWX",
                "organization_id": "org:min-bzk",
                "post_id": "post:sg-min-bzk",
                "role": "Secretaris-Generaal",
                "start_date": "2022-09-01",
                "end_date": None,
                "appointment": {
                    "decision": "KB 2022-08-15",
                    "staatscourant_url": "https://zoek.officielebekendmakingen.nl/stcrt-2022-1.html",
                },
                "sources": [
                    {
                        "id": "staatscourant",
                        "url": "https://zoek.officielebekendmakingen.nl/stcrt-2022-1.html",
                        "retrieved": "2022-08-20",
                    }
                ],
            }
        ],
        "sources": [
            {
                "id": "roo",
                "url": "https://organisaties.overheid.nl/9632/",
                "retrieved": "2026-05-08",
            }
        ],
    }
    _write_yaml(data_dir / "personen" / "current" / "jansen-jp-1965.yaml", persoon)

    return data_dir


def test_build_sqlite_creates_tables_and_rows(fixture_data: Path, tmp_path: Path) -> None:
    out = tmp_path / "polder.db"
    build_sqlite(fixture_data, out)
    assert out.exists()

    conn = sqlite3.connect(out)
    try:
        cur = conn.cursor()
        tables = {
            row[0] for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"organisaties", "personen", "posten", "mandaten", "sources"} <= tables

        assert cur.execute("SELECT COUNT(*) FROM organisaties").fetchone()[0] == 1
        assert cur.execute("SELECT COUNT(*) FROM personen").fetchone()[0] == 1
        assert cur.execute("SELECT COUNT(*) FROM posten").fetchone()[0] == 1
        assert cur.execute("SELECT COUNT(*) FROM mandaten").fetchone()[0] == 1

        org_id, identifiers_json = cur.execute(
            "SELECT id, identifiers FROM organisaties"
        ).fetchone()
        assert org_id == "org:min-bzk"
        assert json.loads(identifiers_json)["oin"] == "00000001003214345000"

        m_person, m_post = cur.execute("SELECT person_id, post_id FROM mandaten").fetchone()
        assert m_person == "person:jansen-jp-1965"
        assert m_post == "post:sg-min-bzk"

        # sources tabel: minimaal 3 rijen (org + persoon + mandaat)
        assert cur.execute("SELECT COUNT(*) FROM sources").fetchone()[0] >= 3
    finally:
        conn.close()


def test_build_csv_produces_expected_columns(fixture_data: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "csv"
    build_csv(fixture_data, out_dir)

    expected_files = {
        "organisaties.csv",
        "personen.csv",
        "posten.csv",
        "mandaten.csv",
        "sources.csv",
    }
    assert {p.name for p in out_dir.iterdir()} >= expected_files

    with (out_dir / "organisaties.csv").open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        assert reader.fieldnames is not None
        for col in ("id", "type", "name", "oin", "tooi", "wikidata"):
            assert col in reader.fieldnames
        assert len(rows) == 1
        assert rows[0]["oin"] == "00000001003214345000"
        assert rows[0]["name"].startswith("Ministerie")

    with (out_dir / "personen.csv").open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        for col in ("id", "name_full", "name_family", "birth_year", "wikidata"):
            assert col in reader.fieldnames
        assert rows[0]["birth_year"] == "1965"

    with (out_dir / "mandaten.csv").open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        assert rows[0]["person_id"] == "person:jansen-jp-1965"
        assert rows[0]["post_id"] == "post:sg-min-bzk"


def test_build_datapackage_produces_valid_json(fixture_data: Path, tmp_path: Path) -> None:
    csv_dir = tmp_path / "dist" / "csv"
    out = tmp_path / "dist" / "datapackage.json"
    build_csv(fixture_data, csv_dir)
    build_datapackage(fixture_data, csv_dir, out)

    assert out.exists()
    with out.open(encoding="utf-8") as fh:
        descriptor = json.load(fh)

    assert descriptor["name"] == "polder"
    assert descriptor["version"] == "0.0.1"
    assert descriptor["licenses"][0]["name"] == "CC0-1.0"

    resource_names = {r["name"] for r in descriptor["resources"]}
    assert {"organisaties", "personen", "posten", "mandaten", "sources"} <= resource_names

    organisaties = next(r for r in descriptor["resources"] if r["name"] == "organisaties")
    field_names = {f["name"] for f in organisaties["schema"]["fields"]}
    assert {"id", "type", "name", "oin"} <= field_names


def test_root_datapackage_json_is_valid() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    path = repo_root / "datapackage.json"
    assert path.exists()
    with path.open(encoding="utf-8") as fh:
        descriptor = json.load(fh)
    assert descriptor["name"] == "polder"
    assert descriptor["profile"] == "data-package"
    assert any(r["name"] == "organisaties" for r in descriptor["resources"])
