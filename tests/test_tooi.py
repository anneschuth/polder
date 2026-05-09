"""Tests voor `polder.fetchers.tooi`."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import yaml
from jsonschema import Draft202012Validator

from polder.fetchers import tooi

# ---------------------------------------------------------------------------
# Fixture: minimale SKOS/RDF voor een paar ministeries
# ---------------------------------------------------------------------------

MINISTERIES_RDF = b"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF
    xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
    xmlns:skos="http://www.w3.org/2004/02/skos/core#">

  <skos:ConceptScheme rdf:about="https://identifier.overheid.nl/tooi/def/scheme/ministeries">
    <skos:prefLabel xml:lang="nl">Ministeries</skos:prefLabel>
  </skos:ConceptScheme>

  <skos:Concept rdf:about="https://identifier.overheid.nl/tooi/id/ministerie/mnre1034">
    <skos:prefLabel xml:lang="nl">Ministerie van Binnenlandse Zaken en Koninkrijksrelaties</skos:prefLabel>
    <skos:altLabel xml:lang="nl">BZK</skos:altLabel>
    <skos:notation>mnre1034</skos:notation>
    <skos:inScheme rdf:resource="https://identifier.overheid.nl/tooi/def/scheme/ministeries"/>
  </skos:Concept>

  <skos:Concept rdf:about="https://identifier.overheid.nl/tooi/id/ministerie/mnre1090">
    <skos:prefLabel xml:lang="nl">Ministerie van Financien</skos:prefLabel>
    <skos:altLabel xml:lang="nl">FIN</skos:altLabel>
    <skos:notation>mnre1090</skos:notation>
    <skos:inScheme rdf:resource="https://identifier.overheid.nl/tooi/def/scheme/ministeries"/>
  </skos:Concept>

  <rdf:Description rdf:about="https://identifier.overheid.nl/tooi/id/ministerie/mnre0000">
    <rdf:type rdf:resource="http://www.w3.org/2004/02/skos/core#Concept"/>
    <skos:prefLabel xml:lang="nl">Niet-bestaand ministerie (typed via rdf:Description)</skos:prefLabel>
  </rdf:Description>
</rdf:RDF>
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client_returning(content: bytes, *, status: int = 200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status, content=content, headers={"content-type": "application/rdf+xml"}
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# parse_skos_rdf
# ---------------------------------------------------------------------------


def test_parse_skos_rdf_extracts_concepts() -> None:
    concepts = tooi.parse_skos_rdf(MINISTERIES_RDF)
    by_uri = {c["uri"]: c for c in concepts}
    assert "https://identifier.overheid.nl/tooi/id/ministerie/mnre1034" in by_uri
    bzk = by_uri["https://identifier.overheid.nl/tooi/id/ministerie/mnre1034"]
    assert bzk["pref_label"].startswith("Ministerie van Binnenlandse Zaken")
    assert "BZK" in bzk["alt_labels"]
    assert bzk["notation"] == "mnre1034"
    assert bzk["in_scheme"] == "https://identifier.overheid.nl/tooi/def/scheme/ministeries"


def test_parse_skos_rdf_picks_up_typed_descriptions() -> None:
    concepts = tooi.parse_skos_rdf(MINISTERIES_RDF)
    uris = {c["uri"] for c in concepts}
    # rdf:Description met rdf:type skos:Concept moet ook meekomen.
    assert "https://identifier.overheid.nl/tooi/id/ministerie/mnre0000" in uris


TOOI_NATIVE_RDF = b"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF
    xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
    xmlns:tooiont="https://identifier.overheid.nl/tooi/def/ont/">
  <rdf:Description rdf:about="https://identifier.overheid.nl/tooi/id/ministerie/mnre1058">
    <rdf:type rdf:resource="https://identifier.overheid.nl/tooi/def/ont/Ministerie"/>
    <rdfs:label>ministerie van Justitie en Veiligheid</rdfs:label>
    <tooiont:afkorting>JenV</tooiont:afkorting>
    <tooiont:organisatiecode>mnre1058</tooiont:organisatiecode>
  </rdf:Description>
  <rdf:Description rdf:about="https://identifier.overheid.nl/tooi/set/rwc_ministeries_compleet/6">
    <rdf:type rdf:resource="https://identifier.overheid.nl/tooi/def/wl/RegisterwaardelijstCompleet"/>
    <rdfs:label>Register ministeries compleet</rdfs:label>
  </rdf:Description>
</rdf:RDF>
"""


def test_parse_native_tooi_dialect() -> None:
    concepts = tooi.parse_skos_rdf(TOOI_NATIVE_RDF)
    # Alleen het Ministerie-concept moet meekomen, niet de waardelijst-set zelf.
    assert len(concepts) == 1
    c = concepts[0]
    assert c["uri"] == "https://identifier.overheid.nl/tooi/id/ministerie/mnre1058"
    assert c["pref_label"] == "ministerie van Justitie en Veiligheid"
    assert "JenV" in c["alt_labels"]
    assert c["notation"] == "mnre1058"
    assert "Ministerie" in c["types"]


def test_parse_skos_rdf_handles_empty_rdf() -> None:
    payload = b"""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"></rdf:RDF>
"""
    assert tooi.parse_skos_rdf(payload) == []


# ---------------------------------------------------------------------------
# fetch_tooi_concepts
# ---------------------------------------------------------------------------


def test_fetch_tooi_concepts_writes_cache(tmp_path: Path) -> None:
    client = _client_returning(MINISTERIES_RDF)
    result = tooi.fetch_tooi_concepts(
        "ministeries",
        cache_dir=tmp_path,
        client=client,
    )
    assert result["scheme"] == "ministeries"
    assert result["url"].endswith("/rwc_ministeries_compleet_6.rdf")
    assert result["cache_path"].exists()
    assert len(result["concepts"]) >= 2


def test_fetch_tooi_concepts_dry_run_skips_write(tmp_path: Path) -> None:
    client = _client_returning(MINISTERIES_RDF)
    result = tooi.fetch_tooi_concepts(
        "ministeries",
        cache_dir=tmp_path,
        client=client,
        dry_run=True,
    )
    assert not result["cache_path"].exists()
    assert len(result["concepts"]) >= 2


def test_fetch_tooi_concepts_raises_on_http_error(tmp_path: Path) -> None:
    client = _client_returning(b"server error", status=500)
    with pytest.raises(httpx.HTTPError):
        tooi.fetch_tooi_concepts("ministeries", cache_dir=tmp_path, client=client)


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def test_cli_dry_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_fetch(*args: object, **kwargs: object) -> dict[str, object]:
        return {
            "scheme": "ministeries",
            "url": "https://standaarden.overheid.nl/tooi/data/ministeries.rdf",
            "cache_path": tmp_path / "ministeries.rdf",
            "concepts": [{"uri": "x"}, {"uri": "y"}],
        }

    monkeypatch.setattr(tooi, "fetch_tooi_concepts", fake_fetch)
    rc = tooi.main(["--scheme", "ministeries", "--cache", str(tmp_path), "--dry-run"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "ministeries" in err
    assert "2 concepten" in err


# ---------------------------------------------------------------------------
# Historie: parse_history_rdf en apply_history_to_records
# ---------------------------------------------------------------------------


HISTORY_RDF = b"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF
    xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
    xmlns:tooiont="https://identifier.overheid.nl/tooi/def/ont/"
    xmlns:prov="http://www.w3.org/ns/prov#">

  <!-- Live ministerie: opvolger uit fusie -->
  <rdf:Description rdf:about="https://identifier.overheid.nl/tooi/id/ministerie/mnre1045">
    <rdf:type rdf:resource="https://identifier.overheid.nl/tooi/def/ont/Ministerie"/>
    <rdfs:label>ministerie van Economische Zaken en Klimaat</rdfs:label>
    <tooiont:officieleNaamExclSoort>Economische Zaken en Klimaat</tooiont:officieleNaamExclSoort>
    <tooiont:officieleNaamInclSoort>ministerie van Economische Zaken en Klimaat</tooiont:officieleNaamInclSoort>
    <tooiont:afkorting>EZK</tooiont:afkorting>
    <tooiont:organisatiecode>mnre1045</tooiont:organisatiecode>
    <tooiont:begindatum rdf:datatype="http://www.w3.org/2001/XMLSchema#date">2010-10-14</tooiont:begindatum>
  </rdf:Description>

  <!-- Opgeheven (historisch) ministerie: voorganger -->
  <rdf:Description rdf:about="https://identifier.overheid.nl/tooi/id/ministerie/mnre1040">
    <rdf:type rdf:resource="https://identifier.overheid.nl/tooi/def/ont/Ministerie"/>
    <rdfs:label>ministerie van Economische Zaken</rdfs:label>
    <tooiont:officieleNaamExclSoort>Economische Zaken</tooiont:officieleNaamExclSoort>
    <tooiont:afkorting>EZ</tooiont:afkorting>
    <tooiont:organisatiecode>mnre1040</tooiont:organisatiecode>
    <tooiont:einddatum rdf:datatype="http://www.w3.org/2001/XMLSchema#date">2010-10-13</tooiont:einddatum>
  </rdf:Description>

  <!-- HistorischeVersie: hoort bij mnre1045 -->
  <rdf:Description rdf:about="https://identifier.overheid.nl/tooi/id/ministerie/hv_05434137">
    <rdf:type rdf:resource="https://identifier.overheid.nl/tooi/def/ont/HistorischeVersie"/>
    <rdf:type rdf:resource="https://identifier.overheid.nl/tooi/def/ont/Ministerie"/>
    <prov:specializationOf rdf:resource="https://identifier.overheid.nl/tooi/id/ministerie/mnre1045"/>
    <rdfs:label>ministerie van Economische Zaken</rdfs:label>
    <tooiont:officieleNaamExclSoort>Economische Zaken</tooiont:officieleNaamExclSoort>
    <tooiont:afkorting>EZ</tooiont:afkorting>
    <tooiont:einddatumHV rdf:datatype="http://www.w3.org/2001/XMLSchema#date">2017-12-31</tooiont:einddatumHV>
  </rdf:Description>

  <!-- Samenvoeging: mnre1040 + mnre1150 -> mnre1045 -->
  <rdf:Description rdf:about="https://identifier.overheid.nl/tooi/id/ministerie/wzg_06434036">
    <rdf:type rdf:resource="https://identifier.overheid.nl/tooi/def/ont/Samenvoeging"/>
    <prov:generated rdf:resource="https://identifier.overheid.nl/tooi/id/ministerie/mnre1045"/>
    <prov:invalidated rdf:resource="https://identifier.overheid.nl/tooi/id/ministerie/mnre1040"/>
    <prov:invalidated rdf:resource="https://identifier.overheid.nl/tooi/id/ministerie/mnre1150"/>
    <tooiont:tijdstipWijziging rdf:datatype="http://www.w3.org/2001/XMLSchema#dateTime">2010-10-14T00:00:00+02:00</tooiont:tijdstipWijziging>
  </rdf:Description>

  <!-- Tweede voorganger LNV -->
  <rdf:Description rdf:about="https://identifier.overheid.nl/tooi/id/ministerie/mnre1150">
    <rdf:type rdf:resource="https://identifier.overheid.nl/tooi/def/ont/Ministerie"/>
    <rdfs:label>ministerie van Landbouw, Natuur en Voedselkwaliteit</rdfs:label>
    <tooiont:officieleNaamExclSoort>Landbouw, Natuur en Voedselkwaliteit</tooiont:officieleNaamExclSoort>
    <tooiont:afkorting>LNV</tooiont:afkorting>
    <tooiont:organisatiecode>mnre1150</tooiont:organisatiecode>
    <tooiont:einddatum rdf:datatype="http://www.w3.org/2001/XMLSchema#date">2010-10-13</tooiont:einddatum>
  </rdf:Description>

  <!-- Afsplitsing: mnre1045 -> mnre1153 (LVVN) -->
  <rdf:Description rdf:about="https://identifier.overheid.nl/tooi/id/ministerie/mnre1153">
    <rdf:type rdf:resource="https://identifier.overheid.nl/tooi/def/ont/Ministerie"/>
    <rdfs:label>ministerie van Landbouw, Visserij, Voedselzekerheid en Natuur</rdfs:label>
    <tooiont:officieleNaamExclSoort>Landbouw, Visserij, Voedselzekerheid en Natuur</tooiont:officieleNaamExclSoort>
    <tooiont:afkorting>LVVN</tooiont:afkorting>
    <tooiont:organisatiecode>mnre1153</tooiont:organisatiecode>
    <tooiont:begindatum rdf:datatype="http://www.w3.org/2001/XMLSchema#date">2017-10-26</tooiont:begindatum>
  </rdf:Description>

  <rdf:Description rdf:about="https://identifier.overheid.nl/tooi/id/ministerie/wzg_01378363">
    <rdf:type rdf:resource="https://identifier.overheid.nl/tooi/def/ont/Afsplitsing"/>
    <prov:generated rdf:resource="https://identifier.overheid.nl/tooi/id/ministerie/mnre1153"/>
    <prov:used rdf:resource="https://identifier.overheid.nl/tooi/id/ministerie/mnre1045"/>
    <tooiont:tijdstipWijziging rdf:datatype="http://www.w3.org/2001/XMLSchema#dateTime">2017-10-26T00:00:00+02:00</tooiont:tijdstipWijziging>
  </rdf:Description>
</rdf:RDF>
"""


def test_parse_history_rdf_extracts_orgs_and_events() -> None:
    orgs, events = tooi.parse_history_rdf(HISTORY_RDF)
    by_uri = {o.uri: o for o in orgs}
    ezk = by_uri["https://identifier.overheid.nl/tooi/id/ministerie/mnre1045"]
    assert ezk.afkorting == "EZK"
    assert ezk.naam_excl_soort == "Economische Zaken en Klimaat"
    assert ezk.begin_datum == "2010-10-14"
    assert ezk.eind_datum is None
    assert not ezk.is_historische_versie

    ez_oud = by_uri["https://identifier.overheid.nl/tooi/id/ministerie/mnre1040"]
    assert ez_oud.eind_datum == "2010-10-13"
    assert not ez_oud.is_historische_versie

    hv = by_uri["https://identifier.overheid.nl/tooi/id/ministerie/hv_05434137"]
    assert hv.is_historische_versie
    assert hv.specialization_of == ("https://identifier.overheid.nl/tooi/id/ministerie/mnre1045")

    by_type = {ev.event_type for ev in events}
    assert "Samenvoeging" in by_type
    assert "Afsplitsing" in by_type

    samen = next(e for e in events if e.event_type == "Samenvoeging")
    assert samen.generated == ["https://identifier.overheid.nl/tooi/id/ministerie/mnre1045"]
    assert set(samen.invalidated) == {
        "https://identifier.overheid.nl/tooi/id/ministerie/mnre1040",
        "https://identifier.overheid.nl/tooi/id/ministerie/mnre1150",
    }
    assert samen.tijdstip == "2010-10-14"


def _write_yaml(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)


def test_apply_history_updates_existing_and_creates_historic(tmp_path: Path) -> None:
    out = tmp_path / "data" / "organisaties"
    sub = out / "ministeries"
    # Bestaand record voor live EZK (mnre1045) en LVVN (mnre1153).
    _write_yaml(
        sub / "ezk.yaml",
        {
            "id": "org:min-ezk",
            "type": "ministerie",
            "identifiers": {
                "tooi": "https://identifier.overheid.nl/tooi/id/ministerie/mnre1045",
            },
            "names": [
                {"value": "Economische Zaken en Klimaat", "abbr": "EZK", "valid_from": "2010-10-14"}
            ],
            "valid_from": "2010-10-14",
            "valid_until": None,
            "sources": [
                {"id": "roo", "url": "https://example.test/ezk", "retrieved": "2026-05-09"}
            ],
        },
    )
    _write_yaml(
        sub / "lvvn.yaml",
        {
            "id": "org:min-lvvn",
            "type": "ministerie",
            "identifiers": {
                "tooi": "https://identifier.overheid.nl/tooi/id/ministerie/mnre1153",
            },
            "names": [
                {
                    "value": "Landbouw, Visserij, Voedselzekerheid en Natuur",
                    "abbr": "LVVN",
                    "valid_from": "2017-10-26",
                }
            ],
            "valid_from": "2017-10-26",
            "valid_until": None,
            "sources": [
                {"id": "roo", "url": "https://example.test/lvvn", "retrieved": "2026-05-09"}
            ],
        },
    )

    orgs, events = tooi.parse_history_rdf(HISTORY_RDF)
    summary = tooi.apply_history_to_records(
        orgs=orgs,
        events=events,
        out_dir=out,
        scheme="ministeries",
        today="2026-05-09",
    )

    assert summary["updated_existing"] == 2
    assert summary["created_historic"] == 2  # mnre1040 (EZ) + mnre1150 (LNV)

    # EZK: predecessors zijn de twee historische records, geen successor (nog levend).
    ezk = yaml.safe_load((sub / "ezk.yaml").read_text())
    assert "successor_id" not in ezk or ezk["successor_id"] is None
    assert sorted(ezk["predecessor_id"]) == [
        "org:min-economische-zaken",
        "org:min-landbouw-natuur-en-voedselkwaliteit",
    ]
    # TOOI-source toegevoegd.
    assert any(s["id"] == "tooi" for s in ezk["sources"])

    # LVVN: predecessor is EZK (afsplitsing), geen successor.
    lvvn = yaml.safe_load((sub / "lvvn.yaml").read_text())
    assert lvvn["predecessor_id"] == ["org:min-ezk"]

    # Nieuw historisch EZ-record: heeft TOOI-URI, einddatum, successor naar EZK.
    ez = yaml.safe_load((sub / "economische-zaken.yaml").read_text())
    assert ez["id"] == "org:min-economische-zaken"
    assert ez["valid_until"] == "2010-10-13"
    assert ez["successor_id"] == "org:min-ezk"
    assert ez["names"][0]["abbr"] == "EZ"
    assert any(s["id"] == "tooi" for s in ez["sources"])


def test_apply_history_idempotent(tmp_path: Path) -> None:
    out = tmp_path / "data" / "organisaties"
    sub = out / "ministeries"
    _write_yaml(
        sub / "ezk.yaml",
        {
            "id": "org:min-ezk",
            "type": "ministerie",
            "identifiers": {
                "tooi": "https://identifier.overheid.nl/tooi/id/ministerie/mnre1045",
            },
            "names": [
                {"value": "Economische Zaken en Klimaat", "abbr": "EZK", "valid_from": "2010-10-14"}
            ],
            "valid_from": "2010-10-14",
            "valid_until": None,
            "sources": [
                {"id": "roo", "url": "https://example.test/ezk", "retrieved": "2026-05-09"}
            ],
        },
    )
    _write_yaml(
        sub / "lvvn.yaml",
        {
            "id": "org:min-lvvn",
            "type": "ministerie",
            "identifiers": {
                "tooi": "https://identifier.overheid.nl/tooi/id/ministerie/mnre1153",
            },
            "names": [
                {
                    "value": "Landbouw, Visserij, Voedselzekerheid en Natuur",
                    "abbr": "LVVN",
                    "valid_from": "2017-10-26",
                }
            ],
            "valid_from": "2017-10-26",
            "valid_until": None,
            "sources": [
                {"id": "roo", "url": "https://example.test/lvvn", "retrieved": "2026-05-09"}
            ],
        },
    )

    orgs, events = tooi.parse_history_rdf(HISTORY_RDF)
    first = tooi.apply_history_to_records(
        orgs=orgs,
        events=events,
        out_dir=out,
        scheme="ministeries",
        today="2026-05-09",
    )
    assert first["updated_existing"] == 2
    assert first["created_historic"] == 2

    second = tooi.apply_history_to_records(
        orgs=orgs,
        events=events,
        out_dir=out,
        scheme="ministeries",
        today="2026-05-09",
    )
    assert second["updated_existing"] == 0
    assert second["created_historic"] == 0


def test_apply_history_skips_unknown_scheme(tmp_path: Path) -> None:
    summary = tooi.apply_history_to_records(
        orgs=[],
        events=[],
        out_dir=tmp_path,
        scheme="onbekend-scheme",
        today="2026-05-09",
    )
    assert summary["updated_existing"] == 0
    assert summary["created_historic"] == 0
    assert "onbekend-scheme" in summary["reason"]


# ---------------------------------------------------------------------------
# Schema-validatie van successor/predecessor velden
# ---------------------------------------------------------------------------


def test_schema_accepts_successor_and_predecessor() -> None:
    schema_path = Path(__file__).parent.parent / "schemas" / "organisatie.schema.json"
    schema = json.loads(schema_path.read_text())
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)

    record = {
        "id": "org:min-ezk",
        "type": "ministerie",
        "names": [{"value": "Economische Zaken en Klimaat", "valid_from": "2010-10-14"}],
        "valid_from": "2010-10-14",
        "successor_id": "org:min-lvvn",
        "predecessor_id": ["org:min-economische-zaken", "org:min-lnv"],
        "sources": [
            {
                "id": "tooi",
                "url": "https://identifier.overheid.nl/tooi/id/ministerie/mnre1045",
                "retrieved": "2026-05-09",
            }
        ],
    }
    errors = list(validator.iter_errors(record))
    assert errors == []


def test_schema_rejects_invalid_successor_id() -> None:
    schema_path = Path(__file__).parent.parent / "schemas" / "organisatie.schema.json"
    schema = json.loads(schema_path.read_text())
    validator = Draft202012Validator(schema)
    record = {
        "id": "org:min-ezk",
        "type": "ministerie",
        "names": [{"value": "Economische Zaken en Klimaat", "valid_from": "2010-10-14"}],
        "valid_from": "2010-10-14",
        "successor_id": "Q12345",  # geen org:-prefix
        "sources": [
            {
                "id": "tooi",
                "url": "https://identifier.overheid.nl/tooi/id/ministerie/mnre1045",
                "retrieved": "2026-05-09",
            }
        ],
    }
    errors = list(validator.iter_errors(record))
    assert errors, "schema moet niet-org: successor_id afwijzen"
