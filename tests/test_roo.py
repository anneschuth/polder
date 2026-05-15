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
    assert record["identifiers"]["tooi"] == "https://identifier.overheid.nl/tooi/id/oorg/oorg12350"
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
            {
                "id": "roo",
                "url": "https://organisaties.overheid.nl/9632/",
                "retrieved": "2026-01-01",
            },
            {
                "id": "wikidata",
                "url": "https://www.wikidata.org/wiki/Q1727053",
                "retrieved": "2026-01-01",
            },
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
            {
                "id": "roo",
                "url": "https://organisaties.overheid.nl/9632/",
                "retrieved": "2026-05-09",
            },
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


# ---------------------------------------------------------------------------
# Extraction helpers — full ROO-superset
# ---------------------------------------------------------------------------


# Real-shape ROO-XML: identifiers via <identificatiecodes><resourceIdentifier
# p:naam="X">value</resourceIdentifier> rather than losse <oin>/<rsin> children.
FULL_IDENTIFIERS_XML = """
<organisatie xmlns:p="https://organisaties.overheid.nl/static/schema/oo/export/2.6.9"
             p:systeemId="21849"
             p:resourceIdentifierTOOI="https://identifier.overheid.nl/tooi/id/gemeente/gm0794"
             p:resourceIdentifierOWMS="http://standaarden.overheid.nl/owms/terms/Helmond_(gemeente)">
  <naam>Gemeente Helmond</naam>
  <afkorting>Helmond</afkorting>
  <types><type>Gemeente</type></types>
  <identificatiecodes>
    <resourceIdentifier p:naam="resourceIdentifierOWMS">http://standaarden.overheid.nl/owms/terms/Helmond_(gemeente)</resourceIdentifier>
    <resourceIdentifier p:naam="resourceIdentifierTOOI">https://identifier.overheid.nl/tooi/id/gemeente/gm0794</resourceIdentifier>
    <resourceIdentifier p:naam="Organisatiecode">gm0794</resourceIdentifier>
    <resourceIdentifier p:naam="systeemId">21849</resourceIdentifier>
    <resourceIdentifier p:naam="OIN">00000001001600291000</resourceIdentifier>
    <resourceIdentifier p:naam="KVK-nummer">17272669</resourceIdentifier>
    <resourceIdentifier p:naam="rsin">001600291</resourceIdentifier>
    <resourceIdentifier p:naam="ICTU-code">00794</resourceIdentifier>
    <resourceIdentifier p:naam="ATU">http://publications.europa.eu/resource/authority/atu/NLD_GEM_HLM</resourceIdentifier>
    <resourceIdentifier p:naam="btw-nummer">NL001600291B01</resourceIdentifier>
    <resourceIdentifier p:naam="Loonheffingennummer">001600291L01</resourceIdentifier>
  </identificatiecodes>
</organisatie>
"""


def test_extract_identifiers_full_eleven_types():
    node = etree.fromstring(FULL_IDENTIFIERS_XML)
    record = roo.parse_organisatie(node)
    assert record is not None
    ids = record["identifiers"]
    assert ids["roo_id"] == "21849"
    assert ids["tooi"] == "https://identifier.overheid.nl/tooi/id/gemeente/gm0794"
    assert ids["owms"] == "http://standaarden.overheid.nl/owms/terms/Helmond_(gemeente)"
    assert ids["organisatiecode"] == "gm0794"
    assert ids["oin"] == "00000001001600291000"
    assert ids["kvk"] == "17272669"
    assert ids["rsin"] == "001600291"
    assert ids["ictu"] == "00794"
    assert ids["atu"] == "http://publications.europa.eu/resource/authority/atu/NLD_GEM_HLM"
    assert ids["btw"] == "NL001600291B01"
    assert ids["loonheffing"] == "001600291L01"


def test_extract_identifiers_rsin_appears_in_yaml():
    """Regressie: RSIN werd voorheen wel uit XML gelezen maar nooit naar YAML
    geschreven (bug in oude `parse_organisatie`). Deze test pint vast dat
    RSIN nu echt in `record["identifiers"]` zit."""
    node = etree.fromstring(FULL_IDENTIFIERS_XML)
    record = roo.parse_organisatie(node)
    assert record is not None
    assert "rsin" in record["identifiers"]
    assert record["identifiers"]["rsin"] == "001600291"


# Adressen: meerdere <adres>-blokken met BAG-velden.
ADDRESSES_XML = """
<organisatie xmlns:p="https://organisaties.overheid.nl/static/schema/oo/export/2.6.9"
             p:systeemId="100">
  <naam>Foo</naam>
  <types><type>Gemeente</type></types>
  <adressen>
    <adres>
      <adresType>Bezoekadres</adresType>
      <openbareRuimte>Hoofdstraat</openbareRuimte>
      <huisnummer>1</huisnummer>
      <toevoeging>A</toevoeging>
      <postcode>1234 AB</postcode>
      <woonplaats>Utrecht</woonplaats>
      <provincie>https://identifier.overheid.nl/tooi/id/provincie/pv26</provincie>
    </adres>
    <adres>
      <adresType>Postadres</adresType>
      <postbus>42</postbus>
      <postcode>5678 CD</postcode>
      <woonplaats>Utrecht</woonplaats>
    </adres>
  </adressen>
</organisatie>
"""


def test_extract_addresses_structured_and_legacy():
    node = etree.fromstring(ADDRESSES_XML)
    record = roo.parse_organisatie(node)
    assert record is not None
    contact = record["contact"]
    addrs = contact["addresses"]
    assert len(addrs) == 2
    bezoek = next(a for a in addrs if a["type"] == "Bezoekadres")
    assert bezoek["openbare_ruimte"] == "Hoofdstraat"
    assert bezoek["huisnummer"] == "1"
    assert bezoek["huisnummer_toevoeging"] == "A"
    assert bezoek["postcode"] == "1234 AB"
    post = next(a for a in addrs if a["type"] == "Postadres")
    assert post["postbus"] == "42"
    # Legacy plain-text gegenereerd uit gestructureerde data.
    assert "Hoofdstraat" in contact["bezoekadres"]
    assert "Postbus 42" in contact["postadres"]


# Classificaties.
CLASSIFICATIONS_XML = """
<organisatie xmlns:p="https://organisaties.overheid.nl/static/schema/oo/export/2.6.9"
             p:systeemId="1">
  <naam>X</naam>
  <types><type>Gemeente</type></types>
  <classificaties>
    <classificatie p:type="Woo" p:url="https://example.org/woo">
      <wettelijkeGrondslagen>
        <wettelijkeGrondslag>
          <opschrift>Wet open overheid</opschrift>
          <referentie>https://wetten.overheid.nl/BWBR0045754</referentie>
        </wettelijkeGrondslag>
      </wettelijkeGrondslagen>
    </classificatie>
    <classificatie p:type="WNT-instelling" p:url="https://example.org/wnt"/>
  </classificaties>
</organisatie>
"""


def test_extract_classifications():
    node = etree.fromstring(CLASSIFICATIONS_XML)
    record = roo.parse_organisatie(node)
    assert record is not None
    cls = record["classifications"]
    assert len(cls) == 2
    woo = next(c for c in cls if c["type"] == "Woo")
    assert woo["url"] == "https://example.org/woo"
    assert woo["wettelijke_grondslagen"][0]["opschrift"] == "Wet open overheid"
    wnt = next(c for c in cls if c["type"] == "WNT-instelling")
    assert wnt["url"] == "https://example.org/wnt"
    assert "wettelijke_grondslagen" not in wnt


# Geografie + raad voor gemeente.
GEMEENTE_RICH_XML = """
<organisatie xmlns:p="https://organisaties.overheid.nl/static/schema/oo/export/2.6.9"
             p:systeemId="100">
  <naam>Gemeente Helmond</naam>
  <afkorting>Helmond</afkorting>
  <types><type>Gemeente</type></types>
  <geografie>
    <oppervlakte p:eenheid="km2">54,56</oppervlakte>
    <aantalInwoners>96860</aantalInwoners>
    <inwoners p:eenheid="per km2">1775</inwoners>
    <bevatPlaatsen>Helmond, Stiphout</bevatPlaatsen>
  </geografie>
  <raad>
    <totaalZetels>37</totaalZetels>
    <partijen>
      <partij><naam>Helder Helmond</naam><aantalZetels>8</aantalZetels></partij>
      <partij><naam>VVD</naam><aantalZetels>5</aantalZetels></partij>
    </partijen>
  </raad>
</organisatie>
"""


def test_extract_geografie_dutch_decimal():
    node = etree.fromstring(GEMEENTE_RICH_XML)
    record = roo.parse_organisatie(node)
    assert record is not None
    geo = record["geography"]
    # Dutch decimal komma → float.
    assert geo["oppervlakte_km2"] == 54.56
    assert geo["aantal_inwoners"] == 96860
    assert geo["inwoners_per_km2"] == 1775.0
    assert geo["bevat_plaatsen"] == ["Helmond", "Stiphout"]


def test_extract_council():
    node = etree.fromstring(GEMEENTE_RICH_XML)
    record = roo.parse_organisatie(node)
    assert record is not None
    council = record["council"]
    assert council["total_seats"] == 37
    assert len(council["parties"]) == 2
    assert council["parties"][0] == {"naam": "Helder Helmond", "aantal_zetels": 8}


# Description (organisatieBeschrijving) + relation_to_ministerie + afspraak.
INSPECTIE_XML = """
<organisatie xmlns:p="https://organisaties.overheid.nl/static/schema/oo/export/2.6.9"
             p:systeemId="5445">
  <naam>Inspecteur-Generaal der Krijgsmacht</naam>
  <afkorting>IGK</afkorting>
  <types><type>Inspectie</type></types>
  <relatieMetMinisterie p:systeemId="4958"
                        p:resourceIdentifierTOOI="https://identifier.overheid.nl/tooi/id/ministerie/mnre1018"
                        p:resourceIdentifierOWMS="http://standaarden.overheid.nl/owms/terms/Ministerie_van_Defensie">Defensie</relatieMetMinisterie>
  <organisatieBeschrijving>
    <beschrijvingText>De IGK bemiddelt.</beschrijvingText>
    <url>https://www.defensie.nl/onderwerpen/igk</url>
  </organisatieBeschrijving>
  <afspraak>
    <email>igk@mindef.nl</email>
    <telefoonnummer>(088) 956 63 23</telefoonnummer>
  </afspraak>
  <organogram>
    <url>https://www.defensie.nl/organogram</url>
  </organogram>
  <datumMutatie>2025-10-02</datumMutatie>
  <datumTerVerificatie>2026-04-09</datumTerVerificatie>
  <rechtsvorm>Publiekrechtelijk - Onderdeel Staat der Nederlanden</rechtsvorm>
</organisatie>
"""


def test_extract_relation_description_afspraak_organogram():
    node = etree.fromstring(INSPECTIE_XML)
    record = roo.parse_organisatie(node)
    assert record is not None
    rel = record["relation_to_ministerie"]
    assert rel["naam"] == "Defensie"
    assert rel["roo_id"] == "4958"
    assert rel["tooi"].endswith("mnre1018")
    assert rel["owms"].endswith("Ministerie_van_Defensie")
    assert record["description"]["text"] == "De IGK bemiddelt."
    assert record["description"]["url"] == "https://www.defensie.nl/onderwerpen/igk"
    assert record["afspraak"] == {
        "email": "igk@mindef.nl",
        "telefoonnummer": "(088) 956 63 23",
    }
    assert record["organogram_url"] == "https://www.defensie.nl/organogram"
    assert record["last_mutation"] == "2025-10-02"
    assert record["last_verified"] == "2026-04-09"
    assert record["legal_form"] == "Publiekrechtelijk - Onderdeel Staat der Nederlanden"


# Contact-block: telefoonnummers, emailadressen, internetadressen.
CONTACT_XML = """
<organisatie xmlns:p="https://organisaties.overheid.nl/static/schema/oo/export/2.6.9"
             p:systeemId="1">
  <naam>X</naam>
  <types><type>Gemeente</type></types>
  <contact>
    <telefoonnummers>
      <telefoonnummer><nummer>14 030</nummer><label>algemeen</label></telefoonnummer>
    </telefoonnummers>
    <emailadressen>
      <emailadres><email>info@x.nl</email><label>algemeen</label></emailadres>
    </emailadressen>
    <internetadressen>
      <internetadres><url>https://x.nl</url><label>algemeen</label></internetadres>
    </internetadressen>
    <contactformulieren>
      <contactformulier><url>https://x.nl/contact</url><label>Algemeen</label></contactformulier>
    </contactformulieren>
  </contact>
</organisatie>
"""


def test_extract_contact_block():
    node = etree.fromstring(CONTACT_XML)
    record = roo.parse_organisatie(node)
    assert record is not None
    c = record["contact"]
    assert c["phones"] == [{"nummer": "14 030", "label": "algemeen"}]
    assert c["emails"] == [{"email": "info@x.nl", "label": "algemeen"}]
    assert c["internet_addresses"] == [{"url": "https://x.nl", "label": "algemeen"}]
    assert c["contact_forms"] == [{"url": "https://x.nl/contact", "label": "Algemeen"}]
    # `email` legacy-veld wordt afgeleid uit `emails[0]`.
    assert c["email"] == "info@x.nl"


# Gemeenschappelijke regeling.
GR_XML = """
<regeling xmlns:p="https://organisaties.overheid.nl/static/schema/oo/export/2.6.9"
          p:systeemId="25408161">
  <titel>BVO Recreatie Midden-Nederland</titel>
  <citeertitel>BVO RMN</citeertitel>
  <types><type>Gemeenschappelijke regeling</type></types>
  <samenwerkingsvorm p:afkorting="BVO">Bedrijfsvoeringsorganisatie</samenwerkingsvorm>
  <bevoegdheidsverkrijgingen>
    <bevoegdheidsverkrijging>Delegatie</bevoegdheidsverkrijging>
  </bevoegdheidsverkrijgingen>
  <regionaalSamenwerkingsorgaan p:systeemId="25408166">RMN</regionaalSamenwerkingsorgaan>
  <bronhouder p:systeemId="25408166">RMN</bronhouder>
  <archiefzorgdrager p:systeemId="25408166">RMN</archiefzorgdrager>
  <taalcode>nl-NL</taalcode>
  <registratiehouder>BZK</registratiehouder>
  <instellingsbesluiten>
    <referentie>https://example.org/cvdr/410316/1</referentie>
    <referentie>https://example.org/prb-2016-3821</referentie>
  </instellingsbesluiten>
  <wettelijkeGrondslagen>
    <wettelijkeGrondslag>
      <opschrift>Wet gemeenschappelijke regelingen</opschrift>
      <referentie>http://wetten.overheid.nl/jci1.3:c:BWBR0003740</referentie>
    </wettelijkeGrondslag>
  </wettelijkeGrondslagen>
  <bevoegdheden>
    <bevoegdheid>
      <kopArtikel>Artikel 4 Bevoegdheden</kopArtikel>
      <inhoudArtikel>1. Bestuur is bevoegd tot het vaststellen van de organisatiestructuur.</inhoudArtikel>
    </bevoegdheid>
  </bevoegdheden>
  <doel>Centrale dienstverlening voor recreatieschappen.</doel>
  <datumInwerkingtreding>2016-07-01</datumInwerkingtreding>
  <datumMutatie>2024-03-15</datumMutatie>
</regeling>
"""


def test_parse_gemeenschappelijke_regeling():
    node = etree.fromstring(GR_XML)
    record = roo.parse_gemeenschappelijke_regeling(node)
    assert record is not None
    assert record["type"] == "gemeenschappelijke-regeling"
    assert record["identifiers"]["roo_id"] == "25408161"
    # GRs hebben geen eigen afkorting; samenwerkingsvorm-afkorting (BVO/RSO)
    # blijft in `gr_meta.samenwerkingsvorm.afkorting`, niet in `name.abbr`.
    assert "abbr" not in record["names"][0]
    gr = record["gr_meta"]
    assert gr["citeertitel"] == "BVO RMN"
    assert gr["doel"].startswith("Centrale dienstverlening")
    assert gr["samenwerkingsvorm"] == {
        "value": "Bedrijfsvoeringsorganisatie",
        "afkorting": "BVO",
    }
    assert gr["bevoegdheidsverkrijgingen"] == ["Delegatie"]
    assert gr["taalcode"] == "nl-NL"
    assert gr["registratiehouder"] == "BZK"
    assert len(gr["instellingsbesluiten"]) == 2
    assert gr["bevoegdheden"][0]["kop_artikel"] == "Artikel 4 Bevoegdheden"
    assert "vaststellen" in gr["bevoegdheden"][0]["inhoud_artikel"]
    assert gr["regionaal_samenwerkingsorgaan"]["roo_id"] == "25408166"
    assert record["valid_from"] == "2016-07-01"
    assert record["last_mutation"] == "2024-03-15"


def test_parse_export_includes_regelingen(tmp_path: Path):
    """parse_export verwerkt zowel <organisatie> als <regeling> nodes."""
    full_xml = (
        b"<?xml version='1.0' encoding='UTF-8'?>\n"
        b"<overheidsorganisaties xmlns:p='https://organisaties.overheid.nl/static/schema/oo/export/2.6.9'>\n"
        b"<organisaties><organisatie p:systeemId='1'><naam>Min X</naam>"
        b"<types><type>Ministerie</type></types></organisatie></organisaties>\n"
        b"<gemeenschappelijkeRegelingen>" + GR_XML.encode() + b"</gemeenschappelijkeRegelingen>\n"
        b"</overheidsorganisaties>\n"
    )
    target = tmp_path / "export.xml"
    target.write_bytes(full_xml)
    records = roo.parse_export(target)
    assert len(records) == 2
    types = {r["type"] for r in records}
    assert types == {"ministerie", "gemeenschappelijke-regeling"}


def test_extract_kaderwet_preserves_nested_structure():
    """`<kaderwet>` heeft vrije nested structuur; we gebruiken _xml_to_dict
    om alles bit-faithful te bewaren."""
    xml = """
    <organisatie xmlns:p="https://organisaties.overheid.nl/static/schema/oo/export/2.6.9" p:systeemId="1">
      <naam>X</naam>
      <types><type>Adviescollege</type></types>
      <kaderwet>
        <kaderwetAdviescollege>
          <kaderwetVanToepassing>ja</kaderwetVanToepassing>
          <afwijkendeBepalingKaderwet>
            <artikel>19</artikel>
            <toelichting>De Onderwijsraad kent ten hoogste negentien leden.</toelichting>
          </afwijkendeBepalingKaderwet>
        </kaderwetAdviescollege>
      </kaderwet>
    </organisatie>
    """
    node = etree.fromstring(xml)
    record = roo.parse_organisatie(node)
    assert record is not None
    kw = record["kaderwet"]
    assert kw["kaderwetAdviescollege"]["kaderwetVanToepassing"] == "ja"
    assert kw["kaderwetAdviescollege"]["afwijkendeBepalingKaderwet"]["artikel"] == "19"


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
