"""`polder dedup` command.

Deduplicate mandaten binnen persoon-yaml-records. Twee mandaten met
identieke (post_id, organization_id, start_date, end_date) tellen als
duplicaten — verschillende role-strings of source-id's worden gemerged.

Dit is een onderhouds-tool; idempotency-check in apply-staging zou
moeten voorkomen dat duplicates ontstaan, maar bestaande data kan ze
nog hebben uit eerdere imports.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
import yaml


def _key(m: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(m.get("post_id") or ""),
        str(m.get("organization_id") or ""),
        str(m.get("start_date") or ""),
        str(m.get("end_date") or ""),
    )


def _src_key(s: dict[str, Any]) -> tuple[str, str]:
    return (str(s.get("id") or ""), str(s.get("url") or ""))


def dedup_record(record: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Return (new_record, n_dups_collapsed). Merge sources van duplicates
    in de eerste van een groep."""
    mandaten = record.get("mandaten") or []
    if not mandaten:
        return record, 0
    groups: dict[tuple[str, str, str, str], list[int]] = {}
    for i, m in enumerate(mandaten):
        groups.setdefault(_key(m), []).append(i)
    n_dups = sum(len(v) - 1 for v in groups.values() if len(v) > 1)
    if n_dups == 0:
        return record, 0
    kept: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for i, m in enumerate(mandaten):
        k = _key(m)
        if k in seen:
            continue
        seen.add(k)
        merged = dict(m)
        merged_sources = list(merged.get("sources") or [])
        existing_src_keys = {_src_key(s) for s in merged_sources if isinstance(s, dict)}
        for dup_i in groups[k][1:]:
            for src in mandaten[dup_i].get("sources") or []:
                if isinstance(src, dict) and _src_key(src) not in existing_src_keys:
                    merged_sources.append(src)
                    existing_src_keys.add(_src_key(src))
        merged["sources"] = merged_sources
        kept.append(merged)
    new_record = dict(record)
    new_record["mandaten"] = kept
    return new_record, n_dups


def dedup(
    data_dir: Annotated[
        Path,
        typer.Option("--data", help="Pad naar data/ root."),
    ] = Path("data"),
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Toon wat zou worden gemerged zonder te schrijven."),
    ] = False,
) -> None:
    """Dedupliceer mandaten in alle persoon-yamls.

    Twee mandaten met identieke (post_id, organization_id, start_date,
    end_date) zijn duplicaten. Eerste wint, sources van overige worden
    in de eerste gemerged.
    """
    personen_dir = data_dir / "personen"
    if not personen_dir.exists():
        typer.echo(f"{personen_dir} bestaat niet.", err=True)
        raise typer.Exit(2)

    total_dups = 0
    n_files = 0
    for yp in sorted(personen_dir.glob("*.yaml")):
        try:
            d = yaml.safe_load(yp.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            continue
        if not isinstance(d, dict):
            continue
        new_d, n_dups = dedup_record(d)
        if n_dups > 0:
            total_dups += n_dups
            n_files += 1
            typer.echo(f"  {yp.name}: {n_dups} duplicate mandaten gemerged")
            if not dry_run:
                yp.write_text(
                    yaml.safe_dump(new_d, sort_keys=False, allow_unicode=True),
                    encoding="utf-8",
                )

    suffix = " (dry-run)" if dry_run else ""
    typer.echo(f"\n{total_dups} duplicate mandaten in {n_files} files{suffix}.")
