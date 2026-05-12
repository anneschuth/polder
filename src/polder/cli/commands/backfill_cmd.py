"""`polder backfill <bron>`.

Run een skill opnieuw op alle historische cache-input. Vervangt
`scripts/reparse_abd_nieuws.sh` (497 regels bash). Gebruikt dezelfde
`polder.llm.runner.run_skill` als de daily ingest, dus prompt-cache en
response-cache stapelen automatisch.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(
    name="backfill",
    no_args_is_help=True,
    add_completion=False,
    help="Run een skill opnieuw op historische cache-input.",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _print_summary(label: str, result) -> None:
    typer.echo("")
    typer.echo(f"=== {label} ===")
    typer.echo(f"kandidaten:       {result.total_candidates}")
    typer.echo(f"pre-filter skip:  {result.pre_filtered}")
    typer.echo(f"cache-hits:       {result.cache_hits}")
    typer.echo(f"parsed:           {result.parsed}")
    typer.echo(f"failed:           {result.failed}")
    typer.echo(f"cost (USD):       ${result.cost_usd:.4f}")
    typer.echo(
        f"tokens:           in={result.input_tokens} "
        f"out={result.output_tokens} "
        f"cache_read={result.cache_read_tokens} "
        f"cache_create={result.cache_creation_tokens}"
    )
    if result.rate_limited:
        typer.echo("STATUS: rate-limit gedetecteerd, fase afgebroken.")
    for note in result.notes:
        typer.echo(f"note: {note}")


@app.command("abd-nieuws")
def abd_nieuws(
    since: Annotated[
        str | None,
        typer.Option("--since", help="Alleen files met mtime >= deze ISO-datum (YYYY-MM-DD)."),
    ] = None,
    until: Annotated[
        str | None,
        typer.Option("--until", help="Alleen files met mtime <= deze ISO-datum (YYYY-MM-DD)."),
    ] = None,
    pattern: Annotated[
        str | None,
        typer.Option("--filter", help="Regex op het pad; alleen matches verwerken."),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="Maximaal aantal HTMLs verwerken."),
    ] = None,
    parallel: Annotated[
        int,
        typer.Option("--parallel", "-p", help="Aantal parallelle SkillSessions."),
    ] = 1,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Override default model (Haiku 4.5)."),
    ] = None,
    no_cache: Annotated[
        bool,
        typer.Option("--no-cache", help="Sla response-cache over (forceer LLM-calls)."),
    ] = False,
    max_cost_usd: Annotated[
        float | None,
        typer.Option("--max-cost-usd", help="Stop als kosten deze cap raken."),
    ] = None,
) -> None:
    """Run parse-abd-nieuws op alle gedownloade ABD-nieuwsberichten."""
    from polder.backfill.abd_nieuws import backfill

    result = backfill(
        _repo_root(),
        since=since,
        until=until,
        pattern=pattern,
        limit=limit,
        parallel=parallel,
        model=model,
        use_cache=not no_cache,
        max_cost_usd=max_cost_usd,
    )
    _print_summary("abd-nieuws backfill", result)


@app.command("staatscourant")
def staatscourant(
    since: Annotated[
        str | None,
        typer.Option("--since", help="Alleen files met mtime >= deze ISO-datum (YYYY-MM-DD)."),
    ] = None,
    until: Annotated[
        str | None,
        typer.Option("--until", help="Alleen files met mtime <= deze ISO-datum (YYYY-MM-DD)."),
    ] = None,
    pattern: Annotated[
        str | None,
        typer.Option("--filter", help="Regex op het pad; alleen matches verwerken."),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="Maximaal aantal XMLs verwerken."),
    ] = None,
    parallel: Annotated[
        int,
        typer.Option("--parallel", "-p", help="Aantal parallelle SkillSessions."),
    ] = 1,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Override default model (Haiku 4.5)."),
    ] = None,
    no_cache: Annotated[
        bool,
        typer.Option("--no-cache", help="Sla response-cache over (forceer LLM-calls)."),
    ] = False,
    max_cost_usd: Annotated[
        float | None,
        typer.Option("--max-cost-usd", help="Stop als kosten deze cap raken."),
    ] = None,
) -> None:
    """Run parse-staatscourant op alle gedownloade Staatscourant-XMLs."""
    from polder.backfill.staatscourant import backfill

    result = backfill(
        _repo_root(),
        since=since,
        until=until,
        pattern=pattern,
        limit=limit,
        parallel=parallel,
        model=model,
        use_cache=not no_cache,
        max_cost_usd=max_cost_usd,
    )
    _print_summary("staatscourant backfill", result)
