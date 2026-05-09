"""Tests voor `polder.fetchers.tooi`."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

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
        return httpx.Response(status, content=content, headers={"content-type": "application/rdf+xml"})

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
