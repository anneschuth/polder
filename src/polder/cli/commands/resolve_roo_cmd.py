"""`polder resolve-roo` command.

Resolve een ROO functie/medewerker-staging-file naar bestaande polder-posten
en -personen. Drie auto-merge lanes (post enrichment, mandaat bevestiging,
mandaat creation); rest gaat naar `<input>.unresolved.json` voor de
`resolve-staging-proposals`-skill.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer

from polder.resolve_roo import resolve


def resolve_roo(
    proposals: Annotated[
        Path,
        typer.Argument(help="Pad naar `roo-functies-YYYY-MM-DD.json`."),
    ],
    data: Annotated[Path, typer.Option("--data", help="Polder data-root.")] = Path("data"),
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Niets schrijven.")] = False,
    verbose: Annotated[bool, typer.Option("-v", "--verbose")] = False,
) -> None:
    """Resolve ROO functie/medewerker-proposals naar bestaande posts/personen."""
    if not proposals.exists():
        typer.echo(f"Proposals-file bestaat niet: {proposals}", err=True)
        raise typer.Exit(code=2)
    if not data.exists():
        typer.echo(f"Data-directory bestaat niet: {data}", err=True)
        raise typer.Exit(code=2)
    stats, _staging = resolve(proposals, data, dry_run=dry_run)
    label = "dry-run" if dry_run else "wrote changes"
    typer.echo(f"=== ROO resolve-stats ({label}) ===", err=True)
    typer.echo(f"  posts enriched:       {stats.posts_enriched}", err=True)
    typer.echo(f"  mandaten confirmed:   {stats.mandaten_confirmed}", err=True)
    typer.echo(f"  mandaten created:     {stats.mandaten_created}", err=True)
    typer.echo(f"  person not found:     {stats.person_not_found}", err=True)
    typer.echo(f"  person ambiguous:     {stats.person_ambiguous}", err=True)
    typer.echo(f"  post not found:       {stats.post_not_found}", err=True)
    typer.echo(f"  skipped (no org):     {stats.skipped_no_org}", err=True)
    typer.echo(f"  → staging:            {stats.proposals_to_staging}", err=True)

    if verbose:
        sys.exit(0)
