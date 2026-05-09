"""Tests voor `polder.fetchers.roo`."""

from __future__ import annotations

from pathlib import Path

import yaml
from lxml import etree

from polder.fetchers import roo

# ---------------------------------------------------------------------------
# Slugify
# ---------------------------------------------------------------------------


def test_slugify_basic():
    assert roo.slugify("Ministerie van BZK") == "ministerie-van-bzk"


def test_slugify_strips_accents():
    assert roo.slugify("'s-Hertogenbosch") == "s-hertogenbosch"
    assert roo.slugify("Curaçao") == "curacao"
    assert roo.slugify("Súdwest-Fryslân") == "sudwest-fryslan"


def test_slugify_collapses_whitespace_and_punct():
    assert roo.slugify("  Provincie Noord--Holland  ") == "provincie-noord-holland"
    assert roo.slugify("A & B") == "a-en-b"


def test_slugify_empty():
    assert roo.slugify("") == ""
    assert roo.slugify("???") == ""


# ---------------------------------------------------------------------------
# Type-mapping
# ---------------------------------------------------------------------------


def test_type_mapping_gemeente():
    result = roo.roo_type_to_internal("gemeente")
    assert result is not None
    internal, sub_folder, prefix = result
    assert internal == "gemeente"
    assert sub_folder == "gemeenten"
    assert prefix == "gemeente"


def test_type_mapping_ministerie():
    result = roo.roo_type_to_internal("Ministerie")
    assert result is not None
    assert result[0] == "ministerie"
    assert result[1] == "ministeries"
    assert result[2] == "min"


def test_type_mapping_zbo_variants():
    direct = roo.roo_type_to_internal("Zelfstandig Bestuursorgaan")
    abbr = roo.roo_type_to_internal("ZBO")
    nested = roo.roo_type_to_internal("Zelfstandig Bestuursorgaan (ZBO)")
    for result in (direct, abbr, nested):
        assert result is not None
        assert result[0] == "zbo"


def test_type_mapping_unknown():
    assert roo.roo_type_to_internal("Stichting Iets") is None
    assert roo.roo_type_to_internal(None) is None
    assert roo.roo_type_to_internal("") is None


# ---------------------------------------------------------------------------
# build_id
# ---------------------------------------------------------------------------


def test_build_id_avoids_double_prefix():
    assert roo.build_id("min", "min-bzk") == "org:min-bzk"
    assert roo.build_id("gemeente", "amsterdam") == "org:gemeente-amsterdam"
    assert roo.build_id("prov", "prov-utrecht") == "org:prov-utrecht"


# ---------------------------------------------------------------------------
# parse_organisatie
# ---------------------------------------------------------------------------


MINISTERIE_XML = """
<organisatie>
  <id>9632</id>
  <naam>Ministerie van Binnenlandse Zaken en Koninkrijksrelaties</naam>
  <afkorting>BZK</afkorting>
  <type>ministerie</type>
  <oin>00000001003214345000</oin>
  <tooi>https://identifier.overheid.nl/tooi/id/ministerie/mnre1034</tooi>
  <website>https://www.rijksoverheid.nl/ministeries/ministerie-van-binnenlandse-zaken-en-koninkrijksrelaties</website>
  <bezoekadres>Turfmarkt 147, 2511 DP Den Haag</bezoekadres>
  <opgericht>2010-10-14</opgericht>
</organisatie>
"""


GEMEENTE_XML = """
<organisatie>
  <id>1234</id>
  <naam>Gemeente Utrecht</naam>
  <type>gemeente</type>
  <website>https://www.utrecht.nl</website>
  <opgericht>1122-01-01</opgericht>
</organisatie>
"""


UNKNOWN_XML = """
<organisatie>
  <id>9999</id>
  <naam>Stichting Iets</naam>
  <type>stichting</type>
</organisatie>
"""


def test_parse_organisatie_ministerie():
    node = etree.fromstring(MINISTERIE_XML)
    record = roo.parse_organisatie(node)
    assert record is not None
    assert record["id"] == "org:min-bzk"
    assert record["type"] == "ministerie"
    assert record["identifiers"]["oin"] == "00000001003214345000"
    assert (
        record["identifiers"]["tooi"]
        == "https://identifier.overheid.nl/tooi/id/ministerie/mnre1034"
    )
    assert record["identifiers"]["roo_id"] == "9632"
    assert record["names"][0]["value"].startswith("Ministerie van Binnenlandse")
    assert record["names"][0]["abbr"] == "BZK"
    assert record["names"][0]["valid_from"] == "2010-10-14"
    assert record["contact"]["bezoekadres"] == "Turfmarkt 147, 2511 DP Den Haag"
    assert record["valid_from"] == "2010-10-14"
    assert record["valid_until"] is None
    assert record["sources"][0]["id"] == "roo"
    assert record["sources"][0]["url"] == "https://organisaties.overheid.nl/9632/"
    assert record["_sub_folder"] == "ministeries"
    assert record["_slug"] == "bzk"


def test_parse_organisatie_gemeente():
    node = etree.fromstring(GEMEENTE_XML)
    record = roo.parse_organisatie(node)
    assert record is not None
    assert record["id"] == "org:gemeente-utrecht"
    assert record["type"] == "gemeente"
    assert record["_sub_folder"] == "gemeenten"
    assert record["contact"]["website"] == "https://www.utrecht.nl"


def test_parse_organisatie_unknown_type_returns_none():
    node = etree.fromstring(UNKNOWN_XML)
    assert roo.parse_organisatie(node) is None


# ROO-export 2.6.9 zet TOOI-URI als attribute, niet als child-element. We
# lezen die direct van de organisatie-node, niet van nested kinderen zoals
# `<relatieMetMinisterie>` (die zelf ook een TOOI-attribute kan dragen).
ATTR_TOOI_XML = """
<p:organisatie xmlns:p="https://organisaties.overheid.nl/static/schema/oo/export/2.6.9"
               p:systeemId="5445"
               p:resourceIdentifierTOOI="https://identifier.overheid.nl/tooi/id/oorg/oorg12350">
  <p:naam>Inspecteur-Generaal der Krijgsmacht</p:naam>
  <p:afkorting>IGK</p:afkorting>
  <p:types><p:type>Inspectie</p:type></p:types>
  <p:relatieMetMinisterie p:systeemId="4958"
                          p:resourceIdentifierTOOI="https://identifier.overheid.nl/tooi/id/ministerie/mnre1018">Defensie</p:relatieMetMinisterie>
</p:organisatie>
"""


def test_parse_organisatie_reads_tooi_attribute():
    node = etree.fromstring(ATTR_TOOI_XML)
    record = roo.parse_organisatie(node)
    assert record is not None
    assert (
        record["identifiers"]["tooi"]
        == "https://identifier.overheid.nl/tooi/id/oorg/oorg12350"
    )
    # `roo_id` komt uit `systeemId` van de organisatie-node zelf.
    assert record["identifiers"]["roo_id"] == "5445"


# ROO's `<startDatum>` is geen betrouwbare valid_from voor de naam: het is de
# datum waarop het legale entity-record is aangemaakt, vaak ouder dan de
# huidige naam. Voor EZK/IenW levert dat 2010-10-14 op terwijl die namen pas
# in 2017 ontstonden. We negeren `<startDatum>` en vallen terug op de sentinel
# 1900-01-01; de Wikidata-fetcher vult de echte datum in via P571.
STARTDATUM_XML = """
<organisatie>
  <id>10621</id>
  <naam>Ministerie van Economische Zaken en Klimaat</naam>
  <afkorting>EZK</afkorting>
  <type>ministerie</type>
  <startDatum>2010-10-14</startDatum>
</organisatie>
"""


def test_parse_organisatie_ignores_startDatum_for_valid_from():
    node = etree.fromstring(STARTDATUM_XML)
    record = roo.parse_organisatie(node)
    assert record is not None
    assert record["valid_from"] == "1900-01-01"
    assert record["names"][0]["valid_from"] == "1900-01-01"


# Als de XML een echte `<opgericht>` heeft (test-fixtures, of toekomstige
# ROO-versies), gebruiken we die wel.
OPGERICHT_XML = """
<organisatie>
  <id>9632</id>
  <naam>Ministerie van Binnenlandse Zaken en Koninkrijksrelaties</naam>
  <afkorting>BZK</afkorting>
  <type>ministerie</type>
  <opgericht>1798-03-12</opgericht>
</organisatie>
"""


def test_parse_organisatie_uses_opgericht_when_present():
    node = etree.fromstring(OPGERICHT_XML)
    record = roo.parse_organisatie(node)
    assert record is not None
    assert record["valid_from"] == "1798-03-12"
    assert record["names"][0]["valid_from"] == "1798-03-12"


# ---------------------------------------------------------------------------
# Organisatieonderdeel
# ---------------------------------------------------------------------------


ONDERDEEL_EXPORT_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<organisaties>
  <organisatie>
    <id>12159</id>
    <naam>Justis</naam>
    <afkorting>Justis</afkorting>
    <type>agentschap</type>
    <organisaties>
      <organisatie>
        <id>12190</id>
        <naam>Landelijk Bureau Bibob</naam>
        <afkorting>LBB</afkorting>
        <type>organisatieonderdeel</type>
      </organisatie>
    </organisaties>
  </organisatie>
</organisaties>
"""


def test_type_mapping_organisatieonderdeel():
    result = roo.roo_type_to_internal("Organisatieonderdeel")
    assert result is not None
    internal, sub_folder, prefix = result
    assert internal == "organisatieonderdeel"
    assert sub_folder == "organisatieonderdelen"
    assert prefix == "onderdeel"


def test_parse_export_organisatieonderdeel_resolves_parent(tmp_path: Path):
    target = tmp_path / "export.xml"
    target.write_bytes(ONDERDEEL_EXPORT_XML)
    records = roo.parse_export(target)
    assert len(records) == 2

    by_id = {r["id"]: r for r in records}
    onderdeel = by_id["org:onderdeel-lbb"]
    assert onderdeel["type"] == "organisatieonderdeel"
    assert onderdeel["_sub_folder"] == "organisatieonderdelen"
    assert onderdeel["names"][0]["abbr"] == "LBB"
    # parent_id wordt gezet door _resolve_parents in write_records.
    roo.write_records(records, tmp_path / "out", dry_run=True)
    assert onderdeel["parent_id"] == "org:agentschap-justis"


# ---------------------------------------------------------------------------
# parse_export
# ---------------------------------------------------------------------------


EXPORT_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<organisaties>
  <organisatie>
    <id>9632</id>
    <naam>Ministerie van Binnenlandse Zaken en Koninkrijksrelaties</naam>
    <afkorting>BZK</afkorting>
    <type>ministerie</type>
    <oin>00000001003214345000</oin>
  </organisatie>
  <organisatie>
    <id>1234</id>
    <naam>Gemeente Utrecht</naam>
    <type>gemeente</type>
  </organisatie>
  <organisatie>
    <id>9999</id>
    <naam>Iets Geks</naam>
    <type>stichting</type>
  </organisatie>
</organisaties>
"""


def test_parse_export(tmp_path: Path):
    target = tmp_path / "export.xml"
    target.write_bytes(EXPORT_XML)
    records = roo.parse_export(target)
    assert len(records) == 2
    types = {r["type"] for r in records}
    assert types == {"ministerie", "gemeente"}


def test_parse_export_respects_limit(tmp_path: Path):
    target = tmp_path / "export.xml"
    target.write_bytes(EXPORT_XML)
    records = roo.parse_export(target, limit=1)
    assert len(records) == 1


# ---------------------------------------------------------------------------
# merge_yaml
# ---------------------------------------------------------------------------


def test_merge_yaml_preserves_local_wikidata():
    existing = {
        "id": "org:min-bzk",
        "type": "ministerie",
        "identifiers": {
            "oin": "00000001003214345000",
            "wikidata": "Q1727053",
            "roo_id": "9632",
        },
        "names": [
            {
                "value": "Ministerie van Binnenlandse Zaken en Koninkrijksrelaties",
                "abbr": "BZK",
                "valid_from": "2010-10-14",
            }
        ],
        "valid_from": "2010-10-14",
        "valid_until": None,
        "sources": [
            {"id": "roo", "url": "https://organisaties.overheid.nl/9632/", "retrieved": "2026-01-01"},
            {"id": "wikidata", "url": "https://www.wikidata.org/wiki/Q1727053", "retrieved": "2026-01-01"},
        ],
    }
    new = {
        "id": "org:min-bzk",
        "type": "ministerie",
        "identifiers": {
            "oin": "00000001003214345000",
            "roo_id": "9632",
            "tooi": "https://identifier.overheid.nl/tooi/id/ministerie/mnre1034",
        },
        "names": [
            {
                "value": "Ministerie van Binnenlandse Zaken en Koninkrijksrelaties",
                "abbr": "BZK",
                "valid_from": "2010-10-14",
            }
        ],
        "valid_from": "2010-10-14",
        "valid_until": None,
        "sources": [
            {"id": "roo", "url": "https://organisaties.overheid.nl/9632/", "retrieved": "2026-05-09"},
        ],
    }
    merged = roo.merge_yaml(existing, new)
    # Wikidata blijft.
    assert merged["identifiers"]["wikidata"] == "Q1727053"
    # ROO heeft tooi toegevoegd.
    assert (
        merged["identifiers"]["tooi"]
        == "https://identifier.overheid.nl/tooi/id/ministerie/mnre1034"
    )
    # Wikidata-bron blijft, roo-bron is geupdate.
    source_ids = {s["id"] for s in merged["sources"]}
    assert source_ids == {"roo", "wikidata"}
    roo_source = next(s for s in merged["sources"] if s["id"] == "roo")
    assert roo_source["retrieved"] == "2026-05-09"


def test_merge_yaml_updates_name():
    existing = {
        "id": "org:gemeente-utrecht",
        "names": [{"value": "Gemeente Utrecht", "valid_from": "1122-01-01"}],
        "identifiers": {"wikidata": "Q42"},
    }
    new = {
        "id": "org:gemeente-utrecht",
        "names": [{"value": "Gemeente Utrecht", "valid_from": "1122-01-01"}],
        "identifiers": {"roo_id": "1234"},
    }
    merged = roo.merge_yaml(existing, new)
    assert merged["identifiers"] == {"wikidata": "Q42", "roo_id": "1234"}
    assert merged["names"] == [{"value": "Gemeente Utrecht", "valid_from": "1122-01-01"}]


def test_merge_yaml_empty_existing():
    new = {"id": "org:min-bzk", "type": "ministerie"}
    merged = roo.merge_yaml({}, new)
    assert merged == new


# ---------------------------------------------------------------------------
# write_records (idempotency end-to-end)
# ---------------------------------------------------------------------------


def test_write_records_writes_yaml(tmp_path: Path):
    node = etree.fromstring(MINISTERIE_XML)
    record = roo.parse_organisatie(node)
    assert record is not None
    n = roo.write_records([record], tmp_path)
    assert n == 1
    target = tmp_path / "ministeries" / "bzk.yaml"
    assert target.exists()
    loaded = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert loaded["id"] == "org:min-bzk"
    assert loaded["type"] == "ministerie"
    assert loaded["names"][0]["abbr"] == "BZK"


def test_write_records_dry_run(tmp_path: Path):
    node = etree.fromstring(MINISTERIE_XML)
    record = roo.parse_organisatie(node)
    assert record is not None
    n = roo.write_records([record], tmp_path, dry_run=True)
    assert n == 1
    assert not (tmp_path / "ministeries" / "bzk.yaml").exists()


def test_write_records_idempotent_preserves_local_fields(tmp_path: Path):
    target_dir = tmp_path / "ministeries"
    target_dir.mkdir(parents=True)
    target = target_dir / "bzk.yaml"
    target.write_text(
        yaml.safe_dump(
            {
                "id": "org:min-bzk",
                "type": "ministerie",
                "identifiers": {"wikidata": "Q1727053"},
                "names": [
                    {
                        "value": "Ministerie van Binnenlandse Zaken en Koninkrijksrelaties",
                        "abbr": "BZK",
                        "valid_from": "2010-10-14",
                    }
                ],
                "valid_from": "2010-10-14",
                "valid_until": None,
                "sources": [
                    {
                        "id": "roo",
                        "url": "https://organisaties.overheid.nl/9632/",
                        "retrieved": "2026-01-01",
                    }
                ],
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    node = etree.fromstring(MINISTERIE_XML)
    record = roo.parse_organisatie(node)
    assert record is not None
    roo.write_records([record], tmp_path)

    loaded = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert loaded["identifiers"]["wikidata"] == "Q1727053"
    assert loaded["identifiers"]["oin"] == "00000001003214345000"
