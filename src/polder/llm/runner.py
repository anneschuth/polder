"""One-shot convenience-API voor LLM-calls.

`run_skill(...)` bouwt een `SkillSession` op, doet één call, drained. Geen
cache-reuse-winst, maar wel een cleane API voor `polder skill <naam>` CLI-
aanroepen en losse scripts. Voor bulk-werk (ingest, backfill) blijft een
langlevende `SkillSession` per worker-thread efficiënter.

Combineert de response-cache (`polder.llm.cache`) met de session: bij een
cache-hit gaat er geen subprocess open en bij een miss wordt het resultaat
na de call gestored.
"""

from __future__ import annotations

import logging
from pathlib import Path

from polder.llm import cache as response_cache
from polder.llm.session import SkillResult, SkillSession

logger = logging.getLogger("polder.llm.runner")


def _load_input(input_payload: str | Path) -> tuple[str, bytes]:
    """Retourneert (text-vorm voor session, raw bytes voor cache-key)."""
    if isinstance(input_payload, Path):
        raw = input_payload.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
        return text, raw
    raw = input_payload.encode("utf-8")
    return input_payload, raw


def run_skill(
    skill_name: str,
    input_payload: str | Path,
    *,
    model: str | None = None,
    output: Path | None = None,
    use_cache: bool = True,
    max_budget_usd: float = 0.50,
) -> SkillResult:
    """Roep een skill één keer aan.

    Bij `use_cache=True` (default) wordt eerst de response-cache geraadpleegd.
    Cache-hits sparen het hele `claude -p` subprocess uit. Een miss start een
    SkillSession, doet de call, en schrijft het resultaat naar de cache (tenzij
    rate-limited of error).

    `output`, indien meegegeven, krijgt `result.text` geschreven na een
    succesvolle call. Schrijfgedrag is identiek aan het oude `run_skill.sh`:
    een rate-limited of error-result schrijft niet.
    """
    text_input, raw_input = _load_input(input_payload)

    effective_model = _effective_model(skill_name, model)
    cache_key: str | None = None
    if use_cache:
        skill_hash = response_cache.skill_content_hash(skill_name)
        cache_key = response_cache.cache_key(skill_name, skill_hash, effective_model, raw_input)
        cached = response_cache.lookup(skill_name, cache_key)
        if cached is not None:
            logger.debug("Cache hit voor skill=%s", skill_name)
            if output is not None and not cached.is_error and not cached.rate_limited:
                _write_output(output, cached.text)
            return cached

    with SkillSession(skill_name, model=effective_model, max_budget_usd=max_budget_usd) as session:
        result = session.call(text_input)

    if use_cache and cache_key is not None:
        response_cache.store(skill_name, cache_key, result)

    if output is not None and not result.is_error and not result.rate_limited:
        _write_output(output, result.text)

    return result


def _effective_model(skill_name: str, model: str | None) -> str:
    """Resolveer model zoals SkillSession dat doet, zonder hem te starten."""
    from polder.llm.session import _resolve_model

    return _resolve_model(skill_name, model)


def _write_output(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
