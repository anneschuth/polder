"""`polder skill <name>` commands.

Wrappers rond de bash-runners onder `scripts/`. Iedere subcommand is een
dunne `subprocess`-call; geen Python-port van de skill-logica.
"""

from __future__ import annotations

import shutil
import subprocess
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
