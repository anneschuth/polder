"""`polder daily-update` command.

Lokale variant van `.github/workflows/daily-update.yml`. Stappen:

1. Run alle deterministische fetchers (fail-soft).
2. `polder validate` (hard fail).
3. `polder diff` -> diff.json + proposals.json.
4. Bepaal PR-label op basis van confidence-cijfers in de diff.
5. Genereer PR-body via review-pr-diff skill.
6. Print samenvatting.

Geen git-commits, geen PR. Anne reviewt en commit handmatig.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import typer

logger = logging.getLogger("polder.cli.daily")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _run_subcommand(label: str, args: list[str], *, fail_soft: bool) -> int:
    """Roep `polder <args>` aan via een nieuw Python-proces.

    Subprocess ipv directe import zodat een geïsoleerde failure (een fetcher
    die crasht) niet de andere stappen meeneemt.
    """
    typer.echo(f"[{label}]  polder {' '.join(args)}", err=True)
    proc = subprocess.run(
        [sys.executable, "-m", "polder", *args],
        check=False,
        cwd=_repo_root(),
    )
    code = proc.returncode
    if code != 0:
        if fail_soft:
            typer.echo(f"[{label}] exit={code} (continue)", err=True)
        else:
            typer.echo(f"[{label}] exit={code}", err=True)
    return code


def _determine_label(diff_path: Path) -> str:
    """Bepaal PR-label op basis van confidence-cijfers in diff.json."""
    if not diff_path.exists():
        return "needs-review"
    try:
        diffs = json.loads(diff_path.read_text(encoding="utf-8") or "[]")
    except json.JSONDecodeError:
        diffs = []

    def confidence_ok(entry: dict) -> bool:
        after = entry.get("after") or {}
        for source in after.get("sources", []) or []:
            conf = source.get("confidence")
            if conf is not None and conf < 0.95:
                return False
        return True

    has_high_stakes = any(d.get("high_stakes") for d in diffs)
    all_confident = all(confidence_ok(d) for d in diffs)
    return "auto-merge" if all_confident and not has_high_stakes else "needs-review"


def _count_records(diff_path: Path) -> int:
    if not diff_path.exists():
        return 0
    try:
        diffs = json.loads(diff_path.read_text(encoding="utf-8") or "[]")
    except json.JSONDecodeError:
        return 0
    return len(diffs)


def daily_update() -> None:
    """Run de daily-update pipeline lokaal."""
    root = _repo_root()
    dist = root / "dist"
    dist.mkdir(parents=True, exist_ok=True)

    typer.echo("=== polder daily-update ===")
    typer.echo(f"repo:  {root}")
    typer.echo(f"datum: {datetime.now(UTC).isoformat(timespec='seconds')}")
    typer.echo("")

    fetchers = [
        ("ROO", ["fetch", "roo"]),
        ("Logius COR", ["fetch", "logius"]),
        ("Wikidata orgs", ["fetch", "wikidata", "--orgs"]),
        ("TK OData", ["fetch", "tk"]),
        ("EK scrape", ["fetch", "ek"]),
        ("AR RWT", ["fetch", "ar-rwt"]),
    ]
    for label, args in fetchers:
        _run_subcommand(label, args, fail_soft=True)
        typer.echo("")

    typer.echo("[validate] polder validate")
    code = _run_subcommand("validate", ["validate"], fail_soft=False)
    if code != 0:
        raise typer.Exit(code=code)
    typer.echo("")

    typer.echo("[diff] polder diff")
    _run_subcommand(
        "diff",
        [
            "diff",
            "--cache",
            "_cache",
            "--data",
            "data",
            "--out",
            "diff.json",
            "--proposals",
            "proposals.json",
        ],
        fail_soft=True,
    )
    typer.echo("")

    diff_path = root / "diff.json"
    proposals_path = root / "proposals.json"
    pr_label = _determine_label(diff_path)
    label_path = dist / "pr-label.txt"
    label_path.write_text(pr_label + "\n", encoding="utf-8")
    typer.echo(f"[label] {pr_label} -> {label_path}")
    typer.echo("")

    pr_body = dist / "pr-body.md"
    if diff_path.exists():
        typer.echo("[review] genereer PR-body via review-pr-diff skill")
        from polder.llm.runner import run_skill

        result = run_skill("review-pr-diff", diff_path, output=pr_body)
        if result.rate_limited:
            typer.echo("[review] rate-limit, PR-body niet geschreven", err=True)
        elif result.is_error:
            typer.echo(f"[review] skill-fout: {result.error_message}", err=True)
        else:
            typer.echo(f"[review] PR-body geschreven naar {pr_body}")
    else:
        typer.echo("[review] geen diff.json, sla PR-body over")
    typer.echo("")

    typer.echo("=== samenvatting ===")
    typer.echo(f"records gewijzigd: {_count_records(diff_path)}")
    typer.echo(f"label:             {pr_label}")
    typer.echo(f"pr-body:           {pr_body}")
    typer.echo(f"diff.json:         {diff_path}")
    if proposals_path.exists():
        typer.echo(f"proposals.json:    {proposals_path}")
    typer.echo("")
    typer.echo("Geen commit gemaakt. Review en commit handmatig.")
