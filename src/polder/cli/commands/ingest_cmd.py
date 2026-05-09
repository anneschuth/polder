"""`polder ingest` command — end-to-end staging-pipeline.

Eén commando dat per bron parse -> resolve -> apply -> validate doorloopt en
optioneel ook build, commit en push afhandelt. Bedoeld voor dagelijkse
automatische runs (CI of `scripts/ingest_local.sh`).

De zware logica zit in `polder.ingest`; dit bestand levert alleen de typer-
binding plus een Nederlandstalige stdout-samenvatting.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated

import typer

from polder.ingest import (
    ALL_SOURCES,
    IngestBudget,
    IngestResult,
    Source,
    commit_changes,
    format_dry_run_summary,
    ingest_source,
    run_build,
)


def _repo_root() -> Path:
    """`src/polder/cli/commands/ingest_cmd.py` -> repo root vier niveaus omhoog."""
    return Path(__file__).resolve().parents[4]


def _resolve_sources(label: str) -> tuple[Source, ...]:
    if label == "all":
        return ALL_SOURCES
    if label in ALL_SOURCES:
        return (label,)  # type: ignore[return-value]
    raise typer.BadParameter(
        f"onbekende source {label!r}. Kies uit: all, {', '.join(ALL_SOURCES)}."
    )


def _print_result(result: IngestResult, *, dry_run: bool) -> None:
    typer.echo(f"[{result.source}]", err=True)
    if dry_run:
        typer.echo(
            f"  Parse:    {result.parsed} nieuwe input(s) gepland "
            f"(~${result.parse_cost_estimate_usd:.2f})",
            err=True,
        )
        typer.echo(
            f"  Resolve:  {result.resolved} staging-file(s) zonder .resolved.json "
            f"(~${result.resolve_cost_estimate_usd:.2f})",
            err=True,
        )
        typer.echo(
            f"  Apply:    {result.applied} records auto-mergeable, "
            f"{result.needs_review} needs-review, {result.skipped} skip",
            err=True,
        )
    else:
        typer.echo(
            f"  Parse:    {result.parsed} ok, {result.parse_failed} failed",
            err=True,
        )
        typer.echo(
            f"  Resolve:  {result.resolved} ok, {result.resolve_failed} failed",
            err=True,
        )
        typer.echo(
            f"  Apply:    {result.applied} records aangemaakt, "
            f"{result.skipped} skipped",
            err=True,
        )
        if result.validate_ok is True:
            typer.echo("  Validate: clean", err=True)
        elif result.validate_ok is False:
            typer.echo("  Validate: FAILED", err=True)
    if result.budget_hit:
        typer.echo("  Budget:   cap bereikt — fase afgebroken", err=True)
    for note in result.notes:
        typer.echo(f"  - {note}", err=True)
    typer.echo("", err=True)


def ingest(
    source: Annotated[
        str,
        typer.Option(
            "--source",
            help="abd-nieuws | staatscourant | organogram | all",
        ),
    ] = "all",
    threshold: Annotated[
        float,
        typer.Option(
            "--threshold",
            help="Confidence-drempel voor apply-staging (default 0.85).",
        ),
    ] = 0.85,
    commit: Annotated[
        bool,
        typer.Option(
            "--commit",
            help="Maak een git-commit van data/ + dist/ na succesvolle pipeline.",
        ),
    ] = False,
    push: Annotated[
        bool,
        typer.Option(
            "--push",
            help="Push naar origin na commit (impliceert --commit).",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Plan tonen zonder iets uit te voeren.",
        ),
    ] = False,
    limit: Annotated[
        int | None,
        typer.Option(
            "--limit",
            help="Max aantal nieuwe parse-jobs per bron (handig om kosten te beperken).",
        ),
    ] = None,
    max_claude_calls: Annotated[
        int | None,
        typer.Option(
            "--max-claude-calls",
            help=(
                "Hard cap op LLM-calls (parse + resolve, alle bronnen samen). "
                "None = unlimited. Zodra de cap geraakt is stopt de pipeline "
                "die fase voor de huidige bron en gaat door naar apply."
            ),
        ),
    ] = None,
    branch: Annotated[
        str,
        typer.Option(
            "--branch",
            help="Push-target branch (default main).",
        ),
    ] = "main",
    parallel: Annotated[
        int,
        typer.Option(
            "--parallel",
            help=(
                "Aantal parallelle workers voor parse + resolve fases. "
                "Default 5; iedere worker doet één claude-subprocess tegelijk. "
                "Verhoog tot ~8 voor snellere runs als rate-limits het toelaten."
            ),
        ),
    ] = 5,
) -> None:
    """End-to-end: parse + resolve (parallel) -> apply -> validate -> [build] [commit per bron] [push]."""
    repo_root = _repo_root()
    sources = _resolve_sources(source)
    if push and not commit:
        commit = True
    if parallel < 1:
        raise typer.BadParameter(f"--parallel moet >= 1 zijn, kreeg {parallel}")

    typer.echo(
        f"Ingest run: source={source}, threshold={threshold}, "
        f"commit={commit}, push={push}, dry_run={dry_run}, "
        f"max_claude_calls={max_claude_calls}, parallel={parallel}",
        err=True,
    )
    typer.echo("", err=True)

    budget = IngestBudget(max_claude_calls=max_claude_calls)

    results: list[IngestResult] = []
    any_validate_failed = False
    today = date.today().isoformat()
    for s in sources:
        result = ingest_source(
            s,
            repo_root=repo_root,
            threshold=threshold,
            dry_run=dry_run,
            limit=limit,
            budget=budget,
            parallel=parallel,
        )
        _print_result(result, dry_run=dry_run)
        results.append(result)
        if result.validate_ok is False or result.apply_failed:
            any_validate_failed = True
            # Geen per-source commit als deze bron faalde.
            continue

        # Per-source commit zodra deze bron records heeft toegevoegd. Reden:
        # kleinere, reviewbare commits; één rollback raakt de andere bronnen niet.
        if not dry_run and commit and result.applied > 0:
            needs_review_label = (
                f" ({result.needs_review} needs-review)"
                if result.needs_review
                else ""
            )
            message = (
                f"Daily ingest {s} {today}: "
                f"+{result.applied} records{needs_review_label}"
            )
            sha = commit_changes(
                message,
                repo_root=repo_root,
                paths=("data",),
                push=False,  # build + push pas na alle bronnen
                branch=branch,
            )
            if sha:
                result.commit_sha = sha
                typer.echo(f"  Commit: {sha[:7]} {message!r}", err=True)

    if dry_run:
        typer.echo("", err=True)
        typer.echo(
            format_dry_run_summary(
                results,
                threshold=threshold,
                parallelism=parallel,
                budget=budget,
            ),
            err=True,
        )
        typer.echo("", err=True)
        typer.echo("Dry-run klaar. Geen wijzigingen geschreven.", err=True)
        raise typer.Exit(code=0)

    if any_validate_failed:
        typer.echo(
            "Ingest gestopt: apply of validate signaleerde fouten. "
            "Geen build, geen finale commit, geen push.",
            err=True,
        )
        raise typer.Exit(code=2)

    total_applied = sum(r.applied for r in results)
    if total_applied == 0:
        typer.echo(
            "Ingest klaar: 0 nieuwe records. Geen build/commit nodig.",
            err=True,
        )
        if budget.max_claude_calls is not None:
            typer.echo(
                f"Budget: {budget.used_calls}/{budget.max_claude_calls} calls "
                f"verbruikt (~${budget.cost_estimate_usd:.2f}).",
                err=True,
            )
        raise typer.Exit(code=0)

    # Build alleen als we daadwerkelijk records hebben aangemaakt.
    typer.echo("Build: dist/polder.db + csv + datapackage", err=True)
    build_ok = run_build(repo_root=repo_root)
    if not build_ok:
        typer.echo("Build faalde. Geen extra commit, geen push.", err=True)
        raise typer.Exit(code=3)

    if not commit:
        typer.echo(
            f"Klaar: {total_applied} records, build ok. "
            "Niet gecommit (geen --commit).",
            err=True,
        )
        raise typer.Exit(code=0)

    # Aparte commit voor de gegenereerde dist/-output, zodat data-commits per
    # bron blijven en de build één afsluitende commit is.
    build_message = f"Daily build {today}: dist/ + datapackage"
    build_sha = commit_changes(
        build_message,
        repo_root=repo_root,
        paths=("dist", "datapackage.json"),
        push=push,
        branch=branch,
    )
    if build_sha:
        typer.echo(f"Build-commit: {build_sha[:7]} {build_message!r}", err=True)
    elif push:
        # Niets nieuws te committen, maar misschien moeten de data-commits nog gepusht worden.
        committed = [r for r in results if r.commit_sha]
        if committed:
            import subprocess

            subprocess.run(
                ["git", "-C", str(repo_root), "push", "origin", branch],
                check=False,
            )

    if push:
        typer.echo(f"Push: origin/{branch}", err=True)
    if budget.max_claude_calls is not None:
        typer.echo(
            f"Budget: {budget.used_calls}/{budget.max_claude_calls} calls "
            f"verbruikt (~${budget.cost_estimate_usd:.2f}).",
            err=True,
        )
    raise typer.Exit(code=0)


def main() -> None:  # pragma: no cover - voor `python -m`
    typer.run(ingest)


if __name__ == "__main__":  # pragma: no cover
    main()
