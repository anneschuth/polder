"""`polder validate` command — dunne wrapper over `polder.validate`."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer


def validate(
    data: Annotated[Path, typer.Option(help="Data directory om te valideren.")] = Path("data"),
    schemas: Annotated[Path, typer.Option(help="Schemas directory.")] = Path("schemas"),
    strict: Annotated[bool, typer.Option(help="Exit non-zero ook bij alleen warnings.")] = False,
) -> None:
    """Valideer YAML-records tegen JSON Schema en cross-record regels."""
    from polder.validate import count_files, exit_code, print_report, run_all_checks

    issues = run_all_checks(data, schemas)
    n_files = count_files(data)
    print_report(issues, n_files)
    code = exit_code(issues, strict=strict)
    raise typer.Exit(code=code)
