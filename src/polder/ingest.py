"""End-to-end staging-pipeline voor polder.

Eén entrypoint dat per bron alle stappen doorloopt die anders met de hand of
met aparte scripts moeten worden gedraaid:

1. Parse: nieuwe HTML/XML/PDF in `_cache/<bron>/` -> `data/_staging/<bron>-<key>.json`
   via de bestaande `scripts/parse_*_local.sh` runners (claude -p Sonnet).
2. Resolve: staging-files zonder `.resolved.json` companion -> `.resolved.json`
   via `scripts/resolve_staging_local.sh`.
3. Apply: `polder apply-staging data/_staging/ --apply --threshold T`.
4. Validate: `polder.validate.run_all_checks`.
5. (Build/commit/push delegeert de caller — zie `cli/commands/ingest_cmd.py`.)

`ingest_source` is pure plan-bouw plus subprocess-aanroepen. Geen LLM-logica
in dit bestand zelf — die zit in de skill-runners onder `scripts/`. Subprocess-
calls zijn geïsoleerd zodat tests ze kunnen mocken.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

logger = logging.getLogger("polder.ingest")

Source = Literal["abd-nieuws", "staatscourant", "organogram"]
ALL_SOURCES: tuple[Source, ...] = ("abd-nieuws", "staatscourant", "organogram")

# Default LLM-model voor parse + resolve. Haiku 4.5 is een orde van grootte
# goedkoper dan Sonnet 4.6 en accuraat genoeg voor de extraction-taken in deze
# pipeline. Vision-skills (parse-organogram) overrulen dit zelf naar Opus.
DEFAULT_MODEL = "claude-haiku-4-5"

# Speciale exit-code uit run_skill.sh / backfill-scripts: rate-limit bereikt,
# output is bewust niet geschreven. ingest_source detecteert deze code en
# breekt de huidige fase af zodat de pipeline geen garbage-output blijft
# stagen tot het reset-window om 22:00 Europe/Amsterdam.
RATE_LIMIT_EXIT_CODE = 99


# ---------------------------------------------------------------------------
# Budget / kostenramingen
# ---------------------------------------------------------------------------


# Geschatte gemiddelde dollars-per-call per modelfamilie. Voor dry-run rapportage
# en voor de hard cap in `IngestBudget`. Cijfers zijn ruwe schattingen op basis
# van eerdere parse-runs (Sonnet 4.6 gemiddeld ~$0.025 voor parse-skills met
# ~5-10K tokens input + 1-2K output). Pas aan als je nauwkeuriger metingen hebt.
COST_PER_CALL_USD: dict[str, float] = {
    "sonnet-4-6": 0.025,
    "claude-sonnet-4-6": 0.025,
    "haiku-4-5": 0.005,
    "claude-haiku-4-5": 0.005,
    "opus-4-7": 0.10,
    "claude-opus-4-7": 0.10,
}

# Default aanname per fase. resolve + lookup zijn lichter dan parse omdat ze
# kortere prompts gebruiken. Voor 2026 H1 mikt de pipeline op Haiku als default;
# Sonnet 4.6 hits rate-limits en is 5x duurder.
COST_PARSE_USD = COST_PER_CALL_USD["haiku-4-5"]
COST_RESOLVE_USD = COST_PER_CALL_USD["haiku-4-5"]
# Lookup-person wordt soms binnen resolve aangeroepen; we rekenen 0.5 call
# extra per resolve-job als gemiddelde ondergrens.
COST_LOOKUP_FACTOR = 0.5


@dataclass
class IngestBudget:
    """Hard cap op LLM-calls per pipeline-run.

    `max_claude_calls=None` betekent unlimited. `consume()` telt verbruikte
    calls bij elkaar op zodat `check()` weet of er nog budget is. De
    kosten-schatting volgt het modelnummer in `consume()`.
    """

    max_claude_calls: int | None = None
    used_calls: int = 0
    cost_estimate_usd: float = 0.0

    def check(self) -> bool:
        """True als er nog ruimte is voor minstens één call."""
        if self.max_claude_calls is None:
            return True
        return self.used_calls < self.max_claude_calls

    def remaining(self) -> int | None:
        if self.max_claude_calls is None:
            return None
        return max(0, self.max_claude_calls - self.used_calls)

    def consume(self, n: int = 1, *, model: str = DEFAULT_MODEL) -> None:
        """Boek `n` calls op het verbruik en werk de kostenschatting bij."""
        self.used_calls += n
        per_call = COST_PER_CALL_USD.get(model, COST_PARSE_USD)
        self.cost_estimate_usd += n * per_call


def estimate_cost(
    *, parse_jobs: int, resolve_jobs: int, model: str = DEFAULT_MODEL
) -> float:
    """Schatting voor full-run kosten gegeven aantal parse + resolve jobs."""
    per_call = COST_PER_CALL_USD.get(model, COST_PARSE_USD)
    parse_cost = parse_jobs * per_call
    # Resolve doet doorgaans 1 call + ~0.5 lookup-person calls per staging-file.
    resolve_cost = resolve_jobs * per_call * (1 + COST_LOOKUP_FACTOR)
    return parse_cost + resolve_cost


# ---------------------------------------------------------------------------
# Result objects
# ---------------------------------------------------------------------------


@dataclass
class ParsePlan:
    """Welke parse-jobs zouden draaien voor één bron."""

    source: Source
    jobs: list[ParseJob] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.jobs)


@dataclass
class ParseJob:
    """Concrete parse-call: input cache-file + output staging-pad."""

    input_path: Path
    output_path: Path
    extra_args: tuple[str, ...] = ()


@dataclass
class IngestResult:
    """Samenvatting van één bron-pipeline."""

    source: Source
    parsed: int = 0
    parse_failed: int = 0
    resolved: int = 0
    resolve_failed: int = 0
    applied: int = 0
    skipped: int = 0
    needs_review: int = 0
    apply_failed: bool = False
    validate_ok: bool | None = None
    # Dry-run kostenramingen per fase, in USD. Voor echte runs wordt
    # `claude_calls_used` bijgehouden via de `IngestBudget`.
    parse_cost_estimate_usd: float = 0.0
    resolve_cost_estimate_usd: float = 0.0
    claude_calls_used: int = 0
    budget_hit: bool = False
    # True als een parse/resolve-job exit 99 retourneerde (rate-limit van de
    # claude-API). De pipeline laat de huidige fase dan stoppen en stagest niets
    # meer voor die bron. Apply + validate worden wel nog gedraaid op het
    # bestaande staging-materiaal.
    aborted_rate_limit: bool = False
    commit_sha: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def total_cost_estimate_usd(self) -> float:
        return self.parse_cost_estimate_usd + self.resolve_cost_estimate_usd


# ---------------------------------------------------------------------------
# Discovery: welke cache-files hebben nog geen staging-output?
# ---------------------------------------------------------------------------


def _staging_key_for_html(html_path: Path) -> str:
    """Voor abd-nieuws: gebruik de basename zonder extensie als key."""
    return html_path.stem


def _staging_key_for_xml(xml_path: Path) -> str:
    """Voor staatscourant: gebruik 'YYYY-MM-stcrt-NNNN' op basis van pad+stem."""
    # Pad-shape: _cache/staatscourant/2025/03/stcrt-2025-9250.xml
    return xml_path.stem


def _staging_key_for_pdf(pdf_path: Path, ministerie: str) -> str:
    """Voor organogram: combineer ministerie-slug + pdf-stem."""
    return f"{ministerie}-{pdf_path.stem}"


def _existing_staging_keys(staging_dir: Path, prefix: str) -> set[str]:
    """Verzamel alle keys waarvoor al een staging-file bestaat."""
    keys: set[str] = set()
    if not staging_dir.exists():
        return keys
    for path in staging_dir.glob(f"{prefix}-*.json"):
        if path.name.endswith(".resolved.json"):
            continue
        # `<prefix>-<key>.json` -> key
        stem = path.stem  # zonder .json
        if stem.startswith(f"{prefix}-"):
            keys.add(stem[len(prefix) + 1 :])
    return keys


def _plan_abd_nieuws(
    cache_root: Path, staging_dir: Path, *, limit: int | None
) -> ParsePlan:
    plan = ParsePlan(source="abd-nieuws")
    src_dir = cache_root / "abd-nieuws"
    if not src_dir.exists():
        return plan
    seen = _existing_staging_keys(staging_dir, "abd-nieuws")
    for html in sorted(src_dir.glob("*.html")):
        key = _staging_key_for_html(html)
        if key in seen:
            continue
        out = staging_dir / f"abd-nieuws-{key}.json"
        plan.jobs.append(ParseJob(input_path=html, output_path=out))
        if limit is not None and len(plan.jobs) >= limit:
            break
    return plan


def _plan_staatscourant(
    cache_root: Path, staging_dir: Path, *, limit: int | None
) -> ParsePlan:
    plan = ParsePlan(source="staatscourant")
    src_dir = cache_root / "staatscourant"
    if not src_dir.exists():
        return plan
    seen = _existing_staging_keys(staging_dir, "staatscourant")
    for xml in sorted(src_dir.rglob("*.xml")):
        if xml.name.endswith(".sru.xml"):
            continue
        key = _staging_key_for_xml(xml)
        if key in seen:
            continue
        out = staging_dir / f"staatscourant-{key}.json"
        plan.jobs.append(ParseJob(input_path=xml, output_path=out))
        if limit is not None and len(plan.jobs) >= limit:
            break
    return plan


def _plan_organogram(
    cache_root: Path, staging_dir: Path, *, limit: int | None
) -> ParsePlan:
    plan = ParsePlan(source="organogram")
    src_dir = cache_root / "abd-organogrammen"
    if not src_dir.exists():
        return plan
    seen = _existing_staging_keys(staging_dir, "organogram")
    for ministerie_dir in sorted(p for p in src_dir.iterdir() if p.is_dir()):
        ministerie = ministerie_dir.name
        for pdf in sorted(ministerie_dir.rglob("*.pdf")):
            key = _staging_key_for_pdf(pdf, ministerie)
            if key in seen:
                continue
            out = staging_dir / f"organogram-{key}.json"
            plan.jobs.append(
                ParseJob(
                    input_path=pdf,
                    output_path=out,
                    extra_args=(ministerie,),
                )
            )
            if limit is not None and len(plan.jobs) >= limit:
                break
    return plan


def plan_parse(
    source: Source,
    *,
    cache_root: Path,
    staging_dir: Path,
    limit: int | None = None,
) -> ParsePlan:
    """Bouw de lijst parse-jobs voor één bron, idempotent."""
    if source == "abd-nieuws":
        return _plan_abd_nieuws(cache_root, staging_dir, limit=limit)
    if source == "staatscourant":
        return _plan_staatscourant(cache_root, staging_dir, limit=limit)
    if source == "organogram":
        return _plan_organogram(cache_root, staging_dir, limit=limit)
    raise ValueError(f"onbekende bron: {source}")


def plan_resolve(staging_dir: Path, *, source: Source | None = None) -> list[Path]:
    """Lijst staging-files zonder `.resolved.json` companion."""
    if not staging_dir.exists():
        return []
    pending: list[Path] = []
    for path in sorted(staging_dir.glob("*.json")):
        name = path.name
        if name.endswith(".resolved.json"):
            continue
        if source is not None and not name.startswith(f"{source}-"):
            continue
        if path.with_suffix(".resolved.json").exists():
            continue
        pending.append(path)
    return pending


# ---------------------------------------------------------------------------
# Subprocess-callers (one per stap, mocked in tests)
# ---------------------------------------------------------------------------


SubprocessRunner = Callable[[list[str]], int]


# Module-local "current model" gelezen door _default_runner. Wordt door
# `ingest_source` gezet voor de hele fase (alle parallelle workers gebruiken
# hetzelfde model). Geen env-mutaties dus geen race-conditions.
_CURRENT_MODEL: str | None = None


def _default_runner(cmd: list[str]) -> int:
    """Default runner: print + run + return exit-code.

    Geeft `POLDER_CLAUDE_MODEL` als env-var mee aan de subprocess. Het
    sub-shell-script (scripts/run_skill.sh, scripts/parse_*_local.sh) leest
    die env-var en geeft hem door aan `claude --model`.
    """
    logger.info("+ %s", " ".join(cmd))
    print("+ " + " ".join(cmd), file=sys.stderr, flush=True)
    env = os.environ.copy()
    if _CURRENT_MODEL is not None:
        env["POLDER_CLAUDE_MODEL"] = _CURRENT_MODEL
    proc = subprocess.run(cmd, check=False, env=env)
    return proc.returncode


def _scripts_dir(repo_root: Path) -> Path:
    return repo_root / "scripts"


def _run_with_model(
    cmd: list[str], runner: SubprocessRunner, model: str | None
) -> int:
    """Roep `runner` aan met model-context.

    Voor de default runner: subprocess krijgt `POLDER_CLAUDE_MODEL` via
    `subprocess.run(env=...)`. Geen mutatie van de huidige proces-env, dus
    parallelle workers in een ThreadPoolExecutor stappen elkaar niet op de
    tenen. `_CURRENT_MODEL` wordt op één plek gezet (in `ingest_source`,
    voor de hele fase) en gelezen door `_default_runner`.

    Test-mock-runners zien het model in `_CURRENT_MODEL`. Als ze de env
    willen inspecteren kunnen ze `polder.ingest._CURRENT_MODEL` lezen
    of het via een eigen wrapper meekijken.
    """
    return runner(cmd)


def run_parse_job(
    job: ParseJob,
    source: Source,
    *,
    repo_root: Path,
    runner: SubprocessRunner = _default_runner,
    model: str | None = None,
) -> tuple[bool, int]:
    """Roep de juiste parse-skill aan via de bestaande bash-runner.

    Retourneert `(ok, exit_code)`. `ok` is True als de skill een output-bestand
    heeft geschreven met exit-code 0. `exit_code == RATE_LIMIT_EXIT_CODE` is
    het signaal dat de claude-API rate-limit heeft gestuurd; aanroepers (ingest)
    interpreteren dat als reden om de hele fase te stoppen.
    """
    scripts = _scripts_dir(repo_root)
    if source == "abd-nieuws":
        script = scripts / "parse_abd_nieuws_local.sh"
        cmd = ["bash", str(script), str(job.input_path), str(job.output_path)]
    elif source == "staatscourant":
        script = scripts / "parse_staatscourant_local.sh"
        cmd = ["bash", str(script), str(job.input_path), str(job.output_path)]
    elif source == "organogram":
        script = scripts / "parse_organogram_local.sh"
        ministerie = job.extra_args[0] if job.extra_args else "unknown"
        cmd = [
            "bash",
            str(script),
            str(job.input_path),
            ministerie,
            str(job.output_path),
        ]
    else:
        raise ValueError(f"onbekende bron: {source}")
    job.output_path.parent.mkdir(parents=True, exist_ok=True)
    code = _run_with_model(cmd, runner, model)
    return code == 0, code


def run_resolve_job(
    staging_path: Path,
    *,
    repo_root: Path,
    runner: SubprocessRunner = _default_runner,
    model: str | None = None,
) -> tuple[bool, int]:
    """Roep de resolve-skill aan. Retourneert `(ok, exit_code)`.

    `exit_code == RATE_LIMIT_EXIT_CODE` betekent: rate-limit, fase afbreken.
    """
    scripts = _scripts_dir(repo_root)
    script = scripts / "resolve_staging_local.sh"
    cmd = ["bash", str(script), str(staging_path)]
    code = _run_with_model(cmd, runner, model)
    return code == 0, code


# ---------------------------------------------------------------------------
# Apply + validate (in-process, geen subprocess)
# ---------------------------------------------------------------------------


def run_apply(
    staging_dir: Path,
    *,
    data_dir: Path,
    threshold: float,
    skip_persons: bool = False,
) -> tuple[int, int]:
    """Roep apply.plan_apply + execute_apply aan. Retourneert (applied, skipped)."""
    from polder.apply import (
        SkippedProposal,
        execute_apply,
        load_resolved_input,
        plan_apply,
    )

    proposals = load_resolved_input(staging_dir)
    if not proposals:
        return 0, 0
    only_high_confidence = threshold >= 0.95
    actions, skipped = plan_apply(
        proposals,
        data_dir,
        only_high_confidence=only_high_confidence,
        skip_persons=skip_persons,
    )
    # Bovenop de basisdrempel van plan_apply (0.85) leggen we hier optioneel
    # een strengere filter neer als caller threshold > 0.85 vraagt.
    if threshold > 0.85 and not only_high_confidence:
        kept = []
        for action in actions:
            if action.confidence >= threshold:
                kept.append(action)
            else:
                skipped.append(
                    SkippedProposal(
                        proposal=action.source_proposal,
                        reasons=[
                            f"confidence {action.confidence:.2f} < drempel {threshold:.2f}"
                        ],
                    )
                )
        actions = kept
    written = execute_apply(actions, data_dir)
    return written, len(skipped)


def run_validate(*, data_dir: Path, schemas_dir: Path) -> bool:
    """True als validate clean (exit-code 0)."""
    from polder.validate import exit_code, run_all_checks

    issues = run_all_checks(data_dir, schemas_dir)
    return exit_code(issues, strict=False) == 0


def _safe_load_resolved(staging_dir: Path, *, source: Source) -> list[dict]:
    """Lees alle `<source>-*.resolved.json` proposals zonder te crashen.

    `apply.load_resolved_input` raised JSONDecodeError als één file kapot is.
    Voor dry-run en cost-estimation willen we doorlopen en alleen valide
    files meetellen.
    """
    import json as _json

    items: list[dict] = []
    if not staging_dir.exists():
        return items
    for path in sorted(staging_dir.glob(f"{source}-*.resolved.json")):
        try:
            with path.open(encoding="utf-8") as f:
                data = _json.load(f)
        except (OSError, _json.JSONDecodeError):
            continue
        if not isinstance(data, list):
            continue
        for entry in data:
            if not isinstance(entry, dict):
                continue
            entry.setdefault("_source_filename", path.name)
            items.append(entry)
    return items


# ---------------------------------------------------------------------------
# Per-bron pipeline
# ---------------------------------------------------------------------------


def _count_needs_review(
    proposals: list[dict], *, threshold: float
) -> int:
    """Tel proposals die boven `threshold` zitten maar als 'needs-review'
    staan gemarkeerd."""
    n = 0
    for p in proposals:
        rec = p.get("merge_recommendation")
        conf = float(p.get("confidence", 0.0) or 0.0)
        if rec == "needs-review" and conf >= threshold:
            n += 1
    return n


def ingest_source(
    source: Source,
    *,
    repo_root: Path,
    cache_root: Path | None = None,
    staging_dir: Path | None = None,
    data_dir: Path | None = None,
    schemas_dir: Path | None = None,
    threshold: float = 0.85,
    dry_run: bool = False,
    limit: int | None = None,
    runner: SubprocessRunner = _default_runner,
    budget: IngestBudget | None = None,
    parallel: int = 5,
    model: str = DEFAULT_MODEL,
    abort_on_rate_limit: bool = True,
) -> IngestResult:
    """Run parse -> resolve -> apply -> validate voor één bron.

    `dry_run=True` slaat alle subprocess-calls en de echte schrijf-acties over;
    de result-velden tonen wat de run zou hebben gedaan, inclusief geschatte
    LLM-kosten in USD.

    `budget` legt een hard cap op LLM-calls. Zodra `budget.check()` False
    teruggeeft stopt de fase met `budget_hit=True` op het result. In dry-run
    wordt het budget ook geconsumeerd zodat een `ingest_all` over meerdere
    bronnen de cap correct doorrekent.

    `parallel` (default 5) bepaalt het aantal worker-threads voor de parse-
    en resolve-fase. Iedere worker doet `subprocess.run` op de claude-binary,
    dus de threads wachten vrijwel volledig op IO; `ThreadPoolExecutor` is
    daardoor goedkoper dan `ProcessPoolExecutor`. De apply-fase blijft single-
    threaded omdat die naar `data/` schrijft.

    `model` wordt via env-var `POLDER_CLAUDE_MODEL` doorgegeven aan de
    skill-runners. Default Haiku 4.5; vision-skills overrulen zelf naar Opus.

    `abort_on_rate_limit` (default True): zodra één parse- of resolve-job
    `RATE_LIMIT_EXIT_CODE` retourneert breekt de pipeline de huidige fase af.
    Voorkomt dat een rate-limited claude oneindig garbage als JSON blijft
    stagen tot het reset-window om 22:00 Europe/Amsterdam.
    """
    if parallel < 1:
        raise ValueError(f"parallel moet >= 1 zijn, kreeg {parallel}")
    cache_root = cache_root or (repo_root / "_cache")
    staging_dir = staging_dir or (repo_root / "data" / "_staging")
    data_dir = data_dir or (repo_root / "data")
    schemas_dir = schemas_dir or (repo_root / "schemas")

    # Zet het module-level "current model" zodat _default_runner het via
    # subprocess-env kan doorgeven aan scripts/run_skill.sh. Restoren aan
    # het einde zodat tests geen state lekken naar elkaar.
    global _CURRENT_MODEL
    _previous_model = _CURRENT_MODEL
    _CURRENT_MODEL = model
    try:
        return _ingest_source_impl(
            source,
            repo_root=repo_root,
            cache_root=cache_root,
            staging_dir=staging_dir,
            data_dir=data_dir,
            schemas_dir=schemas_dir,
            threshold=threshold,
            dry_run=dry_run,
            limit=limit,
            runner=runner,
            budget=budget,
            parallel=parallel,
            model=model,
            abort_on_rate_limit=abort_on_rate_limit,
        )
    finally:
        _CURRENT_MODEL = _previous_model


def _ingest_source_impl(
    source: Source,
    *,
    repo_root: Path,
    cache_root: Path,
    staging_dir: Path,
    data_dir: Path,
    schemas_dir: Path,
    threshold: float,
    dry_run: bool,
    limit: int | None,
    runner: SubprocessRunner,
    budget: IngestBudget | None,
    parallel: int,
    model: str,
    abort_on_rate_limit: bool,
) -> IngestResult:
    """Body van ingest_source met de modelcontext al opgezet."""

    result = IngestResult(source=source)

    # Per-call kost gebaseerd op gekozen model. Geen hardcoded Sonnet meer.
    per_call_cost = COST_PER_CALL_USD.get(model, COST_PARSE_USD)

    # Stap 1: parse-plan
    plan = plan_parse(
        source, cache_root=cache_root, staging_dir=staging_dir, limit=limit
    )
    if dry_run:
        if budget is not None and budget.max_claude_calls is not None:
            remaining = budget.remaining() or 0
            planned_parse = min(plan.count, remaining)
        else:
            planned_parse = plan.count
        result.parsed = planned_parse
        result.parse_cost_estimate_usd = planned_parse * per_call_cost
        result.notes.append(
            f"[dry-run] parse: {planned_parse}/{plan.count} jobs "
            f"~${result.parse_cost_estimate_usd:.2f} ({model})"
        )
        if budget is not None:
            budget.consume(planned_parse, model=model)
            if planned_parse < plan.count:
                result.budget_hit = True
                result.notes.append(
                    f"[dry-run] budget-cap stopt parse na {planned_parse} "
                    f"van {plan.count} jobs"
                )
    else:
        # Bepaal vooraf welke jobs binnen het budget vallen — dan submit alleen
        # die naar de pool. Geen halverwege-stop midden in concurrent jobs.
        if budget is not None and budget.max_claude_calls is not None:
            remaining = budget.remaining() or 0
            jobs_within_budget = plan.jobs[:remaining]
            if len(jobs_within_budget) < len(plan.jobs):
                result.budget_hit = True
                result.notes.append(
                    f"Budget cap: {budget.used_calls + len(jobs_within_budget)} / "
                    f"{budget.max_claude_calls} max — parse beperkt tot "
                    f"{len(jobs_within_budget)}/{len(plan.jobs)} jobs"
                )
        else:
            jobs_within_budget = list(plan.jobs)

        if budget is not None:
            # Reserveer het budget vooraf zodat parallelle resolve-fase
            # de gedeelde teller correct ziet.
            budget.consume(len(jobs_within_budget), model=model)
            result.claude_calls_used += len(jobs_within_budget)

        if jobs_within_budget:
            workers = max(1, min(parallel, len(jobs_within_budget)))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(
                        run_parse_job,
                        job,
                        source,
                        repo_root=repo_root,
                        runner=runner,
                        model=model,
                    ): job
                    for job in jobs_within_budget
                }
                rate_limit_seen = False
                for future in as_completed(futures):
                    job = futures[future]
                    try:
                        ok, code = future.result()
                    except Exception as exc:
                        logger.error(
                            "parse-job %s gefaald: %s", job.input_path, exc
                        )
                        ok, code = False, 1
                    if code == RATE_LIMIT_EXIT_CODE:
                        rate_limit_seen = True
                        result.parse_failed += 1
                        continue
                    if ok and job.output_path.exists():
                        result.parsed += 1
                    else:
                        result.parse_failed += 1

            if rate_limit_seen and abort_on_rate_limit:
                result.aborted_rate_limit = True
                msg = (
                    f"RATE-LIMIT bereikt. Pipeline gestopt na "
                    f"{result.parsed} succesvolle parse-calls. "
                    "Wacht tot reset (22:00 Europe/Amsterdam) of switch model."
                )
                result.notes.append(msg)
                print(msg, file=sys.stderr, flush=True)

    # Stap 2: resolve-plan (gebaseerd op huidige staging-state).
    # Als parse abort heeft veroorzaakt slaan we de resolve-fase over: de
    # claude-API is rate-limited, een tweede ronde calls heeft geen zin.
    if result.aborted_rate_limit:
        pending_resolve: list[Path] = []
    else:
        pending_resolve = plan_resolve(staging_dir, source=source)
    if dry_run:
        if budget is not None and budget.max_claude_calls is not None:
            remaining = budget.remaining() or 0
            planned_resolve = min(len(pending_resolve), remaining)
        else:
            planned_resolve = len(pending_resolve)
        result.resolved = planned_resolve
        result.resolve_cost_estimate_usd = (
            planned_resolve * per_call_cost * (1 + COST_LOOKUP_FACTOR)
        )
        result.notes.append(
            f"[dry-run] resolve: {planned_resolve}/{len(pending_resolve)} jobs "
            f"~${result.resolve_cost_estimate_usd:.2f} "
            f"(incl. ~{COST_LOOKUP_FACTOR:.1f} lookup-person calls/staging)"
        )
        if budget is not None:
            budget.consume(planned_resolve, model=model)
            if planned_resolve < len(pending_resolve):
                result.budget_hit = True
                result.notes.append(
                    f"[dry-run] budget-cap stopt resolve na {planned_resolve} "
                    f"van {len(pending_resolve)} jobs"
                )
    elif not result.aborted_rate_limit:
        # Zelfde pattern als parse: vooraf afgekapt op budget, dan parallel.
        # Lookup-person calls binnen één resolve zijn impliciet sequentieel,
        # parallelism op staging-file-niveau is veilig.
        if budget is not None and budget.max_claude_calls is not None:
            remaining = budget.remaining() or 0
            paths_within_budget = pending_resolve[:remaining]
            if len(paths_within_budget) < len(pending_resolve):
                result.budget_hit = True
                result.notes.append(
                    f"Budget cap: {budget.used_calls + len(paths_within_budget)} / "
                    f"{budget.max_claude_calls} max — resolve beperkt tot "
                    f"{len(paths_within_budget)}/{len(pending_resolve)} jobs"
                )
        else:
            paths_within_budget = list(pending_resolve)

        if budget is not None:
            budget.consume(len(paths_within_budget), model=model)
            result.claude_calls_used += len(paths_within_budget)

        if paths_within_budget:
            workers = max(1, min(parallel, len(paths_within_budget)))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(
                        run_resolve_job,
                        staging_path,
                        repo_root=repo_root,
                        runner=runner,
                        model=model,
                    ): staging_path
                    for staging_path in paths_within_budget
                }
                rate_limit_seen = False
                for future in as_completed(futures):
                    staging_path = futures[future]
                    try:
                        ok, code = future.result()
                    except Exception as exc:
                        logger.error(
                            "resolve-job %s gefaald: %s", staging_path, exc
                        )
                        ok, code = False, 1
                    if code == RATE_LIMIT_EXIT_CODE:
                        rate_limit_seen = True
                        result.resolve_failed += 1
                        continue
                    if ok and staging_path.with_suffix(".resolved.json").exists():
                        result.resolved += 1
                    else:
                        result.resolve_failed += 1
            if rate_limit_seen and abort_on_rate_limit:
                result.aborted_rate_limit = True
                msg = (
                    f"RATE-LIMIT bereikt in resolve-fase. Pipeline gestopt na "
                    f"{result.resolved} succesvolle resolve-calls. "
                    "Wacht tot reset (22:00 Europe/Amsterdam) of switch model."
                )
                result.notes.append(msg)
                print(msg, file=sys.stderr, flush=True)

    # Stap 3: apply
    if dry_run:
        # Plan-only: tel hoeveel records auto-mergeable zijn. We laden zelf
        # zodat corrupt JSON niet de hele dry-run laat crashen.
        proposals = _safe_load_resolved(staging_dir, source=source)
        if proposals:
            from polder.apply import plan_apply

            try:
                actions, skipped = plan_apply(
                    proposals,
                    data_dir,
                    only_high_confidence=threshold >= 0.95,
                )
            except Exception as exc:
                result.notes.append(f"[dry-run] plan_apply overgeslagen: {exc}")
            else:
                actions = [a for a in actions if a.confidence >= threshold]
                result.applied = len(actions)
                result.skipped = len(skipped)
                result.needs_review = _count_needs_review(
                    proposals, threshold=threshold
                )
                result.notes.append(
                    f"[dry-run] apply: {len(actions)} auto-mergeable boven "
                    f"threshold {threshold:.2f}, "
                    f"{result.needs_review} needs-review, {len(skipped)} skip"
                )
    else:
        try:
            applied, skipped = run_apply(
                staging_dir, data_dir=data_dir, threshold=threshold
            )
            result.applied = applied
            result.skipped = skipped
        except Exception as exc:
            result.apply_failed = True
            result.notes.append(f"apply-error: {exc}")
            return result

    # Stap 4: validate
    if dry_run:
        result.validate_ok = None
        result.notes.append("[dry-run] validate overgeslagen")
    else:
        try:
            result.validate_ok = run_validate(
                data_dir=data_dir, schemas_dir=schemas_dir
            )
        except Exception as exc:
            result.validate_ok = False
            result.notes.append(f"validate-error: {exc}")
    return result


def ingest_all(
    sources: Iterable[Source],
    *,
    repo_root: Path,
    threshold: float = 0.85,
    dry_run: bool = False,
    limit: int | None = None,
    runner: SubprocessRunner = _default_runner,
    budget: IngestBudget | None = None,
    parallel: int = 5,
    model: str = DEFAULT_MODEL,
    abort_on_rate_limit: bool = True,
) -> list[IngestResult]:
    """Run `ingest_source` voor elk gegeven bronlabel.

    `budget` wordt gedeeld over alle bronnen zodat de cap voor de hele run geldt.
    `parallel` wordt doorgegeven aan elke `ingest_source`-call.

    Als één bron `aborted_rate_limit=True` retourneert en
    `abort_on_rate_limit` aan staat, slaan we de resterende bronnen over: de
    claude-API is voor de huidige sessie op slot. Apply + validate van de
    afgebroken bron worden wel uitgevoerd op het reeds gestagete materiaal.
    """
    results: list[IngestResult] = []
    for source in sources:
        result = ingest_source(
            source,
            repo_root=repo_root,
            threshold=threshold,
            dry_run=dry_run,
            limit=limit,
            runner=runner,
            budget=budget,
            parallel=parallel,
            model=model,
            abort_on_rate_limit=abort_on_rate_limit,
        )
        results.append(result)
        if result.aborted_rate_limit and abort_on_rate_limit:
            break
    return results


# ---------------------------------------------------------------------------
# Dry-run rapportage
# ---------------------------------------------------------------------------


def format_dry_run_summary(
    results: list[IngestResult],
    *,
    threshold: float,
    parallelism: int = 5,
    budget: IngestBudget | None = None,
    model: str = DEFAULT_MODEL,
) -> str:
    """Render een Nederlandstalige samenvatting van een dry-run.

    Gebruikt door `ingest_cmd.py`. Aparte functie zodat tests de output kunnen
    inspecteren zonder de CLI te draaien.
    """
    lines: list[str] = ["Ingest dry-run analyse:", ""]
    total_parse = 0
    total_resolve = 0
    total_cost = 0.0
    total_applied = 0
    total_review = 0
    for r in results:
        lines.append(f"[{r.source}]")
        lines.append(
            f"  Phase 1 parse: {r.parsed} jobs, "
            f"~${r.parse_cost_estimate_usd:.2f} ({model})"
        )
        lines.append(
            f"  Phase 2 resolve: {r.resolved} staging-files unresolved, "
            f"~${r.resolve_cost_estimate_usd:.2f}"
        )
        lines.append(
            f"  Phase 3 apply: ~{r.applied} records auto-mergeable boven "
            f"threshold {threshold:.2f}, ~{r.needs_review} needs-review"
        )
        if r.budget_hit:
            lines.append("  ! budget-cap actief — fase voortijdig gestopt")
        lines.append("")
        total_parse += r.parsed
        total_resolve += r.resolved
        total_cost += r.total_cost_estimate_usd
        total_applied += r.applied
        total_review += r.needs_review

    # Wall-clock schatting: parse ~30s/job sequentieel, resolve ~15s/job;
    # met parallelism=N delen we door N. Geef bandbreedte van 0.7-1.3x.
    seq_seconds = total_parse * 30 + total_resolve * 15
    par_seconds = seq_seconds / max(1, parallelism)
    par_low_h = par_seconds * 0.7 / 3600
    par_high_h = par_seconds * 1.3 / 3600

    lines.append(
        f"Totale geschatte kosten: ~${total_cost:.2f}. "
        f"Wall-clock parallel={parallelism}: ~{par_low_h:.1f}-{par_high_h:.1f} uur."
    )
    if budget is not None and budget.max_claude_calls is not None:
        lines.append(
            f"Budget cap: {budget.used_calls}/{budget.max_claude_calls} "
            f"calls gepland (~${budget.cost_estimate_usd:.2f})."
        )
    lines.append(
        f"Totaal: {total_applied} auto-mergeable, {total_review} needs-review."
    )
    lines.append("Run zonder --dry-run om de pipeline echt te starten.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Build + commit + push helpers (subprocess, mockable)
# ---------------------------------------------------------------------------


def run_build(
    *,
    repo_root: Path,
    runner: SubprocessRunner = _default_runner,
) -> bool:
    """Run `polder build all` via subprocess. Retourneert True bij exit 0."""
    cmd = ["uv", "run", "polder", "build", "all"]
    return runner(cmd) == 0


def commit_changes(
    message: str,
    *,
    repo_root: Path,
    paths: tuple[str, ...] = ("data", "dist", "datapackage.json"),
    push: bool = False,
    branch: str = "main",
) -> str | None:
    """Stage given paths, commit en (optioneel) push. Returnt SHA of None."""
    env = os.environ.copy()
    # Niets stageen als er niets gewijzigd is.
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--porcelain", *paths],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if proc.returncode != 0:
        logger.error("git status failed: %s", proc.stderr)
        return None
    if not proc.stdout.strip():
        logger.info("commit_changes: niets gewijzigd in %s", paths)
        return None

    add = subprocess.run(
        ["git", "-C", str(repo_root), "add", "--", *paths],
        check=False,
        env=env,
    )
    if add.returncode != 0:
        return None

    commit = subprocess.run(
        ["git", "-C", str(repo_root), "commit", "-m", message],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if commit.returncode != 0:
        logger.error("git commit failed: %s", commit.stderr)
        return None

    show = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    sha = show.stdout.strip() if show.returncode == 0 else None

    if push:
        push_proc = subprocess.run(
            ["git", "-C", str(repo_root), "push", "origin", branch],
            check=False,
            env=env,
        )
        if push_proc.returncode != 0:
            logger.error("git push failed (commit %s blijft lokaal)", sha)
    return sha
