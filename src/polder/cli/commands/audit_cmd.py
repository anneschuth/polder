"""`polder audit` command.

Diepe data-audit op `data/` met findings die de schema-validator niet vangt.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def audit(
    data_dir: Annotated[
        Path,
        typer.Option("--data", help="Pad naar data/ root."),
    ] = Path("data"),
    category: Annotated[
        str | None,
        typer.Option(
            "--category",
            "-c",
            help="Filter op één categorie (bv. `start_after_end`).",
        ),
    ] = None,
    max_per_category: Annotated[
        int,
        typer.Option(
            "--max-per-category",
            help="Toon maximaal N findings per categorie.",
        ),
    ] = 10,
    explain: Annotated[
        bool,
        typer.Option("--explain", help="Toon een korte uitleg per categorie."),
    ] = False,
    strict: Annotated[
        bool,
        typer.Option(
            "--strict",
            help="Exit non-zero als er findings zijn (voor CI-gebruik).",
        ),
    ] = False,
) -> None:
    """Run diepe data-audit op `data/`."""
    from polder.audit import CATEGORY_HELP, run_audit, summary

    if not data_dir.is_absolute():
        data_dir = _repo_root() / data_dir
    if not data_dir.exists():
        typer.echo(f"data-dir niet gevonden: {data_dir}", err=True)
        raise typer.Exit(code=2)

    issues = run_audit(data_dir)
    n_cats, n_findings = summary(issues)

    typer.echo(f"=== audit ({data_dir}) ===")
    typer.echo(f"  categorieën met findings: {n_cats}")
    typer.echo(f"  totaal findings:          {n_findings}")
    typer.echo("")

    if not issues:
        typer.echo("Geen inconsistenties gevonden.")
        return

    categories = sorted(issues.keys())
    if category is not None:
        if category not in issues:
            typer.echo(f"Geen findings voor categorie '{category}'.")
            available = ", ".join(categories)
            typer.echo(f"Beschikbaar: {available}")
            raise typer.Exit(code=0)
        categories = [category]

    for cat in categories:
        items = issues[cat]
        typer.echo(f"{cat}: {len(items)}")
        if explain and cat in CATEGORY_HELP:
            typer.echo(f"  → {CATEGORY_HELP[cat]}")
        for item in items[:max_per_category]:
            typer.echo(f"  {item}")
        if len(items) > max_per_category:
            typer.echo(f"  ... +{len(items) - max_per_category} meer")
        typer.echo("")

    if strict and n_findings:
        raise typer.Exit(code=1)
