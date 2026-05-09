"""`polder diff` command: dunne wrapper rond `polder.diff:main`."""

from __future__ import annotations

from typing import Annotated

import typer


def diff(
    args: Annotated[
        list[str] | None,
        typer.Argument(
            help="Argumenten die naar `polder-diff` worden doorgegeven (bv `--cache _cache --data data`).",
        ),
    ] = None,
) -> None:
    """Vergelijk `_cache/` met `data/` en schrijf `diff.json` + `proposals.json`."""
    from polder.diff import main as diff_main

    code = diff_main(list(args or []))
    raise typer.Exit(code=code or 0)
