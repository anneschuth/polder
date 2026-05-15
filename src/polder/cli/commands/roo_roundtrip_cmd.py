"""`polder roo-roundtrip` command.

Bewijst dat polder een strict superset van ROO is: voor élk leaf-element in
de ROO-XML check dat zijn waarde ergens in de bijbehorende YAML aanwezig is.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from polder.roo_roundtrip import format_report, run_roundtrip


def roo_roundtrip(
    xml: Annotated[
        Path,
        typer.Option(
            "--xml",
            help="Pad naar ROO-export XML (bijv. _cache/roo-export-2026-05-15.xml).",
        ),
    ],
    data: Annotated[
        Path,
        typer.Option("--data", help="Pad naar data/organisaties/ root."),
    ] = Path("data/organisaties"),
    top: Annotated[
        int,
        typer.Option("--top", help="Aantal slechtst-scorende velden te tonen."),
    ] = 30,
) -> None:
    """Round-trip reconstructie-test: verifieer dat élk ROO-leaf-veld in
    polder's YAMLs zit. Print coverage-rapport naar stdout."""
    if not xml.exists():
        typer.echo(f"XML-bestand bestaat niet: {xml}", err=True)
        raise typer.Exit(code=2)
    if not data.exists():
        typer.echo(f"Data-directory bestaat niet: {data}", err=True)
        raise typer.Exit(code=2)

    report = run_roundtrip(xml, data)
    typer.echo(format_report(report, top_n=top))
