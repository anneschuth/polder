"""Polder CLI entrypoint.

`polder` is het ENIGE entrypoint voor de polder-toolchain. Het bundelt:

- `polder fetch <bron>` over alle elf fetchers, plus `fetch all`
- `polder validate` / `diff` / `build` / `list` / `show` / `export`
- `polder skill <name>` voor de Claude Code skills (review-diff, parse-*)
- `polder daily-update` als shortcut naar de daily-update pipeline
- `polder serve` voor datasette op de gebouwde SQLite-database

De oude `polder-fetch-*`, `polder-validate`, `polder-diff` en `polder-build`
entrypoints in `pyproject.toml` blijven staan voor backwards-compatibility.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Annotated

import typer

from polder.cli.commands import (
    apply_staging_cmd,
    audit_cmd,
    backfill_cmd,
    build_cmd,
    daily_cmd,
    diff_cmd,
    export_cmd,
    fetch_cmd,
    ingest_cmd,
    list_cmd,
    resolve_cmd,
    search_cmd,
    serve_cmd,
    show_cmd,
    skill_cmd,
    validate_cmd,
)

app = typer.Typer(
    name="polder",
    no_args_is_help=True,
    add_completion=False,
    help="Polder CLI: dataset van Nederlandse overheidsorganisaties, personen, posten en mandaten.",
)


@app.callback()
def _root(
    verbose: Annotated[
        bool,
        typer.Option(
            "-v",
            "--verbose",
            help="Verbose logging op alle subcommands.",
        ),
    ] = False,
) -> None:
    """Top-level opties die op alle subcommands gelden."""
    if verbose:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
        )
        os.environ["POLDER_VERBOSE"] = "1"


# ---------------------------------------------------------------------------
# Sub-apps (typer-trees)
# ---------------------------------------------------------------------------

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

app.add_typer(fetch_cmd.app, name="fetch")
app.add_typer(skill_cmd.app, name="skill")
app.add_typer(backfill_cmd.app, name="backfill")


# ---------------------------------------------------------------------------
# Single-shot commands
# ---------------------------------------------------------------------------

app.command("show")(show_cmd.show)
app.command("search")(search_cmd.search)
app.command("export")(export_cmd.export)
app.command("validate")(validate_cmd.validate)
app.command("audit")(audit_cmd.audit)
app.command("diff")(diff_cmd.diff)
app.command("build")(build_cmd.build)
app.command("serve")(serve_cmd.serve)
app.command("daily-update")(daily_cmd.daily_update)
app.command("apply-staging")(apply_staging_cmd.apply_staging)
app.command("resolve")(resolve_cmd.resolve)
app.command("ingest")(ingest_cmd.ingest)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
