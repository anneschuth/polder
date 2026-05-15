"""Unit-tests voor `polder.roo_roundtrip`.

Bewijst dat de fetcher YAMLs produceert die élk leaf-veld uit ROO bevatten.
Eind-tot-eind test: parse XML → write YAML → roundtrip → ≥99% coverage.
"""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from polder.fetchers import roo
from polder.roo_roundtrip import (
    CoverageReport,
    _build_yaml_value_set,
    _flatten_to_strings,
    _is_allowed_missing,
    _iter_leaves,
    _normalize_value,
    _xml_org_id,
    compare_org,
    format_report,
    run_roundtrip,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_normalize_value_collapses_whitespace_and_lowercases():
    assert _normalize_value("  Hello   World  ") == "hello world"
    assert _normalize_value("Foo\nBar") == "foo bar"


def test_flatten_to_strings_walks_nested_structures():
    obj = {
        "a": "alpha",
        "b": [{"c": "gamma"}, "delta"],
        "n": 42,
        "skip": None,
    }
    out = list(_flatten_to_strings(obj))
    assert "alpha" in out
    assert "gamma" in out
    assert "delta" in out
    assert "42" in out


def test_build_yaml_value_set_normalizes():
    rec = {"x": "Hello World", "y": ["Foo Bar"]}
    s = _build_yaml_value_set(rec)
    assert "hello world" in s
    assert "foo bar" in s


# ---------------------------------------------------------------------------
# XML leaf-iteration
# ---------------------------------------------------------------------------


def test_iter_leaves_yields_text_and_attrs():
    xml = """
    <organisatie systeemId="42">
      <naam>Foo</naam>
      <empty/>
      <nested><child>bar</child></nested>
    </organisatie>
    """
    elem = etree.fromstring(xml)
    leaves = list(_iter_leaves(elem))
    paths = {p for p, _ in leaves}
    assert "/organisatie@systeemId" in paths
    assert "/organisatie/naam" in paths
    assert "/organisatie/nested/child" in paths
    # `<empty/>` heeft geen text en geen attributes — telt niet als leaf.
    assert "/organisatie/empty" not in paths


def test_iter_leaves_skips_namespace_declarations():
    xml = '<o xmlns:p="https://example.org/" p:foo="bar"><n>x</n></o>'
    elem = etree.fromstring(xml)
    leaves = list(_iter_leaves(elem))
    paths = {p for p, _ in leaves}
    # `xmlns:p` declaration zou niet als leaf moeten verschijnen, maar `p:foo`
    # (een echte attribute met betekenisvolle naam) wel.
    assert "/o@foo" in paths


def test_is_allowed_missing_pattern_matching():
    assert _is_allowed_missing("/personeel")
    assert _is_allowed_missing("/functies/functie/naam")
    assert _is_allowed_missing("/identificatiecodes/resourceIdentifier@naam")
    assert not _is_allowed_missing("/naam")
    assert not _is_allowed_missing("/adressen/adres/postcode")


# ---------------------------------------------------------------------------
# _xml_org_id
# ---------------------------------------------------------------------------


def test_xml_org_id_matches_parse_organisatie_id():
    """`_xml_org_id` moet exact dezelfde slug produceren als
    `parse_organisatie`. Anders kan de round-trip records niet linken."""
    xml = """
    <organisatie xmlns:p="https://organisaties.overheid.nl/static/schema/oo/export/2.6.9" p:systeemId="42">
      <naam>Gemeente Utrecht</naam>
      <types><type>Gemeente</type></types>
    </organisatie>
    """
    node = etree.fromstring(xml)
    record = roo.parse_organisatie(node)
    assert record is not None
    assert _xml_org_id(node) == record["id"]


def test_xml_org_id_returns_none_for_unknown_type():
    xml = """
    <organisatie>
      <naam>Stichting Iets</naam>
      <types><type>onbekend-type</type></types>
    </organisatie>
    """
    node = etree.fromstring(xml)
    assert _xml_org_id(node) is None


# ---------------------------------------------------------------------------
# End-to-end: parse → write → round-trip
# ---------------------------------------------------------------------------


_TEST_EXPORT_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<overheidsorganisaties xmlns:p="https://organisaties.overheid.nl/static/schema/oo/export/2.6.9">
<organisaties>
  <organisatie p:systeemId="21849"
               p:resourceIdentifierTOOI="https://identifier.overheid.nl/tooi/id/gemeente/gm0794">
    <naam>Gemeente Helmond</naam>
    <afkorting>Helmond</afkorting>
    <types><type>Gemeente</type></types>
    <identificatiecodes>
      <resourceIdentifier p:naam="systeemId">21849</resourceIdentifier>
      <resourceIdentifier p:naam="OIN">00000001001600291000</resourceIdentifier>
      <resourceIdentifier p:naam="rsin">001600291</resourceIdentifier>
    </identificatiecodes>
    <adressen>
      <adres>
        <adresType>Bezoekadres</adresType>
        <openbareRuimte>Weg op den Heuvel</openbareRuimte>
        <huisnummer>35</huisnummer>
        <postcode>5701 NV</postcode>
        <woonplaats>HELMOND</woonplaats>
      </adres>
    </adressen>
    <contact>
      <telefoonnummers>
        <telefoonnummer><nummer>14 0492</nummer><label>algemeen</label></telefoonnummer>
      </telefoonnummers>
      <emailadressen>
        <emailadres><email>gemeente@helmond.nl</email><label>algemeen</label></emailadres>
      </emailadressen>
    </contact>
    <classificaties>
      <classificatie p:type="Woo" p:url="https://example.org/woo">
        <wettelijkeGrondslagen>
          <wettelijkeGrondslag>
            <opschrift>Wet open overheid</opschrift>
            <referentie>https://wetten.overheid.nl/BWBR0045754</referentie>
          </wettelijkeGrondslag>
        </wettelijkeGrondslagen>
      </classificatie>
    </classificaties>
    <geografie>
      <oppervlakte p:eenheid="km2">54,56</oppervlakte>
      <aantalInwoners>96860</aantalInwoners>
      <bevatPlaatsen>Helmond, Stiphout</bevatPlaatsen>
    </geografie>
    <raad>
      <totaalZetels>37</totaalZetels>
      <partijen>
        <partij><naam>Helder Helmond</naam><aantalZetels>8</aantalZetels></partij>
      </partijen>
    </raad>
    <datumMutatie>2026-05-02</datumMutatie>
    <datumTerVerificatie>2026-05-02</datumTerVerificatie>
  </organisatie>
</organisaties>
</overheidsorganisaties>
"""


def test_end_to_end_roundtrip_full_coverage(tmp_path: Path):
    """Schrijf XML → parse → write YAML → roundtrip moet 100% coverage geven."""
    xml_path = tmp_path / "export.xml"
    xml_path.write_bytes(_TEST_EXPORT_XML)
    out_dir = tmp_path / "out"

    records = roo.parse_export(xml_path)
    n = roo.write_records(records, out_dir)
    assert n >= 1

    report = run_roundtrip(xml_path, out_dir)
    total_seen = sum(f.seen for f in report.fields.values())
    total_matched = sum(f.matched for f in report.fields.values())
    assert total_seen > 0, "Round-trip moet ten minste één leaf zien"
    coverage = total_matched / total_seen
    assert (
        coverage >= 0.99
    ), f"End-to-end coverage onder 99%: {coverage:.2%}\n{format_report(report, top_n=10)}"
    assert report.missing_records == []


def test_compare_org_records_seen_and_matched():
    """Sanity: compare_org telt seen+matched correct."""
    xml = """
    <organisatie>
      <naam>X</naam>
      <afkorting>X</afkorting>
    </organisatie>
    """
    node = etree.fromstring(xml)
    yaml_record = {"names": [{"value": "X", "abbr": "X"}]}
    report = CoverageReport()
    compare_org(node, yaml_record, report)
    assert report.fields["/naam"].seen == 1
    assert report.fields["/naam"].matched == 1
    assert report.fields["/afkorting"].matched == 1


def test_compare_org_unmatched_recorded():
    xml = "<organisatie><naam>Foo</naam></organisatie>"
    node = etree.fromstring(xml)
    yaml_record = {"names": [{"value": "completely different"}]}
    report = CoverageReport()
    compare_org(node, yaml_record, report)
    assert report.fields["/naam"].seen == 1
    assert report.fields["/naam"].matched == 0
    # Unmatched-example wordt opgeslagen voor diagnostics.
    assert len(report.unmatched_examples) == 1
    _, path, value = report.unmatched_examples[0]
    assert path == "/naam"
    assert value == "Foo"


def test_emit_field_map_renders_markdown_table():
    """`emit_field_map` produceert een markdown-tabel met één rij per veld."""
    from polder.roo_roundtrip import emit_field_map

    report = CoverageReport()
    report.fields["/naam"].seen = 10
    report.fields["/naam"].matched = 10
    report.fields["/raad/totaalZetels"].seen = 5
    report.fields["/raad/totaalZetels"].matched = 4

    md = emit_field_map(report)
    assert "# ROO field-map" in md
    assert "| Coverage | Matched / Seen | XML-pad |" in md
    assert "| 100.00% | 10 / 10 | `/naam` |" in md
    assert "| 80.00% | 4 / 5 | `/raad/totaalZetels` |" in md
    # 100%-velden bovenaan (sortering op coverage descending).
    assert md.index("`/naam`") < md.index("`/raad/totaalZetels`")
