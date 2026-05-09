"""Tests voor `polder.fetchers.koop_sru`."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest

from polder.fetchers import koop_sru as ks

# ---------------------------------------------------------------------------
# Fixture: minimale SRU-feed met twee Staatscourant-records
# ---------------------------------------------------------------------------

SRU_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<sru:searchRetrieveResponse
    xmlns:sru="http://docs.oasis-open.org/ns/search-ws/sruResponse"
    xmlns:gzd="http://standaarden.overheid.nl/sru"
    xmlns:dcterms="http://purl.org/dc/terms/">
  <sru:numberOfRecords>2</sru:numberOfRecords>
  <sru:records>
    <sru:record>
      <sru:recordSchema>gzd</sru:recordSchema>
      <sru:recordData>
        <gzd:gzd>
          <gzd:originalData>
            <dcterms:identifier>stcrt-2026-12345</dcterms:identifier>
            <dcterms:title>Besluit benoeming Secretaris-Generaal</dcterms:title>
            <dcterms:date>2026-04-15</dcterms:date>
            <dcterms:modified>2026-04-15</dcterms:modified>
          </gzd:originalData>
          <gzd:enrichedData>
            <gzd:itemUrl manifestation="xml">https://repository.overheid.nl/frbr/officielepublicaties/stcrt/2026/stcrt-2026-12345/1/xml/stcrt-2026-12345.xml</gzd:itemUrl>
            <gzd:itemUrl manifestation="html">https://zoek.officielebekendmakingen.nl/stcrt-2026-12345.html</gzd:itemUrl>
          </gzd:enrichedData>
        </gzd:gzd>
      </sru:recordData>
    </sru:record>
    <sru:record>
      <sru:recordSchema>gzd</sru:recordSchema>
      <sru:recordData>
        <gzd:gzd>
          <gzd:originalData>
            <dcterms:identifier>stcrt-2026-67890</dcterms:identifier>
            <dcterms:title>Benoeming directeur-generaal</dcterms:title>
            <dcterms:date>2026-05-01</dcterms:date>
            <dcterms:modified>2026-05-02</dcterms:modified>
          </gzd:originalData>
          <gzd:enrichedData>
            <gzd:itemUrl manifestation="xml">https://repository.overheid.nl/frbr/officielepublicaties/stcrt/2026/stcrt-2026-67890/1/xml/stcrt-2026-67890.xml</gzd:itemUrl>
          </gzd:enrichedData>
        </gzd:gzd>
      </sru:recordData>
    </sru:record>
  </sru:records>
</sru:searchRetrieveResponse>
"""

EMPTY_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<sru:searchRetrieveResponse
    xmlns:sru="http://docs.oasis-open.org/ns/search-ws/sruResponse">
  <sru:numberOfRecords>0</sru:numberOfRecords>
  <sru:records></sru:records>
</sru:searchRetrieveResponse>
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client_returning(*responses: bytes) -> httpx.Client:
    """Bouw een httpx.Client met een MockTransport die opeenvolgende responses serveert."""
    iterator = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        try:
            payload = next(iterator)
        except StopIteration:
            return httpx.Response(200, content=EMPTY_FEED)
        return httpx.Response(200, content=payload, headers={"content-type": "application/xml"})

    transport = httpx.MockTransport(handler)
    return httpx.Client(transport=transport)


# ---------------------------------------------------------------------------
# build_query
# ---------------------------------------------------------------------------


def test_build_query_includes_product_area_and_term() -> None:
    cql = ks.build_query("benoeming Secretaris-Generaal")
    assert "c.product-area==officielepublicaties" in cql
    assert "benoeming Secretaris-Generaal" in cql
    assert "dt.modified" not in cql


def test_build_query_with_since_appends_modified_filter() -> None:
    cql = ks.build_query("benoeming", since=date(2026, 1, 1))
    assert "dt.modified>=2026-01-01" in cql


# ---------------------------------------------------------------------------
# extract_record_metadata
# ---------------------------------------------------------------------------


def test_extract_record_metadata_picks_xml_manifestation() -> None:
    from lxml import etree

    root = etree.fromstring(SRU_FEED)
    records = root.findall(".//sru:record", namespaces=ks.NAMESPACES)
    meta = ks.extract_record_metadata(records[0])
    assert meta["identifier"] == "stcrt-2026-12345"
    assert meta["modified"] == "2026-04-15"
    # XML-manifestation moet voorrang krijgen op HTML.
    assert meta["url"] is not None and meta["url"].endswith(".xml")


# ---------------------------------------------------------------------------
# search → cache writes
# ---------------------------------------------------------------------------


def test_search_writes_xml_per_record(tmp_path: Path) -> None:
    client = _client_returning(SRU_FEED, EMPTY_FEED)
    paths = ks.search(
        "benoeming",
        max_records=10,
        cache_dir=tmp_path,
        client=client,
    )
    assert len(paths) == 2
    # Pad-shape: <cache>/<jaar>/<maand>/<id>.xml
    p0 = paths[0]
    assert p0.parent.parent.name in {"2026"}
    assert p0.parent.name in {"04", "05"}
    assert p0.suffix == ".xml"
    assert p0.exists()
    content = p0.read_bytes()
    assert b"stcrt-2026-" in content


def test_search_dry_run_writes_nothing(tmp_path: Path) -> None:
    client = _client_returning(SRU_FEED, EMPTY_FEED)
    paths = ks.search(
        "benoeming",
        max_records=10,
        cache_dir=tmp_path,
        client=client,
        dry_run=True,
    )
    assert len(paths) == 2
    for p in paths:
        assert not p.exists()


def test_search_respects_max_records(tmp_path: Path) -> None:
    client = _client_returning(SRU_FEED)
    paths = ks.search(
        "benoeming",
        max_records=1,
        cache_dir=tmp_path,
        client=client,
    )
    assert len(paths) == 1


def test_search_handles_empty_feed(tmp_path: Path) -> None:
    client = _client_returning(EMPTY_FEED)
    paths = ks.search("nietbestaand", cache_dir=tmp_path, client=client, max_records=50)
    assert paths == []


# ---------------------------------------------------------------------------
# fetch_kb_text
# ---------------------------------------------------------------------------


def test_fetch_kb_text_caches_under_kb_dir(tmp_path: Path) -> None:
    body = b"<kb><titel>Voorbeeld</titel></kb>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    target = ks.fetch_kb_text(
        "https://repository.overheid.nl/frbr/officielepublicaties/stcrt/2026/stcrt-2026-12345/1/xml/stcrt-2026-12345.xml",
        cache_dir=tmp_path,
        client=client,
    )
    assert target.exists()
    assert target.parent.name == "kb"
    assert target.read_bytes() == body


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def test_cli_dry_run_via_main(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_search(*args: object, **kwargs: object) -> list[Path]:
        return [tmp_path / "2026" / "04" / "stcrt-2026-12345.xml"]

    monkeypatch.setattr(ks, "search", fake_search)
    rc = ks.main(
        [
            "--since",
            "2026-04-01",
            "--query",
            "benoeming",
            "--limit",
            "5",
            "--cache",
            str(tmp_path),
            "--dry-run",
        ]
    )
    assert rc == 0
    err = capsys.readouterr().err
    assert "1 XML-records" in err
