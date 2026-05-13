"""`polder fetch <bron>` commands.

Dunne wrappers rond de bestaande fetcher-modules onder `polder.fetchers.*`.
Iedere subcommand bouwt een `argv`-lijst en delegeert naar de `main()` van
de bijbehorende fetcher; geen nieuwe logica.

`polder fetch all` draait de deterministische fetchers sequentieel
(geen ABD/KOOP, omdat die credentials of prompting vragen).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer

from polder.fetchers import (
    abd_nieuws,
    abd_organogrammen,
    ar_rwt,
    ek_scrape,
    kiesraad,
    koop_sru,
    logius_cor,
    open_raadsinformatie,
    roo,
    tk_odata,
    tooi,
    wikidata_sparql,
)

app = typer.Typer(
    name="fetch",
    no_args_is_help=True,
    add_completion=False,
    help="Haal data op uit externe bronnen (ROO, TK, EK, Logius, Wikidata, ...).",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _common_argv(
    cache: Path | None,
    out: Path | None,
    limit: int | None,
    dry_run: bool,
    verbose: bool,
) -> list[str]:
    argv: list[str] = []
    if cache is not None:
        argv += ["--cache", str(cache)]
    if out is not None:
        argv += ["--out", str(out)]
    if limit is not None:
        argv += ["--limit", str(limit)]
    if dry_run:
        argv.append("--dry-run")
    if verbose:
        argv.append("-v")
    return argv


def _delegate(fn: Callable[[list[str]], int], argv: list[str]) -> None:
    code = fn(argv)
    raise typer.Exit(code=code or 0)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


@app.command("roo")
def fetch_roo(
    cache: Annotated[Path, typer.Option(help="Cache-directory voor downloads.")] = Path("_cache"),
    out: Annotated[Path, typer.Option(help="Output-directory.")] = Path("data/organisaties"),
    limit: Annotated[int | None, typer.Option(help="Max records (voor testen).")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Niets schrijven.")] = False,
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Verbose logging.")] = False,
) -> None:
    """ROO: download exportOO.xml en schrijf organisatie-records."""
    _delegate(roo.main, _common_argv(cache, out, limit, dry_run, verbose))


@app.command("tk")
def fetch_tk(
    cache: Annotated[Path, typer.Option(help="Cache-directory voor downloads.")] = Path("_cache"),
    out: Annotated[Path, typer.Option(help="Output-directory.")] = Path("data/personen"),
    limit: Annotated[int | None, typer.Option(help="Max records.")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Niets schrijven.")] = False,
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Verbose logging.")] = False,
) -> None:
    """Tweede Kamer: OData-feed met huidige en oud-Kamerleden."""
    _delegate(tk_odata.main, _common_argv(cache, out, limit, dry_run, verbose))


@app.command("ek")
def fetch_ek(
    cache: Annotated[Path, typer.Option(help="Cache-directory voor downloads.")] = Path("_cache"),
    out: Annotated[Path, typer.Option(help="Output-directory.")] = Path("data/personen"),
    limit: Annotated[int | None, typer.Option(help="Max records.")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Niets schrijven.")] = False,
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Verbose logging.")] = False,
) -> None:
    """Eerste Kamer: scrape leden via eerstekamer.nl."""
    _delegate(ek_scrape.main, _common_argv(cache, out, limit, dry_run, verbose))


@app.command("logius")
def fetch_logius(
    cache: Annotated[Path, typer.Option(help="Cache-directory voor downloads.")] = Path("_cache"),
    out: Annotated[Path, typer.Option(help="Output-directory.")] = Path("data/personen"),
    limit: Annotated[int | None, typer.Option(help="Max records.")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Niets schrijven.")] = False,
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Verbose logging.")] = False,
) -> None:
    """Logius College van Rijksadviseurs (CoR)."""
    _delegate(logius_cor.main, _common_argv(cache, out, limit, dry_run, verbose))


@app.command("wikidata")
def fetch_wikidata(
    cache: Annotated[Path, typer.Option(help="Cache-directory voor downloads.")] = Path("_cache"),
    out: Annotated[Path, typer.Option(help="Output-directory.")] = Path("data/personen"),
    limit: Annotated[int | None, typer.Option(help="Max records.")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Niets schrijven.")] = False,
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Verbose logging.")] = False,
) -> None:
    """Wikidata SPARQL: ministers, staatssecretarissen, burgemeesters."""
    _delegate(wikidata_sparql.main, _common_argv(cache, out, limit, dry_run, verbose))


@app.command("ar-rwt")
def fetch_ar_rwt(
    cache: Annotated[Path, typer.Option(help="Cache-directory voor downloads.")] = Path("_cache"),
    out: Annotated[Path, typer.Option(help="Output-directory.")] = Path("data/organisaties/rwt"),
    limit: Annotated[int | None, typer.Option(help="Max records.")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Niets schrijven.")] = False,
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Verbose logging.")] = False,
) -> None:
    """Algemene Rekenkamer: RWT-register."""
    _delegate(ar_rwt.main, _common_argv(cache, out, limit, dry_run, verbose))


@app.command("koop")
def fetch_koop(
    cache: Annotated[Path, typer.Option(help="Cache-directory voor downloads.")] = Path("_cache"),
    out: Annotated[Path, typer.Option(help="Output-directory.")] = Path("data/_staging"),
    limit: Annotated[int | None, typer.Option(help="Max records.")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Niets schrijven.")] = False,
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Verbose logging.")] = False,
) -> None:
    """KOOP SRU: Staatscourant-feed met benoemingen."""
    _delegate(koop_sru.main, _common_argv(cache, out, limit, dry_run, verbose))


@app.command("ori")
def fetch_ori(
    cache: Annotated[Path, typer.Option(help="Cache-directory voor downloads.")] = Path("_cache"),
    out: Annotated[Path, typer.Option(help="Output-directory.")] = Path("data/personen"),
    limit: Annotated[int | None, typer.Option(help="Max records.")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Niets schrijven.")] = False,
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Verbose logging.")] = False,
) -> None:
    """Open Raadsinformatie: gemeentelijke colleges en raden."""
    _delegate(open_raadsinformatie.main, _common_argv(cache, out, limit, dry_run, verbose))


@app.command("tooi")
def fetch_tooi(
    cache: Annotated[Path, typer.Option(help="Cache-directory voor downloads.")] = Path("_cache"),
    out: Annotated[Path, typer.Option(help="Output-directory.")] = Path("data/organisaties"),
    scheme: Annotated[
        str, typer.Option(help="TOOI-scheme (ministeries, gemeenten, provincies, ...).")
    ] = "ministeries",
    apply_history: Annotated[
        bool,
        typer.Option(
            "--apply-history",
            help="Schrijf successor_id/predecessor_id/valid_until naar data/.",
        ),
    ] = False,
    limit: Annotated[int | None, typer.Option(help="Max records.")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Niets schrijven.")] = False,
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Verbose logging.")] = False,
) -> None:
    """TOOI: thesaurus-URI's voor overheidsorganisaties."""
    extra: list[str] = ["--scheme", scheme]
    if apply_history:
        extra.append("--apply-history")
    _delegate(tooi.main, _common_argv(cache, out, limit, dry_run, verbose) + extra)


@app.command("enrich-wikidata")
def fetch_enrich_wikidata(
    data: Annotated[Path, typer.Option(help="Polder data-root.")] = Path("data"),
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="Max aantal kandidaten (testen)."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Niets schrijven, alleen tellen."),
    ] = False,
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Verbose logging.")] = False,
) -> None:
    """Verrijk ORI-persoon-records met Wikidata-birth-year.

    Loopt door `data/personen/*.yaml`, vindt records die uit ORI komen
    (alleen `open_raadsinformatie`-source) zonder birth.year. Doet een
    SPARQL-lookup op (family, given) in Wikidata. Als er PRECIES EEN match
    met birth-year is, vult de fetcher dat in plus de wikidata-Q-id.

    Bij meerdere matches: skip (voorkomt verkeerde Q-id-koppeling).
    """
    import logging

    if verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    from polder.fetchers.wikidata_enrich import enrich_ori_records

    stats = enrich_ori_records(data, limit=limit, dry_run=dry_run)
    typer.echo("=== Wikidata-enrichment ===")
    typer.echo(f"  kandidaten:       {stats.candidates}")
    typer.echo(f"  verrijkt:         {stats.enriched}")
    typer.echo(f"  geen-match:       {stats.no_matches}")
    typer.echo(f"  ambigu (>1):      {stats.ambiguous}")
    typer.echo(f"  implausibele age: {stats.implausible_age}")
    typer.echo(f"  fouten:           {stats.errors}")


@app.command("kiesraad")
def fetch_kiesraad(
    cache: Annotated[Path, typer.Option(help="Cache-directory voor downloads.")] = Path("_cache"),
    out: Annotated[Path, typer.Option(help="Output-directory.")] = Path("data/personen"),
    limit: Annotated[int | None, typer.Option(help="Max records.")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Niets schrijven.")] = False,
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Verbose logging.")] = False,
) -> None:
    """Kiesraad: verkiezingsuitslagen en kandidatenlijsten."""
    _delegate(kiesraad.main, _common_argv(cache, out, limit, dry_run, verbose))


@app.command("abd")
def fetch_abd(
    cache: Annotated[Path, typer.Option(help="Cache-directory voor downloads.")] = Path("_cache"),
    out: Annotated[Path, typer.Option(help="Output-directory.")] = Path("_cache/abd"),
    limit: Annotated[int | None, typer.Option(help="Max records.")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Niets schrijven.")] = False,
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Verbose logging.")] = False,
) -> None:
    """ABD-organogrammen: download PDF's voor latere LLM-parsing."""
    _delegate(abd_organogrammen.main, _common_argv(cache, out, limit, dry_run, verbose))


@app.command("abd-nieuws")
def fetch_abd_nieuws(
    since: Annotated[
        str | None,
        typer.Option(help="Ondergrens artikel-datum (ISO `YYYY-MM-DD`)."),
    ] = None,
    cache: Annotated[Path, typer.Option(help="Cache-root (default: _cache).")] = Path("_cache"),
    cache_dir: Annotated[
        Path | None,
        typer.Option(help="Override volledige cache-pad (default: <cache>/abd-nieuws)."),
    ] = None,
    limit: Annotated[int | None, typer.Option(help="Max artikelen.")] = None,
    deep: Annotated[
        bool,
        typer.Option("--deep", help="Forceer sitemap-index walk voor backfill tot 2010."),
    ] = False,
    no_articles: Annotated[
        bool,
        typer.Option("--no-articles", help="Skip artikel-downloads; alleen index.json."),
    ] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Niets schrijven.")] = False,
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Verbose logging.")] = False,
) -> None:
    """ABD-nieuws: download nieuwsberichten van algemenebestuursdienst.nl."""
    argv: list[str] = []
    if since is not None:
        argv += ["--since", since]
    if cache is not None:
        argv += ["--cache", str(cache)]
    if cache_dir is not None:
        argv += ["--cache-dir", str(cache_dir)]
    if limit is not None:
        argv += ["--limit", str(limit)]
    if deep:
        argv.append("--deep")
    if no_articles:
        argv.append("--no-articles")
    if dry_run:
        argv.append("--dry-run")
    if verbose:
        argv.append("-v")
    _delegate(abd_nieuws.main, argv)


# ---------------------------------------------------------------------------
# `polder fetch all`
# ---------------------------------------------------------------------------

# Alleen deterministische fetchers. ABD en KOOP zitten er bewust niet in:
# ABD-PDF's worden via een aparte LLM-skill geparsed, KOOP-feeds bevatten
# benoemingen die we via parse-staatscourant naar staging schrijven.
DETERMINISTIC_FETCHERS: list[tuple[str, Callable[[list[str]], int]]] = [
    ("roo", roo.main),
    ("tk", tk_odata.main),
    ("ek", ek_scrape.main),
    ("logius", logius_cor.main),
    ("wikidata", wikidata_sparql.main),
    ("ar-rwt", ar_rwt.main),
    ("ori", open_raadsinformatie.main),
    ("tooi", tooi.main),
    ("kiesraad", kiesraad.main),
]


@app.command("all")
def fetch_all(
    cache: Annotated[Path, typer.Option(help="Cache-directory voor downloads.")] = Path("_cache"),
    limit: Annotated[int | None, typer.Option(help="Max records per fetcher.")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Niets schrijven.")] = False,
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Verbose logging.")] = False,
    fail_fast: Annotated[
        bool, typer.Option(help="Stop bij eerste failure (default: continue).")
    ] = False,
) -> None:
    """Run alle deterministische fetchers sequentieel.

    Sluit ABD en KOOP uit; die hebben aparte LLM-stappen en zitten niet in
    de gewone CI-loop.
    """
    failures: list[tuple[str, int | BaseException]] = []
    argv = _common_argv(cache, None, limit, dry_run, verbose)
    for name, fn in DETERMINISTIC_FETCHERS:
        typer.echo(f"==> polder fetch {name}", err=True)
        try:
            code = fn(list(argv)) or 0
        except SystemExit as exc:  # argparse roept exit aan bij fouten
            code = int(exc.code) if isinstance(exc.code, int) else 1
        except Exception as exc:  # fail-soft per fetcher
            typer.echo(f"    fout in {name}: {exc}", err=True)
            failures.append((name, exc))
            if fail_fast:
                raise typer.Exit(code=1) from exc
            continue
        if code != 0:
            failures.append((name, code))
            if fail_fast:
                raise typer.Exit(code=code)

    if failures:
        typer.echo(f"klaar met {len(failures)} failure(s):", err=True)
        for name, status in failures:
            typer.echo(f"  - {name}: {status}", err=True)
        raise typer.Exit(code=1)
