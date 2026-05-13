"""Tests voor `lookup_person_by_name` plus de `polder skill lookup-person` CLI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from polder.fetchers import wikidata_sparql as ws

# ---------------------------------------------------------------------------
# Fixture-payloads (Wikidata SPARQL JSON)
# ---------------------------------------------------------------------------


RUTTE_RESPONSE: dict[str, Any] = {
    "head": {"vars": ["person", "label", "birthyear", "description"]},
    "results": {
        "bindings": [
            {
                "person": {"type": "uri", "value": "http://www.wikidata.org/entity/Q57792"},
                "label": {"type": "literal", "xml:lang": "nl", "value": "Mark Rutte"},
                "birthyear": {"type": "literal", "value": "1967"},
                "description": {
                    "type": "literal",
                    "xml:lang": "nl",
                    "value": "Nederlands politicus, minister-president 2010-2024",
                },
            },
            {
                # Een tweede Rutte (fictief) zonder geboortejaar.
                "person": {"type": "uri", "value": "http://www.wikidata.org/entity/Q9999999"},
                "label": {"type": "literal", "xml:lang": "nl", "value": "Pieter Rutte"},
            },
        ]
    },
}


EMPTY_RESPONSE: dict[str, Any] = {
    "head": {"vars": ["person", "label", "birthyear", "description"]},
    "results": {"bindings": []},
}


# ---------------------------------------------------------------------------
# Stub-client voor httpx (zelfde shape als test_wikidata_sparql)
# ---------------------------------------------------------------------------


class _StubResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _StubClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    def get(self, url: str, params: dict[str, Any], headers: dict[str, str]) -> _StubResponse:
        self.calls.append((url, params, headers))
        return _StubResponse(self.payload)


# ---------------------------------------------------------------------------
# resolve_endpoint
# ---------------------------------------------------------------------------


def test_resolve_endpoint_aliassen():
    assert ws.resolve_endpoint("qlever") == ws.QLEVER_ENDPOINT
    assert ws.resolve_endpoint("wdqs") == ws.SPARQL_ENDPOINT
    # URL passthrough.
    assert ws.resolve_endpoint("https://example.org/sparql") == "https://example.org/sparql"


# ---------------------------------------------------------------------------
# lookup_person_by_name
# ---------------------------------------------------------------------------


def test_lookup_person_by_name_rutte_geeft_qid_en_jaar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(ws, "MIN_REQUEST_INTERVAL", 0.0)
    monkeypatch.setattr(ws, "QLEVER_REQUEST_INTERVAL", 0.0)

    # Patch query_sparql direct; lookup_person_by_name is een dunne wrapper.
    captured: dict[str, Any] = {}

    def fake_query_sparql(query: str, **kwargs: Any) -> list[dict[str, Any]]:
        captured["query"] = query
        captured["endpoint"] = kwargs.get("endpoint")
        return list(RUTTE_RESPONSE.get("results", {}).get("bindings", []))

    monkeypatch.setattr(ws, "query_sparql", fake_query_sparql)

    rows = ws.lookup_person_by_name(
        "Rutte",
        initials="M.P.",
        given="Mark",
        endpoint="qlever",
        cache_dir=tmp_path / "cache",
    )

    assert len(rows) == 2
    rutte = rows[0]
    assert rutte["qid"] == "Q57792"
    assert rutte["label"] == "Mark Rutte"
    assert rutte["birth_year"] == 1967
    assert rutte["description"].startswith("Nederlands politicus")

    pieter = rows[1]
    assert pieter["qid"] == "Q9999999"
    assert pieter["birth_year"] is None

    # QLever-endpoint is geresolved naar URL.
    assert captured["endpoint"] == ws.QLEVER_ENDPOINT
    # Query bevat family-naam.
    assert "Rutte" in captured["query"]


def test_lookup_person_by_name_kewal_leeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ws, "MIN_REQUEST_INTERVAL", 0.0)
    monkeypatch.setattr(ws, "QLEVER_REQUEST_INTERVAL", 0.0)
    monkeypatch.setattr(
        ws, "query_sparql", lambda *a, **k: list(EMPTY_RESPONSE["results"]["bindings"])
    )
    rows = ws.lookup_person_by_name("Kewal", initials="S.", given="Suzie", cache_dir=tmp_path / "c")
    assert rows == []


def test_lookup_person_by_name_lege_family_raised():
    with pytest.raises(ValueError):
        ws.lookup_person_by_name("")
    with pytest.raises(ValueError):
        ws.lookup_person_by_name("   ")


def test_lookup_person_by_name_dedupliceert_qid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Twee bindings met hetzelfde person-URI → één kandidaat."""
    payload = {
        "head": {"vars": ["person", "label"]},
        "results": {
            "bindings": [
                {
                    "person": {
                        "type": "uri",
                        "value": "http://www.wikidata.org/entity/Q57792",
                    },
                    "label": {"type": "literal", "value": "Mark Rutte"},
                },
                {
                    "person": {
                        "type": "uri",
                        "value": "http://www.wikidata.org/entity/Q57792",
                    },
                    "label": {"type": "literal", "value": "Mark Rutte (alias)"},
                },
            ]
        },
    }
    monkeypatch.setattr(ws, "MIN_REQUEST_INTERVAL", 0.0)
    monkeypatch.setattr(ws, "query_sparql", lambda *a, **k: list(payload["results"]["bindings"]))
    rows = ws.lookup_person_by_name("Rutte", cache_dir=tmp_path / "c")
    assert len(rows) == 1
    assert rows[0]["qid"] == "Q57792"


# ---------------------------------------------------------------------------
# CLI: polder skill lookup-person
# ---------------------------------------------------------------------------


def test_cli_lookup_person_schrijft_staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """De CLI schrijft een JSON-bestand met de kandidaten naar de --out-pad."""
    from typer.testing import CliRunner

    from polder.cli.commands import skill_cmd

    monkeypatch.setattr(ws, "MIN_REQUEST_INTERVAL", 0.0)
    monkeypatch.setattr(
        ws, "query_sparql", lambda *a, **k: list(RUTTE_RESPONSE["results"]["bindings"])
    )

    out = tmp_path / "lookup-mark-rutte.json"
    runner = CliRunner()
    result = runner.invoke(
        skill_cmd.app,
        [
            "lookup-person",
            "Mark Rutte",
            "--out",
            str(out),
            "--cache",
            str(tmp_path / "cache"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["input"]["name"]["family"] == "Rutte"
    assert data["input"]["name"]["given"] == "Mark"
    assert len(data["candidates"]) == 2
    assert data["candidates"][0]["qid"] == "Q57792"
    assert data["candidates"][0]["birth_year"] == 1967


def test_cli_lookup_person_lege_naam_faalt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from typer.testing import CliRunner

    from polder.cli.commands import skill_cmd

    monkeypatch.setattr(ws, "MIN_REQUEST_INTERVAL", 0.0)
    monkeypatch.setattr(ws, "query_sparql", lambda *a, **k: [])

    runner = CliRunner()
    result = runner.invoke(
        skill_cmd.app,
        [
            "lookup-person",
            "   ",
            "--out",
            str(tmp_path / "x.json"),
            "--cache",
            str(tmp_path / "c"),
        ],
    )
    assert result.exit_code != 0
