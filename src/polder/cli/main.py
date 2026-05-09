"""Polder CLI entrypoint.

`polder` ontsluit list/show/export/validate over een lokale Polder-checkout.
"""

from __future__ import annotations

import os
from pathlib import Path

import typer

from polder.cli.commands import export_cmd, list_cmd, show_cmd, validate_cmd

app = typer.Typer(
    name="polder",
    no_args_is_help=True,
    add_completion=False,
    help="Polder CLI: dataset van Nederlandse overheidsorganisaties, personen, posten en mandaten.",
)

list_app = typer.Typer(
    name="list",
    no_args_is_help=True,
    help="Lijst entiteiten (organisaties, personen, posten, mandaten).",
)
app.add_typer(list_app, name="list")

list_app.command("organisaties")(list_cmd.list_organisaties)
list_app.command("personen")(list_cmd.list_personen)
list_app.command("posten")(list_cmd.list_posten)
list_app.command("mandaten")(list_cmd.list_mandaten)

app.command("show")(show_cmd.show)
app.command("export")(export_cmd.export)
app.command("validate")(validate_cmd.validate)


def find_data_root(explicit: Path | None = None) -> Path:
    """Resolve de polder-root volgens: --data | $POLDER_DATA | ./ als die data/ heeft."""
    if explicit is not None:
        return explicit.resolve()
    env = os.environ.get("POLDER_DATA")
    if env:
        return Path(env).resolve()
    cwd = Path.cwd()
    if (cwd / "data").exists():
        return cwd
    return cwd


if __name__ == "__main__":  # pragma: no cover
    app()
