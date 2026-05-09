"""`polder skill <name>` commands.

Wrappers rond de bash-runners onder `scripts/`. Iedere subcommand is een
dunne `subprocess`-call; geen Python-port van de skill-logica.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unicodedata
from datetime import date
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(
    name="skill",
    no_args_is_help=True,
    add_completion=False,
    help="Roep een Claude Code skill aan via de scripts/-runners.",
)


def _repo_root() -> Path:
    """Project-root: package zit in `src/polder/cli/commands/`."""
    return Path(__file__).resolve().parents[4]


def _scripts_dir() -> Path:
    return _repo_root() / "scripts"


def _run(cmd: list[str]) -> None:
    typer.echo(f"+ {' '.join(cmd)}", err=True)
    proc = subprocess.run(cmd, check=False)
    raise typer.Exit(code=proc.returncode)


def _ensure_bash() -> str:
    bash = shutil.which("bash")
    if not bash:
        raise typer.BadParameter("bash niet gevonden in PATH.")
    return bash


@app.command("review-diff")
def review_diff(
    diff_path: Annotated[Path, typer.Argument(help="Pad naar diff.json.")],
    output: Annotated[
        Path | None,
        typer.Argument(help="Output markdown-pad (default: dist/pr-body.md)."),
    ] = None,
) -> None:
    """Genereer een PR-body markdown uit een diff.json."""
    bash = _ensure_bash()
    script = _scripts_dir() / "review_pr_diff_local.sh"
    cmd = [bash, str(script), str(diff_path)]
    if output is not None:
        cmd.append(str(output))
    _run(cmd)


@app.command("parse-staatscourant")
def parse_staatscourant(
    xml_path: Annotated[Path, typer.Argument(help="KB/Staatscourant XML-bestand.")],
    output: Annotated[
        Path | None,
        typer.Argument(help="Output JSON-pad (default: data/_staging/staatscourant-<datum>.json)."),
    ] = None,
) -> None:
    """Parse een Staatscourant-XML naar Membership-proposals."""
    bash = _ensure_bash()
    script = _scripts_dir() / "parse_staatscourant_local.sh"
    cmd = [bash, str(script), str(xml_path)]
    if output is not None:
        cmd.append(str(output))
    _run(cmd)


@app.command("parse-abd-nieuws")
def parse_abd_nieuws(
    html_path: Annotated[Path, typer.Argument(help="ABD-nieuwsbericht HTML-bestand.")],
    output: Annotated[
        Path | None,
        typer.Argument(help="Output JSON-pad (default: data/_staging/abd-nieuws-<datum>.json)."),
    ] = None,
) -> None:
    """Parse een ABD-nieuwsbericht naar Membership-proposals."""
    bash = _ensure_bash()
    script = _scripts_dir() / "parse_abd_nieuws_local.sh"
    cmd = [bash, str(script), str(html_path)]
    if output is not None:
        cmd.append(str(output))
    _run(cmd)


@app.command("parse-organogram")
def parse_organogram(
    pdf_path: Annotated[Path, typer.Argument(help="ABD organogram-PDF.")],
    ministerie: Annotated[
        str, typer.Argument(help="Ministerie-slug, bv `min-bzk`.")
    ],
    output: Annotated[
        Path | None,
        typer.Argument(help="Output JSON-pad (default: data/_staging/organogram-<min>-<datum>.json)."),
    ] = None,
) -> None:
    """Parse een ABD-organogram-PDF naar mandaat-proposals."""
    bash = _ensure_bash()
    script = _scripts_dir() / "parse_organogram_local.sh"
    cmd = [bash, str(script), str(pdf_path), ministerie]
    if output is not None:
        cmd.append(str(output))
    _run(cmd)


@app.command("entity-resolution")
def entity_resolution(
    input_path: Annotated[Path, typer.Argument(help="Input-JSON met kandidaten.")],
    output: Annotated[
        Path | None, typer.Argument(help="Output JSON-pad (default: stdout).")
    ] = None,
) -> None:
    """Run de entity-resolution skill op een input-JSON."""
    bash = _ensure_bash()
    script = _scripts_dir() / "run_skill.sh"
    cmd = [bash, str(script), "entity-resolution", str(input_path)]
    if output is not None:
        cmd.append(str(output))
    _run(cmd)


def _name_filename_slug(name: str) -> str:
    """Bouw een veilig bestandsnaam-fragment uit een persoonsnaam."""
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_name = decomposed.encode("ascii", "ignore").decode("ascii").lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")
    return cleaned or "onbekend"


def _split_name(name: str) -> tuple[str, str | None, str | None]:
    """Splits 'Mark Rutte' naar (family, given, initials).

    Heuristiek: laatste woord = family, eerste = given. Een token van max 4
    hoofdletters met punten wordt als initialen herkend.
    """
    tokens = [t for t in name.strip().split() if t]
    if not tokens:
        raise typer.BadParameter("Lege naam-string.")
    if len(tokens) == 1:
        return tokens[0], None, None
    family = tokens[-1]
    rest = tokens[:-1]
    given: str | None = None
    initials: str | None = None
    for tok in rest:
        compact = re.sub(r"[^A-Za-z]", "", tok)
        if compact and compact == compact.upper() and len(compact) <= 4:
            initials = "".join(f"{ch.upper()}." for ch in compact)
        elif given is None:
            given = tok
    return family, given, initials


@app.command("lookup-person")
def lookup_person(
    name: Annotated[str, typer.Argument(help='Volledige naam, bv "Suzie Kewal".')],
    organization: Annotated[
        str | None,
        typer.Option("--organization", "-o", help="Optionele organisatie-context."),
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option(
            "--out", help="Output JSON-pad (default: data/_staging/lookup-<naam>.json)."
        ),
    ] = None,
    endpoint: Annotated[
        str,
        typer.Option(
            "--endpoint",
            help="SPARQL-endpoint: 'qlever' (default) of 'wdqs'.",
        ),
    ] = "qlever",
    cache: Annotated[
        Path,
        typer.Option(help="Cache-directory voor SPARQL-responses."),
    ] = Path("_cache/wikidata-personen"),
) -> None:
    """Zoek persoon in Wikidata, retourneer kandidaten plus geboortejaar."""
    from polder.fetchers.wikidata_sparql import lookup_person_by_name

    family, given, initials = _split_name(name)
    candidates = lookup_person_by_name(
        family,
        initials=initials,
        given=given,
        endpoint=endpoint,
        cache_dir=cache,
    )

    payload = {
        "input": {
            "name": {"raw": name, "family": family, "given": given, "initials": initials},
            "organization": organization,
        },
        "candidates": candidates,
        "retrieved": date.today().isoformat(),
    }

    if out is None:
        out = (
            _repo_root()
            / "data"
            / "_staging"
            / f"lookup-{_name_filename_slug(name)}.json"
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    typer.echo(f"Wrote {len(candidates)} candidates to {out}")


@app.command("resolve-staging")
def resolve_staging(
    input_path: Annotated[Path, typer.Argument(help="Pad naar staging-file in data/_staging/.")],
    output: Annotated[
        Path | None,
        typer.Option(
            "--out",
            "-o",
            help="Output-pad (default: <input-stem>.resolved.json naast de input).",
        ),
    ] = None,
) -> None:
    """Match staging-proposals aan bestaande records in `data/`."""
    bash = _ensure_bash()
    script = _scripts_dir() / "resolve_staging_local.sh"
    cmd = [bash, str(script), str(input_path)]
    if output is not None:
        cmd.append(str(output))
    _run(cmd)
