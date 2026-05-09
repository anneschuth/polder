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
    (root / "data" / "personen").mkdir(parents=True)
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


# ---------------------------------------------------------------------------
# IngestBudget unit tests
# ---------------------------------------------------------------------------


def test_budget_unlimited_default() -> None:
    b = IngestBudget()
    assert b.max_claude_calls is None
    assert b.check() is True
    b.consume(100)
    assert b.check() is True  # blijft unlimited
    assert b.used_calls == 100
    assert b.cost_estimate_usd == pytest.approx(100 * COST_PARSE_USD)
    assert b.remaining() is None


def test_budget_cap_blocks_after_n_calls() -> None:
    b = IngestBudget(max_claude_calls=10)
    for _ in range(10):
        assert b.check() is True
        b.consume(1)
    assert b.check() is False
    assert b.used_calls == 10
    assert b.remaining() == 0
    assert b.cost_estimate_usd == pytest.approx(10 * COST_PARSE_USD)


def test_budget_consume_uses_model_specific_cost() -> None:
    b = IngestBudget(max_claude_calls=100)
    b.consume(2, model="haiku-4-5")
    b.consume(1, model="opus-4-7")
    assert b.cost_estimate_usd == pytest.approx(2 * 0.005 + 1 * 0.10)
    assert b.used_calls == 3


def test_estimate_cost_parse_plus_resolve() -> None:
    cost = estimate_cost(parse_jobs=100, resolve_jobs=20)
    expected = 100 * COST_PARSE_USD + 20 * COST_RESOLVE_USD * 1.5
    assert cost == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Budget-cap in ingest_source
# ---------------------------------------------------------------------------


@pytest.fixture
def cache_with_5_html(mini_root: Path) -> Path:
    """5 fake HTML-files in abd-nieuws cache zonder staging-output."""
    cache = mini_root / "_cache" / "abd-nieuws"
    for i in range(5):
        (cache / f"news-{i:02d}.html").write_text("<html/>", encoding="utf-8")
    return mini_root


def test_ingest_source_dry_run_respects_budget(cache_with_5_html: Path) -> None:
    budget = IngestBudget(max_claude_calls=2)
    result = ingest_source(
        "abd-nieuws",
        repo_root=cache_with_5_html,
        cache_root=cache_with_5_html / "_cache",
        staging_dir=cache_with_5_html / "data" / "_staging",
        data_dir=cache_with_5_html / "data",
        schemas_dir=cache_with_5_html / "schemas",
        dry_run=True,
        budget=budget,
    )
    assert result.parsed == 2  # cap kort
    assert result.budget_hit is True
    assert budget.used_calls == 2
    assert result.parse_cost_estimate_usd == pytest.approx(2 * COST_PARSE_USD)


def test_ingest_source_dry_run_zero_budget_plans_nothing(
    cache_with_5_html: Path,
) -> None:
    """`--max-claude-calls 0` betekent niets plannen."""
    budget = IngestBudget(max_claude_calls=0)
    result = ingest_source(
        "abd-nieuws",
        repo_root=cache_with_5_html,
        cache_root=cache_with_5_html / "_cache",
        staging_dir=cache_with_5_html / "data" / "_staging",
        data_dir=cache_with_5_html / "data",
        schemas_dir=cache_with_5_html / "schemas",
        dry_run=True,
        budget=budget,
    )
    assert result.parsed == 0
    assert result.budget_hit is True
    assert budget.used_calls == 0


def test_ingest_source_real_run_stops_at_budget(cache_with_5_html: Path) -> None:
    """Met cap=2 mag de runner maar 2x worden aangeroepen."""
    calls: list[list[str]] = []

    def fake_runner(cmd: list[str]) -> int:
        calls.append(cmd)
        # Schrijf de output-staging-file aan zodat result.parsed += 1.
        out_path = next(Path(c) for c in cmd if c.endswith(".json"))
        out_path.write_text("[]", encoding="utf-8")
        return 0

    budget = IngestBudget(max_claude_calls=2)
    result = ingest_source(
        "abd-nieuws",
        repo_root=cache_with_5_html,
        cache_root=cache_with_5_html / "_cache",
        staging_dir=cache_with_5_html / "data" / "_staging",
        data_dir=cache_with_5_html / "data",
        schemas_dir=cache_with_5_html / "schemas",
        dry_run=False,
        budget=budget,
        runner=fake_runner,
    )
    assert len(calls) == 2
    assert result.parsed == 2
    assert result.budget_hit is True
    assert budget.used_calls == 2


# ---------------------------------------------------------------------------
# Dry-run rapportage
# ---------------------------------------------------------------------------


def _make_result(
    source: str,
    *,
    parsed: int,
    resolved: int,
    applied: int,
    needs_review: int,
) -> IngestResult:
    r = IngestResult(source=source)  # type: ignore[arg-type]
    r.parsed = parsed
    r.resolved = resolved
    r.applied = applied
    r.needs_review = needs_review
    r.parse_cost_estimate_usd = parsed * COST_PARSE_USD
    r.resolve_cost_estimate_usd = resolved * COST_RESOLVE_USD * 1.5
    return r


def test_dry_run_summary_has_per_source_breakdown_and_cost() -> None:
    results = [
        _make_result(
            "abd-nieuws", parsed=2906, resolved=12, applied=28, needs_review=12
        ),
        _make_result(
            "staatscourant", parsed=568, resolved=0, applied=6, needs_review=3
        ),
    ]
    output = format_dry_run_summary(results, threshold=0.85)
    assert "[abd-nieuws]" in output
    assert "[staatscourant]" in output
    assert "Phase 1 parse" in output
    assert "Phase 2 resolve" in output
    assert "Phase 3 apply" in output
    assert "Sonnet 4.6" in output
    assert "Totale geschatte kosten" in output
    assert "Wall-clock" in output
    # Sanity check: parse-cost = 2906 * 0.025 = 72.65
    assert "$72.65" in output


def test_dry_run_summary_with_budget_includes_cap_line() -> None:
    results = [
        _make_result("abd-nieuws", parsed=10, resolved=0, applied=0, needs_review=0)
    ]
    budget = IngestBudget(max_claude_calls=10)
    budget.consume(10)
    output = format_dry_run_summary(results, threshold=0.85, budget=budget)
    assert "Budget cap" in output
    assert "10/10" in output


# ---------------------------------------------------------------------------
# Per-source commits via CLI
# ---------------------------------------------------------------------------


def test_cli_per_source_commits_zijn_apart(
    mini_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Twee bronnen met records -> twee commits, niet één."""
    monkeypatch.chdir(mini_root)

    # Drie resultaten: abd + staatscourant met records, organogram zonder.
    fake_results = {
        "abd-nieuws": IngestResult(
            source="abd-nieuws",
            parsed=1,
            resolved=1,
            applied=28,
            needs_review=12,
            validate_ok=True,
        ),
        "staatscourant": IngestResult(
            source="staatscourant",
            parsed=1,
            resolved=1,
            applied=6,
            needs_review=3,
            validate_ok=True,
        ),
        "organogram": IngestResult(
            source="organogram",
            parsed=0,
            resolved=0,
            applied=0,
            needs_review=0,
            validate_ok=True,
        ),
    }

    def fake_ingest_source(source, **kwargs):
        return fake_results[source]

    commit_calls: list[tuple[str, tuple[str, ...]]] = []

    def fake_commit(message, *, repo_root, paths=("data",), push=False, branch="main"):
        commit_calls.append((message, tuple(paths)))
        # Eerste twee zijn data-commits, derde de build-commit.
        return f"sha-{len(commit_calls):03d}-abcdef0"

    def fake_build(**kwargs):
        return True

    monkeypatch.setattr(
        "polder.cli.commands.ingest_cmd.ingest_source", fake_ingest_source
    )
    monkeypatch.setattr(
        "polder.cli.commands.ingest_cmd.commit_changes", fake_commit
    )
    monkeypatch.setattr(
        "polder.cli.commands.ingest_cmd.run_build", fake_build
    )

    with patch(
        "polder.cli.commands.ingest_cmd._repo_root", return_value=mini_root
    ):
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "ingest",
                "--source",
                "all",
                "--commit",
            ],
        )

    assert result.exit_code == 0, result.output
    # 1 commit per bron + 1 build-commit = 3.
    data_commits = [c for c in commit_calls if c[1] == ("data",)]
    assert len(data_commits) == 2
    messages = [m for m, _ in data_commits]
    assert any("abd-nieuws" in m and "+28" in m for m in messages)
    assert any("staatscourant" in m and "+6" in m for m in messages)
    # Plus build-commit op dist/.
    build_commits = [c for c in commit_calls if "dist" in c[1]]
    assert len(build_commits) == 1


# ---------------------------------------------------------------------------
# Parallel parse + resolve
# ---------------------------------------------------------------------------


def test_ingest_source_parallel_real_run_uses_pool(mini_root: Path) -> None:
    """Met parallel=4 over 8 jobs moet de pool concurrent draaien.

    We tellen het max gelijktijdig actieve runner-aanroepen door een
    threading-counter; bij sequentieel zou dat 1 zijn.
    """
    import threading
    import time

    cache = mini_root / "_cache" / "abd-nieuws"
    for i in range(8):
        (cache / f"news-{i:02d}.html").write_text("<html/>", encoding="utf-8")

    active = 0
    max_active = 0
    lock = threading.Lock()

    def fake_runner(cmd: list[str]) -> int:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        # Simuleer wat IO-wachttijd zodat threads daadwerkelijk overlappen.
        time.sleep(0.05)
        with lock:
            active -= 1
        out_path = next(Path(c) for c in cmd if c.endswith(".json"))
        out_path.write_text("[]", encoding="utf-8")
        return 0

    result = ingest_source(
        "abd-nieuws",
        repo_root=mini_root,
        cache_root=mini_root / "_cache",
        staging_dir=mini_root / "data" / "_staging",
        data_dir=mini_root / "data",
        schemas_dir=mini_root / "schemas",
        dry_run=False,
        runner=fake_runner,
        parallel=4,
    )

    assert result.parsed == 8
    # Met 8 jobs en 4 workers verwachten we tussen 2 en 4 gelijktijdig actief.
    # Sequentieel zou max_active == 1 zijn — dat moet falen.
    assert max_active >= 2, f"verwachtte parallel uitvoer, max_active={max_active}"
    assert max_active <= 4


def test_ingest_source_parallel_respects_budget(mini_root: Path) -> None:
    """Met cap=3 en parallel=4 worden er nog steeds maar 3 jobs gedraaid."""
    cache = mini_root / "_cache" / "abd-nieuws"
    for i in range(6):
        (cache / f"news-{i:02d}.html").write_text("<html/>", encoding="utf-8")

    calls: list[list[str]] = []
    lock = __import__("threading").Lock()

    def fake_runner(cmd: list[str]) -> int:
        with lock:
            calls.append(cmd)
        out_path = next(Path(c) for c in cmd if c.endswith(".json"))
        out_path.write_text("[]", encoding="utf-8")
        return 0

    budget = IngestBudget(max_claude_calls=3)
    result = ingest_source(
        "abd-nieuws",
        repo_root=mini_root,
        cache_root=mini_root / "_cache",
        staging_dir=mini_root / "data" / "_staging",
        data_dir=mini_root / "data",
        schemas_dir=mini_root / "schemas",
        dry_run=False,
        runner=fake_runner,
        parallel=4,
        budget=budget,
    )

    assert len(calls) == 3
    assert result.parsed == 3
    assert result.budget_hit is True
    assert budget.used_calls == 3


def test_ingest_source_parallel_exception_in_one_does_not_stop_others(
    mini_root: Path,
) -> None:
    """Als één thread crasht, draaien de andere door (zowel parse- als
    resolve-fase gebruiken dezelfde pool-pattern)."""
    import threading

    cache = mini_root / "_cache" / "abd-nieuws"
    for i in range(5):
        (cache / f"news-{i:02d}.html").write_text("<html/>", encoding="utf-8")

    parse_calls = 0
    parse_lock = threading.Lock()

    def fake_runner(cmd: list[str]) -> int:
        nonlocal parse_calls
        # Tel alleen parse-calls (parse_abd_nieuws_local.sh in cmd[1]).
        if "parse_abd_nieuws_local.sh" in " ".join(cmd):
            with parse_lock:
                parse_calls += 1
            out_path = next(Path(c) for c in cmd if c.endswith(".json"))
            if "news-02" in str(out_path):
                raise RuntimeError("fake claude crash op news-02")
            out_path.write_text("[]", encoding="utf-8")
            return 0
        # resolve-call: schrijf .resolved.json companion.
        if "resolve_staging_local.sh" in " ".join(cmd):
            staging_path = Path(cmd[-1])
            staging_path.with_suffix(".resolved.json").write_text("[]")
            return 0
        return 0

    result = ingest_source(
        "abd-nieuws",
        repo_root=mini_root,
        cache_root=mini_root / "_cache",
        staging_dir=mini_root / "data" / "_staging",
        data_dir=mini_root / "data",
        schemas_dir=mini_root / "schemas",
        dry_run=False,
        runner=fake_runner,
        parallel=3,
    )

    # Alle 5 parse-jobs zijn gesubmit ondanks de crash op news-02.
    assert parse_calls == 5
    assert result.parsed == 4
    assert result.parse_failed == 1


def test_ingest_source_parallel_invalid_value_raises(mini_root: Path) -> None:
    with pytest.raises(ValueError):
        ingest_source(
            "abd-nieuws",
            repo_root=mini_root,
            cache_root=mini_root / "_cache",
            staging_dir=mini_root / "data" / "_staging",
            data_dir=mini_root / "data",
            schemas_dir=mini_root / "schemas",
            dry_run=True,
            parallel=0,
        )


def test_ingest_cli_parallel_flag_in_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["ingest", "--help"])
    assert result.exit_code == 0
    assert "--parallel" in result.stdout


def test_ingest_cli_parallel_flag_doorgegeven(
    mini_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--parallel 7` moet bij `ingest_source` aankomen."""
    monkeypatch.chdir(mini_root)

    captured: dict[str, int] = {}

    def fake_ingest_source(source, **kwargs):
        captured["parallel"] = kwargs.get("parallel")
        return IngestResult(
            source=source, parsed=0, resolved=0, applied=0, validate_ok=True
        )

    monkeypatch.setattr(
        "polder.cli.commands.ingest_cmd.ingest_source", fake_ingest_source
    )

    with patch(
        "polder.cli.commands.ingest_cmd._repo_root", return_value=mini_root
    ):
        cli = CliRunner()
        result = cli.invoke(
            app,
            [
                "ingest",
                "--source",
                "abd-nieuws",
                "--parallel",
                "7",
                "--dry-run",
            ],
        )

    assert result.exit_code == 0, result.output
    assert captured["parallel"] == 7


def test_cli_max_claude_calls_zero_dry_run_plant_niets(
    mini_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`polder ingest --max-claude-calls 0 --dry-run` plant 0 calls."""
    monkeypatch.chdir(mini_root)
    cache = mini_root / "_cache" / "abd-nieuws"
    for i in range(3):
        (cache / f"x-{i}.html").write_text("<html/>")

    with patch(
        "polder.cli.commands.ingest_cmd._repo_root", return_value=mini_root
    ):
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "ingest",
                "--source",
                "abd-nieuws",
                "--max-claude-calls",
                "0",
                "--dry-run",
            ],
        )

    assert result.exit_code == 0, result.output
    combined = result.output + (result.stderr or "")
    assert "0/3" in combined or "0 nieuwe" in combined
    assert "Dry-run klaar" in combined
