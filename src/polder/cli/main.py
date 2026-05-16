"""Polder CLI entrypoint.

`polder` is het ENIGE entrypoint voor de polder-toolchain. Het bundelt:

- `polder fetch <bron>` over alle elf fetchers, plus `fetch all`
- `polder validate` / `diff` / `build` / `list` / `show` / `export`
- `polder skill <name>` voor de Claude Code skills (review-diff, parse-*)
- `polder daily-update` als shortcut naar de daily-update pipeline
- `polder serve` voor datasette op de gebouwde SQLite-database

De oude `polder-fetch-*`, `polder-validate`, `polder-diff` en `polder-build`
entrypoints in `pyproject.toml` blijven staan voor backwards-compatibility.

Subcommand-modules worden lazy geïmporteerd. `polder show person:...`
hoeft geen `bs4`/`lxml`/`httpx`/`pydantic`-fetchers te laden, dus die
imports gebeuren pas als je `polder fetch ...` aanroept. Dat scheelt
~100ms per call op de hot paths (show, search, list, validate).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Annotated

import typer

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
# Lazy command registration
# ---------------------------------------------------------------------------
#
# Importing every subcommand module up front pulls in bs4, lxml, httpx,
# concurrent.futures and all fetcher modules — ~100ms on the hot paths.
# We instead inspect argv and only register what the user asked for.
# `--help` and unknown args fall back to "register everything" so that
# typer can produce its full help text.

_SINGLE_COMMANDS: dict[str, tuple[str, str]] = {
    # name on app -> (module path, attribute)
    "show": ("polder.cli.commands.show_cmd", "show"),
    "search": ("polder.cli.commands.search_cmd", "search"),
    "export": ("polder.cli.commands.export_cmd", "export"),
    "validate": ("polder.cli.commands.validate_cmd", "validate"),
    "dedup": ("polder.cli.commands.dedup_cmd", "dedup"),
    "fix-casing": ("polder.cli.commands.fix_casing_cmd", "fix_casing"),
    "diff": ("polder.cli.commands.diff_cmd", "diff"),
    "build": ("polder.cli.commands.build_cmd", "build"),
    "serve": ("polder.cli.commands.serve_cmd", "serve"),
    "daily-update": ("polder.cli.commands.daily_cmd", "daily_update"),
    "apply-staging": ("polder.cli.commands.apply_staging_cmd", "apply_staging"),
    "resolve": ("polder.cli.commands.resolve_cmd", "resolve"),
    "ingest": ("polder.cli.commands.ingest_cmd", "ingest"),
}

_SUBAPPS: dict[str, tuple[str, str]] = {
    # name on app -> (module path, app attribute)
    "fetch": ("polder.cli.commands.fetch_cmd", "app"),
    "roo": ("polder.cli.commands.roo_cmd", "app"),
    "skill": ("polder.cli.commands.skill_cmd", "app"),
    "backfill": ("polder.cli.commands.backfill_cmd", "app"),
    "lookup": ("polder.cli.commands.lookup_cmd", "app"),
    "audit": ("polder.cli.commands.audit_cmd", "app"),
    "merge": ("polder.cli.commands.merge_cmd", "app"),
}

_LIST_COMMANDS: dict[str, tuple[str, str]] = {
    "organisaties": ("polder.cli.commands.list_cmd", "list_organisaties"),
    "personen": ("polder.cli.commands.list_cmd", "list_personen"),
    "posten": ("polder.cli.commands.list_cmd", "list_posten"),
    "mandaten": ("polder.cli.commands.list_cmd", "list_mandaten"),
}


def _register_single(name: str) -> None:
    import importlib

    mod_path, attr = _SINGLE_COMMANDS[name]
    mod = importlib.import_module(mod_path)
    app.command(name)(getattr(mod, attr))


def _register_subapp(name: str) -> None:
    import importlib

    mod_path, attr = _SUBAPPS[name]
    mod = importlib.import_module(mod_path)
    app.add_typer(getattr(mod, attr), name=name)


_list_app: typer.Typer | None = None


def _register_list_app(only: str | None = None) -> None:
    """Bouw de `polder list <subject>` boom. Met `only="personen"` wordt
    alleen dat ene leaf-command geladen — niet de hele list_cmd module."""
    global _list_app
    import importlib

    if _list_app is None:
        _list_app = typer.Typer(
            name="list",
            no_args_is_help=True,
            help="Lijst entiteiten (organisaties, personen, posten, mandaten).",
        )
        app.add_typer(_list_app, name="list")

    targets = [only] if only and only in _LIST_COMMANDS else list(_LIST_COMMANDS)
    seen_mods: dict[str, object] = {}
    for sub in targets:
        mod_path, attr = _LIST_COMMANDS[sub]
        mod = seen_mods.get(mod_path)
        if mod is None:
            mod = importlib.import_module(mod_path)
            seen_mods[mod_path] = mod
        _list_app.command(sub)(getattr(mod, attr))


def _register_all() -> None:
    for name in _SINGLE_COMMANDS:
        _register_single(name)
    for name in _SUBAPPS:
        _register_subapp(name)
    _register_list_app()


def _register_for_argv(argv: list[str]) -> None:
    """Bekijk argv[1:] en registreer alleen wat nodig is.

    Onbekende of help-achtige aanroepen krijgen de volledige boom.
    """
    args = [a for a in argv[1:] if not a.startswith("-")]
    if not args:
        _register_all()
        return

    first = args[0]
    if first == "list":
        sub = args[1] if len(args) > 1 else None
        _register_list_app(only=sub)
        return
    if first in _SUBAPPS:
        _register_subapp(first)
        return
    if first in _SINGLE_COMMANDS:
        _register_single(first)
        return

    # Unknown command: let typer produce its full error.
    _register_all()


_register_for_argv(sys.argv)


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
