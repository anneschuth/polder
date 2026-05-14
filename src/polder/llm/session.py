"""Lange-leef `claude -p` stream-json sessie per skill.

Eén lopend `claude -p` proces per `SkillSession`-instantie. Anthropic prompt-
cached automatisch de ~40K default-context op call 1, en leest hem voor 10%
van de prijs bij call 2..N. Voor een back-to-back backfill is dat factor 8
goedkoper dan per-call subprocesses.

Gebruik:

    with SkillSession("parse-abd-nieuws") as session:
        for html_path in paths:
            result = session.call(html_path)
            if result.rate_limited:
                break  # caller mapt naar exit 99
            ...

Eén `SkillSession`-instantie is bedoeld voor sequentieel gebruik binnen één
thread. Een `ThreadPoolExecutor` met N workers maakt N parallelle sessies
aan, één per worker.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self

logger = logging.getLogger("polder.llm.session")


RATE_LIMIT_EXIT_CODE = 99


DEFAULT_MODEL = "claude-haiku-4-5"


MODEL_OVERRIDES: dict[str, str] = {
    "parse-organogram": "claude-opus-4-7",
}


_RATE_LIMIT_PATTERNS = re.compile(
    r"hit your (usage |rate )?limit"
    r"|rate[ -]limit"
    r"|rate limited"
    r"|usage limit reached"
    r"|exceeded.*limit"
    r"|overloaded"
    r"|429",
    re.IGNORECASE,
)


CACHE_HIT_THRESHOLD = 5_000


def _repo_root() -> Path:
    """Project-root: bestand zit in `src/polder/llm/`."""
    return Path(__file__).resolve().parents[3]


def _skill_path(skill_name: str) -> Path:
    path = _repo_root() / ".claude" / "skills" / skill_name / "SKILL.md"
    if not path.exists():
        raise FileNotFoundError(f"Skill niet gevonden: {path}")
    return path


def _resolve_model(skill_name: str, model: str | None) -> str:
    if model is not None:
        return model
    return MODEL_OVERRIDES.get(skill_name, DEFAULT_MODEL)


@dataclass(frozen=True)
class SkillResult:
    """Resultaat van één `SkillSession.call()`."""

    text: str
    model: str
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    rate_limited: bool = False
    is_error: bool = False
    error_message: str | None = None

    @property
    def cache_hit(self) -> bool:
        """Heeft Anthropic substantieel cache gelezen voor deze call?"""
        return self.cache_read_tokens >= CACHE_HIT_THRESHOLD


@dataclass
class _Stats:
    """Per-sessie accumulator voor reporting."""

    calls: int = 0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_hits: int = 0
    rate_limited: int = 0


class SkillSession:
    """Context-managed lange-leef `claude -p` stream-json proces."""

    def __init__(
        self,
        skill_name: str,
        *,
        model: str | None = None,
        max_budget_usd: float = 0.50,
        fallback_model: str | None = "claude-sonnet-4-6",
        claude_bin: str = "claude",
        allow_tools: bool = False,
        extra_args: list[str] | None = None,
    ) -> None:
        self.skill_name = skill_name
        self.model = _resolve_model(skill_name, model)
        self.max_budget_usd = max_budget_usd
        self.fallback_model = fallback_model
        self.claude_bin = claude_bin
        # Default: geen tools, model produceert direct response. Skills die
        # tools nodig hebben staan hier expliciet.
        # - parse-organogram: Read-tool voor de PDF.
        # - lookup-person: Bash voor `polder show/search/lookup wikidata`,
        #   WebFetch en WebSearch voor externe bronnen.
        #
        # parse-staatscourant en parse-abd-nieuws hebben GEEN tools nodig: de
        # resolver lost slug-hallucinaties (ministerie-X -> min-X, minister-X
        # -> minister-min-X) achteraf op via fuzzy-match in
        # PolderIndex.posts_by_org_class. Skills tools geven kost 20-50x zoveel
        # tijd (tool-call-roundtrips per record) en levert geen extra waarde.
        _TOOLED_SKILLS = {"parse-organogram", "lookup-person"}
        self.allow_tools = allow_tools or skill_name in _TOOLED_SKILLS
        self.extra_args = list(extra_args or [])
        self._skill_path = _skill_path(skill_name)
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self.stats = _Stats()

    def _build_cmd(self) -> list[str]:
        cmd: list[str] = [
            self.claude_bin,
            "-p",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
            "--system-prompt-file",
            str(self._skill_path),
            "--model",
            self.model,
            "--no-session-persistence",
            "--max-budget-usd",
            str(self.max_budget_usd),
            "--disable-slash-commands",
            "--permission-mode",
            "bypassPermissions",
        ]
        if not self.allow_tools:
            cmd += ["--tools", ""]
        if self.fallback_model:
            cmd += ["--fallback-model", self.fallback_model]
        cmd += self.extra_args
        return cmd

    def __enter__(self) -> Self:
        cmd = self._build_cmd()
        logger.debug("Starting SkillSession: %s", " ".join(cmd))
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=_repo_root(),
            bufsize=1,
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self, *, timeout: float = 10.0) -> None:
        """Sluit stdin, drain stdout/stderr, wacht op exit."""
        if self._proc is None:
            return
        proc = self._proc
        self._proc = None
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except BrokenPipeError:
            pass
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.warning("SkillSession close timeout, terminating")
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()

    def _read_payload(self, input_payload: str | Path) -> str:
        if isinstance(input_payload, Path):
            return input_payload.read_text(encoding="utf-8")
        return input_payload

    def call(self, input_payload: str | Path) -> SkillResult:
        """Stuur één user-message, retourneer het resultaat-event."""
        if self._proc is None:
            raise RuntimeError("SkillSession is niet open; gebruik als context manager")
        proc = self._proc
        assert proc.stdin is not None
        assert proc.stdout is not None

        content = self._read_payload(input_payload)
        event = {"type": "user", "message": {"role": "user", "content": content}}
        payload = json.dumps(event, ensure_ascii=False) + "\n"

        with self._lock:
            try:
                proc.stdin.write(payload)
                proc.stdin.flush()
            except BrokenPipeError as e:
                stderr_tail = self._read_stderr_nonblocking()
                raise RuntimeError(f"claude -p subprocess broke: {stderr_tail}") from e

            result_event: dict | None = None
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("Non-JSON line on claude stdout: %r", line[:200])
                    continue
                if obj.get("type") == "result":
                    result_event = obj
                    break

        if result_event is None:
            stderr_tail = self._read_stderr_nonblocking()
            return SkillResult(
                text="",
                model=self.model,
                is_error=True,
                error_message=f"No result event before EOF. stderr={stderr_tail!r}",
            )

        result = self._parse_result(result_event)
        self._update_stats(result)
        return result

    def _read_stderr_nonblocking(self) -> str:
        """Lees beschikbare stderr zonder te blokkeren. Best-effort."""
        if self._proc is None or self._proc.stderr is None:
            return ""
        try:
            import os
            import select

            fd = self._proc.stderr.fileno()
            chunks: list[str] = []
            while True:
                ready, _, _ = select.select([fd], [], [], 0)
                if not ready:
                    break
                data = os.read(fd, 4096)
                if not data:
                    break
                chunks.append(data.decode("utf-8", errors="replace"))
            return "".join(chunks)[-1000:]
        except OSError:
            return ""

    def _parse_result(self, event: dict) -> SkillResult:
        usage = event.get("usage") or {}
        text = event.get("result", "") or ""
        is_error = bool(event.get("is_error"))
        api_status = event.get("api_error_status")

        rate_limited = False
        if api_status == 429:
            rate_limited = True
        if is_error and api_status:
            rate_limited = rate_limited or _RATE_LIMIT_PATTERNS.search(str(api_status)) is not None
        if _RATE_LIMIT_PATTERNS.search(text):
            rate_limited = True

        return SkillResult(
            text=text,
            model=self.model,
            cost_usd=float(event.get("total_cost_usd") or 0.0),
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            cache_read_tokens=int(usage.get("cache_read_input_tokens") or 0),
            cache_creation_tokens=int(usage.get("cache_creation_input_tokens") or 0),
            rate_limited=rate_limited,
            is_error=is_error,
            error_message=text if is_error else None,
        )

    def _update_stats(self, result: SkillResult) -> None:
        self.stats.calls += 1
        self.stats.cost_usd += result.cost_usd
        self.stats.input_tokens += result.input_tokens
        self.stats.output_tokens += result.output_tokens
        self.stats.cache_read_tokens += result.cache_read_tokens
        self.stats.cache_creation_tokens += result.cache_creation_tokens
        if result.cache_hit:
            self.stats.cache_hits += 1
        if result.rate_limited:
            self.stats.rate_limited += 1


# ---------------------------------------------------------------------------
# Thread-local SkillSession-pool
# ---------------------------------------------------------------------------

# Een ThreadPoolExecutor-worker krijgt zijn eigen langlevende SkillSession via
# `get_or_create_session(skill_name, model)`. Die wordt over alle jobs binnen
# één batch hergebruikt, zodat Anthropic prompt-cache de ~40K default-context
# pakt na call 1. Dit maakt parse/resolve-runs ongeveer factor 8 goedkoper
# dan een SkillSession per job openen+sluiten.
#
# Caller (ingest_source) moet `close_thread_local_sessions()` aanroepen aan
# het eind van de batch (in een finally-blok) om de subprocessen netjes te
# stoppen.

_thread_local = threading.local()

# Globale registry van actieve sessies per thread, zodat een atexit-handler
# (of een batch-cleanup van buitenaf) ze allemaal kan sluiten. Workers in
# ThreadPoolExecutor zijn anonymous voor de main thread, dus we tracken
# expliciet.
_all_sessions: list[SkillSession] = []
_all_sessions_lock = threading.Lock()


def _register_session(session: SkillSession) -> None:
    with _all_sessions_lock:
        _all_sessions.append(session)


def close_all_sessions() -> None:
    """Sluit ALLE actieve SkillSessions, ongeacht thread.

    Aanroepen aan het eind van een batch (bv. ingest_source-finally) om de
    workers in een ThreadPoolExecutor niet eeuwig subprocessen open te
    laten houden. Idempotent.
    """
    with _all_sessions_lock:
        sessions = list(_all_sessions)
        _all_sessions.clear()
    for session in sessions:
        try:
            session.close()
        except Exception as exc:
            logger.warning("Fout bij sluiten van SkillSession: %s", exc)


import atexit as _atexit  # noqa: E402

_atexit.register(close_all_sessions)


def get_or_create_session(
    skill_name: str,
    *,
    model: str | None = None,
    max_budget_usd: float = 0.50,
) -> SkillSession:
    """Geef de thread-local SkillSession voor `skill_name`.

    Als er voor deze thread + skill nog geen session is, opent deze functie
    er één. De caller hoeft de session niet zelf te sluiten; dat doet
    `close_thread_local_sessions()` aan het eind van de batch.

    Als de bestaande session op een ander model draait dan gevraagd, wordt
    hij gesloten en een nieuwe geopend.
    """
    sessions: dict[tuple[str, str], SkillSession] = getattr(_thread_local, "sessions", None) or {}
    if not getattr(_thread_local, "sessions", None):
        _thread_local.sessions = sessions

    resolved_model = _resolve_model(skill_name, model)
    key = (skill_name, resolved_model)
    existing = sessions.get(key)
    if existing is not None and existing._proc is not None:
        return existing

    # Geen sessie of subprocess al dood: open een nieuwe
    session = SkillSession(skill_name, model=resolved_model, max_budget_usd=max_budget_usd)
    session.__enter__()  # start subprocess
    sessions[key] = session
    _register_session(session)
    return session


def close_thread_local_sessions() -> None:
    """Sluit alle SkillSessions van de huidige thread.

    Aan het eind van een ingest/backfill-batch aanroepen in een finally-blok.
    Idempotent.
    """
    sessions: dict[tuple[str, str], SkillSession] = getattr(_thread_local, "sessions", None) or {}
    for session in list(sessions.values()):
        try:
            session.close()
        except Exception as exc:
            logger.warning("Fout bij sluiten van SkillSession: %s", exc)
    sessions.clear()


def thread_session_stats() -> dict[tuple[str, str], _Stats]:
    """Geef per (skill, model) de stats van deze thread's sessions terug."""
    sessions: dict[tuple[str, str], SkillSession] = getattr(_thread_local, "sessions", None) or {}
    return {k: s.stats for k, s in sessions.items()}
