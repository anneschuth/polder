"""`polder resolve` command: code-only staging-resolver.

Vervangt de dure resolve-staging-proposals LLM-skill voor het overgrote deel
van de proposals. Werkt op `data/_staging/*.json`, schrijft
`*.resolved.json`-companions.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
from typing import Annotated

import typer

logger = logging.getLogger("polder.cli.resolve")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def resolve(
    target: Annotated[
        Path,
        typer.Argument(help="Pad naar staging-file of -directory."),
    ] = Path("data/_staging"),
    overwrite: Annotated[
        bool,
        typer.Option(
            "--overwrite",
            help="Overschrijf bestaande .resolved.json files (default: skip).",
        ),
    ] = False,
    data: Annotated[
        Path,
        typer.Option("--data", help="Polder data-root."),
    ] = Path("data"),
    enrich_wikidata: Annotated[
        bool,
        typer.Option(
            "--enrich-wikidata/--no-enrich-wikidata",
            help=(
                "Bij no_match voor persoon: probeer Wikidata-lookup om "
                "birth_year op te halen en alsnog een match of "
                "creatable_new_person-status te krijgen."
            ),
        ),
    ] = False,
    enrich_llm: Annotated[
        bool,
        typer.Option(
            "--enrich-llm",
            help=(
                "Bij person-resolution onder 0.95 confidence: roep de "
                "`lookup-person` skill aan voor disambigueren / birth_year "
                "ophalen / nieuwe persoon voorstellen. Kost geld; gebruik "
                "--max-cost-usd om te begrenzen."
            ),
        ),
    ] = False,
    max_cost_usd: Annotated[
        float,
        typer.Option(
            "--max-cost-usd",
            help="Hard budget-cap voor de LLM-enrich-pass (USD).",
        ),
    ] = 1.0,
    quote_or_die: Annotated[
        bool,
        typer.Option(
            "--quote-or-die/--no-quote-or-die",
            help=(
                "Verifieer evidence_snippet door de source-URL te fetchen en "
                "substring-match te doen. Strict; alleen allowed hosts "
                "(wikidata, wikipedia, rijksoverheid). Default: aan."
            ),
        ),
    ] = True,
    verbose: Annotated[
        bool,
        typer.Option("-v", "--verbose", help="Verbose logging."),
    ] = False,
) -> None:
    """Resolve staging-proposals via deterministische code-matching.

    Voor proposals waar code niet kan disambigueren (meerdere kandidaten):
    `merge_recommendation` wordt `needs-review`. Die zijn kandidaat voor
    handmatige review of een toekomstige LLM-fallback-pas.
    """
    from polder.resolve.matcher import PolderIndex
    from polder.resolve.proposal import resolve_proposal

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(message)s")
    else:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    enricher = None
    if enrich_wikidata:
        from polder.resolve.wikidata_enricher import make_wikidata_enricher

        enricher = make_wikidata_enricher()
        typer.echo("Wikidata-enrichment aan voor no-match-personen.")

    if not data.is_absolute():
        data = _repo_root() / data
    if not target.is_absolute():
        target = _repo_root() / target

    if not target.exists():
        typer.echo(f"target niet gevonden: {target}", err=True)
        raise typer.Exit(code=2)

    typer.echo(f"Index laden uit {data}...")
    idx = PolderIndex.load(data)
    typer.echo(
        f"  {len(idx.persons_by_id)} persons, {len(idx.org_ids)} orgs, "
        f"{len(idx.post_ids)} posts"
    )

    # Verzamel files
    if target.is_file():
        files = [target]
    else:
        files = sorted(f for f in target.glob("*.json") if not f.name.endswith(".resolved.json"))
        if not overwrite:
            files = [f for f in files if not (f.parent / (f.stem + ".resolved.json")).exists()]

    if not files:
        typer.echo("Geen files om te resolven.")
        return

    typer.echo(f"\nResolve {len(files)} staging-files...")

    n_proposals = 0
    n_files_written = 0
    all_resolved: list[dict] = []
    llm_stats = None

    if enrich_llm:
        from polder.resolve.llm_enrich import EnrichStats

        llm_stats = EnrichStats()
        typer.echo(f"LLM-enrich aan (skill=lookup-person, max-cost=${max_cost_usd:.2f}).")

    budget_remaining = max_cost_usd
    for path in files:
        try:
            data_raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            typer.echo(f"  SKIP {path.name}: invalid JSON ({exc})", err=True)
            continue
        if not isinstance(data_raw, list):
            continue

        resolved: list[dict] = []
        for proposal in data_raw:
            if not isinstance(proposal, dict):
                continue
            result = resolve_proposal(proposal, idx, enricher=enricher)
            resolved.append(result)
            n_proposals += 1

        if not resolved:
            continue

        if enrich_llm and budget_remaining > 0:
            from polder.resolve.llm_enrich import enrich_resolved

            verifier = None
            if quote_or_die:
                from polder.resolve.quote_or_die import make_verifier

                verifier = make_verifier()

            resolved, file_stats = enrich_resolved(
                resolved,
                max_cost_usd=budget_remaining,
                quote_or_die_check=verifier,
            )
            budget_remaining = max(0.0, budget_remaining - file_stats.total_cost_usd)
            assert llm_stats is not None
            llm_stats.candidates += file_stats.candidates
            llm_stats.skipped_budget += file_stats.skipped_budget
            llm_stats.skill_calls += file_stats.skill_calls
            llm_stats.cache_hits += file_stats.cache_hits
            llm_stats.rate_limited += file_stats.rate_limited
            llm_stats.skill_errors += file_stats.skill_errors
            llm_stats.matched_existing += file_stats.matched_existing
            llm_stats.created_new += file_stats.created_new
            llm_stats.no_match += file_stats.no_match
            llm_stats.quote_or_die_rejected += file_stats.quote_or_die_rejected
            llm_stats.total_cost_usd += file_stats.total_cost_usd

        out_path = path.parent / (path.stem + ".resolved.json")
        out_path.write_text(
            json.dumps(resolved, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        n_files_written += 1
        all_resolved.extend(resolved)

    recs: Counter = Counter()
    person_matched = 0
    person_unmatched = 0
    for r in all_resolved:
        recs[r.get("merge_recommendation", "skip")] += 1
        if r.get("resolved_person_id"):
            person_matched += 1
        elif r.get("merge_recommendation") != "skip":
            person_unmatched += 1

    typer.echo("")
    typer.echo("=== Resolve klaar ===")
    typer.echo(f"  files geschreven: {n_files_written}")
    typer.echo(f"  proposals:        {n_proposals}")
    typer.echo(f"  auto-merge:       {recs.get('auto-merge', 0)}")
    typer.echo(f"  needs-review:     {recs.get('needs-review', 0)}")
    typer.echo(f"  skip:             {recs.get('skip', 0)}")
    typer.echo(f"  person-matched:   {person_matched}")
    typer.echo(f"  person-unmatched: {person_unmatched}")
    if llm_stats is not None:
        typer.echo("")
        typer.echo("=== LLM-enrich ===")
        typer.echo(f"  candidates:         {llm_stats.candidates}")
        typer.echo(f"  skill-calls:        {llm_stats.skill_calls}")
        typer.echo(f"  cache-hits:         {llm_stats.cache_hits}")
        typer.echo(f"  matched-existing:   {llm_stats.matched_existing}")
        typer.echo(f"  created-new:        {llm_stats.created_new}")
        typer.echo(f"  no-match:           {llm_stats.no_match}")
        typer.echo(f"  quote-or-die-rej:   {llm_stats.quote_or_die_rejected}")
        typer.echo(f"  rate-limited:       {llm_stats.rate_limited}")
        typer.echo(f"  errors:             {llm_stats.skill_errors}")
        typer.echo(f"  skipped-budget:     {llm_stats.skipped_budget}")
        typer.echo(f"  total-cost-usd:     ${llm_stats.total_cost_usd:.4f}")
