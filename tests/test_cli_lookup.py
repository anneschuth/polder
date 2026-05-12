"""Smoke-tests voor `polder lookup wikidata`."""

from __future__ import annotations

import json
from unittest.mock import patch

from typer.testing import CliRunner

from polder.cli.main import app

runner = CliRunner()


def test_lookup_wikidata_returns_candidates_as_json() -> None:
    with patch(
        "polder.fetchers.wikidata_sparql.lookup_person_by_name",
        return_value=[
            {"qid": "Q57792", "label": "Mark Rutte", "birth_year": 1967, "description": "Nederlands politicus"},
        ],
    ):
        result = runner.invoke(app, ["lookup", "wikidata", "--name", "Mark Rutte"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["name"] == "Mark Rutte"
    assert payload["parsed"]["family"] == "rutte"
    assert len(payload["candidates"]) == 1
    assert payload["candidates"][0]["qid"] == "Q57792"


def test_lookup_wikidata_passes_role_and_org_hints() -> None:
    with patch(
        "polder.fetchers.wikidata_sparql.lookup_person_by_name",
        return_value=[],
    ):
        result = runner.invoke(
            app,
            [
                "lookup",
                "wikidata",
                "--name",
                "Esther van Deursen",
                "--role",
                "directeur Toezicht mbo",
                "--org",
                "Inspectie OCW",
            ],
        )
    payload = json.loads(result.output)
    assert payload["role_hint"] == "directeur Toezicht mbo"
    assert payload["org_hint"] == "Inspectie OCW"


def test_lookup_wikidata_plausible_age_filters_out_old_candidates() -> None:
    with patch(
        "polder.fetchers.wikidata_sparql.lookup_person_by_name",
        return_value=[
            {"qid": "Q1", "label": "Jansen", "birth_year": 1820, "description": "historicus"},
            {"qid": "Q2", "label": "Jansen", "birth_year": 1970, "description": "ambtenaar"},
            {"qid": "Q3", "label": "Jansen", "birth_year": None, "description": "geen jaar"},
        ],
    ):
        result = runner.invoke(
            app,
            ["lookup", "wikidata", "--name", "P. Jansen", "--plausible-age-only"],
        )
    payload = json.loads(result.output)
    qids = [c["qid"] for c in payload["candidates"]]
    assert qids == ["Q2"]


def test_lookup_wikidata_returns_error_payload_on_network_failure() -> None:
    with patch(
        "polder.fetchers.wikidata_sparql.lookup_person_by_name",
        side_effect=RuntimeError("network down"),
    ):
        result = runner.invoke(app, ["lookup", "wikidata", "--name", "Onbekend"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert "error" in payload
    assert "RuntimeError" in payload["error"]


def test_lookup_wikidata_rejects_empty_family() -> None:
    result = runner.invoke(app, ["lookup", "wikidata", "--name", "MA"])
    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["error"] == "geen familienaam in input"
