"""Tests voor `polder search`."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from polder.cli.commands.search_cmd import _flatten, _matches
from polder.cli.main import app

import re


@pytest.fixture
def fake_polder(tmp_path: Path) -> Path:
    """Bouw een minimale polder/-tree met 1 org, 1 person, 1 post."""
    data = tmp_path / "data"
    (data / "organisaties" / "ministeries").mkdir(parents=True)
    (data / "posten").mkdir()
    (data / "personen").mkdir()

    org = {
        "id": "org:min-test",
        "type": "ministerie",
        "names": [{"value": "Ministerie van Test", "valid_from": "2020-01-01"}],
        "valid_from": "2020-01-01",
        "sources": [{"id": "roo", "url": "https://x", "retrieved": "2026-01-01"}],
    }
    (data / "organisaties" / "ministeries" / "min-test.yaml").write_text(
        yaml.safe_dump(org, sort_keys=False), encoding="utf-8"
    )

    post = {
        "id": "post:minister-min-test",
        "organization_id": "org:min-test",
        "label": "Minister van Test",
        "classification": "bewindspersoon",
        "seat_count": 1,
        "valid_from": "2020-01-01",
    }
    (data / "posten" / "minister-min-test.yaml").write_text(
        yaml.safe_dump(post, sort_keys=False), encoding="utf-8"
    )

    person = {
        "id": "person:klaverblad-m-1970",
        "name": {"family": "Klaverblad", "full": "Mark Klaverblad", "initials": "M."},
        "birth": {"year": 1970},
        "sources": [{"id": "roo", "url": "https://x", "retrieved": "2026-01-01"}],
    }
    (data / "personen" / "klaverblad-m-1970.yaml").write_text(
        yaml.safe_dump(person, sort_keys=False), encoding="utf-8"
    )

    return tmp_path


def test_flatten_strings_only() -> None:
    obj = {"id": "person:x", "name": {"family": "Jansen"}, "year": 1970}
    paths = dict(_flatten(obj))
    assert paths["id"] == "person:x"
    assert paths["name.family"] == "Jansen"
    assert paths["year"] == "1970"


def test_flatten_lists_use_index() -> None:
    obj = {"names": [{"value": "A"}, {"value": "B"}]}
    paths = dict(_flatten(obj))
    assert paths["names[0].value"] == "A"
    assert paths["names[1].value"] == "B"


def test_matches_substring() -> None:
    data = {"id": "person:rutte-m-1967", "name": {"family": "Rutte"}}
    matcher = re.compile(re.escape("rutte"), re.IGNORECASE)
    hits = _matches(data, matcher, field_filter=None)
    assert ("id", "person:rutte-m-1967") in hits
    assert ("name.family", "Rutte") in hits


def test_matches_field_filter() -> None:
    data = {"id": "person:rutte-m-1967", "name": {"family": "Rutte"}}
    matcher = re.compile(re.escape("rutte"), re.IGNORECASE)
    hits = _matches(data, matcher, field_filter="name.family")
    assert hits == [("name.family", "Rutte")]


def test_cli_finds_person_by_substring(fake_polder: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["search", "Klaverblad", "--data", str(fake_polder)])
    assert result.exit_code == 0, result.output
    assert "person:klaverblad-m-1970" in result.output


def test_cli_no_results_exits_one(fake_polder: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["search", "bestaatNiet", "--data", str(fake_polder)])
    assert result.exit_code == 1
    assert "Geen resultaten" in result.output


def test_cli_type_filter(fake_polder: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["search", "test", "--type", "org", "--data", str(fake_polder)],
    )
    assert result.exit_code == 0
    assert "org:min-test" in result.output
    assert "post:minister-min-test" not in result.output
    assert "person:klaverblad" not in result.output


def test_cli_field_filter(fake_polder: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["search", "Klaverblad", "--field", "name.family", "--data", str(fake_polder)],
    )
    assert result.exit_code == 0
    assert "person:klaverblad-m-1970" in result.output


def test_cli_regex(fake_polder: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["search", r"^org:min-", "--regex", "--data", str(fake_polder)],
    )
    assert result.exit_code == 0
    assert "org:min-test" in result.output


def test_cli_invalid_regex_fails(fake_polder: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["search", "(", "--regex", "--data", str(fake_polder)],
    )
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Backend-parity: rg en python moeten dezelfde resultaten geven
# ---------------------------------------------------------------------------


def _result_ids(output: str) -> set[str]:
    """Pluk record-IDs uit JSON-output."""
    import json
    data = json.loads(output)
    return {r["id"] for r in data["results"]}


def test_rg_and_python_find_same_ids_substring(fake_polder: Path) -> None:
    """rg en python backends moeten identieke ID-sets vinden voor substring."""
    import shutil
    if not shutil.which("rg"):
        pytest.skip("rg niet beschikbaar")
    runner = CliRunner()
    r_rg = runner.invoke(
        app,
        ["search", "Klaverblad", "--json", "--backend", "rg", "--data", str(fake_polder)],
    )
    r_py = runner.invoke(
        app,
        ["search", "Klaverblad", "--json", "--backend", "python", "--data", str(fake_polder)],
    )
    assert r_rg.exit_code == 0
    assert r_py.exit_code == 0
    assert _result_ids(r_rg.output) == _result_ids(r_py.output)


def test_rg_and_python_find_same_ids_with_field(fake_polder: Path) -> None:
    import shutil
    if not shutil.which("rg"):
        pytest.skip("rg niet beschikbaar")
    runner = CliRunner()
    r_rg = runner.invoke(
        app,
        ["search", "Klaver", "-f", "name.family", "--json", "--backend", "rg",
         "--data", str(fake_polder)],
    )
    r_py = runner.invoke(
        app,
        ["search", "Klaver", "-f", "name.family", "--json", "--backend", "python",
         "--data", str(fake_polder)],
    )
    assert r_rg.exit_code == 0
    assert r_py.exit_code == 0
    assert _result_ids(r_rg.output) == _result_ids(r_py.output)


def test_rg_and_python_find_same_ids_regex_anchor(fake_polder: Path) -> None:
    """`^org:min-` regex werkt in beide backends (rg: anchor wordt vertaald)."""
    import shutil
    if not shutil.which("rg"):
        pytest.skip("rg niet beschikbaar")
    runner = CliRunner()
    r_rg = runner.invoke(
        app,
        ["search", r"^org:min-", "--regex", "--json", "--backend", "rg",
         "--data", str(fake_polder)],
    )
    r_py = runner.invoke(
        app,
        ["search", r"^org:min-", "--regex", "--json", "--backend", "python",
         "--data", str(fake_polder)],
    )
    assert r_rg.exit_code == 0
    assert r_py.exit_code == 0
    assert _result_ids(r_rg.output) == _result_ids(r_py.output)


def test_cli_invalid_backend_fails(fake_polder: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["search", "test", "--backend", "elasticsearch", "--data", str(fake_polder)],
    )
    assert result.exit_code != 0


def test_cli_json_output_format(fake_polder: Path) -> None:
    """JSON-output is parseable en bevat results-key."""
    import json
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["search", "Klaverblad", "--json", "--data", str(fake_polder)],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["query"] == "Klaverblad"
    assert "results" in payload
    assert payload["backend"] in ("rg", "python")
    assert isinstance(payload["results"], list)
