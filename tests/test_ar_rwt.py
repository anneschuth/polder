"""Tests voor `polder.fetchers.ar_rwt`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from polder.fetchers import ar_rwt

# ---------------------------------------------------------------------------
# Fixture HTML: een vereenvoudigde versie van de Rekenkamer-pagina-structuur.
# ---------------------------------------------------------------------------

FIXTURE_HTML = """
<!doctype html>
<html lang="nl">
<head><title>RWT-register</title></head>
<body>
<main>
  <div>
    <p>Overzicht van rechtspersonen met een wettelijke taak (rwt).</p>
    <p>Voor het laatst bijgewerkt op 14 maart 2023.</p>
  </div>
  <div>
    <h2>Binnenlandse Zaken en Koninkrijksrelaties (BZK)</h2>
    <ul>
      <li>Bureau Architectenregister</li>
      <li>Dienst voor het Kadaster en de openbare registers (Kadaster)</li>
      <li>Stichting Waarborgfonds Sociale Woningbouw (WSW)</li>
    </ul>
  </div>
  <div>
    <h2>Economische Zaken en Klimaat (EZK)</h2>
    <ul>
      <li>Centraal Bureau voor de Statistiek (CBS)</li>
      <li>Kamer van Koophandel</li>
      <li>Waarborginstellingen (cluster)
        <ul>
          <li>Edelmetaal Waarborg Nederland (EWN)</li>
          <li>Waarborg Holland</li>
        </ul>
      </li>
    </ul>
  </div>
  <div>
    <h2>Onderwijs, Cultuur en Wetenschap (OCW)</h2>
    <ul>
      <li>Koninklijke Bibliotheek (KB)</li>
    </ul>
  </div>
</main>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Slug & name-key
# ---------------------------------------------------------------------------


def test_slugify_matches_roo_convention() -> None:
    assert ar_rwt.slugify("Centraal Bureau voor de Statistiek") == (
        "centraal-bureau-voor-de-statistiek"
    )
    assert ar_rwt.slugify("Curaçao") == "curacao"
    assert ar_rwt.slugify("") == ""


def test_name_key_strips_parens_and_suffix() -> None:
    # Afkorting tussen haakjes en rechtsvorm-suffix worden uit de key gehaald.
    assert ar_rwt.name_key("De Nederlandsche Bank N.V. (DNB)") == "de nederlandsche bank"
    assert ar_rwt.name_key("Stichting Waarborgfonds Sociale Woningbouw (WSW)") == (
        "waarborgfonds sociale woningbouw"
    )


# ---------------------------------------------------------------------------
# parse_register
# ---------------------------------------------------------------------------


def test_parse_register_extracts_per_ministerie() -> None:
    records = ar_rwt.parse_register(FIXTURE_HTML)
    names = [r["name"] for r in records]
    assert "Bureau Architectenregister" in names
    assert "Centraal Bureau voor de Statistiek (CBS)" in names
    assert "Koninklijke Bibliotheek (KB)" in names

    bureau = next(r for r in records if r["name"] == "Bureau Architectenregister")
    assert bureau["ministerie"].startswith("Binnenlandse Zaken")
    assert bureau["cluster"] is False
    assert bureau["parent"] is None


def test_parse_register_handles_clusters() -> None:
    records = ar_rwt.parse_register(FIXTURE_HTML)
    cluster = next(r for r in records if r["name"].lower().startswith("waarborginstellingen"))
    assert cluster["cluster"] is True
    children = [r for r in records if r.get("parent") == cluster["name"]]
    assert {c["name"] for c in children} == {
        "Edelmetaal Waarborg Nederland (EWN)",
        "Waarborg Holland",
    }


def test_parse_register_returns_empty_for_unrelated_html() -> None:
    assert ar_rwt.parse_register("<html><body><p>geen lijst</p></body></html>") == []


# ---------------------------------------------------------------------------
# Match & apply
# ---------------------------------------------------------------------------


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)


def test_match_record_finds_existing_zbo(tmp_path: Path) -> None:
    data_dir = tmp_path / "organisaties"
    _write_yaml(
        data_dir / "zbo" / "cbs.yaml",
        {
            "id": "org:zbo-cbs",
            "type": "zbo",
            "names": [
                {
                    "value": "Centraal Bureau voor de Statistiek",
                    "abbr": "CBS",
                    "valid_from": "1899-01-09",
                },
            ],
            "valid_from": "1899-01-09",
            "valid_until": None,
            "sources": [
                {
                    "id": "roo",
                    "url": "https://organisaties.overheid.nl/archive/exportOO.xml",
                    "retrieved": "2026-01-01",
                }
            ],
        },
    )
    index = ar_rwt._load_existing_index(data_dir)
    rwt = {"name": "Centraal Bureau voor de Statistiek (CBS)", "cluster": False, "parent": None}
    match = ar_rwt.match_record(rwt, index)
    assert match is not None
    assert match.name == "cbs.yaml"


def test_apply_records_updates_existing_and_creates_new(tmp_path: Path) -> None:
    data_dir = tmp_path / "organisaties"
    cbs = data_dir / "zbo" / "cbs.yaml"
    _write_yaml(
        cbs,
        {
            "id": "org:zbo-cbs",
            "type": "zbo",
            "classification": "zbo",
            "names": [
                {
                    "value": "Centraal Bureau voor de Statistiek",
                    "abbr": "CBS",
                    "valid_from": "1899-01-09",
                },
            ],
            "valid_from": "1899-01-09",
            "valid_until": None,
            "sources": [
                {
                    "id": "roo",
                    "url": "https://organisaties.overheid.nl/archive/exportOO.xml",
                    "retrieved": "2026-01-01",
                }
            ],
        },
    )
    rwts = ar_rwt.parse_register(FIXTURE_HTML)
    matched, created, review = ar_rwt.apply_records(rwts, data_dir)
    assert matched >= 1
    assert created >= 1  # Bureau Architectenregister, KB, etc. zijn nieuw

    # CBS heeft nu twee sources.
    with cbs.open("r", encoding="utf-8") as fh:
        updated = yaml.safe_load(fh)
    src_ids = {s["id"] for s in updated["sources"]}
    assert src_ids == {"roo", "ar_rwt"}
    ar_src = next(s for s in updated["sources"] if s["id"] == "ar_rwt")
    assert ar_src["url"] == ar_rwt.RWT_REGISTER_URL
    assert ar_src["fields"] == ["rwt-status"]

    # Een nieuwe RWT is geschreven onder rwt/.
    new_record = data_dir / "rwt" / "bureau-architectenregister.yaml"
    assert new_record.exists()
    with new_record.open("r", encoding="utf-8") as fh:
        body = yaml.safe_load(fh)
    assert body["type"] == "rwt"
    assert body["id"] == "org:rwt-bureau-architectenregister"
    assert body["sources"][0]["id"] == "ar_rwt"

    # Cluster-heading "Waarborginstellingen (cluster)" is overgeslagen voor schrijven
    # (review-list) maar de twee sub-items zijn wel aangemaakt.
    assert any(r["name"].lower().startswith("waarborginstellingen") for r in review)
    assert (data_dir / "rwt" / "edelmetaal-waarborg-nederland.yaml").exists() or (
        data_dir / "rwt" / "edelmetaal-waarborg-nederland-ewn.yaml"
    ).exists()


def test_apply_records_dry_run_writes_nothing(tmp_path: Path) -> None:
    data_dir = tmp_path / "organisaties"
    rwts = ar_rwt.parse_register(FIXTURE_HTML)
    matched, created, _ = ar_rwt.apply_records(rwts, data_dir, dry_run=True)
    assert created > 0
    assert matched == 0
    # Niets geschreven.
    assert not any(data_dir.rglob("*.yaml"))


def test_apply_records_is_idempotent(tmp_path: Path) -> None:
    data_dir = tmp_path / "organisaties"
    rwts = ar_rwt.parse_register(FIXTURE_HTML)
    ar_rwt.apply_records(rwts, data_dir)
    snapshot = {p: p.read_text(encoding="utf-8") for p in data_dir.rglob("*.yaml")}
    ar_rwt.apply_records(rwts, data_dir)
    after = {p: p.read_text(encoding="utf-8") for p in data_dir.rglob("*.yaml")}
    assert snapshot == after
