"""`polder search <query>` command.

Vrije-tekst zoek over alle entity-types in `data/`. Default: case-insensitive
substring-match op alle string-waarden in elk record. Voor één-shot lookups
op de CLI; voor exploratief werk: `polder serve` (datasette).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated, Any

import typer
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


def _flatten(value: Any, prefix: str = "") -> Iterable[tuple[str, str]]:
    """Yield (dotted-field-path, string-value) voor elke str-leaf in `value`."""
    if value is None:
        return
    if isinstance(value, str):
        yield prefix, value
        return
    if isinstance(value, (int, float, bool)):
        yield prefix, str(value)
        return
    if isinstance(value, dict):
        for k, v in value.items():
            sub = f"{prefix}.{k}" if prefix else str(k)
            yield from _flatten(v, sub)
        return
    if isinstance(value, list):
        for i, item in enumerate(value):
            sub = f"{prefix}[{i}]"
            yield from _flatten(item, sub)


def _record_to_dict(obj: Any) -> dict[str, Any]:
    """Pydantic-model -> dict, of dict as-is. Skip None-velden."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json", exclude_none=True)
    if isinstance(obj, dict):
        return obj
    return {}


def _entity_type(obj: Any) -> str:
    obj_id = getattr(obj, "id", None) or _record_to_dict(obj).get("id", "")
    if obj_id.startswith("org:"):
        return "org"
    if obj_id.startswith("person:"):
        return "person"
    if obj_id.startswith("post:"):
        return "post"
    return "?"


def _matches(
    data: dict[str, Any],
    matcher: re.Pattern[str],
    field_filter: str | None,
) -> list[tuple[str, str]]:
    """Geef (field, value) terug voor velden die de matcher hit.

    `field_filter`, indien gezet, restrict de zoek tot velden waarvan het
    dotted path met `field_filter` begint (bv. `name.family`).
    """
    hits: list[tuple[str, str]] = []
    for field, value in _flatten(data):
        if field_filter is not None and not field.startswith(field_filter):
            continue
        if matcher.search(value):
            hits.append((field, value))
    return hits


def _truncate(text: str, max_len: int = 80) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def search(
    query: Annotated[str, typer.Argument(help="Zoekterm (substring of regex met --regex).")],
    type_: Annotated[
        str | None,
        typer.Option(
            "--type",
            "-t",
            help="Beperk tot entity-type: org | person | post.",
        ),
    ] = None,
    field: Annotated[
        str | None,
        typer.Option(
            "--field",
            "-f",
            help="Beperk tot één veld (dotted path, bv. `name.family`).",
        ),
    ] = None,
    regex: Annotated[
        bool,
        typer.Option("--regex", help="Behandel query als regex i.p.v. substring."),
    ] = False,
    case_sensitive: Annotated[
        bool,
        typer.Option("--case-sensitive", help="Case-sensitive match (default: case-insensitive)."),
    ] = False,
    limit: Annotated[
        int,
        typer.Option("--limit", "-n", help="Max aantal resultaten."),
    ] = 50,
    data: Annotated[Path | None, typer.Option("--data", help="Polder root.")] = None,
) -> None:
    """Vrije-tekst zoek over alle entity-types in `data/`.

    Voorbeelden:

      polder search rutte                       # alle records met "rutte"
      polder search "minister van financien"    # exacte phrase
      polder search Dijk -t person -f name.family
      polder search "^org:min-" --regex
    """
    pattern_text = query if regex else re.escape(query)
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        matcher = re.compile(pattern_text, flags)
    except re.error as exc:
        raise typer.BadParameter(f"Ongeldige regex: {exc}") from exc

    if type_ is not None and type_ not in ("org", "person", "post"):
        raise typer.BadParameter("--type moet 'org', 'person' of 'post' zijn.")

    root = _resolve_root(data)
    polder = Polder.local(root)

    sources: list[tuple[str, Iterable[Any]]] = []
    if type_ in (None, "org"):
        sources.append(("org", polder.organisaties.all()))
    if type_ in (None, "person"):
        sources.append(("person", polder.personen.all()))
    if type_ in (None, "post"):
        sources.append(("post", polder.posten.all()))

    console = Console()
    table = Table(show_header=True, header_style="bold")
    table.add_column("type")
    table.add_column("id")
    table.add_column("veld")
    table.add_column("waarde")

    n_results = 0
    for entity_label, records in sources:
        for obj in records:
            if n_results >= limit:
                break
            d = _record_to_dict(obj)
            hits = _matches(d, matcher, field)
            if not hits:
                continue
            obj_id = d.get("id", "?")
            first_field, first_value = hits[0]
            table.add_row(
                entity_label,
                obj_id,
                first_field,
                _truncate(first_value),
            )
            n_results += 1
            extra = len(hits) - 1
            if extra > 0:
                table.add_row("", "", f"+{extra} velden", "")
        if n_results >= limit:
            break

    if n_results == 0:
        typer.echo(f"Geen resultaten voor: {query}")
        raise typer.Exit(code=1)

    console.print(table)
    if n_results >= limit:
        console.print(f"[dim](limiet bereikt op {limit}; gebruik --limit voor meer)[/dim]")
