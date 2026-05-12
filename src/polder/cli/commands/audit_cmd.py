"""`polder audit` command.

Diepe data-audit op `data/` met findings die de schema-validator niet vangt.
Toont errors en review-items gescheiden. Geverifieerde findings (uit
`data/_audit/verified.yaml`) worden standaard gefilterd.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def audit(
    data_dir: Annotated[
        Path,
        typer.Option("--data", help="Pad naar data/ root."),
    ] = Path("data"),
    category: Annotated[
        str | None,
        typer.Option(
            "--category",
            "-c",
            help="Filter op één categorie (bv. `start_after_end`).",
        ),
    ] = None,
    severity: Annotated[
        str | None,
        typer.Option(
            "--severity",
            "-s",
            help="Filter op severity: error | review.",
        ),
    ] = None,
    max_per_category: Annotated[
        int,
        typer.Option(
            "--max-per-category",
            help="Toon maximaal N findings per categorie.",
        ),
    ] = 10,
    explain: Annotated[
        bool,
        typer.Option("--explain", help="Toon een korte uitleg per categorie."),
    ] = False,
    strict: Annotated[
        bool,
        typer.Option(
            "--strict",
            help="Exit non-zero als er error-findings zijn (voor CI-gebruik).",
        ),
    ] = False,
    include_verified: Annotated[
        bool,
        typer.Option(
            "--include-verified",
            help="Toon ook geverifieerde findings (zijn standaard uitgefilterd).",
        ),
    ] = False,
) -> None:
    """Run diepe data-audit op `data/`."""
    from polder.audit import CATEGORIES, run_audit, summary

    if not data_dir.is_absolute():
        data_dir = _repo_root() / data_dir
    if not data_dir.exists():
        typer.echo(f"data-dir niet gevonden: {data_dir}", err=True)
        raise typer.Exit(code=2)

    if severity is not None and severity not in ("error", "review"):
        raise typer.BadParameter("--severity moet 'error' of 'review' zijn.")

    report = run_audit(data_dir, apply_whitelist=not include_verified)
    n_cats, n_findings = summary(report)

    typer.echo(f"=== audit ({data_dir}) ===")
    typer.echo(f"  categorieën met findings: {n_cats}")
    typer.echo(f"  totaal findings:          {n_findings}")
    if report.verified_skipped:
        typer.echo(
            f"  geverifieerd (gefilterd): {report.verified_skipped} "
            f"(gebruik --include-verified om te tonen)"
        )
    typer.echo("")

    if n_findings == 0:
        typer.echo("Geen inconsistenties gevonden.")
        if strict:
            return
        return

    by_cat = report.by_category()
    # Sort: severity (error voor review), dan alfabetisch
    def _sort_key(cat: str) -> tuple[int, str]:
        sev = CATEGORIES.get(cat).severity if cat in CATEGORIES else "error"
        return (0 if sev == "error" else 1, cat)

    categories = sorted(by_cat.keys(), key=_sort_key)
    if category is not None:
        if category not in by_cat:
            typer.echo(f"Geen findings voor categorie '{category}'.")
            typer.echo(f"Beschikbaar: {', '.join(categories)}")
            raise typer.Exit(code=0)
        categories = [category]
    if severity is not None:
        categories = [
            c for c in categories
            if c in CATEGORIES and CATEGORIES[c].severity == severity
        ]
        if not categories:
            typer.echo(f"Geen findings met severity '{severity}'.")
            raise typer.Exit(code=0)

    current_sev: str | None = None
    for cat in categories:
        cat_meta = CATEGORIES.get(cat)
        sev = cat_meta.severity if cat_meta else "error"
        if sev != current_sev:
            current_sev = sev
            label = "ERRORS" if sev == "error" else "REVIEW (mogelijk legitiem)"
            typer.echo(f"--- {label} ---")
            typer.echo("")

        items = by_cat[cat]
        typer.echo(f"{cat}: {len(items)}")
        if explain and cat_meta:
            typer.echo(f"  → {cat_meta.help}")
        for item in items[:max_per_category]:
            typer.echo(f"  {item.message}")
        if len(items) > max_per_category:
            typer.echo(f"  ... +{len(items) - max_per_category} meer")
        typer.echo("")

    if strict:
        n_errors = sum(
            1
            for f in report.findings
            if (CATEGORIES.get(f.category).severity if f.category in CATEGORIES else "error")
            == "error"
        )
        if n_errors:
            raise typer.Exit(code=1)
