"""`polder skill <name>` commands.

Roepen Claude Code skills aan via `polder.llm.runner.run_skill`. Geen
subprocess naar shell-scripts; geen `bash` afhankelijkheid.

Output-pad-conventies (default `data/_staging/<bron>-<key>-<datum>.json`)
kwamen voorheen uit `scripts/parse_*_local.sh`. Die conventies zijn hier
geport zodat callers byte-identieke output-paden krijgen.

Pre-filters (`polder.llm.prefilters`) draaien voor de `parse-*`-skills:
input zonder personeels-signaal levert direct `[]` op zonder LLM-call.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import Annotated

import typer

from polder.llm import prefilters
from polder.llm.runner import run_skill
from polder.llm.session import RATE_LIMIT_EXIT_CODE

logger = logging.getLogger("polder.cli.skill")

app = typer.Typer(
    name="skill",
    no_args_is_help=True,
    add_completion=False,
    help="Roep een Claude Code skill aan via de in-process runner.",
)


def _repo_root() -> Path:
    """Project-root: package zit in `src/polder/cli/commands/`."""
    return Path(__file__).resolve().parents[4]


def _staging_dir() -> Path:
    return _repo_root() / "data" / "_staging"


def _today() -> str:
    return date.today().isoformat()


def _exit_for_result(result) -> None:
    """Map een SkillResult op een typer.Exit."""
    if result.rate_limited:
        typer.echo("rate-limit gedetecteerd, output NIET geschreven", err=True)
        raise typer.Exit(code=RATE_LIMIT_EXIT_CODE)
    if result.is_error:
        typer.echo(f"skill-fout: {result.error_message}", err=True)
        raise typer.Exit(code=1)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_empty_array(path: Path) -> None:
    _write_text(path, "[]\n")


@app.command("review-diff")
def review_diff(
    diff_path: Annotated[Path, typer.Argument(help="Pad naar diff.json.")],
    output: Annotated[
        Path | None,
        typer.Argument(help="Output markdown-pad (default: dist/pr-body.md)."),
    ] = None,
) -> None:
    """Genereer een PR-body markdown uit een diff.json."""
    if output is None:
        output = _repo_root() / "dist" / "pr-body.md"
    result = run_skill("review-pr-diff", diff_path, output=output)
    _exit_for_result(result)
    typer.echo(f"PR-body geschreven naar {output}", err=True)


@app.command("parse-staatscourant")
def parse_staatscourant(
    xml_path: Annotated[Path, typer.Argument(help="KB/Staatscourant XML-bestand.")],
    output: Annotated[
        Path | None,
        typer.Argument(help="Output JSON-pad (default: data/_staging/staatscourant-<key>-<datum>.json)."),
    ] = None,
) -> None:
    """Parse een Staatscourant-XML naar Membership-proposals."""
    if output is None:
        base = xml_path.stem
        output = _staging_dir() / f"staatscourant-{base}-{_today()}.json"

    xml_text = xml_path.read_text(encoding="utf-8")
    if not prefilters.staatscourant_has_signal(xml_text):
        _write_empty_array(output)
        typer.echo(f"pre-filter skip (geen-benoeming): {output}", err=True)
        return

    result = run_skill("parse-staatscourant", xml_path, output=output)
    _exit_for_result(result)
    typer.echo(f"proposals geschreven naar {output}", err=True)


@app.command("parse-abd-nieuws")
def parse_abd_nieuws(
    html_path: Annotated[Path, typer.Argument(help="ABD-nieuwsbericht HTML-bestand.")],
    output: Annotated[
        Path | None,
        typer.Argument(help="Output JSON-pad (default: data/_staging/abd-nieuws-<key>-<datum>.json)."),
    ] = None,
) -> None:
    """Parse een ABD-nieuwsbericht naar Membership-proposals."""
    if output is None:
        base = html_path.stem
        output = _staging_dir() / f"abd-nieuws-{base}-{_today()}.json"

    html_text = html_path.read_text(encoding="utf-8")
    if not prefilters.abd_nieuws_has_signal(html_text):
        _write_empty_array(output)
        typer.echo(f"pre-filter skip (geen-benoeming-marker): {output}", err=True)
        return

    result = run_skill("parse-abd-nieuws", html_path, output=output)
    _exit_for_result(result)
    typer.echo(f"proposals geschreven naar {output}", err=True)


@app.command("parse-organogram")
def parse_organogram(
    pdf_path: Annotated[Path, typer.Argument(help="ABD organogram-PDF.")],
    ministerie: Annotated[str, typer.Argument(help="Ministerie-slug, bv `min-bzk`.")],
    output: Annotated[
        Path | None,
        typer.Argument(help="Output JSON-pad (default: data/_staging/organogram-<min>-<datum>.json)."),
    ] = None,
) -> None:
    """Parse een ABD-organogram-PDF naar mandaat-proposals."""
    if output is None:
        output = _staging_dir() / f"organogram-{ministerie}-{_today()}.json"

    abs_pdf = pdf_path.resolve()
    prompt = (
        f"Ministerie: {ministerie}\n"
        f"PDF-pad: {abs_pdf}\n\n"
        f"Lees de PDF met de Read-tool en parse het organogram volgens de skill-instructies."
    )
    result = run_skill("parse-organogram", prompt, output=output)
    _exit_for_result(result)
    typer.echo(f"proposals geschreven naar {output}", err=True)


@app.command("entity-resolution")
def entity_resolution(
    input_path: Annotated[Path, typer.Argument(help="Input-JSON met kandidaten.")],
    output: Annotated[
        Path | None, typer.Argument(help="Output JSON-pad (default: stdout).")
    ] = None,
) -> None:
    """Run de entity-resolution skill op een input-JSON."""
    result = run_skill("entity-resolution", input_path, output=output)
    _exit_for_result(result)
    if output is None:
        typer.echo(result.text)
    else:
        typer.echo(f"output geschreven naar {output}", err=True)


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
        "retrieved": _today(),
    }

    if out is None:
        out = _staging_dir() / f"lookup-{_name_filename_slug(name)}.json"
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
    if output is None:
        output = input_path.with_suffix("").with_suffix(".resolved.json")
        if input_path.suffix == ".json":
            output = input_path.parent / f"{input_path.stem}.resolved.json"

    result = run_skill("resolve-staging-proposals", input_path, output=output)
    _exit_for_result(result)
    typer.echo(f"resolved naar {output}", err=True)
