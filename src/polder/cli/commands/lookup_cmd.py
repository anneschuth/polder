"""`polder lookup <bron>` commands.

Doelgroep: skills en scripts die snel één persoon (of straks: org, post)
willen opzoeken in een externe bron. Output is altijd JSON op stdout zodat
caller-side parsing triviaal blijft.

Voor v1 alleen `polder lookup wikidata`. Volgende bronnen kunnen er onder
gehangen worden naarmate de skill-fallback-pad ze nodig heeft.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(
    name="lookup",
    no_args_is_help=True,
    add_completion=False,
    help="Eén-shot lookups in externe bronnen (Wikidata, ...).",
)


def _normalize_for_age_filter(year: int) -> bool:
    """ABD-functionaris is plausibel 18-80. Sluit naamgenoten uit (historisch,
    kind)."""
    from datetime import date

    age = date.today().year - year
    return 18 <= age <= 80


@app.command("wikidata")
def lookup_wikidata(
    name: Annotated[str, typer.Option(help="Volledige naam zoals in de bron, bv 'Esther van Deursen'.")],
    role: Annotated[str | None, typer.Option(help="Optionele rol-context voor de skill om in te wegen.")] = None,
    org: Annotated[str | None, typer.Option(help="Optionele organisatie-context.")] = None,
    plausible_age_only: Annotated[
        bool,
        typer.Option(help="Filter kandidaten op leeftijd 18-80; default uit, skill kan zelf kiezen."),
    ] = False,
    cache_dir: Annotated[Path | None, typer.Option(help="Cache-dir voor SPARQL-results.")] = None,
) -> None:
    """Zoek personen in Wikidata op naam. Print JSON met kandidaten op stdout.

    Output-formaat:
    ```
    {
      "name": "Esther van Deursen",
      "parsed": {"family": "deursen", "given": "esther", "initials": null},
      "candidates": [
        {"qid": "Q...", "label": "...", "birth_year": 1972, "description": "..."}
      ]
    }
    ```

    De skill gebruikt dit om disambigueren of birth_year toevoegen. Geen
    filtering of scoring; ranking is aan de caller (de skill weet de context).
    """
    from polder.fetchers.wikidata_sparql import lookup_person_by_name
    from polder.resolve.names import parse_person_name

    parsed = parse_person_name(name)
    if not parsed.family:
        typer.echo(
            json.dumps({"name": name, "error": "geen familienaam in input"}, ensure_ascii=False),
        )
        raise typer.Exit(code=2)

    try:
        candidates = lookup_person_by_name(
            parsed.family,
            initials=parsed.initials,
            given=parsed.given,
            cache_dir=cache_dir,
        )
    except Exception as exc:  # network of parsing-issue: niet fataal voor de skill
        typer.echo(
            json.dumps(
                {"name": name, "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            ),
        )
        raise typer.Exit(code=1) from exc

    if plausible_age_only:
        candidates = [
            c
            for c in candidates
            if isinstance(c.get("birth_year"), int) and _normalize_for_age_filter(c["birth_year"])
        ]

    payload = {
        "name": name,
        "role_hint": role,
        "org_hint": org,
        "parsed": {
            "family": parsed.family,
            "given": parsed.given,
            "initials": parsed.initials,
        },
        "candidates": candidates,
    }
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
