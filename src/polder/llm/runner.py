"""One-shot convenience-API voor LLM-calls.

`run_skill(...)` opent per default een verse `SkillSession` per call.
Combineert de response-cache (`polder.llm.cache`) met de session: bij een
cache-hit gaat er geen subprocess open en bij een miss wordt het resultaat
na de call gestored.

LET OP — de oude documentatie claimde dat een langlevende `SkillSession`
"factor 8 goedkoper" was door Anthropic prompt-cache reuse. Dat klopt niet
in de praktijk. `SkillSession` heeft `--no-session-persistence`, maar dat
gaat over disk-persistentie, niet over of het assistant prior messages in
context houdt. Binnen één lopend `claude -p` proces met stream-json input
stapelt elke user-message bovenop de vorige. Na N calls zit ALLE prior
input + responses in conversation-state — `cache_creation_tokens` per
nieuwe call groeit lineair en domineert de kosten.

Gemeten op parse-abd-nieuws (na payload-extractie ~1.3KB per file):
- `reuse_session=True`, 5 calls in één SkillSession: $0.157/call gemiddeld
- `reuse_session=False`, verse SkillSession per call: $0.033/call gemiddeld

Verse SkillSession per call wint factor 5. Subprocess-spawn-overhead (~1s)
is verwaarloosbaar tegenover output-generation (~10s/call dominant). Voor
de `polder skill <naam>`-CLI is reuse-winst sowieso nul (één call). Voor
backfill/ingest geldt dezelfde meting.

`reuse_session=True` is alleen zinvol als je écht meerdere kleine queries
op precies dezelfde context wilt doen (bv. multi-turn dialog binnen één
sessie). Voor parse/resolve-skills met variërende payloads: laat het uit.
"""

from __future__ import annotations

import logging
import re
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
    max_budget_usd: float = 1000.0,
    reuse_session: bool = True,
) -> SkillResult:
    """Roep een skill aan via een thread-local SkillSession.

    Bij `use_cache=True` (default) wordt eerst de response-cache geraadpleegd.
    Cache-hits sparen het hele `claude -p` subprocess uit. Een miss roept
    via `get_or_create_session` de thread-local SkillSession aan, die over
    meerdere calls heen blijft hangen zodat Anthropic prompt-cache de ~40K
    default-context hergebruikt (factor 8 goedkoper dan elke call een nieuwe
    sessie openen).

    Voor losse, one-shot calls (CLI `polder skill X file`) is `reuse_session`
    op True ook prima — er is dan gewoon één session in de huidige thread.

    Met `reuse_session=False` valt deze functie terug op de oude één-shot-
    sessie-per-call (handig voor tests).

    `output`, indien meegegeven, krijgt `result.text` geschreven na een
    succesvolle call. Rate-limited of error-result schrijft niet.
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
                _write_output(output, cached.text, skill_name=skill_name)
            # Disk-cache-hit kost de huidige run niets; overschrijf cost_usd
            # naar 0 zodat budget-caps en cost-rapportage correct zijn. Markeer
            # met response_cache_hit zodat callers cache-hits kunnen tellen
            # zonder te raden op cost==0.
            from dataclasses import replace

            return replace(cached, cost_usd=0.0, response_cache_hit=True)

    if reuse_session:
        from polder.llm.session import get_or_create_session

        session = get_or_create_session(
            skill_name, model=effective_model, max_budget_usd=max_budget_usd
        )
        result = session.call(text_input)
    else:
        with SkillSession(
            skill_name, model=effective_model, max_budget_usd=max_budget_usd
        ) as session:
            result = session.call(text_input)

    if use_cache and cache_key is not None:
        response_cache.store(skill_name, cache_key, result)

    if output is not None and not result.is_error and not result.rate_limited:
        _write_output(output, result.text, skill_name=skill_name)

    return result


def _effective_model(skill_name: str, model: str | None) -> str:
    """Resolveer model zoals SkillSession dat doet, zonder hem te starten."""
    from polder.llm.session import _resolve_model

    return _resolve_model(skill_name, model)


# Skills die JSON moeten leveren (vs. markdown zoals review-pr-diff). Voor deze
# skills strippen we eventuele markdown-fences of inleidende prose voordat we
# de output naar disk schrijven, zodat downstream-consumers (resolver, apply,
# tests) altijd kale JSON zien — ongeacht of het model zich aan de SKILL.md
# "ALLEEN JSON"-instructie houdt.
_JSON_SKILLS: frozenset[str] = frozenset(
    {
        "parse-staatscourant",
        "parse-abd-nieuws",
        "parse-organogram",
        "resolve-staging-proposals",
        "entity-resolution",
        "lookup-person",
    }
)

_FENCE_RE = re.compile(
    r"```(?:json|JSON)?\s*\n(?P<body>.*?)\n```",
    re.DOTALL,
)


def _extract_json_payload(text: str) -> str:
    # Best-effort: lever kale JSON. Strategieën, in volgorde:
    #   1. Hele tekst is al kale JSON ([ of {).
    #   2. Eerste ```json ... ``` block uitpakken.
    #   3. Eerste top-level [ ... ] of { ... } in de tekst pakken.
    # Faalt alles, geef de originele tekst terug zodat de fout opvalt in de
    # downstream JSON-parser in plaats van hier stilletjes te raden.
    stripped = text.strip()
    if stripped.startswith(("[", "{")):
        return stripped + ("\n" if not stripped.endswith("\n") else "")

    fence = _FENCE_RE.search(stripped)
    if fence is not None:
        return fence.group("body").strip() + "\n"

    for opener, closer in (("[", "]"), ("{", "}")):
        start = stripped.find(opener)
        end = stripped.rfind(closer)
        if start != -1 and end > start:
            return stripped[start : end + 1] + "\n"

    return text


def _write_output(path: Path, text: str, *, skill_name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _extract_json_payload(text) if skill_name in _JSON_SKILLS else text
    path.write_text(payload, encoding="utf-8")
