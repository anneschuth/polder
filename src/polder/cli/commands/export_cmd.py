"""`polder export` command. MVP: CSV en JSON over alle vier de repos."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Annotated

import typer

from polder.lib import Polder


def _resolve_root(data: Path | None) -> Path:
    if data is not None:
        return data.resolve()
    cwd = Path.cwd()
    if (cwd / "data").exists():
        return cwd
    raise typer.BadParameter(f"Geen data/ in {cwd}. Gebruik --data om een polder-root op te geven.")


def _dump_csv(rows: list[dict], path: Path) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for k in row:
            if k not in fieldnames:
                fieldnames.append(k)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def export(
    target: Annotated[str, typer.Argument(help="csv of json.")],
    out: Annotated[Path, typer.Argument(help="Output directory of bestand.")],
    data: Annotated[Path | None, typer.Option(help="Polder root.")] = None,
) -> None:
    """Exporteer alle entiteiten naar `out`. Voor MVP alleen csv en json."""
    if target not in {"csv", "json"}:
        raise typer.BadParameter(f"Onbekend target {target!r}. MVP ondersteunt: csv, json.")
    root = _resolve_root(data)
    p = Polder.local(root)

    out.mkdir(parents=True, exist_ok=True)

    repos = {
        "organisaties": [
            o.model_dump(mode="json", exclude_none=True) for o in p.organisaties.all()
        ],
        "personen": [x.model_dump(mode="json", exclude_none=True) for x in p.personen.all()],
        "posten": [x.model_dump(mode="json", exclude_none=True) for x in p.posten.all()],
        "mandaten": [m.model_dump(mode="json", exclude_none=True) for m in p.mandaten.all()],
    }

    if target == "json":
        for name, rows in repos.items():
            path = out / f"{name}.json"
            path.write_text(
                json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
            typer.echo(f"wrote {path} ({len(rows)} rows)")
    else:  # csv
        for name, rows in repos.items():
            # Flatten one level: only top-level scalar fields go into CSV.
            flat = []
            for row in rows:
                flat.append({k: v for k, v in row.items() if not isinstance(v, (dict, list))})
            path = out / f"{name}.csv"
            _dump_csv(flat, path)
            typer.echo(f"wrote {path} ({len(flat)} rows)")
