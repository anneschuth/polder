"""Tests voor de ABD-organogrammen fetcher.

Mockt httpx via fixture-HTML in ``tests/fixtures/abd/``. Geen netwerk.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from polder.fetchers import abd_organogrammen as mod
from polder.fetchers.abd_organogrammen import (
    MINISTERIES,
    SHARED_ORGANOGRAM,
    OrganogramAsset,
    build_manifest,
    discover_organisatie_subpath,
    discover_organogram_assets,
    discover_publicatie_links,
    discover_subpages,
    extract_inline_text,
    ministerie_root_url,
    organisatie_url,
    scrape_ministerie,
    write_manifest,
)

FIXTURES = Path(__file__).parent / "fixtures" / "abd"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def test_organisatie_url_bouwt_canonical_url():
    url = organisatie_url("ministerie-van-binnenlandse-zaken-en-koninkrijksrelaties")
    assert (
        url == "https://www.rijksoverheid.nl/ministeries/"
        "ministerie-van-binnenlandse-zaken-en-koninkrijksrelaties/organisatie"
    )


def test_ministeries_mapping_dekt_minimaal_dertien_ministeries():
    # Per 2026 bestaan er ~15 kabinet-ministeries. Sommige delen hun
    # rijksoverheid.nl-organogram met een ander ministerie en zitten in
    # SHARED_ORGANOGRAM in plaats van MINISTERIES. Samen moeten ze dekken.
    assert len(MINISTERIES) + len(SHARED_ORGANOGRAM) >= 13
    # Sleutels gebruiken de interne `min-<afk>` slug-conventie.
    assert all(slug.startswith("min-") for slug in MINISTERIES)
    assert all(slug.startswith("min-") for slug in SHARED_ORGANOGRAM)
    # Waarden van MINISTERIES zijn rijksoverheid url-slugs met `ministerie-van-` prefix.
    assert all(url_slug.startswith("ministerie-van-") for url_slug in MINISTERIES.values())
    # SHARED_ORGANOGRAM verwijst alleen naar slugs die wel in MINISTERIES staan.
    for parent_slug in SHARED_ORGANOGRAM.values():
        assert parent_slug in MINISTERIES, (
            f"shared organogram-parent {parent_slug} moet in MINISTERIES staan"
        )
    # MINISTERIES en SHARED_ORGANOGRAM zijn disjoint.
    assert not (set(MINISTERIES) & set(SHARED_ORGANOGRAM))


# ---------------------------------------------------------------------------
# Asset/link discovery op organisatie-pagina (bzk)
# ---------------------------------------------------------------------------


def test_discover_subpages_vindt_organogram_root():
    html = _load("bzk-organisatie.html")
    base = organisatie_url("ministerie-van-binnenlandse-zaken-en-koninkrijksrelaties")
    organogram_root, _subs = discover_subpages(html, base_url=base)
    assert organogram_root is not None
    assert organogram_root.endswith("/organisatie/organogram")


def test_discover_subpages_op_organogram_subpage_geeft_directie_links():
    html = _load("bzk-organogram.html")
    base = organisatie_url("ministerie-van-binnenlandse-zaken-en-koninkrijksrelaties")
    _root, subs = discover_subpages(html, base_url=base)
    # De BZK-organogram subpage linkt naar tientallen DG-/cluster-/directie-pagina's.
    assert len(subs) >= 10
    # Alle subpages staan onder /organisatie/organogram/.
    for url in subs:
        assert "/organisatie/organogram/" in url
    # Bekende voorbeelden moeten erin zitten.
    joined = "\n".join(subs)
    assert "ambtelijke-leiding" in joined
    assert "dg-volkshuisvesting-en-bouwen" in joined


def test_discover_publicatie_links_vindt_organogram_publicatie():
    html = _load("bzk-organogram.html")
    base = (
        "https://www.rijksoverheid.nl/ministeries/"
        "ministerie-van-binnenlandse-zaken-en-koninkrijksrelaties/organisatie/organogram"
    )
    pubs = discover_publicatie_links(html, base_url=base)
    assert len(pubs) >= 1
    assert any("organogram-ministerie-van-bzk" in url for url in pubs)


def test_discover_organogram_assets_op_publicatie_pagina_vindt_open_overheid_pdf():
    html = _load("bzk-publicatie.html")
    base = (
        "https://www.rijksoverheid.nl/ministeries/"
        "ministerie-van-binnenlandse-zaken-en-koninkrijksrelaties/"
        "documenten/publicaties/2026/02/23/organogram-ministerie-van-bzk"
    )
    assets = discover_organogram_assets(html, base_url=base)
    assert len(assets) >= 1
    asset = assets[0]
    assert asset.url.startswith("https://open.overheid.nl/documenten/")
    assert asset.url.endswith("/file")
    assert asset.content_type_hint == "pdf"


def test_discover_organogram_assets_negeert_irrelevante_images():
    html = _load("bzk-organisatie.html")
    base = organisatie_url("ministerie-van-binnenlandse-zaken-en-koninkrijksrelaties")
    assets = discover_organogram_assets(html, base_url=base)
    # De organisatie-pagina zelf bevat geen organogram-asset (die zit op de
    # subpagina). Iconen/favicons mogen niet matchen.
    for a in assets:
        assert "iconen" not in a.url.lower()
        assert "favicon" not in a.url.lower()


# ---------------------------------------------------------------------------
# Inline text extraction
# ---------------------------------------------------------------------------


def test_extract_inline_text_geeft_iets_terug_op_organogram_subpage():
    html = _load("bzk-organogram.html")
    text = extract_inline_text(html)
    assert text is not None
    assert "organogram" in text.lower() or "directie" in text.lower()


def test_extract_inline_text_geeft_none_op_lege_html():
    assert extract_inline_text("<html><body></body></html>") is None


# ---------------------------------------------------------------------------
# Organisatie-subpath discovery (IenW outlier)
# ---------------------------------------------------------------------------


def test_discover_organisatie_subpath_vindt_organisatie_ienw():
    html = _load("ienw-root.html")
    base = ministerie_root_url("ministerie-van-infrastructuur-en-waterstaat")
    url = discover_organisatie_subpath(html, base_url=base)
    assert url is not None
    assert url.endswith("/organisatie-ienw")


def test_discover_organisatie_subpath_geeft_none_zonder_match():
    html = "<html><body><a href='/elders'>Elders</a></body></html>"
    base = ministerie_root_url("ministerie-van-fictief")
    assert discover_organisatie_subpath(html, base_url=base) is None


# ---------------------------------------------------------------------------
# Mocked end-to-end via fake httpx.Client
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200, content: bytes | None = None):
        self.text = text
        self.status_code = status_code
        self.content = content if content is not None else text.encode("utf-8")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "boom",
                request=httpx.Request("GET", "https://x"),
                response=None,  # type: ignore[arg-type]
            )


class _FakeClient:
    """Minimale stand-in voor httpx.Client met URL-routing fixtures."""

    def __init__(
        self,
        routes: dict[str, str],
        pdf_bytes: bytes = b"%PDF-1.4 fake\n",
        status_overrides: dict[str, int] | None = None,
    ):
        self.routes = routes
        self.pdf_bytes = pdf_bytes
        self.status_overrides = status_overrides or {}
        self.calls: list[str] = []

    def get(self, url: str, **_kwargs: Any) -> _FakeResponse:
        self.calls.append(url)
        if url in self.status_overrides:
            return _FakeResponse("", status_code=self.status_overrides[url])
        if url in self.routes:
            return _FakeResponse(self.routes[url])
        # Voor download-asset: serveer fake bytes.
        if url.startswith("https://open.overheid.nl/"):
            return _FakeResponse("", content=self.pdf_bytes)
        raise httpx.HTTPError(f"unexpected URL in test: {url}")


def _bzk_routes() -> dict[str, str]:
    base = "https://www.rijksoverheid.nl/ministeries/ministerie-van-binnenlandse-zaken-en-koninkrijksrelaties"
    return {
        f"{base}/organisatie": _load("bzk-organisatie.html"),
        f"{base}/organisatie/organogram": _load("bzk-organogram.html"),
        f"{base}/documenten/publicaties/2026/02/23/organogram-ministerie-van-bzk": _load(
            "bzk-publicatie.html"
        ),
    }


def test_scrape_ministerie_end_to_end_vindt_pdf_en_cacht(tmp_path: Path):
    client = _FakeClient(_bzk_routes())
    result = scrape_ministerie(
        "min-bzk",
        "ministerie-van-binnenlandse-zaken-en-koninkrijksrelaties",
        cache_root=tmp_path,
        client=client,  # type: ignore[arg-type]
        today="2026-05-09",
    )
    assert result.error is None
    assert result.organogram_subpage_url is not None
    assert result.organogram_subpage_url.endswith("/organogram")
    # Tenminste de open.overheid.nl PDF moet zijn gevonden.
    pdf_assets = [a for a in result.assets if a.content_type_hint == "pdf"]
    assert len(pdf_assets) >= 1
    pdf = pdf_assets[0]
    assert pdf.url.startswith("https://open.overheid.nl/documenten/")
    # Lokaal pad gezet door download_asset.
    assert pdf.local_path is not None
    assert Path(pdf.local_path).exists()
    assert Path(pdf.local_path).read_bytes().startswith(b"%PDF-")
    # Inline-text-extractie van de subpage.
    assert result.inline_text is not None
    # Directie-subpages zijn meegenomen.
    assert len(result.directie_subpages) >= 5


def test_scrape_ministerie_geen_dubbele_assets(tmp_path: Path):
    client = _FakeClient(_bzk_routes())
    result = scrape_ministerie(
        "min-bzk",
        "ministerie-van-binnenlandse-zaken-en-koninkrijksrelaties",
        cache_root=tmp_path,
        client=client,  # type: ignore[arg-type]
        today="2026-05-09",
    )
    urls = [a.url for a in result.assets]
    assert len(urls) == len(set(urls)), f"duplicate URLs in assets: {urls}"


def test_scrape_ministerie_valt_terug_op_organisatie_subpath(tmp_path: Path):
    """IenW heeft /organisatie-ienw in plaats van /organisatie."""
    base = "https://www.rijksoverheid.nl/ministeries/ministerie-van-infrastructuur-en-waterstaat"
    routes = {
        base: _load("ienw-root.html"),
        f"{base}/organisatie-ienw": _load("ienw-organisatie.html"),
        # Sub-organogram-URL die de fixture aankondigt: leeg, zodat we de
        # fallback-flow testen zonder PDF-discovery.
        f"{base}/organisatie-ienw/organogram": "<html><body></body></html>",
    }
    status_overrides = {f"{base}/organisatie": 404}
    client = _FakeClient(routes, status_overrides=status_overrides)
    result = scrape_ministerie(
        "min-ienw",
        "ministerie-van-infrastructuur-en-waterstaat",
        cache_root=tmp_path,
        client=client,  # type: ignore[arg-type]
        today="2026-05-09",
    )
    assert result.error is None, f"expected fallback to succeed, got: {result.error}"
    assert result.organisatie_url.endswith("/organisatie-ienw")


def test_scrape_ministerie_handelt_404_op_organisatie_af(tmp_path: Path):
    routes: dict[str, str] = {}  # geen routes = elke fetch gooit
    client = _FakeClient(routes)
    result = scrape_ministerie(
        "min-bzk",
        "ministerie-van-binnenlandse-zaken-en-koninkrijksrelaties",
        cache_root=tmp_path,
        client=client,  # type: ignore[arg-type]
        today="2026-05-09",
    )
    assert result.error is not None
    assert "organisatie" in result.error.lower()
    assert result.assets == []


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def test_build_and_write_manifest_roundtrip(tmp_path: Path):
    asset = OrganogramAsset(
        url="https://open.overheid.nl/documenten/abc/file",
        content_type_hint="pdf",
        link_text="organogram BZK",
        source_page="https://www.rijksoverheid.nl/x",
        local_path=str(tmp_path / "fake.pdf"),
    )
    result = mod.MinisterieResult(
        ministerie_slug="min-bzk",
        url_slug="ministerie-van-binnenlandse-zaken-en-koninkrijksrelaties",
        organisatie_url="https://www.rijksoverheid.nl/x/organisatie",
        organogram_subpage_url="https://www.rijksoverheid.nl/x/organisatie/organogram",
        assets=[asset],
        inline_text="DG Bestuur",
        directie_subpages=["https://www.rijksoverheid.nl/x/organisatie/organogram/dg-bestuur"],
    )
    manifest = build_manifest([result], today="2026-05-09")
    assert manifest["version"] == 1
    assert manifest["retrieved"] == "2026-05-09"
    assert len(manifest["ministeries"]) == 1
    target = write_manifest(manifest, staging_dir=tmp_path / "_staging", today="2026-05-09")
    assert target.exists()
    assert target.name == "abd-manifest-2026-05-09.json"
    parsed = json.loads(target.read_text(encoding="utf-8"))
    assert parsed["ministeries"][0]["assets"][0]["url"].startswith("https://open.overheid.nl/")
    assert parsed["ministeries"][0]["assets"][0]["content_type_hint"] == "pdf"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_dry_run_zonder_netwerk(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    rc = mod.main(
        [
            "--ministerie",
            "min-bzk",
            "--dry-run",
            "--cache-root",
            str(tmp_path / "_cache"),
            "--staging-dir",
            str(tmp_path / "_staging"),
        ]
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "min-bzk" in captured.err
    assert "dry-run" in captured.err
    # Dry-run schrijft niets.
    assert not (tmp_path / "_staging").exists() or not any((tmp_path / "_staging").iterdir())


def test_cli_onbekende_ministerie_slug_faalt(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit):
        mod.main(["--ministerie", "min-niet-bestaand", "--dry-run"])


def test_cli_zonder_ministerie_of_all_faalt():
    with pytest.raises(SystemExit):
        mod.main([])


def test_cli_shared_organogram_slug_is_geldig_en_schrijft_stub(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    """min-aenm/min-kgg/min-vro hebben geen eigen pagina maar moeten wel
    in het manifest als shared_with-record verschijnen."""
    rc = mod.main(
        [
            "--ministerie",
            "min-aenm",
            "--cache-root",
            str(tmp_path / "_cache"),
            "--staging-dir",
            str(tmp_path / "_staging"),
        ]
    )
    assert rc == 0
    manifests = list((tmp_path / "_staging").glob("abd-manifest-*.json"))
    assert len(manifests) == 1
    payload = json.loads(manifests[0].read_text(encoding="utf-8"))
    entries = payload["ministeries"]
    assert len(entries) == 1
    assert entries[0]["ministerie_slug"] == "min-aenm"
    assert entries[0]["shared_with"] == "min-jenv"
    assert entries[0]["assets"] == []


def test_cli_end_to_end_met_fake_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    """Volledige main() met gemockte httpx.Client."""

    class _CtxClient(_FakeClient):
        def __enter__(self) -> _CtxClient:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

    fake = _CtxClient(_bzk_routes())

    def fake_client_ctor(*_args: Any, **_kwargs: Any) -> _CtxClient:
        return fake

    monkeypatch.setattr(mod.httpx, "Client", fake_client_ctor)

    rc = mod.main(
        [
            "--ministerie",
            "min-bzk",
            "--cache-root",
            str(tmp_path / "_cache"),
            "--staging-dir",
            str(tmp_path / "_staging"),
        ]
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "Wrote manifest" in captured.err
    # Manifest-bestand bestaat.
    manifests = list((tmp_path / "_staging").glob("abd-manifest-*.json"))
    assert len(manifests) == 1
    payload = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert payload["ministeries"][0]["ministerie_slug"] == "min-bzk"
    assert any(
        a["url"].startswith("https://open.overheid.nl/")
        for a in payload["ministeries"][0]["assets"]
    )
