"""Tests voor de polder CLI."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from polder.cli.main import app

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = REPO_ROOT / "schemas"


@pytest.fixture
def mini_polder(tmp_path: Path) -> Path:
    """Een mini polder-tree, identiek qua vorm aan tests/test_lib.py.

    Bevat ook schemas/ zodat `polder validate` werkt.
    """
    root = tmp_path
    (root / "data" / "organisaties" / "ministeries").mkdir(parents=True)
    (root / "data" / "personen" / "current").mkdir(parents=True)
    (root / "data" / "posten").mkdir(parents=True)
    (root / "data" / "mandaten").mkdir(parents=True)

    schemas_target = root / "schemas"
    schemas_target.mkdir()
    for s in SCHEMAS_DIR.glob("*.schema.json"):
        shutil.copy(s, schemas_target / s.name)

    (root / "data" / "organisaties" / "ministeries" / "bzk.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "org:min-bzk",
                "type": "ministerie",
                "classification": "ministerie",
                "parent_id": None,
                "names": [
                    {"value": "BZK", "abbr": "BZK", "valid_from": "2010-10-14"}
                ],
                "valid_from": "2010-10-14",
                "sources": [
                    {
                        "id": "roo",
                        "url": "https://example.org/roo",
                        "retrieved": "2026-05-09",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    (root / "data" / "posten" / "sg-min-bzk.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "post:sg-min-bzk",
                "organization_id": "org:min-bzk",
                "label": "SG BZK",
                "classification": "abd-tmg",
                "valid_from": "2010-10-14",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    (root / "data" / "personen" / "current" / "jansen.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "person:jansen-jp-1965",
                "name": {"full": "J.P. Jansen", "family": "Jansen"},
                "birth": {"year": 1965},
                "mandaten": [
                    {
                        "id": "m1",
                        "organization_id": "org:min-bzk",
                        "post_id": "post:sg-min-bzk",
                        "role": "Secretaris-generaal",
                        "start_date": "2020-01-01",
                        "end_date": None,
                        "sources": [
                            {
                                "id": "stcrt",
                                "url": "https://example.org/stcrt/1",
                                "retrieved": "2026-05-09",
                            }
                        ],
                    }
                ],
                "sources": [
                    {
                        "id": "abd",
                        "url": "https://example.org/abd",
                        "retrieved": "2026-05-09",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return root


def _run(args: list[str]) -> tuple[int, str]:
    runner = CliRunner()
    result = runner.invoke(app, args)
    return result.exit_code, result.output


def test_help() -> None:
    code, out = _run(["--help"])
    assert code == 0
    assert "Polder CLI" in out


def test_list_organisaties_table(mini_polder: Path) -> None:
    code, out = _run(["list", "organisaties", "--data", str(mini_polder)])
    assert code == 0, out
    assert "org:min-bzk" in out


def test_list_organisaties_filter_type(mini_polder: Path) -> None:
    code, out = _run(
        ["list", "organisaties", "--type", "ministerie", "--data", str(mini_polder)]
    )
    assert code == 0
    assert "org:min-bzk" in out


def test_list_organisaties_json(mini_polder: Path) -> None:
    code, out = _run(
        ["list", "organisaties", "--format", "json", "--data", str(mini_polder)]
    )
    assert code == 0
    parsed = json.loads(out)
    assert any(o["id"] == "org:min-bzk" for o in parsed)


def test_list_personen_current(mini_polder: Path) -> None:
    code, out = _run(["list", "personen", "--current", "--data", str(mini_polder)])
    assert code == 0
    assert "person:jansen-jp-1965" in out


def test_list_posten(mini_polder: Path) -> None:
    code, out = _run(["list", "posten", "--data", str(mini_polder)])
    assert code == 0
    assert "post:sg-min-bzk" in out


def test_list_mandaten(mini_polder: Path) -> None:
    code, out = _run(
        ["list", "mandaten", "--format", "json", "--data", str(mini_polder)]
    )
    assert code == 0
    rows = json.loads(out)
    assert any(r["post_id"] == "post:sg-min-bzk" for r in rows)


def test_show_org(mini_polder: Path) -> None:
    code, out = _run(["show", "org:min-bzk", "--data", str(mini_polder)])
    assert code == 0
    assert "org:min-bzk" in out


def test_show_not_found(mini_polder: Path) -> None:
    code, _out = _run(["show", "org:nope", "--data", str(mini_polder)])
    assert code == 1


def test_show_person_history(mini_polder: Path) -> None:
    code, out = _run(
        ["show", "person:jansen-jp-1965", "--history", "--data", str(mini_polder)]
    )
    assert code == 0
    assert "Secretaris-generaal" in out or "post:sg-min-bzk" in out


def test_validate_runs(mini_polder: Path) -> None:
    code, _out = _run(
        [
            "validate",
            "--data",
            str(mini_polder / "data"),
            "--schemas",
            str(mini_polder / "schemas"),
        ]
    )
    # Geen errors, mogelijk warnings; 0 of 2 acceptabel zonder strict.
    assert code in (0, 2)


def test_export_json(mini_polder: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    code, _out = _run(["export", "json", str(out_dir), "--data", str(mini_polder)])
    assert code == 0
    assert (out_dir / "organisaties.json").exists()
    data = json.loads((out_dir / "organisaties.json").read_text())
    assert any(o["id"] == "org:min-bzk" for o in data)


# ---------------------------------------------------------------------------
# Subcommand-tree
# ---------------------------------------------------------------------------


FETCH_SUBCOMMANDS = [
    "roo",
    "tk",
    "ek",
    "logius",
    "wikidata",
    "ar-rwt",
    "koop",
    "ori",
    "tooi",
    "kiesraad",
    "abd",
    "all",
]

SKILL_SUBCOMMANDS = [
    "review-diff",
    "parse-staatscourant",
    "parse-organogram",
    "entity-resolution",
]


def test_fetch_help_lists_all_subcommands() -> None:
    code, out = _run(["fetch", "--help"])
    assert code == 0, out
    for sub in FETCH_SUBCOMMANDS:
        assert sub in out, f"missing fetch-subcommand in --help: {sub}"


def test_skill_help_lists_all_subcommands() -> None:
    code, out = _run(["skill", "--help"])
    assert code == 0, out
    for sub in SKILL_SUBCOMMANDS:
        assert sub in out, f"missing skill-subcommand in --help: {sub}"


def test_top_level_lists_all_groups() -> None:
    code, out = _run(["--help"])
    assert code == 0
    for cmd in [
        "fetch",
        "skill",
        "list",
        "show",
        "export",
        "validate",
        "diff",
        "build",
        "serve",
        "daily-update",
    ]:
        assert cmd in out, f"missing top-level command in --help: {cmd}"


def test_fetch_roo_help_runs() -> None:
    code, out = _run(["fetch", "roo", "--help"])
    assert code == 0
    assert "--cache" in out
    assert "--dry-run" in out
    assert "--limit" in out


def test_fetch_all_help_runs() -> None:
    code, out = _run(["fetch", "all", "--help"])
    assert code == 0
    assert "--dry-run" in out
    # `all` heeft geen per-fetcher --out, alleen --cache.
    assert "--cache" in out


@pytest.mark.parametrize("sub", FETCH_SUBCOMMANDS[:-1])  # alle behalve `all`
def test_fetch_subcommand_help(sub: str) -> None:
    code, out = _run(["fetch", sub, "--help"])
    assert code == 0, out


def test_fetch_roo_dry_run_delegates(tmp_path: Path) -> None:
    """`polder fetch roo --dry-run --limit 1` mag geen netwerk-call doen.

    We patchen de underlying main() en checken dat de juiste argv aankomt.
    """
    captured: dict[str, list[str]] = {}

    def fake_main(argv: list[str] | None = None) -> int:
        captured["argv"] = list(argv or [])
        return 0

    with patch("polder.fetchers.roo.main", side_effect=fake_main):
        code, _out = _run(
            [
                "fetch",
                "roo",
                "--dry-run",
                "--limit",
                "1",
                "--cache",
                str(tmp_path / "_cache"),
                "--out",
                str(tmp_path / "out"),
            ]
        )

    assert code == 0
    argv = captured["argv"]
    assert "--dry-run" in argv
    assert "--limit" in argv and "1" in argv
    assert "--cache" in argv
    assert "--out" in argv


def test_fetch_tk_dry_run_delegates(tmp_path: Path) -> None:
    captured: dict[str, list[str]] = {}

    def fake_main(argv: list[str] | None = None) -> int:
        captured["argv"] = list(argv or [])
        return 0

    with patch("polder.fetchers.tk_odata.main", side_effect=fake_main):
        code, _out = _run(
            ["fetch", "tk", "--dry-run", "--cache", str(tmp_path / "_cache")]
        )

    assert code == 0
    assert "--dry-run" in captured["argv"]


def test_fetch_all_runs_all_deterministic_fetchers(tmp_path: Path) -> None:
    """`polder fetch all` moet alle 9 deterministische fetchers aanroepen."""
    from polder.cli.commands import fetch_cmd

    called: list[str] = []

    def make_stub(name: str):
        def stub(argv: list[str] | None = None) -> int:
            called.append(name)
            return 0

        return stub

    # Vervang de fetcher-tabel met stubs.
    stub_table = [(name, make_stub(name)) for name, _ in fetch_cmd.DETERMINISTIC_FETCHERS]
    with patch.object(fetch_cmd, "DETERMINISTIC_FETCHERS", new=stub_table):
        code, _out = _run(
            ["fetch", "all", "--dry-run", "--cache", str(tmp_path / "_cache")]
        )

    assert code == 0
    assert called == [name for name, _ in stub_table]


def test_diff_help_runs() -> None:
    code, out = _run(["diff", "--help"])
    assert code == 0
    assert "diff" in out.lower()


def test_build_help_runs() -> None:
    code, out = _run(["build", "--help"])
    assert code == 0
    assert "sqlite" in out.lower() or "csv" in out.lower()


def test_serve_help_runs() -> None:
    code, out = _run(["serve", "--help"])
    assert code == 0
    assert "datasette" in out.lower() or "polder.db" in out


def test_daily_update_help_runs() -> None:
    code, _out = _run(["daily-update", "--help"])
    assert code == 0


def test_skill_review_diff_help_runs() -> None:
    code, out = _run(["skill", "review-diff", "--help"])
    assert code == 0
    assert "diff" in out.lower()


def test_verbose_flag_top_level() -> None:
    """`polder -v --help` mag niet crashen en moet de top-level help tonen."""
    code, out = _run(["-v", "--help"])
    assert code == 0
    assert "fetch" in out
