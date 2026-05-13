"""`polder show <id>` command."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml
from rich.console import Console
from rich.table import Table

from polder.lib.quick_lookup import load_by_id


def _resolve_root(data: Path | None) -> Path:
    if data is not None:
        return data.resolve()
    cwd = Path.cwd()
    if (cwd / "data").exists():
        return cwd
    raise typer.BadParameter(f"Geen data/ in {cwd}. Gebruik --data om een polder-root op te geven.")


def _lookup_fast(root: Path, id: str) -> Any | None:
    """Direct file-read voor person/org/post. Voor mandaten of onbekende
    slug-vormen valt de caller terug op de volledige Polder-loader."""
    return load_by_id(root / "data", id)


def _lookup_full(root: Path, id: str) -> Any | None:
    """Volledige dataset-load — alleen nodig voor mandaten (UUID) of als de
    fast-path geen pad oplevert."""
    from polder.lib import Polder

    p = Polder.local(root)
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


LINK_TEMPLATES: dict[str, str] = {
    "tk_persoon_id": "https://berthub.eu/tkconv/persoon.html?nummer={value}",
    "wikidata": "https://www.wikidata.org/wiki/{value}",
    "tooi": "{value}",
    "roo_id": "https://organisaties.overheid.nl/{value}/",
    "allmanak_id": "https://www.allmanak.nl/cat/{value}/",
    "ek_lid_slug": "https://www.eerstekamer.nl/lid/{value}",
}


def _render_links(obj: Any, console: Console) -> None:
    identifiers = getattr(obj, "identifiers", None)
    if identifiers is None:
        return
    ids = identifiers.model_dump(mode="json", exclude_none=True)
    if not ids:
        return
    table = Table(title="Deep-links", show_header=True, header_style="bold")
    table.add_column("identifier")
    table.add_column("waarde")
    table.add_column("link")
    for kind, value in ids.items():
        template = LINK_TEMPLATES.get(kind)
        link = template.format(value=value) if template else "-"
        table.add_row(kind, str(value), link)
    console.print(table)


def show(
    id: Annotated[str, typer.Argument(help="ID, bv `org:min-bzk` of `person:rutte-...`.")],
    history: Annotated[
        bool, typer.Option(help="Toon mandaat-historie waar van toepassing.")
    ] = False,
    links: Annotated[
        bool, typer.Option(help="Toon deep-links naar externe systemen (tkconv, Wikidata, ROO).")
    ] = False,
    format: Annotated[str, typer.Option(help="table | json | yaml")] = "table",
    data: Annotated[Path | None, typer.Option(help="Polder root.")] = None,
) -> None:
    """Toon een entiteit in detail."""
    root = _resolve_root(data)
    obj = _lookup_fast(root, id)
    if obj is None and not id.startswith(("person:", "post:", "org:")):
        obj = _lookup_full(root, id)
    if obj is None:
        typer.echo(f"Niet gevonden: {id}", err=True)
        raise typer.Exit(code=1)

    if format == "json":
        typer.echo(
            json.dumps(obj.model_dump(mode="json", exclude_none=True), ensure_ascii=False, indent=2)
        )
        return
    if format == "yaml":
        typer.echo(
            yaml.safe_dump(
                obj.model_dump(mode="json", exclude_none=True), sort_keys=False, allow_unicode=True
            )
        )
        return

    console = Console()
    _render_table(obj, console)

    if links:
        _render_links(obj, console)

    if history:
        if id.startswith("person:"):
            console.print("\n[bold]mandaten:[/bold]")
            for m in obj.mandaten or []:
                console.print(f"  {m.start_date} -> {m.end_date or 'open'}  {m.post_id}  {m.role}")
        elif id.startswith("post:"):
            from polder.lib import Polder

            console.print("\n[bold]mandaten op deze post:[/bold]")
            p = Polder.local(root)
            for m in p.mandaten.for_post(id):
                console.print(
                    f"  {m.start_date} -> {m.end_date or 'open'}  {m.person_id}  {m.role}"
                )
