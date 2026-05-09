"""Tests voor `polder.fetchers.kiesraad`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from polder.fetchers import kiesraad as kr

# ---------------------------------------------------------------------------
# Fixtures: CKAN package_search en package_show responses
# ---------------------------------------------------------------------------

PACKAGE_SEARCH_RESPONSE: dict[str, Any] = {
    "success": True,
    "result": {
        "count": 2,
        "results": [
            {
                "name": "verkiezingsuitslag-tweede-kamer-2023",
                "title": "Tweede Kamerverkiezing 22 november 2023",
                "notes": "Officiele uitslag en kandidaatlijsten.",
                "resources": [
                    {
                        "name": "Kandidatenlijsten EML",
                        "format": "XML",
                        "url": "https://example.org/tk2023-kandidatenlijsten.eml.xml",
                    },
                    {
                        "name": "Uitslagen CSV",
                        "format": "CSV",
                        "url": "https://example.org/tk2023-uitslag.csv",
                    },
                ],
            },
            {
                "name": "verkiezingsuitslag-provinciale-staten-2023",
                "title": "Provinciale Statenverkiezing 15 maart 2023",
                "resources": [],
            },
        ],
    },
}


PACKAGE_SHOW_RESPONSE: dict[str, Any] = {
    "success": True,
    "result": {
        "name": "verkiezingsuitslag-tweede-kamer-2023",
        "resources": [
            {
                "name": "Kandidatenlijsten EML",
                "format": "XML",
                "url": "https://example.org/tk2023-kandidatenlijsten.eml.xml",
            },
            {
                "name": "Uitslagen CSV",
                "format": "CSV",
                "url": "https://example.org/tk2023-uitslag.csv",
            },
        ],
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_client(
    *,
    search_payload: dict[str, Any] | None = None,
    show_payload: dict[str, Any] | None = None,
    resource_bytes: dict[str, bytes] | None = None,
) -> httpx.Client:
    resource_bytes = resource_bytes or {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "package_search" in url:
            payload = search_payload if search_payload is not None else {"result": {"results": []}}
            return httpx.Response(200, content=json.dumps(payload).encode())
        if "package_show" in url:
            payload = show_payload if show_payload is not None else {"success": False}
            return httpx.Response(200, content=json.dumps(payload).encode())
        if url in resource_bytes:
            return httpx.Response(200, content=resource_bytes[url])
        # Fallback: 404 voor onbekende URL.
        return httpx.Response(404, content=b"not found")

    return httpx.Client(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# list_datasets
# ---------------------------------------------------------------------------


def test_list_datasets_parses_results() -> None:
    client = _build_client(search_payload=PACKAGE_SEARCH_RESPONSE)
    datasets = kr.list_datasets(client=client)
    assert len(datasets) == 2
    assert datasets[0]["name"] == "verkiezingsuitslag-tweede-kamer-2023"


def test_list_datasets_handles_empty() -> None:
    client = _build_client(search_payload={"result": {"results": []}})
    assert kr.list_datasets(client=client) == []


# ---------------------------------------------------------------------------
# fetch_dataset
# ---------------------------------------------------------------------------


def test_fetch_dataset_downloads_resources(tmp_path: Path) -> None:
    resources = {
        "https://example.org/tk2023-kandidatenlijsten.eml.xml": b"<eml><kandidaat/></eml>",
        "https://example.org/tk2023-uitslag.csv": b"lijst,kandidaat,stemmen\n",
    }
    client = _build_client(show_payload=PACKAGE_SHOW_RESPONSE, resource_bytes=resources)
    target = kr.fetch_dataset(
        "verkiezingsuitslag-tweede-kamer-2023",
        cache_dir=tmp_path,
        client=client,
    )
    assert target.exists()
    files = sorted(p.name for p in target.iterdir())
    # Allebei de resources moeten als bestand neergezet zijn.
    assert any(name.endswith(".xml") for name in files)
    assert any(name.endswith(".csv") for name in files)


def test_fetch_dataset_dry_run_writes_nothing(tmp_path: Path) -> None:
    client = _build_client(show_payload=PACKAGE_SHOW_RESPONSE, resource_bytes={})
    target = kr.fetch_dataset(
        "verkiezingsuitslag-tweede-kamer-2023",
        cache_dir=tmp_path,
        client=client,
        dry_run=True,
    )
    # Directory wordt niet aangemaakt in dry-run.
    assert not target.exists()


def test_fetch_dataset_raises_on_ckan_failure(tmp_path: Path) -> None:
    client = _build_client(show_payload={"success": False, "error": {"message": "not found"}})
    with pytest.raises(ValueError):
        kr.fetch_dataset("ongeldig-id", cache_dir=tmp_path, client=client)


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def test_cli_list_dry_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_list(*args: object, **kwargs: object) -> list[dict[str, Any]]:
        return PACKAGE_SEARCH_RESPONSE["result"]["results"]

    monkeypatch.setattr(kr, "list_datasets", fake_list)
    rc = kr.main(["--list", "--dry-run"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "verkiezingsuitslag-tweede-kamer-2023" in captured.out
    assert "2 datasets gevonden" in captured.err


def test_cli_requires_list_or_dataset(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        kr.main([])
