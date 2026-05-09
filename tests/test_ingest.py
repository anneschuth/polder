"""Tests voor `polder ingest` (ingest.py + cli/commands/ingest_cmd.py).

Subprocess-aanroepen (claude -p, git, uv run polder build) worden gemockt
zodat tests offline draaien en geen LLM-tokens verbranden.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from polder.cli.main import app
from polder.ingest import (
    COST_PARSE_USD,
    COST_RESOLVE_USD,
    IngestBudget,
    IngestResult,
    commit_changes,
    estimate_cost,
    format_dry_run_summary,
    ingest_source,
    plan_parse,
    plan_resolve,
    run_apply,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = REPO_ROOT / "schemas"


# ---------------------------------------------------------------------------
# Fixture: mini polder-tree met cache + staging
# ---------------------------------------------------------------------------


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


@pytest.fixture
def mini_root(tmp_path: Path) -> Path:
    """Mini polder-tree met BZK-ministerie en lege _staging."""
    root = tmp_path
    (root / "_cache" / "abd-nieuws").mkdir(parents=True)
    (root / "_cache" / "staatscourant" / "2025" / "03").mkdir(parents=True)
    (root / "_cache" / "abd-organogrammen" / "min-bzk" / "assets").mkdir(parents=True)
    (root / "data" / "_staging").mkdir(parents=True)
    (root / "data" / "personen" / "current").mkdir(parents=True)
    (root / "data" / "posten").mkdir(parents=True)
    (root / "scripts").mkdir(parents=True)

    schemas_target = root / "schemas"
    schemas_target.mkdir()
    for s in SCHEMAS_DIR.glob("*.schema.json"):
        shutil.copy(s, schemas_target / s.name)

    _write_yaml(
        root / "data" / "organisaties" / "ministeries" / "bzk.yaml",
        {
            "id": "org:min-bzk",
            "type": "ministerie",
            "classification": "ministerie",
            "parent_id": None,
            "names": [
                {"value": "Binnenlandse Zaken en Koninkrijksrelaties", "valid_from": "2010-10-14"}
            ],
            "valid_from": "2010-10-14",
            "sources": [
                {"id": "roo", "url": "https://example.org/roo", "retrieved": "2026-05-09"}
            ],
        },
    )
    return root


# ---------------------------------------------------------------------------
# plan_parse
# ---------------------------------------------------------------------------


def test_plan_parse_abd_nieuws_pickt_alleen_nieuwe_html(mini_root: Path) -> None:
    cache = mini_root / "_cache"
    staging = mini_root / "data" / "_staging"

    # Twee HTMLs in de cache, één heeft al een staging-output.
    (cache / "abd-nieuws" / "alice-directeur-2024-01-01.html").write_text("<html/>")
    (cache / "abd-nieuws" / "bob-directeur-2024-02-01.html").write_text("<html/>")
    (staging / "abd-nieuws-alice-directeur-2024-01-01.json").write_text("[]")

    plan = plan_parse("abd-nieuws", cache_root=cache, staging_dir=staging)

    assert plan.count == 1
    assert plan.jobs[0].input_path.name == "bob-directeur-2024-02-01.html"
    assert plan.jobs[0].output_path.name == "abd-nieuws-bob-directeur-2024-02-01.json"


def test_plan_parse_organogram_voegt_ministerie_toe(mini_root: Path) -> None:
    cache = mini_root / "_cache"
    staging = mini_root / "data" / "_staging"

    pdf = cache / "abd-organogrammen" / "min-bzk" / "assets" / "doc.pdf"
    pdf.write_bytes(b"%PDF-stub")

    plan = plan_parse("organogram", cache_root=cache, staging_dir=staging)

    assert plan.count == 1
    job = plan.jobs[0]
    assert job.extra_args == ("min-bzk",)
    assert job.output_path.name == "organogram-min-bzk-doc.json"


def test_plan_parse_lege_cache(mini_root: Path) -> None:
    plan = plan_parse(
        "abd-nieuws",
        cache_root=mini_root / "_cache",
        staging_dir=mini_root / "data" / "_staging",
    )
    assert plan.count == 0


def test_plan_parse_limit(mini_root: Path) -> None:
    cache = mini_root / "_cache"
    for i in range(5):
        (cache / "abd-nieuws" / f"x-{i}.html").write_text("<html/>")
    plan = plan_parse(
        "abd-nieuws",
        cache_root=cache,
        staging_dir=mini_root / "data" / "_staging",
        limit=2,
    )
    assert plan.count == 2


# ---------------------------------------------------------------------------
# plan_resolve
# ---------------------------------------------------------------------------


def test_plan_resolve_skipt_files_met_companion(mini_root: Path) -> None:
    staging = mini_root / "data" / "_staging"
    (staging / "abd-nieuws-a.json").write_text("[]")
    (staging / "abd-nieuws-a.resolved.json").write_text("[]")
    (staging / "abd-nieuws-b.json").write_text("[]")

    pending = plan_resolve(staging)
    assert [p.name for p in pending] == ["abd-nieuws-b.json"]


def test_plan_resolve_filtert_op_source(mini_root: Path) -> None:
    staging = mini_root / "data" / "_staging"
    (staging / "abd-nieuws-a.json").write_text("[]")
    (staging / "staatscourant-x.json").write_text("[]")

    pending = plan_resolve(staging, source="staatscourant")
    assert [p.name for p in pending] == ["staatscourant-x.json"]


# ---------------------------------------------------------------------------
# ingest_source dry-run
# ---------------------------------------------------------------------------


def test_ingest_source_dry_run_telt_jobs(mini_root: Path) -> None:
    cache = mini_root / "_cache"
    staging = mini_root / "data" / "_staging"
    (cache / "abd-nieuws" / "x-2024-01-01.html").write_text("<html/>")
    (cache / "abd-nieuws" / "y-2024-02-01.html").write_text("<html/>")

    # Tweede heeft al staging maar nog geen .resolved.json -> resolve-job.
    (staging / "abd-nieuws-z-2024-03-01.json").write_text("[]")

    result = ingest_source(
        "abd-nieuws",
        repo_root=mini_root,
        cache_root=cache,
        staging_dir=staging,
        data_dir=mini_root / "data",
        schemas_dir=mini_root / "schemas",
        dry_run=True,
    )

    assert result.parsed == 2
    assert result.resolved == 1
    # Geen subprocess-calls: parse_failed/validate_ok blijven default.
    assert result.parse_failed == 0
    assert result.validate_ok is None


def test_ingest_source_dry_run_lege_cache_doet_niets(mini_root: Path) -> None:
    result = ingest_source(
        "abd-nieuws",
        repo_root=mini_root,
        cache_root=mini_root / "_cache",
        staging_dir=mini_root / "data" / "_staging",
        data_dir=mini_root / "data",
        schemas_dir=mini_root / "schemas",
        dry_run=True,
    )
    assert result.parsed == 0
    assert result.resolved == 0
    assert result.applied == 0
    assert result.skipped == 0


# ---------------------------------------------------------------------------
# run_apply: threshold filter
# ---------------------------------------------------------------------------


def _proposal(
    confidence: float,
    *,
    person: str = "Alice Adelaar",
    post: str = "post:directeur-test",
) -> dict:
    return {
        "person_name": person,
        "organization_id": "org:min-bzk",
        "organization_chain": [],
        "post_id": post,
        "role": "directeur Test",
        "start_date": "2025-01-01",
        "end_date": None,
        "confidence": confidence,
        "abd_nieuws_url": "https://www.algemenebestuursdienst.nl/x",
        "_source_filename": "abd-nieuws-x.resolved.json",
    }


def test_run_apply_filtert_records_onder_threshold(mini_root: Path) -> None:
    staging = mini_root / "data" / "_staging"
    payload = [
        _proposal(0.99, person="Hoog Confident", post="post:directeur-hoog"),
        _proposal(0.86, person="Net Boven Floor", post="post:directeur-mid"),
    ]
    (staging / "abd-nieuws-x.resolved.json").write_text(json.dumps(payload))

    # threshold 0.90: alleen Hoog Confident door.
    applied, skipped = run_apply(
        staging, data_dir=mini_root / "data", threshold=0.90
    )
    assert applied >= 1  # nieuwe persoon + post + mandaat tellen apart
    assert skipped >= 1


def test_run_apply_lege_staging_geeft_nullen(mini_root: Path) -> None:
    applied, skipped = run_apply(
        mini_root / "data" / "_staging",
        data_dir=mini_root / "data",
        threshold=0.85,
    )
    assert applied == 0
    assert skipped == 0


# ---------------------------------------------------------------------------
# commit_changes
# ---------------------------------------------------------------------------


def test_commit_changes_returnt_none_bij_lege_status(
    mini_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wanneer `git status --porcelain` leeg is, doet commit_changes niets."""

    class FakeProc:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "status" in cmd:
            return FakeProc(0, stdout="")  # niets gewijzigd
        return FakeProc(0)

    monkeypatch.setattr("polder.ingest.subprocess.run", fake_run)
    sha = commit_changes("test", repo_root=mini_root, push=False)
    assert sha is None
    # Alleen status-call; geen add of commit.
    assert any("status" in c for c in calls)
    assert not any("add" in c for c in calls)
    assert not any("commit" in c for c in calls)


def test_commit_changes_committeert_en_pusht(
    mini_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeProc:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "status" in cmd:
            return FakeProc(0, stdout=" M data/x.yaml\n")
        if "rev-parse" in cmd:
            return FakeProc(0, stdout="abcdef1234567890\n")
        return FakeProc(0)

    monkeypatch.setattr("polder.ingest.subprocess.run", fake_run)
    sha = commit_changes(
        "Daily ingest test", repo_root=mini_root, push=True, branch="main"
    )
    assert sha == "abcdef1234567890"
    # Verifieer dat add, commit en push allemaal zijn geroepen.
    flat = [" ".join(c) for c in calls]
    assert any("git -C" in f and " add " in f for f in flat)
    assert any("commit -m" in f for f in flat)
    assert any("push origin main" in f for f in flat)


# ---------------------------------------------------------------------------
# CLI integration: --help + --dry-run
# ---------------------------------------------------------------------------


def test_ingest_cli_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["ingest", "--help"])
    assert result.exit_code == 0
    assert "End-to-end" in result.stdout
    assert "--commit" in result.stdout
    assert "--push" in result.stdout
    assert "--threshold" in result.stdout


def test_ingest_cli_dry_run(
    mini_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dry-run mag GEEN subprocess-calls maken."""
    monkeypatch.chdir(mini_root)
    # Voeg twee abd-nieuws HTMLs toe.
    cache = mini_root / "_cache" / "abd-nieuws"
    (cache / "x-2024-01-01.html").write_text("<html/>")
    (cache / "y-2024-02-01.html").write_text("<html/>")

    forbidden_calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        forbidden_calls.append(cmd)

        class P:
            returncode = 0

        return P()

    monkeypatch.setattr("polder.ingest.subprocess.run", fake_run)

    # Patch repo_root zodat ingest_cmd onze tmp_path gebruikt.
    with patch(
        "polder.cli.commands.ingest_cmd._repo_root",
        return_value=mini_root,
    ):
        runner = CliRunner()
        result = runner.invoke(
            app, ["ingest", "--source", "abd-nieuws", "--dry-run"]
        )

    assert result.exit_code == 0, result.stdout
    assert forbidden_calls == []


def test_ingest_cli_validate_failure_blokkeert_commit(
    mini_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Als validate fail returnt, geen build + geen commit + non-zero exit."""
    monkeypatch.chdir(mini_root)

    fake_result = IngestResult(
        source="abd-nieuws",
        parsed=1,
        resolved=1,
        applied=2,
        skipped=0,
        validate_ok=False,
    )

    def fake_ingest_source(*args, **kwargs):
        return fake_result

    build_called: list[bool] = []

    def fake_build(**kwargs):
        build_called.append(True)
        return True

    monkeypatch.setattr(
        "polder.cli.commands.ingest_cmd.ingest_source", fake_ingest_source
    )
    monkeypatch.setattr(
        "polder.cli.commands.ingest_cmd.run_build", fake_build
    )

    with patch(
        "polder.cli.commands.ingest_cmd._repo_root",
        return_value=mini_root,
    ):
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "ingest",
                "--source",
                "abd-nieuws",
                "--commit",
                "--push",
            ],
        )

    assert result.exit_code != 0
    assert build_called == []
