"""`polder audit` command + subcommands.

Diepe data-audit op `data/` met findings die de schema-validator niet vangt.
Toont errors en review-items gescheiden. Geverifieerde findings (uit
`data/_audit/verified.yaml`) worden standaard gefilterd.

Subcommand `polder audit verify <category> <key>` voegt een entry toe aan
`data/_audit/verified.yaml` zodat de finding niet meer getoond wordt.
"""

from __future__ import annotations

import os
import subprocess
from datetime import date
from pathlib import Path
from typing import Annotated

import typer
import yaml

app = typer.Typer(
    name="audit",
    no_args_is_help=False,
    add_completion=False,
    invoke_without_command=True,
    help="Run diepe data-audit op `data/`.",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _git_user() -> str:
    """Vraag git config voor user.name; valt terug op $USER."""
    try:
        result = subprocess.run(
            ["git", "config", "user.name"], capture_output=True, text=True, check=False
        )
        name = result.stdout.strip()
        if name:
            return name
    except FileNotFoundError:
        pass
    return os.environ.get("USER", "unknown")


@app.callback()
def audit_main(
    ctx: typer.Context,
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
    show_keys: Annotated[
        bool,
        typer.Option(
            "--show-keys",
            help="Toon de finding-key (handig voor `polder audit verify`).",
        ),
    ] = False,
) -> None:
    """Run diepe data-audit op `data/`."""
    if ctx.invoked_subcommand is not None:
        return

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
        return

    by_cat = report.by_category()

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
            c for c in categories if c in CATEGORIES and CATEGORIES[c].severity == severity
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
            if show_keys:
                typer.echo(f"  [{item.key}] {item.message}")
            else:
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


@app.command("verify")
def verify(
    category: Annotated[str, typer.Argument(help="Audit-categorie (bv. quasi_dup_family_birth).")],
    key: Annotated[str, typer.Argument(help="Finding-key zoals getoond door `polder audit`.")],
    note: Annotated[
        str,
        typer.Option("--note", "-n", help="Korte uitleg waarom dit ok is."),
    ],
    verified_by: Annotated[
        str | None,
        typer.Option("--by", help="Username (default: git config user.name)."),
    ] = None,
    data_dir: Annotated[
        Path,
        typer.Option("--data", help="Pad naar data/ root."),
    ] = Path("data"),
) -> None:
    """Markeer een audit-finding als geverifieerd-ok.

    Voegt een entry toe aan `data/_audit/verified.yaml`. De finding wordt
    dan standaard niet meer getoond door `polder audit` (toon met
    `--include-verified`).
    """
    if not data_dir.is_absolute():
        data_dir = _repo_root() / data_dir
    if not data_dir.exists():
        typer.echo(f"data-dir niet gevonden: {data_dir}", err=True)
        raise typer.Exit(code=2)

    verified_path = data_dir / "_audit" / "verified.yaml"
    verified_path.parent.mkdir(parents=True, exist_ok=True)

    if verified_path.exists():
        raw = yaml.safe_load(verified_path.read_text(encoding="utf-8")) or {}
    else:
        raw = {}
    entries = raw.get("verified") or []

    for entry in entries:
        if (
            isinstance(entry, dict)
            and entry.get("category") == category
            and entry.get("key") == key
        ):
            typer.echo(f"Reeds geverifieerd: {category} / {key}")
            raise typer.Exit(code=0)

    by = verified_by or _git_user()
    new_entry = {
        "category": category,
        "key": key,
        "note": note,
        "verified_at": date.today().isoformat(),
        "verified_by": by,
    }
    entries.append(new_entry)
    raw["verified"] = entries
    verified_path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    typer.echo(f"Toegevoegd: {category} / {key} (note: {note})")
