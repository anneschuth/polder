"""`polder apply-staging` command.

Pas resolver-output uit `data/_staging/*.resolved.json` automatisch toe op
`data/`. Default is dry-run; pas met `--apply` echt aan.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from polder.apply import (
    ApplyAction,
    SkippedProposal,
    execute_apply,
    load_resolved_input,
    plan_apply,
)


def _icon(action_type: str) -> str:
    return {
        "create-org": "+",
        "create-post": "+",
        "create-person": "+",
        "append-mandaat": "~",
    }.get(action_type, "?")


def _print_plan(
    actions: list[ApplyAction],
    skipped: list[SkippedProposal],
    *,
    input_label: str,
) -> None:
    typer.echo(f"Apply-staging analyse voor {input_label}:")
    typer.echo("")
    if actions:
        typer.echo("Zou aanmaken / aanpassen:")
        for a in actions:
            typer.echo(f"  {_icon(a.type)} {a.target_path}  [{a.type}, conf={a.confidence:.2f}]")
            if a.type == "create-org":
                typer.echo(f"    parent: {a.record.get('parent_id')}, type: {a.record.get('type')}")
            elif a.type == "create-post":
                typer.echo(
                    f"    organization_id: {a.record.get('organization_id')}, "
                    f"classification: {a.record.get('classification')}"
                )
            elif a.type == "create-person":
                man = a.record.get("mandaten") or []
                typer.echo(f"    name: {a.record.get('name', {}).get('full')}, {len(man)} mandaat")
            elif a.type == "append-mandaat":
                man = a.record.get("mandaten") or []
                typer.echo(f"    nieuw mandaat-totaal: {len(man)}")
            for r in a.reasons:
                typer.echo(f"    > {r}")
    else:
        typer.echo("Zou aanmaken / aanpassen:")
        typer.echo("  (geen)")
    typer.echo("")
    if skipped:
        typer.echo("Zou skippen:")
        for s in skipped:
            label = s.proposal.get("person_name") or s.proposal.get("post_id", "<unknown>")
            typer.echo(f"  - {label}")
            for r in s.reasons:
                typer.echo(f"    > {r}")
    else:
        typer.echo("Zou skippen:")
        typer.echo("  (geen)")
    typer.echo("")
    typer.echo(f"{len(actions)} records auto-mergeable, {len(skipped)} needs-review/skip.")


def apply_staging(
    input: Annotated[
        Path,
        typer.Argument(help="Pad naar .resolved.json of map met meerdere .resolved.json."),
    ],
    apply: Annotated[
        bool,
        typer.Option("--apply", help="Schrijf echt naar data/. Default is dry-run."),
    ] = False,
    skip_persons: Annotated[
        bool,
        typer.Option(help="Alleen orgs en posts toevoegen, persons skippen."),
    ] = False,
    only_high_confidence: Annotated[
        bool,
        typer.Option(help="Alleen confidence >= 0.95 toepassen."),
    ] = False,
    data: Annotated[Path, typer.Option(help="Polder data root.")] = Path("data"),
) -> None:
    """Pas resolver-output automatisch toe op `data/`. Default is dry-run."""
    if not input.exists():
        typer.echo(f"apply-staging: input niet gevonden: {input}", err=True)
        raise typer.Exit(code=2)

    proposals = load_resolved_input(input)
    if not proposals:
        typer.echo("apply-staging: geen proposals gevonden in input.", err=True)
        raise typer.Exit(code=1)

    actions, skipped = plan_apply(
        proposals,
        data,
        only_high_confidence=only_high_confidence,
        skip_persons=skip_persons,
    )

    _print_plan(actions, skipped, input_label=str(input))

    if not apply:
        typer.echo("")
        typer.echo("Run met --apply om echt te schrijven.")
        raise typer.Exit(code=0)

    written = execute_apply(actions, data)
    typer.echo("")
    typer.echo(f"Geschreven: {written} files.")

    # Roep validate aan op de data-tree.
    from polder.validate import exit_code, run_all_checks

    schemas = (data.parent / "schemas") if data.name == "data" else Path("schemas")
    if not schemas.exists():
        schemas = Path("schemas")
    issues = run_all_checks(data, schemas)
    code = exit_code(issues, strict=False)
    if code != 0:
        typer.echo("apply-staging: validate signaleerde problemen.", err=True)
        raise typer.Exit(code=code)
    typer.echo("apply-staging: validate clean.")
