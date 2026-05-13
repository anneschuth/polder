"""Tests voor de ABD-nieuws fetcher.

Mockt httpx via fixture-XML/HTML in ``tests/fixtures/abd_nieuws/``. Geen netwerk.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from polder.fetchers import abd_nieuws as mod
from polder.fetchers.abd_nieuws import (
    NEWS_SITEMAP_URL,
    SITEMAP_INDEX_URL,
    ArticleIndexEntry,
    discover_index,
    fetch_article,
    parse_index_metadata,
    parse_news_sitemap,
    parse_sitemap_index,
    slug_for_article,
    url_to_article_date,
    write_index_json,
)

FIXTURES = Path(__file__).parent / "fixtures" / "abd_nieuws"
ARTICLE_URL = (
    "https://www.algemenebestuursdienst.nl/actueel/nieuws/2026/05/08/"
    "esther-pijs-directeur-generaal-migratie-bij-jenv"
)


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def test_url_to_article_date_extracts_iso_date_and_slug() -> None:
    parsed = url_to_article_date(ARTICLE_URL)
    assert parsed == ("2026-05-08", "esther-pijs-directeur-generaal-migratie-bij-jenv")


def test_url_to_article_date_returns_none_for_non_news_url() -> None:
    bad = "https://www.algemenebestuursdienst.nl/over-de-abd/het-team"
    assert url_to_article_date(bad) is None


def test_slug_for_article_combineert_slug_en_datum() -> None:
    assert (
        slug_for_article(ARTICLE_URL)
        == "esther-pijs-directeur-generaal-migratie-bij-jenv-2026-05-08"
    )


# ---------------------------------------------------------------------------
# Sitemap parsing
# ---------------------------------------------------------------------------


def test_parse_news_sitemap_geeft_entries_met_datum_en_lastmod() -> None:
    entries = parse_news_sitemap(_load("news-sitemap.xml"))
    assert len(entries) >= 5
    first = entries[0]
    assert first.url.startswith("https://www.algemenebestuursdienst.nl/actueel/nieuws/2026/05/08/")
    assert first.article_date == "2026-05-08"
    assert first.lastmod is not None and first.lastmod.startswith("2026-")
    assert first.slug


def test_parse_news_sitemap_filtert_niet_nieuws_urls() -> None:
    """`sitemap/N.xml` bevat ook vacatures, magazines, blogs; alleen
    `actueel/nieuws/YYYY/MM/DD/...` mag erdoor."""
    entries = parse_news_sitemap(_load("sitemap-45.xml"))
    # Mag wel of niet items hebben, maar elke entry MOET nieuws zijn.
    for e in entries:
        assert "/actueel/nieuws/" in e.url
        assert e.article_date.count("-") == 2


def test_parse_sitemap_index_geeft_lijst_van_sub_sitemaps() -> None:
    locs = parse_sitemap_index(_load("sitemap-index.xml"))
    assert len(locs) >= 10
    assert NEWS_SITEMAP_URL in locs
    assert all(loc.startswith("https://www.algemenebestuursdienst.nl/") for loc in locs)


# ---------------------------------------------------------------------------
# Article HTML parsing
# ---------------------------------------------------------------------------


def test_parse_index_metadata_extracts_title_and_summary() -> None:
    html = _load("esther-pijs-2026-05-08.html")
    meta = parse_index_metadata(html)
    assert meta.get("title") == "Esther Pijs directeur-generaal Migratie bij JenV"
    summary = meta.get("summary") or ""
    assert "Esther Pijs" in summary
    assert "directeur-generaal Migratie" in summary


def test_parse_index_metadata_extracts_dates() -> None:
    html = _load("esther-pijs-2026-05-08.html")
    meta = parse_index_metadata(html)
    assert meta.get("article_date") == "2026-05-08"
    lastmod = meta.get("lastmod") or ""
    assert lastmod.startswith("2026-05-08T")


# ---------------------------------------------------------------------------
# discover_index met mocked httpx via MockTransport
# ---------------------------------------------------------------------------


def _build_mock_client(routes: dict[str, str]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url in routes:
            return httpx.Response(200, text=routes[url])
        return httpx.Response(404, text=f"not in mock: {url}")

    transport = httpx.MockTransport(handler)
    return httpx.Client(transport=transport)


def test_discover_index_op_news_sitemap_filtert_op_since(tmp_path: Path) -> None:
    routes = {
        NEWS_SITEMAP_URL: _load("news-sitemap.xml"),
    }
    with _build_mock_client(routes) as client:
        entries = discover_index(
            since=date(2026, 5, 7),
            cache_dir=tmp_path,
            client=client,
            today=date(2026, 5, 9),
        )
    # In de fixture staan items van 2026-05-07 en 2026-05-08; alles ouder
    # dan 2026-05-07 valt eruit. De fixture heeft alleen items uit die window.
    assert len(entries) >= 5
    for e in entries:
        assert e.article_date >= "2026-05-07"
    # Sortering descending op datum.
    dates = [e.article_date for e in entries]
    assert dates == sorted(dates, reverse=True)


def test_discover_index_respecteert_limit(tmp_path: Path) -> None:
    """Limit beperkt het resultaat; `deep=False` voorkomt sitemap-index walk
    zodat we hier alleen de news-sitemap nodig hebben."""
    routes = {NEWS_SITEMAP_URL: _load("news-sitemap.xml")}
    with _build_mock_client(routes) as client:
        entries = discover_index(
            since=date(2026, 5, 1),
            limit=3,
            cache_dir=tmp_path,
            client=client,
            today=date(2026, 5, 9),
            deep=False,
        )
    assert len(entries) == 3


def test_discover_index_walks_sitemap_index_when_deep(tmp_path: Path) -> None:
    """`deep=True` triggert het lopen door de sitemap-index naast `news/sitemap`."""
    routes = {
        NEWS_SITEMAP_URL: _load("news-sitemap.xml"),
        SITEMAP_INDEX_URL: _load("sitemap-index.xml"),
    }
    # Voeg dummy 200's toe voor alle sub-sitemaps; wat niet matcht wordt 404.
    sub_sitemap_text = _load("sitemap-45.xml")
    for loc in parse_sitemap_index(_load("sitemap-index.xml")):
        if loc != NEWS_SITEMAP_URL:
            routes[loc] = sub_sitemap_text
    with _build_mock_client(routes) as client:
        entries = discover_index(
            since=date(2026, 1, 1),
            cache_dir=tmp_path,
            client=client,
            today=date(2026, 5, 9),
            deep=True,
        )
    # news-sitemap geeft 7 items, sitemap-45 voegt er nog 1 unieke aan toe (of niet,
    # afhankelijk van overlap). Hoofdcheck: `deep=True` faalt niet en levert
    # minstens de news-sitemap-items.
    assert len(entries) >= 5


# ---------------------------------------------------------------------------
# fetch_article via MockTransport
# ---------------------------------------------------------------------------


def test_fetch_article_caches_html_idempotent(tmp_path: Path) -> None:
    html_payload = _load("esther-pijs-2026-05-08.html")
    routes = {ARTICLE_URL: html_payload}
    with _build_mock_client(routes) as client:
        path1 = fetch_article(ARTICLE_URL, cache_dir=tmp_path, client=client)
        # Tweede call moet uit de cache komen, geen netwerk-fout zelfs als we de
        # mock 'breken' door de route te verwijderen.
    expected = tmp_path / "esther-pijs-directeur-generaal-migratie-bij-jenv-2026-05-08.html"
    assert path1 == expected
    assert path1.exists()
    assert path1.read_text(encoding="utf-8") == html_payload

    # Idempotency: roep opnieuw aan zonder client; mag niet falen omdat de
    # cache hit het netwerk omzeilt.
    path2 = fetch_article(ARTICLE_URL, cache_dir=tmp_path, client=None)
    assert path2 == path1


def test_fetch_article_raises_on_invalid_url(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        fetch_article("https://example.com/foo", cache_dir=tmp_path)


# ---------------------------------------------------------------------------
# Index JSON
# ---------------------------------------------------------------------------


def test_write_index_json_round_trip(tmp_path: Path) -> None:
    entry = ArticleIndexEntry(
        url=ARTICLE_URL,
        article_date="2026-05-08",
        slug="esther-pijs-directeur-generaal-migratie-bij-jenv",
        lastmod="2026-05-08T11:43:43.160Z",
        title="Esther Pijs directeur-generaal Migratie bij JenV",
    )
    target = write_index_json([entry], cache_dir=tmp_path, today=date(2026, 5, 9))
    assert target == tmp_path / "index.json"
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["count"] == 1
    assert payload["retrieved"] == "2026-05-09"
    assert payload["entries"][0]["url"] == ARTICLE_URL
    assert payload["entries"][0]["article_date"] == "2026-05-08"


# ---------------------------------------------------------------------------
# CLI dry-run
# ---------------------------------------------------------------------------


def test_main_dry_run_does_not_touch_network(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code = mod.main(
        [
            "--since",
            "2026-04-01",
            "--cache-dir",
            str(tmp_path),
            "--dry-run",
        ]
    )
    assert code == 0
    err = capsys.readouterr().err
    assert "[dry-run]" in err
    assert "2026-04-01" in err
    # Geen index.json geschreven.
    assert not (tmp_path / "index.json").exists()
