"""`polder roo` subapp: de volledige ROO-pipeline onder één command.

Consolideert wat eerst verspreid zat over `polder fetch roo`,
`polder fetch roo-functies`, `polder resolve-roo` en
`polder roo-roundtrip`. Dezelfde verb-noun-conventie als `polder fetch`
en `polder audit`.

```
polder roo fetch        # exportOO.xml → data/organisaties/
polder roo functies     # functies + medewerkers → data/_staging/ (proposals)
polder roo resolve      # proposals → posten/personen (2 auto-merge lanes)
polder roo roundtrip    # superset-claim verifiëren (XML ⊆ YAML)
```
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(
    name="roo",
    no_args_is_help=True,
    add_completion=False,
    help="ROO-pipeline: fetch organisaties/functies, resolve, roundtrip.",
)


def _delegate(fn, argv: list[str]) -> None:
    from lxml.etree import XMLSyntaxError

    try:
        code = fn(argv)
    except XMLSyntaxError as exc:
        typer.echo(
            f"ROO-cache XML is corrupt of afgekapt: {exc}. "
            f"Verwijder het cache-bestand en draai opnieuw.",
            err=True,
        )
        raise typer.Exit(code=2) from exc
    raise typer.Exit(code=code or 0)


@app.command("fetch")
def roo_fetch(
    cache: Annotated[Path, typer.Option(help="Cache-directory voor downloads.")] = Path("_cache"),
    out: Annotated[Path, typer.Option(help="Output-directory.")] = Path("data/organisaties"),
    limit: Annotated[int | None, typer.Option(help="Max records (voor testen).")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Niets schrijven.")] = False,
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Verbose logging.")] = False,
) -> None:
    """Download exportOO.xml en schrijf organisatie-records (incl. GR-tree)."""
    from polder.fetchers import roo

    argv: list[str] = ["--cache", str(cache), "--out", str(out)]
    if limit is not None:
        argv += ["--limit", str(limit)]
    if dry_run:
        argv.append("--dry-run")
    if verbose:
        argv.append("-v")
    _delegate(roo.main, argv)


@app.command("functies")
def roo_functies(
    cache: Annotated[Path, typer.Option(help="Cache-directory voor downloads.")] = Path("_cache"),
    out: Annotated[Path, typer.Option(help="Staging-directory.")] = Path("data/_staging"),
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Verbose logging.")] = False,
) -> None:
    """Functies + medewerkers naar staging-proposals (geen auto-merge).

    Verwerken via `polder roo resolve`."""
    from polder.fetchers import roo_functies

    argv: list[str] = ["--cache", str(cache), "--out", str(out)]
    if verbose:
        argv.append("-v")
    _delegate(roo_functies.main, argv)


@app.command("resolve")
def roo_resolve(
    proposals: Annotated[
        Path,
        typer.Argument(help="Pad naar `roo-functies-YYYY-MM-DD.json`."),
    ],
    data: Annotated[Path, typer.Option("--data", help="Polder data-root.")] = Path("data"),
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Niets schrijven.")] = False,
    verbose: Annotated[bool, typer.Option("-v", "--verbose")] = False,
) -> None:
    """Resolve ROO functie/medewerker-proposals naar bestaande posts/personen.

    Twee auto-merge lanes (post enrichment + mandaat bevestiging — beide
    additief, geen nieuw benoemingsfeit). Nieuwe benoemingen, ambigue
    matches en onbekende personen/posten gaan naar
    `data/_staging/<input-stem>.unresolved.json`."""
    from polder.resolve_roo import resolve

    if not proposals.exists():
        typer.echo(f"Proposals-file bestaat niet: {proposals}", err=True)
        raise typer.Exit(code=2)
    if not data.exists():
        typer.echo(f"Data-directory bestaat niet: {data}", err=True)
        raise typer.Exit(code=2)
    stats, _staging = resolve(proposals, data, dry_run=dry_run)
    label = "dry-run" if dry_run else "wrote changes"
    typer.echo(f"=== ROO resolve-stats ({label}) ===", err=True)
    typer.echo(f"  posts enriched:       {stats.posts_enriched}", err=True)
    typer.echo(f"  mandaten confirmed:   {stats.mandaten_confirmed}", err=True)
    typer.echo(f"  person not found:     {stats.person_not_found}", err=True)
    typer.echo(f"  person ambiguous:     {stats.person_ambiguous}", err=True)
    typer.echo(f"  post not found:       {stats.post_not_found}", err=True)
    typer.echo(f"  skipped (no org):     {stats.skipped_no_org}", err=True)
    typer.echo(f"  → staging:            {stats.proposals_to_staging}", err=True)


@app.command("roundtrip")
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
    from lxml.etree import XMLSyntaxError

    from polder.roo_roundtrip import emit_field_map, format_report, run_roundtrip

    if not xml.exists():
        typer.echo(f"XML-bestand bestaat niet: {xml}", err=True)
        raise typer.Exit(code=2)
    if not data.exists():
        typer.echo(f"Data-directory bestaat niet: {data}", err=True)
        raise typer.Exit(code=2)

    try:
        report = run_roundtrip(xml, data)
    except XMLSyntaxError as exc:
        typer.echo(
            f"ROO-cache XML is corrupt of afgekapt: {exc}. "
            f"Verwijder het cache-bestand en draai opnieuw.",
            err=True,
        )
        raise typer.Exit(code=2) from exc
    typer.echo(format_report(report, top_n=top))

    if emit_field_map_to is not None:
        emit_field_map_to.parent.mkdir(parents=True, exist_ok=True)
        emit_field_map_to.write_text(emit_field_map(report), encoding="utf-8")
        typer.echo(f"\nField-map geschreven naar {emit_field_map_to}", err=True)
