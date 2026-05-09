"""`polder show <id>` command."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml
from rich.console import Console
from rich.table import Table

from polder.lib import Polder


def _resolve_root(data: Path | None) -> Path:
    if data is not None:
        return data.resolve()
    cwd = Path.cwd()
    if (cwd / "data").exists():
        return cwd
    raise typer.BadParameter(
        f"Geen data/ in {cwd}. Gebruik --data om een polder-root op te geven."
    )


def _lookup(p: Polder, id: str) -> Any | None:
    if id.startswith("org:"):
        return p.organisaties.get(id)
    if id.startswith("person:"):
        return p.personen.get(id)
    if id.startswith("post:"):
        return p.posten.get(id)
    return p.mandaten.get(id)


def _render_table(obj: Any, console: Console) -> None:
    data = obj.model_dump(mode="json", exclude_none=True)
    table = Table(show_header=True, header_style="bold")
    table.add_column("veld")
    table.add_column("waarde")
    for k, v in data.items():
        if isinstance(v, (dict, list)):
            v = yaml.safe_dump(v, sort_keys=False, allow_unicode=True).strip()
        table.add_row(str(k), str(v))
    console.print(table)


def show(
    id: Annotated[str, typer.Argument(help="ID, bv `org:min-bzk` of `person:rutte-...`.")],
    history: Annotated[bool, typer.Option(help="Toon mandaat-historie waar van toepassing.")] = False,
    format: Annotated[str, typer.Option(help="table | json | yaml")] = "table",
    data: Annotated[Path | None, typer.Option(help="Polder root.")] = None,
) -> None:
    """Toon een entiteit in detail."""
    root = _resolve_root(data)
    p = Polder.local(root)
    obj = _lookup(p, id)
    if obj is None:
        typer.echo(f"Niet gevonden: {id}", err=True)
        raise typer.Exit(code=1)

    if format == "json":
        typer.echo(json.dumps(obj.model_dump(mode="json", exclude_none=True), ensure_ascii=False, indent=2))
        return
    if format == "yaml":
        typer.echo(yaml.safe_dump(obj.model_dump(mode="json", exclude_none=True), sort_keys=False, allow_unicode=True))
        return

    console = Console()
    _render_table(obj, console)

    if history:
        if id.startswith("person:"):
            console.print("\n[bold]mandaten:[/bold]")
            for m in obj.mandaten or []:
                console.print(
                    f"  {m.start_date} -> {m.end_date or 'open'}  {m.post_id}  {m.role}"
                )
        elif id.startswith("post:"):
            console.print("\n[bold]mandaten op deze post:[/bold]")
            for m in p.mandaten.for_post(id):
                console.print(
                    f"  {m.start_date} -> {m.end_date or 'open'}  {m.person_id}  {m.role}"
                )
