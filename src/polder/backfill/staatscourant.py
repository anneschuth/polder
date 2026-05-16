"""Backfill voor de parse-staatscourant skill.

Symmetrische tegenhanger van `abd_nieuws.py`. Pipeline per file:
  1. `prefilters.staatscourant_has_signal(xml)` — skip als `<intitule>`
     geen benoeming/ontslag-keyword heeft
  2. `prefilters.extract_staatscourant_payload(xml, filename)` — strip XML
     tags, behoud intitule + body + KB-referentie + URL
  3. Verse `SkillSession` per file via `run_skill` — geen conversation-
     stacking (zie runner.py docstring)
  4. Response-cache op de extracted payload — herhaalde runs zijn gratis
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path

from polder.backfill._budget import CostBudget
from polder.llm import prefilters
from polder.llm.runner import run_skill

logger = logging.getLogger("polder.backfill.staatscourant")


@dataclass
class BackfillResult:
    """Samenvatting van een backfill-run."""

    source: str
    total_candidates: int = 0
    pre_filtered: int = 0
    cache_hits: int = 0
    parsed: int = 0
    failed: int = 0
    rate_limited: bool = False
    cost_capped: bool = False
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    notes: list[str] = field(default_factory=list)


def _staging_path_for(staging_dir: Path, xml_path: Path) -> Path:
    key = xml_path.stem
    today = _date.today().isoformat()
    return staging_dir / f"staatscourant-{key}-{today}.json"


def list_candidates(
    cache_dir: Path,
    *,
    since: str | None = None,
    until: str | None = None,
    pattern: str | None = None,
    limit: int | None = None,
    staging_dir: Path | None = None,
) -> list[Path]:
    """Verzamel XML-paden in `cache_dir` gefilterd op datum / regex / limit.

    Als ``staging_dir`` is gegeven worden XMLs waarvan vandaag al een
    non-empty (>3 byte) staging-file bestaat ook gefilterd. Lege `[]`-files
    tellen niet als "klaar" — die kunnen van een gefaalde parse komen en
    moeten opnieuw langs de prefilter (en eventueel de skill).
    """
    if not cache_dir.exists():
        return []
    # Skip search-result-records (.sru.xml). Die zijn geen besluit-XMLs.
    paths = [p for p in sorted(cache_dir.rglob("*.xml")) if ".sru." not in p.name]

    if pattern:
        rx = re.compile(pattern)
        paths = [p for p in paths if rx.search(str(p))]

    if since or until:
        since_iso = since or ""
        until_iso = until or "9999"
        filtered: list[Path] = []
        for p in paths:
            mtime = _date.fromtimestamp(p.stat().st_mtime).isoformat()
            if since_iso and mtime < since_iso:
                continue
            if until_iso and mtime > until_iso:
                continue
            filtered.append(p)
        paths = filtered

    if staging_dir is not None:
        today_iso = _date.today().isoformat()
        unstaged: list[Path] = []
        for p in paths:
            output = staging_dir / f"staatscourant-{p.stem}-{today_iso}.json"
            # Skip alleen wanneer de today-file substantieel is (>3 byte).
            # Een lege `[]` kan een prefilter-skip zijn, maar net zo goed
            # een gefaalde LLM-parse — die mag opnieuw worden geprobeerd.
            if output.exists() and output.stat().st_size > 3:
                continue
            unstaged.append(p)
        paths = unstaged

    if limit is not None and limit > 0:
        paths = paths[:limit]
    return paths


def _write_empty_array(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[]\n", encoding="utf-8")


def _process_one(
    xml_path: Path,
    staging_dir: Path,
    *,
    use_cache: bool,
    model: str | None,
) -> tuple[str, BackfillResult]:
    """Verwerk één XML-file. Returns ("ok"|"skip"|"hit"|"fail"|"rate_limit", deltas).

    De LLM-call gaat via `run_skill`, die per default een verse SkillSession
    opent (zie runner.py docstring). Response-cache zit in de runner.
    """
    deltas = BackfillResult(source="staatscourant")
    output = _staging_path_for(staging_dir, xml_path)
    # Idempotent: als de staging-file van vandaag al bestaat en niet-leeg
    # is, beschouw als done.
    if output.exists() and output.stat().st_size > 3:
        deltas.cache_hits = 1
        return "hit", deltas
    xml = xml_path.read_text(encoding="utf-8")

    if not prefilters.staatscourant_has_signal(xml):
        _write_empty_array(output)
        deltas.pre_filtered = 1
        return "skip", deltas

    payload = prefilters.extract_staatscourant_payload(xml, source_filename=xml_path.name)
    output.parent.mkdir(parents=True, exist_ok=True)
    result = run_skill(
        "parse-staatscourant",
        payload,
        model=model,
        output=output,
        use_cache=use_cache,
    )
    deltas.cost_usd = result.cost_usd
    deltas.input_tokens = result.input_tokens
    deltas.output_tokens = result.output_tokens
    deltas.cache_read_tokens = result.cache_read_tokens
    deltas.cache_creation_tokens = result.cache_creation_tokens

    if result.rate_limited:
        return "rate_limit", deltas
    if result.is_error:
        return "fail", deltas
    if result.response_cache_hit:
        deltas.cache_hits = 1
        return "hit", deltas
    deltas.parsed = 1
    return "ok", deltas


def backfill(
    repo_root: Path,
    *,
    since: str | None = None,
    until: str | None = None,
    pattern: str | None = None,
    limit: int | None = None,
    parallel: int = 1,
    model: str | None = None,
    use_cache: bool = True,
    abort_on_rate_limit: bool = True,
    max_cost_usd: float | None = None,
) -> BackfillResult:
    """Run parse-staatscourant op alle gefilterde XMLs."""
    cache_dir = repo_root / "_cache" / "staatscourant"
    staging_dir = repo_root / "data" / "_staging"

    candidates = list_candidates(
        cache_dir,
        since=since,
        until=until,
        pattern=pattern,
        limit=limit,
        staging_dir=staging_dir,
    )
    result = BackfillResult(source="staatscourant", total_candidates=len(candidates))
    if not candidates:
        result.notes.append(f"Geen kandidaten in {cache_dir}")
        return result

    budget = CostBudget(max_cost_usd)

    if parallel <= 1:
        for path in candidates:
            if budget.exceeded():
                result.notes.append(
                    f"cost-cap ${max_cost_usd:.2f} bereikt na {result.parsed} parsed"
                )
                break
            status, deltas = _process_one(path, staging_dir, use_cache=use_cache, model=model)
            _merge(result, status, deltas)
            budget.add(deltas.cost_usd)
            if status == "rate_limit" and abort_on_rate_limit:
                result.rate_limited = True
                result.notes.append(
                    f"rate-limit op {path.name}, afgebroken na {result.parsed} parsed"
                )
                break
        return result

    def worker(paths: list[Path]) -> BackfillResult:
        local = BackfillResult(source="staatscourant")
        for path in paths:
            if budget.exceeded():
                local.cost_capped = True
                break
            status, deltas = _process_one(path, staging_dir, use_cache=use_cache, model=model)
            _merge(local, status, deltas)
            budget.add(deltas.cost_usd)
            if status == "rate_limit" and abort_on_rate_limit:
                local.rate_limited = True
                break
        return local

    chunks = _split_chunks(candidates, parallel)
    with ThreadPoolExecutor(max_workers=parallel) as executor:
        futures = [executor.submit(worker, chunk) for chunk in chunks]
        for future in as_completed(futures):
            sub = future.result()
            _aggregate(result, sub)
            if sub.rate_limited and abort_on_rate_limit:
                result.rate_limited = True
                result.notes.append("rate-limit gedetecteerd in worker")
            if sub.cost_capped:
                result.cost_capped = True

    if result.cost_capped and max_cost_usd is not None:
        result.notes.append(
            f"cost-cap ${max_cost_usd:.2f} bereikt (parallel), "
            f"totaal ${budget.spent_usd:.2f} na {result.parsed} parsed"
        )

    return result


def _merge(target: BackfillResult, status: str, deltas: BackfillResult) -> None:
    target.pre_filtered += deltas.pre_filtered
    target.cache_hits += deltas.cache_hits
    target.parsed += deltas.parsed
    target.cost_usd += deltas.cost_usd
    target.input_tokens += deltas.input_tokens
    target.output_tokens += deltas.output_tokens
    target.cache_read_tokens += deltas.cache_read_tokens
    target.cache_creation_tokens += deltas.cache_creation_tokens
    if status == "fail":
        target.failed += 1


def _aggregate(target: BackfillResult, sub: BackfillResult) -> None:
    target.pre_filtered += sub.pre_filtered
    target.cache_hits += sub.cache_hits
    target.parsed += sub.parsed
    target.failed += sub.failed
    target.cost_usd += sub.cost_usd
    target.input_tokens += sub.input_tokens
    target.output_tokens += sub.output_tokens
    target.cache_read_tokens += sub.cache_read_tokens
    target.cache_creation_tokens += sub.cache_creation_tokens


def _split_chunks(items: list[Path], n: int) -> list[list[Path]]:
    if n <= 1 or len(items) <= n:
        return [items]
    chunks: list[list[Path]] = [[] for _ in range(n)]
    for i, item in enumerate(items):
        chunks[i % n].append(item)
    return [c for c in chunks if c]
