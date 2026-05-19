"""End-to-end staging-pipeline voor polder.

Eén entrypoint dat per bron alle stappen doorloopt:

1. Parse: nieuwe HTML/XML/PDF in `_cache/<bron>/` -> `data/_staging/<bron>-<key>.json`
   via `polder.llm.runner.run_skill`. Pre-filters (`polder.llm.prefilters`)
   skippen input zonder personeels-signaal.
2. Resolve: staging-files zonder `.resolved.json` companion -> `.resolved.json`
   via dezelfde runner.
3. Apply: `polder apply-staging data/_staging/ --apply --threshold T`.
4. Validate: `polder.validate.run_all_checks`.
5. Build/commit/push delegeert de caller (zie `cli/commands/ingest_cmd.py`).

De LLM-call zelf draait in-process via `polder.llm.session.SkillSession`,
een lange-leef `claude -p` stream-json proces per worker-thread dat
Anthropic's prompt-cache hergebruikt over alle calls binnen één run.
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
    """Hard cap op LLM-calls + dollarkosten per pipeline-run.

    `max_claude_calls=None` en `max_cost_usd=None` betekenen unlimited.
    `consume()` boekt één call, optioneel met de werkelijke kosten en
    token-counts uit een SkillResult. Caller checkt na elke job of er nog
    ruimte is via `check()`.
    """

    max_claude_calls: int | None = None
    max_cost_usd: float | None = None
    used_calls: int = 0
    cost_estimate_usd: float = 0.0
    cost_actual_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    def check(self) -> bool:
        """True als er nog ruimte is voor minstens één call."""
        if self.max_claude_calls is not None and self.used_calls >= self.max_claude_calls:
            return False
        if self.max_cost_usd is not None and self.cost_actual_usd >= self.max_cost_usd:
            return False
        return True

    def remaining(self) -> int | None:
        if self.max_claude_calls is None:
            return None
        return max(0, self.max_claude_calls - self.used_calls)

    def consume(
        self,
        n: int = 1,
        *,
        model: str = DEFAULT_MODEL,
        actual_cost_usd: float | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
    ) -> None:
        """Boek `n` calls op het verbruik.

        Als `actual_cost_usd` meegegeven wordt (door een SkillResult) telt
        die op `cost_actual_usd`. De schatting `cost_estimate_usd` blijft een
        rough estimate voor dry-run/UX-doeleinden.
        """
        self.used_calls += n
        per_call = COST_PER_CALL_USD.get(model, COST_PARSE_USD)
        self.cost_estimate_usd += n * per_call
        if actual_cost_usd is not None:
            self.cost_actual_usd += actual_cost_usd
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cache_read_tokens += cache_read_tokens
        self.cache_creation_tokens += cache_creation_tokens


def estimate_cost(*, parse_jobs: int, resolve_jobs: int, model: str = DEFAULT_MODEL) -> float:
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


def _plan_abd_nieuws(cache_root: Path, staging_dir: Path, *, limit: int | None) -> ParsePlan:
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


def _plan_staatscourant(cache_root: Path, staging_dir: Path, *, limit: int | None) -> ParsePlan:
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


def _plan_organogram(cache_root: Path, staging_dir: Path, *, limit: int | None) -> ParsePlan:
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
# Skill-invocation (in-process via polder.llm, geen subprocess naar bash)
# ---------------------------------------------------------------------------


# Een SkillRunner krijgt (skill_name, input_payload, output_path, model) en
# retourneert een SkillResult-achtige tuple (rate_limited, is_error, cost_usd,
# input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens).
# Productie-implementatie roept polder.llm.runner.run_skill aan; tests
# kunnen een mock injecteren.
SkillRunner = Callable[..., "SkillRunResult"]


@dataclass
class SkillRunResult:
    """Compacte adapter rond polder.llm.session.SkillResult voor ingest."""

    ok: bool
    exit_code: int
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


def _default_skill_runner(
    skill_name: str,
    input_payload: str | Path,
    *,
    output: Path,
    model: str | None,
    use_cache: bool = True,
) -> SkillRunResult:
    """Default: roep polder.llm.runner.run_skill aan.

    Vertaalt het SkillResult naar (ok, exit_code) plus telemetrie zodat
    `IngestBudget.consume` echte cijfers krijgt.
    """
    from polder.llm.runner import run_skill

    logger.info("skill=%s input=%s output=%s", skill_name, input_payload, output)
    # Twee redenen om reuse_session uit te zetten:
    #
    # 1. Tool-heavy skills (Read, Bash, WebFetch): session-reuse na een
    #    tool-call leidt tot is_error=True bij de volgende job in dezelfde
    #    thread.
    # 2. Skills met variërende payloads (parse-*): conversation-state stapelt
    #    binnen één SkillSession ongeacht --no-session-persistence. Na N calls
    #    zit alle prior input + output in context, en cache_creation per call
    #    blijft groeien. Gemeten: $0.157/call met reuse vs $0.033/call zonder
    #    op parse-abd-nieuws na de payload-extractie. Factor 5 goedkoper en
    #    de wallclock blijft gelijk (subprocess-spawn ~1s, output-generation
    #    ~10s, dominant).
    # 3. resolve-staging-proposals is de meest tool-heavy skill van allemaal
    #    (Read/Bash/Grep over data/). Onder session-reuse degradeert de
    #    sessie na de eerste tool-call: elke volgende job in dezelfde
    #    worker-thread kreeg alleen een begroeting ("I'm ready to help...")
    #    terug i.p.v. JSON. Gemeten op een lokale daily-run: 9391/9393
    #    staatscourant-resolves corrupt (greeting-only), 90% faalratio,
    #    stilletjes als "ok" geteld. Zelfde klasse als reden 1.
    _NO_SESSION_REUSE = {
        "parse-organogram",
        "lookup-person",
        "parse-abd-nieuws",
        "resolve-staging-proposals",
    }
    reuse_session = skill_name not in _NO_SESSION_REUSE
    result = run_skill(
        skill_name,
        input_payload,
        model=model,
        output=output,
        use_cache=use_cache,
        reuse_session=reuse_session,
    )
    if result.rate_limited:
        return SkillRunResult(
            ok=False,
            exit_code=RATE_LIMIT_EXIT_CODE,
            cost_usd=result.cost_usd,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cache_read_tokens=result.cache_read_tokens,
            cache_creation_tokens=result.cache_creation_tokens,
        )
    if result.is_error:
        return SkillRunResult(
            ok=False,
            exit_code=1,
            cost_usd=result.cost_usd,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cache_read_tokens=result.cache_read_tokens,
            cache_creation_tokens=result.cache_creation_tokens,
        )
    return SkillRunResult(
        ok=True,
        exit_code=0,
        cost_usd=result.cost_usd,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cache_read_tokens=result.cache_read_tokens,
        cache_creation_tokens=result.cache_creation_tokens,
    )


def _empty_array(path: Path) -> None:
    """Schrijf `[]\\n` zoals de oude bash pre-filters deden."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[]\n", encoding="utf-8")


def run_parse_job(
    job: ParseJob,
    source: Source,
    *,
    repo_root: Path,
    runner: SkillRunner = _default_skill_runner,
    model: str | None = None,
) -> SkillRunResult:
    """Roep de juiste parse-skill aan in-process via polder.llm.runner.

    Retourneert een `SkillRunResult` (oude tuple `(ok, exit_code)` zit erin
    onder dezelfde namen). Pre-filters (`abd_nieuws_has_signal`,
    `staatscourant_has_signal`) draaien voor de LLM-call; bij negatief
    signaal wordt `[]` weggeschreven en geen LLM aangeroepen.
    """
    del repo_root  # niet langer nodig, behouden voor signature-compat met tests

    from polder.llm import prefilters

    job.output_path.parent.mkdir(parents=True, exist_ok=True)

    if source == "abd-nieuws":
        html = job.input_path.read_text(encoding="utf-8")
        if not prefilters.abd_nieuws_has_signal(html):
            _empty_array(job.output_path)
            return SkillRunResult(ok=True, exit_code=0)
        payload = prefilters.extract_abd_payload(html)
        return runner("parse-abd-nieuws", payload, output=job.output_path, model=model)

    if source == "staatscourant":
        xml = job.input_path.read_text(encoding="utf-8")
        if not prefilters.staatscourant_has_signal(xml):
            _empty_array(job.output_path)
            return SkillRunResult(ok=True, exit_code=0)
        return runner("parse-staatscourant", job.input_path, output=job.output_path, model=model)

    if source == "organogram":
        ministerie = job.extra_args[0] if job.extra_args else "unknown"
        abs_pdf = job.input_path.resolve()
        prompt = (
            f"Ministerie: {ministerie}\n"
            f"PDF-pad: {abs_pdf}\n\n"
            f"Lees de PDF met de Read-tool en parse het organogram volgens de skill-instructies."
        )
        return runner("parse-organogram", prompt, output=job.output_path, model=model)

    raise ValueError(f"onbekende bron: {source}")


def run_resolve_job(
    staging_path: Path,
    *,
    repo_root: Path,
    runner: SkillRunner = _default_skill_runner,
    model: str | None = None,
) -> SkillRunResult:
    """Roep de resolve-skill aan. Retourneert een SkillRunResult.

    `exit_code == RATE_LIMIT_EXIT_CODE` betekent: rate-limit, fase afbreken.
    """
    del repo_root  # niet langer nodig
    output = staging_path.parent / f"{staging_path.stem}.resolved.json"
    return runner("resolve-staging-proposals", staging_path, output=output, model=model)


# ---------------------------------------------------------------------------
# ThreadPool-worker wrappers met SkillSession-cleanup
# ---------------------------------------------------------------------------


def _run_parse_job_with_session_cleanup(
    job: ParseJob,
    source: Source,
    *,
    repo_root: Path,
    runner: SkillRunner = _default_skill_runner,
    model: str | None = None,
) -> SkillRunResult:
    """Run parse-job + sluit thread-local SkillSessions als de worker stopt.

    Python's ThreadPoolExecutor herbruikt threads, dus de session blijft over
    meerdere jobs hangen. Pas wanneer de pool sluit, willen we de subprocess
    eindigen. We checken via een `atexit`-achtige flag of dit de laatste call
    is, maar simpeler: laat de session bij thread-exit hangen — Python sluit
    bij interpreter-shutdown alle subprocessen netjes via TerminateProcess.

    Voor nu: gewoon proxy naar run_parse_job; de cleanup gebeurt in een
    `finally`-blok rond de hele ingest-fase.
    """
    return run_parse_job(job, source, repo_root=repo_root, runner=runner, model=model)


def _run_resolve_job_with_session_cleanup(
    staging_path: Path,
    *,
    repo_root: Path,
    runner: SkillRunner = _default_skill_runner,
    model: str | None = None,
) -> SkillRunResult:
    """Idem voor resolve-jobs."""
    return run_resolve_job(staging_path, repo_root=repo_root, runner=runner, model=model)


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
                        reasons=[f"confidence {action.confidence:.2f} < drempel {threshold:.2f}"],
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


def _count_needs_review(proposals: list[dict], *, threshold: float) -> int:
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
    runner: SkillRunner = _default_skill_runner,
    budget: IngestBudget | None = None,
    parallel: int = 5,
    model: str = DEFAULT_MODEL,
    abort_on_rate_limit: bool = True,
) -> IngestResult:
    """Run parse -> resolve -> apply -> validate voor één bron.

    `dry_run=True` slaat de LLM-calls en de echte schrijf-acties over; de
    result-velden tonen wat de run zou hebben gedaan, inclusief geschatte
    LLM-kosten in USD.

    `budget` legt een hard cap op LLM-calls en (optioneel) op kosten in USD.
    Zodra `budget.check()` False teruggeeft stopt de fase met `budget_hit=True`
    op het result. In dry-run wordt het budget ook geconsumeerd zodat een
    `ingest_all` over meerdere bronnen de cap correct doorrekent.

    `parallel` (default 5) bepaalt het aantal worker-threads voor de parse-
    en resolve-fase. Iedere worker draait een `SkillSession` (lange-leef
    `claude -p` stream-json proces) en wacht vooral op IO; `ThreadPoolExecutor`
    is daardoor goedkoper dan `ProcessPoolExecutor`. De apply-fase blijft
    single-threaded omdat die naar `data/` schrijft.

    `model` wordt doorgegeven aan `polder.llm.runner.run_skill` en eindigt op
    `claude -p --model`. Default Haiku 4.5; vision-skills overrulen zelf naar
    Opus (zie `MODEL_OVERRIDES` in `polder.llm.session`).

    `abort_on_rate_limit` (default True): zodra één parse- of resolve-job
    `RATE_LIMIT_EXIT_CODE` retourneert breekt de pipeline de huidige fase af.
    """
    if parallel < 1:
        raise ValueError(f"parallel moet >= 1 zijn, kreeg {parallel}")
    cache_root = cache_root or (repo_root / "_cache")
    staging_dir = staging_dir or (repo_root / "data" / "_staging")
    data_dir = data_dir or (repo_root / "data")
    schemas_dir = schemas_dir or (repo_root / "schemas")

    # Cleanup thread-local SkillSessions na de batch. Workers in
    # ThreadPoolExecutor blijven bestaan tot de pool sluit; hun sessies
    # zouden anders blijven hangen. We doen het in een atexit-achtig blok
    # via futures die `close_thread_local_sessions` aanroepen.
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
        # Sluit alle SkillSessions (incl. die van pool-workers) na de batch.
        from polder.llm.session import close_all_sessions

        close_all_sessions()


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
    runner: SkillRunner,
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
    plan = plan_parse(source, cache_root=cache_root, staging_dir=staging_dir, limit=limit)
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
                    f"[dry-run] budget-cap stopt parse na {planned_parse} van {plan.count} jobs"
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
                        _run_parse_job_with_session_cleanup,
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
                        outcome = future.result()
                    except Exception as exc:
                        logger.error("parse-job %s gefaald: %s", job.input_path, exc)
                        outcome = SkillRunResult(ok=False, exit_code=1)
                    if budget is not None:
                        budget.cost_actual_usd += outcome.cost_usd
                        budget.input_tokens += outcome.input_tokens
                        budget.output_tokens += outcome.output_tokens
                        budget.cache_read_tokens += outcome.cache_read_tokens
                        budget.cache_creation_tokens += outcome.cache_creation_tokens
                    if outcome.exit_code == RATE_LIMIT_EXIT_CODE:
                        rate_limit_seen = True
                        result.parse_failed += 1
                        continue
                    if outcome.ok and job.output_path.exists():
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
                        _run_resolve_job_with_session_cleanup,
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
                        outcome = future.result()
                    except Exception as exc:
                        logger.error("resolve-job %s gefaald: %s", staging_path, exc)
                        outcome = SkillRunResult(ok=False, exit_code=1)
                    if budget is not None:
                        budget.cost_actual_usd += outcome.cost_usd
                        budget.input_tokens += outcome.input_tokens
                        budget.output_tokens += outcome.output_tokens
                        budget.cache_read_tokens += outcome.cache_read_tokens
                        budget.cache_creation_tokens += outcome.cache_creation_tokens
                    if outcome.exit_code == RATE_LIMIT_EXIT_CODE:
                        rate_limit_seen = True
                        result.resolve_failed += 1
                        continue
                    if outcome.ok and staging_path.with_suffix(".resolved.json").exists():
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
                result.needs_review = _count_needs_review(proposals, threshold=threshold)
                result.notes.append(
                    f"[dry-run] apply: {len(actions)} auto-mergeable boven "
                    f"threshold {threshold:.2f}, "
                    f"{result.needs_review} needs-review, {len(skipped)} skip"
                )
    else:
        try:
            applied, skipped = run_apply(staging_dir, data_dir=data_dir, threshold=threshold)
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
            result.validate_ok = run_validate(data_dir=data_dir, schemas_dir=schemas_dir)
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
    runner: SkillRunner = _default_skill_runner,
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
            f"  Phase 1 parse: {r.parsed} jobs, ~${r.parse_cost_estimate_usd:.2f} ({model})"
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
    lines.append(f"Totaal: {total_applied} auto-mergeable, {total_review} needs-review.")
    lines.append("Run zonder --dry-run om de pipeline echt te starten.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Build + commit + push helpers (subprocess, mockable)
# ---------------------------------------------------------------------------


def run_build(
    *,
    repo_root: Path,
    runner: SkillRunner = _default_skill_runner,
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
