"""`polder roo-roundtrip` command.

Bewijst dat polder een strict superset van ROO is: voor élk leaf-element in
de ROO-XML check dat zijn waarde ergens in de bijbehorende YAML aanwezig is.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from polder.roo_roundtrip import emit_field_map, format_report, run_roundtrip


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
    emit_field_map_to: Annotated[
        Path | None,
        typer.Option(
            "--emit-field-map",
            help="Schrijf veld-mapping markdown naar dit pad (bv. docs/roo_field_map.md).",
        ),
    ] = None,
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

    if emit_field_map_to is not None:
        emit_field_map_to.parent.mkdir(parents=True, exist_ok=True)
        emit_field_map_to.write_text(emit_field_map(report), encoding="utf-8")
        typer.echo(f"\nField-map geschreven naar {emit_field_map_to}", err=True)
