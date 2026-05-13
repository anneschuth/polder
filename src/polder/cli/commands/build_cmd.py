"""`polder build <target>` command: bouw afgeleide formaten uit YAML.

Delegeert naar de bestaande `polder.build`-helpers; dezelfde semantiek als
`polder-build` maar met typer-vriendelijke flags.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from polder.build.to_csv import build_csv
from polder.build.to_datapackage import build_datapackage
from polder.build.to_sqlite import build_sqlite
from polder.build.to_viz import build_viz

VALID_TARGETS = {"sqlite", "csv", "datapackage", "viz", "all"}


def build(
    target: Annotated[
        str,
        typer.Argument(help="sqlite | csv | datapackage | viz | all"),
    ] = "all",
    data_dir: Annotated[Path, typer.Option("--data-dir", help="Source-of-truth data/-pad.")] = Path(
        "data"
    ),
    dist_dir: Annotated[Path, typer.Option("--dist-dir", help="Output dist/-pad.")] = Path("dist"),
) -> None:
    """Bouw `dist/polder.db`, `dist/csv/`, en/of `dist/datapackage.json`."""
    if target not in VALID_TARGETS:
        raise typer.BadParameter(
            f"onbekend target {target!r}. Kies uit: {', '.join(sorted(VALID_TARGETS))}."
        )
    dist_dir.mkdir(parents=True, exist_ok=True)

    if target in ("sqlite", "all"):
        out = dist_dir / "polder.db"
        build_sqlite(data_dir, out)
        typer.echo(f"wrote {out}")

    if target in ("csv", "all"):
        out_dir = dist_dir / "csv"
        build_csv(data_dir, out_dir)
        typer.echo(f"wrote {out_dir}/")

    if target in ("datapackage", "all"):
        csv_dir = dist_dir / "csv"
        out = dist_dir / "datapackage.json"
        build_datapackage(data_dir, csv_dir, out)
        typer.echo(f"wrote {out}")

    if target in ("viz", "all"):
        out_dir = dist_dir / "site"
        build_viz(data_dir, out_dir)
        typer.echo(f"wrote {out_dir}/")
