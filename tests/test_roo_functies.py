"""Tests voor `polder.fetchers.roo_functies`."""

from __future__ import annotations

import json
from pathlib import Path

from polder.fetchers import roo_functies

_FUNCTIES_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<overheidsorganisaties xmlns:p="https://organisaties.overheid.nl/static/schema/oo/export/2.6.9">
<organisaties>
  <organisatie p:systeemId="12583">
    <naam>Centraal Justitieel Incassobureau</naam>
    <afkorting>CJIB</afkorting>
    <types><type>Agentschap</type></types>
    <functies>
      <functie p:systeemId="63319">
        <naam>Algemeen directeur</naam>
        <medewerkers>
          <medewerker p:systeemId="112736">
            <naam>dhr. H.J. Derks</naam>
            <startDatum>2014-03-01</startDatum>
            <partijLidmaatschap>Geen</partijLidmaatschap>
          </medewerker>
        </medewerkers>
      </functie>
      <functie p:systeemId="29230845">
        <naam>Woo-contactpersoon</naam>
        <medewerkers>
          <medewerker p:systeemId="29927054">
            <naam>Algemeen CJIB</naam>
            <contact>
              <emailadressen>
                <emailadres><email>woo@cjib.nl</email></emailadres>
              </emailadressen>
            </contact>
          </medewerker>
        </medewerkers>
      </functie>
    </functies>
  </organisatie>
</organisaties>
</overheidsorganisaties>
"""


def test_extract_functies_basic_structure(tmp_path: Path):
    xml = tmp_path / "export.xml"
    xml.write_bytes(_FUNCTIES_XML)
    proposals = roo_functies.extract_functies(xml)
    assert len(proposals) == 2

    p = next(p for p in proposals if p["roo_functie_naam"] == "Algemeen directeur")
    assert p["roo_functie_id"] == "63319"
    assert p["parent_org_id"] == "org:agentschap-cjib"
    assert p["parent_roo_id"] == "12583"
    assert p["suggested_post_id"] == "post:algemeen-directeur-agentschap-cjib"
    assert p["evidence_snippet"] == "Algemeen directeur"
    assert p["confidence"] == 0.7

    med = p["medewerkers"][0]
    assert med["roo_medewerker_id"] == "112736"
    assert med["naam"] == "dhr. H.J. Derks"
    # Quote-or-die: evidence_snippet is letterlijke <naam>-tekst.
    assert med["evidence_snippet"] == "dhr. H.J. Derks"
    assert med["start_date"] == "2014-03-01"
    assert med["partij_lidmaatschap"] == "Geen"


def test_extract_functies_captures_contact(tmp_path: Path):
    xml = tmp_path / "export.xml"
    xml.write_bytes(_FUNCTIES_XML)
    proposals = roo_functies.extract_functies(xml)
    woo = next(p for p in proposals if p["roo_functie_naam"] == "Woo-contactpersoon")
    med = woo["medewerkers"][0]
    assert med["contact"]["emails"] == [{"email": "woo@cjib.nl"}]


def test_write_staging_creates_json_with_metadata(tmp_path: Path):
    xml = tmp_path / "export.xml"
    xml.write_bytes(_FUNCTIES_XML)
    proposals = roo_functies.extract_functies(xml)

    out_dir = tmp_path / "staging"
    target = roo_functies.write_staging(proposals, out_dir)
    assert target.exists()
    assert target.name.startswith("roo-functies-")

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["source_id"] == "roo"
    assert payload["n_functies"] == 2
    assert payload["n_medewerkers"] == 2
    assert len(payload["proposals"]) == 2


def test_extract_skips_functies_outside_organisatie(tmp_path: Path):
    """Sommige `<functie>`-nodes zitten in GR-bestuursorgaan-trees.
    Die filteren we eruit (parent moet `<functies>` in `<organisatie>` zijn)."""
    xml_data = b"""<?xml version="1.0" encoding="UTF-8"?>
    <overheidsorganisaties xmlns:p="https://organisaties.overheid.nl/static/schema/oo/export/2.6.9">
      <gemeenschappelijkeRegelingen>
        <regeling p:systeemId="9999">
          <titel>Test GR</titel>
          <types><type>Gemeenschappelijke regeling</type></types>
          <bestuursorganen>
            <bestuursorgaan>
              <functie p:systeemId="888"><naam>Lid</naam></functie>
            </bestuursorgaan>
          </bestuursorganen>
        </regeling>
      </gemeenschappelijkeRegelingen>
    </overheidsorganisaties>
    """
    xml = tmp_path / "export.xml"
    xml.write_bytes(xml_data)
    proposals = roo_functies.extract_functies(xml)
    # `<functie>` zat onder <bestuursorgaan>, niet onder <functies>.
    assert proposals == []


def test_extract_includes_rollen_and_dates(tmp_path: Path):
    xml_data = b"""<?xml version="1.0" encoding="UTF-8"?>
    <overheidsorganisaties xmlns:p="https://organisaties.overheid.nl/static/schema/oo/export/2.6.9">
      <organisaties>
        <organisatie p:systeemId="100">
          <naam>X</naam><types><type>Gemeente</type></types>
          <functies>
            <functie p:systeemId="1">
              <naam>Burgemeester</naam>
              <medewerkers>
                <medewerker p:systeemId="2">
                  <naam>Foo</naam>
                  <rollen><rol>Waarnemend</rol></rollen>
                  <startDatum>2026-01-01</startDatum>
                  <eindDatum>2026-08-14</eindDatum>
                </medewerker>
              </medewerkers>
            </functie>
          </functies>
        </organisatie>
      </organisaties>
    </overheidsorganisaties>
    """
    xml = tmp_path / "export.xml"
    xml.write_bytes(xml_data)
    proposals = roo_functies.extract_functies(xml)
    med = proposals[0]["medewerkers"][0]
    assert med["rollen"] == ["Waarnemend"]
    assert med["start_date"] == "2026-01-01"
    assert med["end_date"] == "2026-08-14"
