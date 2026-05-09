"""`polder list <subject>` commands."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from polder.lib import Polder

OutputFormat = str  # "table" | "json" | "csv"


def _resolve_root(data: Path | None) -> Path:
    if data is not None:
        return data.resolve()
    cwd = Path.cwd()
    if (cwd / "data").exists():
        return cwd
    raise typer.BadParameter(
        f"Geen data/ in {cwd}. Gebruik --data om een polder-root op te geven."
    )


def _render(rows: list[dict[str, Any]], columns: list[str], format: str) -> None:
    if format == "json":
        typer.echo(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
        return
    if format == "csv":
        writer = csv.DictWriter(sys.stdout, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in columns})
        return
    if format != "table":
        raise typer.BadParameter(f"Unsupported format: {format}")
    console = Console()
    table = Table(show_header=True, header_style="bold")
    for col in columns:
        table.add_column(col)
    for row in rows:
        table.add_row(*[str(row.get(c, "")) for c in columns])
    console.print(table)
    console.print(f"[dim]{len(rows)} rijen[/dim]")


def list_organisaties(
    type: Annotated[str | None, typer.Option(help="Filter op organisatietype.")] = None,
    classification: Annotated[
        str | None, typer.Option(help="Filter op classification.")
    ] = None,
    format: Annotated[OutputFormat, typer.Option(help="table | json | csv")] = "table",
    data: Annotated[Path | None, typer.Option(help="Polder root.")] = None,
) -> None:
    """Lijst organisaties, optioneel gefilterd op type."""
    root = _resolve_root(data)
    p = Polder.local(root)
    orgs = list(p.organisaties.all())
    if type:
        orgs = [o for o in orgs if o.type == type]
    if classification:
        orgs = [o for o in orgs if o.classification == classification]
    rows = [
        {
            "id": o.id,
            "type": o.type,
            "classification": o.classification or "",
            "name": o.names[0].value if o.names else "",
            "valid_from": o.valid_from,
            "valid_until": o.valid_until or "",
        }
        for o in orgs
    ]
    _render(rows, ["id", "type", "classification", "name", "valid_from", "valid_until"], format)


def list_personen(
    classification: Annotated[
        str | None, typer.Option(help="Filter personen met mandaat van die classification.")
    ] = None,
    current: Annotated[
        bool, typer.Option(help="Alleen personen met >= 1 lopend mandaat.")
    ] = False,
    format: Annotated[OutputFormat, typer.Option(help="table | json | csv")] = "table",
    data: Annotated[Path | None, typer.Option(help="Polder root.")] = None,
) -> None:
    """Lijst personen."""
    root = _resolve_root(data)
    p = Polder.local(root)
    personen = list(p.personen.all())
    if classification:
        personen = p.personen.with_classification(classification)
    if current:
        personen = [x for x in personen if any(m.end_date is None for m in (x.mandaten or []))]
    rows = [
        {
            "id": pers.id,
            "name": pers.name.full,
            "birth_year": pers.birth.year if pers.birth else "",
            "n_mandaten": len(pers.mandaten or []),
        }
        for pers in personen
    ]
    _render(rows, ["id", "name", "birth_year", "n_mandaten"], format)


def list_posten(
    organization: Annotated[
        str | None, typer.Option("--organization", help="Filter op organization_id.")
    ] = None,
    classification: Annotated[str | None, typer.Option(help="Filter op classification.")] = None,
    format: Annotated[OutputFormat, typer.Option(help="table | json | csv")] = "table",
    data: Annotated[Path | None, typer.Option(help="Polder root.")] = None,
) -> None:
    """Lijst posten."""
    root = _resolve_root(data)
    p = Polder.local(root)
    posten = list(p.posten.all())
    if organization:
        posten = [x for x in posten if x.organization_id == organization]
    if classification:
        posten = [x for x in posten if x.classification == classification]
    rows = [
        {
            "id": post.id,
            "organization_id": post.organization_id,
            "label": post.label,
            "classification": post.classification,
            "valid_from": post.valid_from,
        }
        for post in posten
    ]
    _render(rows, ["id", "organization_id", "label", "classification", "valid_from"], format)


def list_mandaten(
    organization: Annotated[
        str | None, typer.Option("--organization", help="Filter op organization_id.")
    ] = None,
    person: Annotated[str | None, typer.Option("--person", help="Filter op person_id.")] = None,
    format: Annotated[OutputFormat, typer.Option(help="table | json | csv")] = "table",
    data: Annotated[Path | None, typer.Option(help="Polder root.")] = None,
) -> None:
    """Lijst mandaten (geflattend van inline + standalone)."""
    root = _resolve_root(data)
    p = Polder.local(root)
    mandaten = list(p.mandaten.all())
    if organization:
        mandaten = [m for m in mandaten if m.organization_id == organization]
    if person:
        mandaten = [m for m in mandaten if m.person_id == person]
    rows = [
        {
            "id": m.id,
            "person_id": m.person_id,
            "post_id": m.post_id,
            "role": m.role,
            "start_date": m.start_date,
            "end_date": m.end_date or "",
        }
        for m in mandaten
    ]
    _render(rows, ["id", "person_id", "post_id", "role", "start_date", "end_date"], format)
