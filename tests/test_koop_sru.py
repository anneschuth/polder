"""Tests voor `polder.fetchers.koop_sru`."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from email.utils import format_datetime
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


def test_build_query_with_since_filters_on_available() -> None:
    cql = ks.build_query("benoeming", since=date(2026, 1, 1))
    assert "dt.available>=2026-01-01" in cql
    # Historische backfill mag niet op de herindexerings-datum filteren.
    assert "dt.modified" not in cql
    assert "sortBy dt.available/sort.ascending" in cql


def test_build_query_with_until_appends_upper_bound() -> None:
    cql = ks.build_query("benoeming", until=date(2017, 12, 31))
    assert "dt.available<=2017-12-31" in cql


def test_build_query_closed_window_for_single_year() -> None:
    cql = ks.build_query(
        "benoeming",
        since=date(2016, 1, 1),
        until=date(2016, 12, 31),
    )
    assert "dt.available>=2016-01-01" in cql
    assert "dt.available<=2016-12-31" in cql
    assert cql.endswith("sortBy dt.available/sort.ascending")


def test_build_query_no_dates_omits_sort_and_window() -> None:
    cql = ks.build_query("benoeming")
    assert "dt.available" not in cql
    assert "sortBy" not in cql


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
    # Call 1 = SRU-zoekrespons; call 2 en 3 = de besluit-XML downloads per
    # record (via gzd:itemUrl). search() schrijft de SRU-metadata naar een
    # .sru.xml sidecar en de besluit-XML naar het .xml-pad dat het teruggeeft.
    besluit_1 = b'<?xml version="1.0"?><officiele-publicatie>besluit stcrt-2026-12345</officiele-publicatie>'
    besluit_2 = b'<?xml version="1.0"?><officiele-publicatie>besluit stcrt-2026-67890</officiele-publicatie>'
    client = _client_returning(SRU_FEED, besluit_1, besluit_2)
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
    # Het teruggegeven pad bevat de besluit-XML.
    assert p0.read_bytes() == besluit_1
    assert paths[1].read_bytes() == besluit_2
    # De SRU-metadata staat in de .sru.xml sidecar ernaast.
    sidecar = p0.with_suffix(".sru.xml")
    assert sidecar.exists()
    assert b"stcrt-2026-" in sidecar.read_bytes()


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


# ---------------------------------------------------------------------------
# Retry-After parsing
# ---------------------------------------------------------------------------


def test_parse_retry_after_delta_seconds() -> None:
    assert ks._parse_retry_after("30") == 30.0


def test_parse_retry_after_http_date_future() -> None:
    when = datetime.now(UTC) + timedelta(seconds=45)
    wait = ks._parse_retry_after(format_datetime(when))
    # Iets speling voor de seconden die verstrijken tijdens de test.
    assert wait is not None and 40.0 <= wait <= 46.0


def test_parse_retry_after_past_date_clamps_to_zero() -> None:
    when = datetime.now(UTC) - timedelta(seconds=120)
    assert ks._parse_retry_after(format_datetime(when)) == 0.0


def test_parse_retry_after_missing_or_garbage() -> None:
    assert ks._parse_retry_after(None) is None
    assert ks._parse_retry_after("") is None
    assert ks._parse_retry_after("binnenkort") is None


# ---------------------------------------------------------------------------
# Adaptieve rate-limiter (AIMD)
# ---------------------------------------------------------------------------


def test_rate_limiter_backs_off_on_throttle() -> None:
    lim = ks._AdaptiveRateLimiter(delay=0.5)
    lim.on_throttled()
    assert lim.delay == pytest.approx(1.0)
    lim.on_throttled()
    assert lim.delay == pytest.approx(2.0)


def test_rate_limiter_decays_on_success() -> None:
    lim = ks._AdaptiveRateLimiter(delay=1.0)
    lim.on_success()
    assert lim.delay == pytest.approx(0.9)


def test_rate_limiter_respects_bounds() -> None:
    lim = ks._AdaptiveRateLimiter(delay=ks.RATE_MAX_DELAY)
    lim.on_throttled()
    assert lim.delay == ks.RATE_MAX_DELAY  # plafond
    lim = ks._AdaptiveRateLimiter(delay=ks.RATE_MIN_DELAY)
    for _ in range(20):
        lim.on_success()
    assert lim.delay == ks.RATE_MIN_DELAY  # bodem


# ---------------------------------------------------------------------------
# search() onder throttling: 429 → Retry-After → alsnog ophalen, niets verliezen
# ---------------------------------------------------------------------------


def _seq_client(steps: list[httpx.Response | bytes]) -> httpx.Client:
    """MockTransport die een vaste reeks responses serveert.

    `bytes` wordt een 200 met die body; een `httpx.Response` gaat ongewijzigd
    door (zo injecteer je een 429 met Retry-After).
    """
    iterator = iter(steps)

    def handler(request: httpx.Request) -> httpx.Response:
        try:
            step = next(iterator)
        except StopIteration:
            return httpx.Response(200, content=EMPTY_FEED)
        if isinstance(step, httpx.Response):
            return step
        return httpx.Response(200, content=step, headers={"content-type": "application/xml"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_search_retries_after_429_and_keeps_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Een 429 met Retry-After mag het record niet laten vallen.

    De besluit-XML moet alsnog op disk landen, en de fetcher moet exact zo
    lang wachten als de Retry-After-header voorschrijft.
    """
    slept: list[float] = []
    monkeypatch.setattr(ks.time, "sleep", lambda s: slept.append(s))

    besluit = b'<?xml version="1.0"?><officiele-publicatie>ok</officiele-publicatie>'
    # SRU-feed heeft 2 records. Per record: eerst een 429 (Retry-After: 7),
    # dan de echte besluit-XML.
    throttled = httpx.Response(429, headers={"Retry-After": "7"})
    client = _seq_client([SRU_FEED, throttled, besluit, throttled, besluit])

    paths = ks.search("benoeming", max_records=10, cache_dir=tmp_path, client=client)

    assert len(paths) == 2
    for p in paths:
        assert p.exists()
        assert p.read_bytes() == besluit
    # Retry-After=7 moet exact gehonoreerd zijn, niet een blinde 2**attempt.
    assert 7.0 in slept
    # Geen failures.log: niets is definitief verloren.
    assert not (tmp_path / "failures.log").exists()


def test_search_logs_failure_after_exhausting_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Aanhoudende 429 → na MAX_ATTEMPTS naar failures.log, niet stil weg."""
    monkeypatch.setattr(ks.time, "sleep", lambda s: None)

    always_429 = httpx.Response(429, headers={"Retry-After": "1"})
    # SRU-feed (2 records) + voor elk record MAX_ATTEMPTS keer 429.
    steps: list[httpx.Response | bytes] = [SRU_FEED]
    steps += [always_429] * (ks.MAX_ATTEMPTS * 2)
    client = _seq_client(steps)

    paths = ks.search("benoeming", max_records=10, cache_dir=tmp_path, client=client)

    # Paden komen terug (de SRU-sidecar is wel geschreven) maar de besluit-XML
    # ontbreekt, en beide records staan in failures.log.
    assert len(paths) == 2
    for p in paths:
        assert not p.exists()
    failures = tmp_path / "failures.log"
    assert failures.exists()
    lines = failures.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert "stcrt-2026-12345" in lines[0]
    assert "429" in lines[0]
