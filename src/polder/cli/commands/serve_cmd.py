"""`polder serve` command: start datasette op de gebouwde SQLite database."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Annotated

import typer


def serve(
    db: Annotated[
        Path, typer.Option(help="Pad naar de polder.db SQLite-database.")
    ] = Path("dist/polder.db"),
    metadata: Annotated[
        Path, typer.Option(help="Pad naar metadata.json.")
    ] = Path("metadata.json"),
    port: Annotated[int, typer.Option(help="Poort om op te luisteren.")] = 8001,
    host: Annotated[str, typer.Option(help="Host-binding.")] = "127.0.0.1",
) -> None:
    """Start datasette op `dist/polder.db` met de Polder-metadata."""
    if not db.exists():
        typer.echo(
            f"database niet gevonden: {db}. Run `polder build sqlite` eerst.",
            err=True,
        )
        raise typer.Exit(code=2)

    cmd = [
        "uv",
        "run",
        "datasette",
        str(db),
        "--port",
        str(port),
        "--host",
        host,
    ]
    if metadata.exists():
        cmd += ["-m", str(metadata)]

    typer.echo(f"+ {' '.join(cmd)}", err=True)
    proc = subprocess.run(cmd, check=False)
    raise typer.Exit(code=proc.returncode)
