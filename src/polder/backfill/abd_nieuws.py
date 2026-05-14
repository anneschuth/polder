"""Backfill voor de parse-abd-nieuws skill.

Vervangt `scripts/reparse_abd_nieuws.sh` en helpers. Doelgebruik: alle gedownloade
ABD-nieuwsberichten opnieuw door de huidige skill halen na een schema- of
skill-tweak. Anthropic's prompt-cache binnen één `SkillSession` plus de
response-cache zorgen dat dit goedkoop is.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path

from polder.llm import cache as response_cache
from polder.llm import prefilters
from polder.llm.session import SkillSession

logger = logging.getLogger("polder.backfill.abd_nieuws")


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
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    notes: list[str] = field(default_factory=list)


def _staging_path_for(staging_dir: Path, html_path: Path) -> Path:
    key = html_path.stem
    today = _date.today().isoformat()
    return staging_dir / f"abd-nieuws-{key}-{today}.json"


def list_candidates(
    cache_dir: Path,
    *,
    since: str | None = None,
    until: str | None = None,
    pattern: str | None = None,
    limit: int | None = None,
    staging_dir: Path | None = None,
) -> list[Path]:
    """Verzamel HTML-paden in `cache_dir` filterd op datum / regex / limit.

    Datum-filter werkt op de file's modification-time (mtime) als ISO-string.
    Voor abd-nieuws is dat een goede proxy voor scrape-datum.

    Als ``staging_dir`` is gegeven worden HTMLs waarvan vandaag al een
    non-empty staging-file bestaat ook gefilterd — anders zou een retry-loop
    met --limit telkens dezelfde alfabetisch-eerste records pakken en geen
    progressie maken in de overige cache.
    """
    if not cache_dir.exists():
        return []
    paths = sorted(cache_dir.rglob("*.html"))

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
            output = staging_dir / f"abd-nieuws-{p.stem}-{today_iso}.json"
            # Skip alles wat vandaag al een staging-file heeft (met content
            # of prefilter-skip-`[]`). Voorkomt dat een retry-loop telkens
            # dezelfde records pakt.
            if output.exists():
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
    session: SkillSession,
    html_path: Path,
    staging_dir: Path,
    *,
    use_cache: bool,
    skill_hash: str,
) -> tuple[str, BackfillResult]:
    """Verwerk één HTML-file. Returns ("ok"|"skip"|"hit"|"fail"|"rate_limit", deltas)."""
    deltas = BackfillResult(source="abd-nieuws")
    output = _staging_path_for(staging_dir, html_path)
    # Idempotent: als de staging-file van vandaag al bestaat en niet-leeg
    # is, beschouw als done. Voorkomt dat een retry-loop telkens dezelfde
    # records opnieuw probeert na rate-limit.
    if output.exists() and output.stat().st_size > 3:
        deltas.cache_hits = 1
        return "hit", deltas
    html = html_path.read_text(encoding="utf-8")

    if not prefilters.abd_nieuws_has_signal(html):
        _write_empty_array(output)
        deltas.pre_filtered = 1
        return "skip", deltas

    if use_cache:
        raw = html.encode("utf-8")
        key = response_cache.cache_key("parse-abd-nieuws", skill_hash, session.model, raw)
        cached = response_cache.lookup("parse-abd-nieuws", key)
        if cached is not None and not cached.is_error and not cached.rate_limited:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(cached.text, encoding="utf-8")
            deltas.cache_hits = 1
            return "hit", deltas

    result = session.call(html_path)
    deltas.cost_usd = result.cost_usd
    deltas.input_tokens = result.input_tokens
    deltas.output_tokens = result.output_tokens
    deltas.cache_read_tokens = result.cache_read_tokens
    deltas.cache_creation_tokens = result.cache_creation_tokens

    if result.rate_limited:
        return "rate_limit", deltas
    if result.is_error:
        return "fail", deltas

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(result.text, encoding="utf-8")

    if use_cache:
        raw = html.encode("utf-8")
        key = response_cache.cache_key("parse-abd-nieuws", skill_hash, session.model, raw)
        response_cache.store("parse-abd-nieuws", key, result)
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
    """Run parse-abd-nieuws op alle gefilterde HTMLs.

    Bij `parallel > 1` worden N parallelle `SkillSession`-instances opgezet,
    elk met eigen prompt-cache. Bij `parallel == 1` profiteert één sessie
    maximaal van Anthropic's prompt-cache; dat is meestal goedkoper per call
    maar duurt langer.
    """
    cache_dir = repo_root / "_cache" / "abd-nieuws"
    staging_dir = repo_root / "data" / "_staging"

    candidates = list_candidates(
        cache_dir,
        since=since,
        until=until,
        pattern=pattern,
        limit=limit,
        staging_dir=staging_dir,
    )
    result = BackfillResult(source="abd-nieuws", total_candidates=len(candidates))
    if not candidates:
        result.notes.append(f"Geen kandidaten in {cache_dir}")
        return result

    skill_hash = response_cache.skill_content_hash("parse-abd-nieuws")

    if parallel <= 1:
        with SkillSession("parse-abd-nieuws", model=model) as session:
            for path in candidates:
                status, deltas = _process_one(
                    session, path, staging_dir, use_cache=use_cache, skill_hash=skill_hash
                )
                _merge(result, status, deltas)
                if status == "rate_limit" and abort_on_rate_limit:
                    result.rate_limited = True
                    result.notes.append(
                        f"rate-limit op {path.name}, afgebroken na {result.parsed} parsed"
                    )
                    break
                if max_cost_usd is not None and result.cost_usd >= max_cost_usd:
                    result.notes.append(
                        f"cost-cap ${max_cost_usd:.2f} bereikt na {result.parsed} parsed"
                    )
                    break
        return result

    # parallel > 1: één sessie per worker
    def worker(paths: list[Path]) -> BackfillResult:
        local = BackfillResult(source="abd-nieuws")
        with SkillSession("parse-abd-nieuws", model=model) as session:
            for path in paths:
                status, deltas = _process_one(
                    session, path, staging_dir, use_cache=use_cache, skill_hash=skill_hash
                )
                _merge(local, status, deltas)
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
    """Verdeel items in n ongeveer-gelijke chunks."""
    if n <= 1 or len(items) <= n:
        return [items]
    chunks: list[list[Path]] = [[] for _ in range(n)]
    for i, item in enumerate(items):
        chunks[i % n].append(item)
    return [c for c in chunks if c]
